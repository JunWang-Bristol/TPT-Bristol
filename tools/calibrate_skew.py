#!/usr/bin/env python3
"""Current-channel skew calibration against an air-cored coil.

Why an air coil
---------------
Skew is a fixed timing offset between the current channel and the voltage
channels.  Core loss is the small IN-PHASE part of an almost purely reactive
signal, so a skew tau rotates reactive power into apparent loss:
``P_err ~ S·omega·tau``.  At 100 kHz with S ~ 2 VA against a ~0.5 W loss, 10 ns
is already ~3 %.  Clamp probes routinely carry 10-30 ns of delay, so leaving
the skew at zero is an assumption, not a default.

To measure it you need a device whose true loop area is *known*, not merely
small — otherwise you are fitting a minimum and calling it a calibration.  An
air core has no core loss, so the (i, lambda) loop area is exactly zero.  That
turns the problem into a root find against an exact target.

Two traps this avoids
---------------------
**Enclosed loop area, not the path integral.**  For an air coil
``v_sec = M·di/dt``, so integrating along an open path gives
``M·(i_end^2 - i_start^2)/2`` — pure artifact from a window that fails to
return to its starting current.  Windows cut on V_pri edges do not close on a
coil that rings.  The shoelace area joins the last point back to the first, so
an open trace is closed by construction.

**Long trains.**  With 8 pulses the analysed last cycle is still ringing down.
24 pulses is the minimum here.

The calibration is absolute: it must run with the bench's own skew correction
DISABLED (``current_channel_skew_s`` absent or 0), or the result is a
correction to a correction.

Usage:  python tools/calibrate_skew.py [--repeats 15]
"""
import argparse
import json
import os
import statistics
import sys
import time

import numpy as np

_TPT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_TPT_ROOT, 'src'))

from tpt import CoreLossMeasurement                       # noqa: E402

# The coil is sub-microhenry, so di/dt = V/L is enormous: drive it gently and
# fast.  These two frequencies exist to CHECK the answer, not to average it —
# a fixed instrumental offset must be the same at both, and a result that
# drifts with frequency means the model is wrong and the number is unusable.
FREQUENCIES = (300e3, 400e3)
RAIL_V = 3.0
I_LIMIT = 3.0
N_PULSES = 24
TAU_RANGE_S = 60e-9          # sweep +/- this
TAU_STEPS = 241


