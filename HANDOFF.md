# Handoff: APEC TPT Rig

**Last verified: 2026-09-06** — rig working end-to-end, engine driving it.

## Current State

The ST2402 half-bridge rig is **operational and verified**. A `8 × 10 µs @ ±10 V`
pulse train produces a clean bipolar square wave; the last full run reached
0.02 % volt-second imbalance, and the train starts and ends at 0 V.

Core loss on TX26/15/10 3C90 agrees with the Princeton MagNet trained model to
**1.17× mean / 1.14× median** (n=7, zero bias), three points inside 5 %. That is
after four corrections, each measured rather than fitted:

| correction | effect |
|---|---|
| catalogue geometry (Ae 55.0 mm², not the stale 52.3 fallback) | 5.2 % in B, 6.7 % in Pv |
| centred opening half-pulse | removed a phantom H_dc ≈ 10 A/m; up to 38 % in Pv |
| current-channel skew +5.84 ± 2.12 ns (air-coil calibration) | −3 to −6 % |
| volt-second turns ratio, harmonic wattmeter, winding-loss split | — |

Nothing is tuned to flatter the agreement: `current_probe_scale` is left at the
probe's nominal 2.0, and applying the measured +4.9 % would move the ratio
*away* from 1.0.

## Hardware Setup

| Item | Value |
|---|---|
| Board | ST2402 half-bridge + NUCLEO-H503RB (`COM4`) |
| PSU | BK9129B (`COM5`) — CH1 = +rail, CH2 = −rail, both set to the same voltage |
| Scope | PicoScope 2408B (USB; the port field in the config is ignored) |
| Firmware | `OPEN_TPT,2402,00000000,0.2.1`, min pulse 10 ns, deadtime 100 ns |
| DUT | TX26/15/10 3C90 toroid, N1:N2 = 10:10 (same wire) |

**Three separate supplies are required.** This is the single most common
bring-up mistake:

1. **Bipolar ±V rails** — PSU CH1 and CH2, drive the DUT.
2. **12 V gate-driver supply** — powers the isolated DC/DC modules (`U1`, `U7`)
   feeding the gate drivers (`U6`, `U8`).
3. **USB** — powers the Nucleo control side.

Without (2) the MOSFETs never switch, but the Nucleo still accepts commands and
increments its pulse-train counter — so the rig looks alive in software while
`V_pri` stays flat at 0 V. It is indistinguishable from a firmware bug unless
you check the gate-driver rail.

## Verified Behaviour (2026-08-03)

| Check | Result |
|---|---|
| Idle before train | max\|V\| = 0.158 V — at 0 |
| Idle after train | settles to 0 (final 50 µs max\|V\| = 0.317 V) |
| Half-period count | 8 of 8 |
| Half-period widths | 9.70–10.08 µs (target 10 µs) |
| Volt-second imbalance | 0.28 % (net −2.19 V·µs of 785 V·µs) |
| Plateaus | +9.88 / −9.80 V, peaks ±10.15 V |
| Post-train LC ringdown | ~149 µs |

**Captures must be ≥ 300 µs.** The ringdown lasts ~149 µs after the train; a
shorter window catches ringing tails that look like extra pulses.

Automated suites: `board_tests.py` 6/6, `oscilloscope_tests.py` 12/12 (2 skips),
`power_supply_tests.py` 17/17 (6 skips, need a load resistor).

## Open Items

### 1. `current_probe_scale` reads 4.9 % low — NOT applied, deliberately
Measured against a 47 Ω ±1 % resistor, 8 captures per point:

```
 5 V  106 mA   R = 49.59 ± 0.05 Ω   +5.51 %
10 V  213 mA   R = 49.35 ± 0.02 Ω   +5.01 %
20 V  426 mA   R = 49.16 ± 0.02 Ω   +4.60 %
28 V  596 mA   R = 49.30 ± 0.01 Ω   +4.90 %
```

Flat to 0.91 % across a 5.6× span of current, so the probe is **linear** and
this is a fixed scale error, not an SNR artifact. R reading high means the
current reads low, implying `current_probe_scale ≈ 2.10`.

