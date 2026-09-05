"""The event stream — the engine's only output channel while a run is live.

Everything a caller can learn about a run in progress arrives as an event:
the CLI prints them, the journal writes them as JSONL, and TPT Studio renders
them.  There is no second path, so the GUI can never show something the
journal does not also record.

The journal is the audit trail for the QC policy: every retry, every gate
verdict and every skip is in it, which is what lets a gap in the dataset be
explained months later.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# Event kinds, listed so a consumer can switch exhaustively.
RUN_STARTED = "run_started"
RUN_FINISHED = "run_finished"
PROCEDURE_STARTED = "procedure_started"
PROCEDURE_FINISHED = "procedure_finished"
POINT_STARTED = "point_started"
POINT_SKIPPED = "point_skipped"        # refused at validation, never attempted
CAPTURE = "capture"                    # one attempt, pass or fail
VERDICT = "verdict"                    # QC outcome of an attempt
POINT_RESULT = "point_result"          # accepted measurement
POINT_REJECTED = "point_rejected"      # all attempts failed the gates
CAPTURE_TRACE = "capture_trace"        # decimated waveform + B-H loop, for UIs
POINT_RESTORED = "point_restored"      # recovered from a prior journal, not re-measured
BENCH_CHECK = "bench_check"
LOG = "log"
WARNING = "warning"


@dataclass
class Event:
    kind: str
    data: Dict[str, Any] = field(default_factory=dict)
    t: float = field(default_factory=time.time)

    def to_dict(self):
        return {"t": self.t, "kind": self.kind, **self.data}

    def to_json(self):
        return json.dumps(self.to_dict(), default=_jsonable)


def _jsonable(o):
    """Last-resort encoder: numpy scalars and arrays are common in payloads."""
    if hasattr(o, "item") and getattr(o, "size", 1) == 1:
        return o.item()
    if hasattr(o, "tolist"):
        return o.tolist()
    return str(o)


class EventBus:
    """Fan-out of events to subscribers and, optionally, a JSONL journal.

    A subscriber that raises is dropped rather than allowed to kill the run —
    a broken UI must not cost a bench hour.  The failure is re-emitted as a
    warning so it is still visible in the journal.
    """

    def __init__(self, journal_path: Optional[str] = None):
        self._subscribers: List[Callable[[Event], None]] = []
        self._journal = None
        self.events: List[Event] = []
        if journal_path:
            p = Path(journal_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._journal = p.open("a", encoding="utf-8")

    def subscribe(self, fn: Callable[[Event], None]):
        self._subscribers.append(fn)
        return fn

    def emit(self, kind: str, **data) -> Event:
        ev = Event(kind, data)
        self.events.append(ev)
        if self._journal is not None:
            self._journal.write(ev.to_json() + "\n")
            self._journal.flush()          # a killed run must keep its journal
        for fn in list(self._subscribers):
            try:
                fn(ev)
            except Exception as exc:       # noqa: BLE001 — see docstring
                self._subscribers.remove(fn)
                self.events.append(Event(WARNING, {
                    "message": f"event subscriber removed after error: {exc!r}"}))
        return ev

    def log(self, message, **data):
        return self.emit(LOG, message=message, **data)

    def warn(self, message, **data):
        return self.emit(WARNING, message=message, **data)

    def close(self):
        if self._journal is not None:
            self._journal.close()
            self._journal = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


_CONSOLE_ENCODING = (getattr(sys.stdout, "encoding", None) or "utf-8")


# The console on this bench is cp1252.  Messages are written in ordinary prose
# with typographic punctuation and SI symbols; rather than police that at every
# call site, transliterate here so "mu''" stays readable instead of becoming
# "?".  Anything not in the table still falls back to "?" rather than raising —
# a run must never die because of a character in a log line.
_TRANSLIT = str.maketrans({
    "—": "-", "–": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", "·": "-",
    "µ": "u", "μ": "u", "×": "x", "²": "^2",
    "³": "^3", "→": "->", "≥": ">=", "≤": "<=",
    "Δ": "delta", "δ": "delta", "′": "'", "″": "''",
})


def _safe(text):
    """Transliterate `text` to plain ASCII for the console.

    Not conditional on ``sys.stdout.encoding``: that reports cp1252, which
    *can* encode an em-dash — and the Windows console then renders it as ``?``
    anyway, because the active codepage is not cp1252.  Testing the declared
    encoding therefore passes and the output is still mangled.  ASCII always
    survives, and the JSONL journal keeps the full UTF-8 text regardless.
    """
    return text.translate(_TRANSLIT).encode("ascii", "replace").decode("ascii")


def emit(text):
    print(_safe(text))


def jsonl_writer(stream=None):
    """A subscriber that writes each event as one JSON line.

    This is the GUI's entire input.  TPT Studio runs the engine as a child
    process and renders this stream — there is no second channel and no shared
    memory, so the UI cannot display a number the journal does not also hold,
    and killing the UI cannot disturb a run in progress.

    The stream is flushed per event: a consumer reading line-by-line must see
    progress as it happens, not in block-buffered bursts.
    """
    out = stream if stream is not None else sys.stdout
    if out is None:
        # A windowed PyInstaller build can leave sys.stdout as None even when
        # the parent handed it a pipe. The event stream is the GUI's only
        # input, so fall back to the OS-level descriptor rather than crashing
        # the run the moment it tries to report anything.
        import io
        import os
        out = io.TextIOWrapper(io.FileIO(1, "w", closefd=False),
                               encoding="utf-8", line_buffering=True)
        del os

    def _write(ev: Event):
        out.write(ev.to_json() + "\n")
        out.flush()

    return _write


def console_printer(verbose=True):
    """A subscriber that renders the stream as the bench log we already read.

    Output goes through :func:`_safe` because the default Windows console
    codepage is cp1252: an em-dash or a µ anywhere in a message — including
    inside a QC failure reason written elsewhere — otherwise prints as ``?``
    or, worse, raises mid-run.
    """

    def _print(ev: Event):
        d = ev.data
        k = ev.kind
        if k == RUN_STARTED:
            emit(f"=== run '{d.get('recipe')}' - {d.get('n_procedures')} procedures")
        elif k == PROCEDURE_STARTED:
            pos = (f"[{d['index']}/{d.get('of', '?')}] "
                   if d.get("index") is not None else "")
            emit(f"\n--- {pos}{d.get('type')}"
                 f"{' - ' + d['label'] if d.get('label') else ''}"
                 f"  ({d.get('n_points', '?')} points)")
        elif k == POINT_STARTED and verbose:
            emit(f"  - {d.get('tag')}")
        elif k == POINT_SKIPPED:
            emit(f"    SKIP {d.get('tag')}: {d.get('reason')}")
        elif k == CAPTURE and verbose:
            emit(f"      attempt {d.get('attempt')}: {d.get('summary', '')}")
        elif k == POINT_RESULT:
            emit(f"    OK   {d.get('tag')}: {d.get('summary')}")
        elif k == POINT_RESTORED:
            emit(f"    KEPT {d.get('tag')}: restored from the previous journal")
        elif k == POINT_REJECTED:
            emit(f"    REJECT {d.get('tag')}: {d.get('reason')}")
        elif k == BENCH_CHECK:
            mark = "ok" if d.get("ok") else "FAIL"
            emit(f"  [{mark}] {d.get('check')}: {d.get('detail')}")
        elif k == WARNING:
            emit(f"  WARNING: {d.get('message')}")
        elif k == LOG and verbose:
            emit(f"  {d.get('message')}")
        elif k == PROCEDURE_FINISHED:
            emit(f"    -> {d.get('accepted')} accepted, {d.get('rejected')} rejected,"
                  f" {d.get('skipped')} skipped")
        elif k == RUN_FINISHED:
            emit(f"\n=== run finished: {d.get('accepted')} points in "
                  f"{d.get('duration_s', 0):.0f} s")

    return _print