def shoelace(x, y):
    """Enclosed (signed) area of the closed polygon through (x, y)."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def loop_area_at(tau, t, i, lam, dt):
    """Area of the (i, lambda) loop with the current shifted by ``tau``.

    The shift is applied the way the scope applies its skew — a whole-sample
    roll — so the number this returns is directly usable as
    ``current_channel_skew_s`` rather than needing a sign convention argument.
    """
    n = int(round(tau / dt))
    return shoelace(np.roll(i, n), lam)


def zero_crossing(taus, areas):
    """The tau where the loop area crosses zero, linearly interpolated."""
    a = np.asarray(areas, float)
    sign = np.sign(a)
    idx = np.where(np.diff(sign) != 0)[0]
    if not len(idx):
        return None
    k = idx[len(idx) // 2]
    a0, a1 = a[k], a[k + 1]
    if a1 == a0:
        return float(taus[k])
    return float(taus[k] + (taus[k + 1] - taus[k]) * (-a0) / (a1 - a0))


def capture(m, frequency):
    b, s = m.board, m.scope
    T_half = 1.0 / (2.0 * frequency)
    try:
        b.flush_buffer()
    except Exception:                                      # noqa: BLE001
        pass
    b.clear_pulses()
    for _ in range(N_PULSES):
        b.add_pulse(T_half)

    dt = max(T_half / 60.0, 16e-9)
    n_samples = int(np.clip(N_PULSES * T_half * 1.4 / dt, 1000, 8000))
    for ch, rng, lab in ((m.CH_VOLTAGE, 5.0, 'V_pri'),
                         (m.CH_SECONDARY, 5.0, 'V_sec'),
                         (m.CH_CURRENT, 5.0, 'I')):
        s.set_channel_configuration(ch, rng, 'DC', 0.0)
        s.set_channel_label(ch, lab)
    s.set_probe_scale(m.CH_VOLTAGE, m.input_voltage_probe_scale)
    s.set_probe_scale(m.CH_SECONDARY, m.output_voltage_probe_scale)
    s.set_probe_scale(m.CH_CURRENT, m.current_probe_scale)
    s.set_number_samples(n_samples)
    s.set_sampling_time(dt)
    s.set_number_pre_trigger_samples(150)
    s.set_rising_trigger(m.CH_VOLTAGE, 0.3, timeout=3000)

    s.start_single_acquisition()
    time.sleep(0.5)
    b.run_pulses(1)
    deadline = time.monotonic() + 10
    while s.get_acquisition_state() != 'COMP':
        if time.monotonic() > deadline:
            return None
        time.sleep(0.02)
    df = s.read_data([m.CH_VOLTAGE, m.CH_SECONDARY, m.CH_CURRENT])
    return (df['time'].to_numpy(), df['V_pri'].to_numpy(),
            df['V_sec'].to_numpy(), df['I'].to_numpy())


def last_cycle_window(t, v, frequency):
    """Indices of the final complete +/- cycle, bounded by real edges."""
    th = 0.3 * np.abs(v).max()
    s = np.where(v > th, 1, np.where(v < -th, -1, 0))
    starts = [k for k in range(1, len(s)) if s[k] == 1 and s[k - 1] <= 0]
    for j in range(len(starts) - 1, 0, -1):
        i0, i1 = starts[j - 1], starts[j]
        if 0.7 / frequency <= t[i1] - t[i0] <= 1.3 / frequency and i1 - i0 > 30:
            return i0, i1
    return None, None


def one_estimate(m, frequency):
    out = capture(m, frequency)
    if out is None:
        return None
    t, vp, vs, i = out
    i0, i1 = last_cycle_window(t, vp, frequency)
    if i0 is None:
        return None
    tc, vsc, ic = t[i0:i1], vs[i0:i1], i[i0:i1]
    dt = float(np.median(np.diff(tc)))
    lam = np.cumsum(vsc) * dt          # flux linkage, integral of v_sec
    lam -= lam.mean()
    taus = np.linspace(-TAU_RANGE_S, TAU_RANGE_S, TAU_STEPS)
    areas = [loop_area_at(x, tc, ic, lam, dt) for x in taus]
    return zero_crossing(taus, areas)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repeats', type=int, default=15)
    args = ap.parse_args()

    cfg_path = os.path.join(_TPT_ROOT, 'hardware_configuration.json')
    cfg = json.load(open(cfg_path, encoding='utf-8'))
    existing = float(cfg.get('current_channel_skew_s', 0.0) or 0.0)
    if existing:
        print(f"REFUSING: current_channel_skew_s is already {existing*1e9:.2f} ns.\n"
              "This calibration must run against an uncorrected channel, or the\n"
              "result is a correction to a correction. Remove it and re-run.")
        return 2

    m = CoreLossMeasurement.from_config(cfg_path)
    per_f = {}
    try:
        m.psu.set_source_voltage(1, RAIL_V)
        m.psu.set_source_voltage(2, RAIL_V)
        m.psu.set_current_limit(1, I_LIMIT)
        m.psu.set_current_limit(2, I_LIMIT)
        m.psu.enable_output(1)
        m.psu.enable_output(2)
        time.sleep(0.8)

        for f in FREQUENCIES:
            vals = []
            for k in range(args.repeats):
                tau = one_estimate(m, f)
                if tau is not None:
                    vals.append(tau)
            per_f[f] = vals
            if vals:
                print(f"  {f/1e3:.0f} kHz  n={len(vals):2d}  "
                      f"mean {statistics.mean(vals)*1e9:+.2f} ns  "
                      f"sd {(statistics.stdev(vals)*1e9 if len(vals) > 1 else 0):.2f}")
            else:
                print(f"  {f/1e3:.0f} kHz  no usable captures")
    finally:
        try:
            m.psu.disable_output(1)
            m.psu.disable_output(2)
        except Exception:                                  # noqa: BLE001
            pass
        m.close()

    allv = [v for vals in per_f.values() for v in vals]
    if len(allv) < 2:
        print("\nnot enough estimates to calibrate")
        return 1

    mean = statistics.mean(allv)
    sd = statistics.stdev(allv)
    se = sd / (len(allv) ** 0.5)
    print("\n" + "=" * 66)
    print(f"  current_channel_skew_s = {mean*1e9:+.2f} ns  "
          f"+/- {se*1e9:.2f} ns (SE, n={len(allv)})")
    print("=" * 66)
    print("  Per-capture scatter is population spread, not something averaging\n"
          "  removes: the MEAN tightens as 1/sqrt(n), the sd does not.")
    if len(per_f) > 1 and all(per_f.values()):
        ms = {f: statistics.mean(v) for f, v in per_f.items() if v}
        spread = (max(ms.values()) - min(ms.values())) * 1e9
        print(f"\n  Frequency agreement: {spread:.2f} ns between "
              f"{'/'.join(f'{f/1e3:.0f}k' for f in ms)}")
        print("  A fixed instrumental offset MUST be the same at every frequency.")
        print("  " + ("consistent — usable as a calibration" if spread < 4 * se * 1e9
                      else "INCONSISTENT — do not use; the model is wrong"))
    print(f"\n  To apply, add to hardware_configuration.json:")
    print(f'      "current_channel_skew_s": {mean:.3e}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