It is **left at 2.0** on purpose. The measurement is against `V_pri`, so the
4.9 % is the *product* of the current and voltage channel errors and cannot be
attributed to one of them from this test alone. Fitting the constant would hide
the discrepancy rather than resolve it. **A second resistor at another decade
(e.g. 470 Ω) would separate the two channels** — that is the next calibration
worth doing.

### 2. Low-flux repeatability is poor
The same point at 50 mT moved ±18 % between runs. Single-shot numbers below
~100 mT carry more uncertainty than the MagNet ratio suggests. Quantify with
`python -m opentpt accept <recipe> -o acc` (two runs, compared point by point)
before quoting anything at low flux.

### 3. The residual is frequency-dependent
50 kHz points agree within 5 %; 25 kHz and 100 kHz sit at 1.29–1.44×. A pure
scale error would be flat, so this is not probe scale — either MagNet's model
near the edges of its 25–200 kHz validity, or something in the drive at those
corners.

## Gotchas

- **`power_supply_tests.py` fuzzes the PSU OVP limit.** It now restores it in
  `tearDownClass`, but if a run is interrupted mid-suite the OVP can be left
  below your target rail — `set_source_voltage()` then silently does nothing and
  the rail reads 0 V. Check `psu.get_voltage_limit(ch)` if a rail won't come up.
- **PicoScope 2408B is 8-bit — only 256 codes across the range.** Keep signals
  in the upper half of the range. `tpt.py` now auto-picks the smallest range
  that fits (`_smallest_range_fitting`); a signal at 8 % of full scale is
  resolved with ~21 codes, which is not enough for `∫V·I dt`.
- **Scope range is ± full scale, not volts/div.** An earlier bug passed a
  current in amps as a volts range.
- **Firmware on the board can drift from the source tree.** Always confirm with
  `get_identification()` / `get_minimum_period()` rather than assuming.

## Rebuilding and Flashing Firmware

No STM32CubeIDE required — the toolchain is vendored at `arm-toolchain/`.

```bash
cd src/boards/NUCLEO-H503RB/firmware
GCC_PATH="C:/Users/Alfonso/OpenTPT/arm-toolchain/bin" bash build.sh
```

Flash by copying `build/TPT_SCPI_Server.bin` onto the ST-Link mass-storage drive
(mounts as `NOD_H503RB`). Confirm afterwards:

```python
board.get_identification()   # -> OPEN_TPT,2402,00000000,0.2.1
board.get_minimum_period()   # -> 1e-08
```

The pulse polarity logic lives in `Core/Src/tpt-scpi.c`. Commit `605a4bb` makes
`reset_pins()` drive **both** PB10 and PB4 low, so a train starts and ends at
0 V instead of leaving one pin high. Firmware on `APEC` is identical to
`origin/main`; `open-tpt` differs only in comments.

## Layout

- `opentpt/` — the measurement **engine**. A run is a pure function of one
  recipe file. `python -m opentpt validate|run|replay|analyse`. See
  `opentpt/README.md`.
  A desktop GUI over this engine (TPT Studio, PySide6) exists but is **not in
  this branch** — it is held back deliberately. The engine is the contract:
  everything measurable is reachable from the CLI, and the GUI is only ever a
  renderer of the engine's event stream.
- `recipes/` — run definitions. `tx26_3c90.recipe.json` is the reference;
  `tx26_3c90_coreloss.recipe.json` is the core-loss-only subset.
- `schemas/mas/` — the MAS JSON schema, vendored (Apache-2.0) so datasets can
  be validated offline. `VERSION.txt` records the upstream commit.
- `src/tpt.py` — the original measurement classes (`CoreLossMeasurement`,
  `InductanceMeasurement`). Superseded by `opentpt/` for sweeps; still the
  home of the instrument drivers the engine uses.
- `tools/` — standalone diagnostic and sweep scripts; all resolve paths via
  `_TPT_ROOT`, so they run from any working directory
- `tests/` — `unittest` suites. `test_engine_*.py` need
  **no hardware** (103 tests, they run in CI); the others drive the bench and
  take ports from `hardware_configuration.json`.
- `_local_scratch/` — unrelated files parked out of the way, safe to delete

Always use the venv: `venv\Scripts\python.exe`.

## Before quoting numbers again

1. **Separate the voltage and current channel scales.** The 47 Ω test gives
   their product as +4.9 %; a second resistor at another decade splits them.
   The acceptance harness cannot catch this — a systematic scale error
   reproduces perfectly.
2. **Run the acceptance check**: `python -m opentpt accept <recipe> -o acc`.
   Two hands-off runs, compared point by point. A point accepted in only one
   of them means the rig is sitting on a QC threshold.
3. **Re-run the MagNet comparison through the engine**, not through
   `tools/magnet_sweep.py` — that script still uses the old, wrong geometry.
4. **Check the recipe deadtime.** Recipes plan with `deadtime_s: 500e-9`, but
   the firmware actually runs **100 ns** (`CONF:DEAD?`). `flux_to_voltage`
   therefore over-estimates the rail needed, worst at high frequency.

## Wiring notes found by the engine

- **The sense winding was wired anti-phase** with the primary: `corr(V_pri,
  V_sec) = -1.000` and `∮i·v_sec dt` negative, while the current channel
  checked out clean on all four physics tests. Swapped at the terminals
  2026-08-06. The engine resolves polarity per capture from the sign of the
  energy integral either way, so archived CSVs from before the swap still
  reduce correctly and nothing in the config depends on it.
- **`current_probe_scale` is still uncalibrated** and remains the highest-value
  bench hour. The two saved capture sets disagree by ~1.75x in current at
  similar flux, which propagates linearly into every Pv and into the 1.25-1.27x
  MagNet ratio. Every dataset the engine writes carries this caveat.

## Core geometry was wrong (fixed 2026-08-06)

`src/tpt.py`'s `CORE_DATABASE` — and therefore every number in
`magnet_sweep_results.csv`, `magnet_comparison.csv` and the published
1.25-1.27x MagNet ratio — used **Ae = 52.3 mm2, le = 63.5 mm, Ve = 3.321 cm3**
for the TX26/15/10.  The OpenMagnetics catalogue gives **Ae = 55.0 mm2,
le = 64.40 mm, Ve = 3.542 cm3**.  That is -4.9 % on every B and -6.2 % on every
Pv.

`opentpt` now resolves geometry from the catalogue via `PyOpenMagnetics`
(`opentpt/omdb.py`), records the source in the dataset's provenance, and falls
back to the old table only when the package is missing - labelled
non-authoritative.  **`tools/magnet_sweep.py` and `tools/magnet_dcbias.py`
still use the old hardcoded geometry**; the MagNet comparison should be re-run
through the engine before the ratio is quoted again.

Rough effect on that ratio: Pv falls 6.2 %, but B falls 4.9 % and the MagNet
reference scales as B^2.6152, so the reference falls ~12 %.  Net, the ratio
*rises* by ~7 % (1.25-1.27x -> ~1.34-1.36x).  It moves away from unity, which
makes current-probe calibration an even more clearly dominant suspect.

## Two upstream bugs found while wiring this up

- **PyOpenMagnetics 1.6.4 pollutes site-packages.**  The wheel installs its
  whole source tree at the root: `AGENTS.md`, `CMakeLists.txt`, `LICENSE`,
  `README.md`, a bare `__init__.py`, `api/`, `docs/`, `examples/`, `src/`,
  `test.py` and - the one that bites - a top-level **`tests/`** package.  That
  shadows any project's own namespace-package `tests/`, so
  `python -m unittest tests.foo` dies with ModuleNotFoundError.  Worked around
  here by adding `tests/__init__.py` (a regular package beats a namespace
  portion regardless of sys.path order).
- **The MAS schema has unresolvable `$ref`s on its own.**  `schemas/` at MAS
  HEAD (5b03f31292) references `https://psma.com/peas/...` for
  `manufacturerInfo`, `resultOrigin`, `signalDescriptor` and 20 other defs.
  PEAS is a separate, non-public repo; MAS's `scripts/validate-samples.py`
  expects it checked out at `../PEAS`.  Anyone validating MAS documents from
  the MAS repo alone cannot check those subtrees.
