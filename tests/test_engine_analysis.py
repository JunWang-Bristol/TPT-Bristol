"""Unit tests for the opentpt reductions — no hardware required.

Two kinds of test here, and the distinction matters:

* **Synthetic** — a waveform whose answer is known analytically (a sinusoid at
  a set loss angle, a perfect square).  These pin the maths.
* **Golden** — real captures saved from this bench.  These pin the *pipeline*:
  cycle detection, integration, and the gates, against data that has already
  been cross-checked against Princeton MagNet.

Run with:  venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_engine_*.py"
"""

import os
import sys
import unittest

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from opentpt import analysis as an          # noqa: E402
from opentpt import qc as qcmod             # noqa: E402
from opentpt.drive import (                 # noqa: E402
    flux_to_voltage, pulse_train, voltage_to_flux,
)


def golden(name):
    return os.path.join(_ROOT, name)


# ─── Synthetic: the maths ─────────────────────────────────────────────────────

class TestComplexPermeability(unittest.TestCase):
    """A sinusoid with a known loss angle must come back with that angle."""

    def _make(self, mu_prime, mu_second, H_amp=100.0, n=4096):
        t = np.linspace(0.0, 1e-5, n)          # exactly one 100 kHz period
        w = 2 * np.pi / (t[-1] - t[0])
        H = H_amp * np.cos(w * (t - t[0]))
        # B = mu0 (mu' H_amp cos + mu'' H_amp sin)  ->  B lags H by delta
        B = an.MU0 * H_amp * (mu_prime * np.cos(w * (t - t[0]))
                              + mu_second * np.sin(w * (t - t[0])))
        return t, B, H

    def test_recovers_mu_prime_and_mu_second(self):
        t, B, H = self._make(2300.0, 250.0)
        mp, ms, tand = an.complex_permeability(t, B, H)
        self.assertAlmostEqual(mp, 2300.0, delta=2.0)
        self.assertAlmostEqual(ms, 250.0, delta=2.0)
        self.assertAlmostEqual(tand, 250.0 / 2300.0, delta=1e-3)

    def test_lossless_core_has_zero_mu_second(self):
        t, B, H = self._make(2300.0, 0.0)
        _, ms, _ = an.complex_permeability(t, B, H)
        self.assertAlmostEqual(ms, 0.0, delta=1.0)

    def test_mu_second_reproduces_the_loop_area(self):
        """Pv = pi f mu0 mu'' H^2 must equal the loop area for a sinusoid.

        For a pure sinusoid the two routes to loss are identical; any gap on a
        real (triangular) waveform is harmonic content, which is exactly why
        the engine reports both instead of one.
        """
        mu_second, H_amp = 250.0, 100.0
        t, B, H = self._make(2300.0, mu_second, H_amp=H_amp)
        f = 1.0 / (t[-1] - t[0])
        # ∮H dB over the cycle, per unit volume, times f
        pv_loop = f * float(np.trapezoid(H, B))
        pv_mu = np.pi * f * an.MU0 * mu_second * H_amp ** 2
        self.assertAlmostEqual(pv_loop / pv_mu, 1.0, delta=1e-3)


class TestLoopLandmarks(unittest.TestCase):

    def test_remanence_and_coercivity_of_an_ellipse(self):
        # Elliptical loop: B = Bm sin(t), H = Hm sin(t + phi).  At H=0 the flux
        # is Bm*sin(phi); at B=0 the field is Hm*sin(phi).
        n, Bm, Hm, phi = 4096, 0.2, 100.0, 0.3
        th = np.linspace(0, 2 * np.pi, n)
        B = Bm * np.sin(th)
        H = Hm * np.sin(th + phi)
        br, hc = an.remanence_coercivity(B, H)
        self.assertAlmostEqual(br, Bm * abs(np.sin(phi)), delta=1e-3)
        self.assertAlmostEqual(hc, Hm * abs(np.sin(phi)), delta=0.5)


class TestImbalance(unittest.TestCase):

    def test_symmetric_square_is_balanced(self):
        t = np.linspace(0, 40e-6, 4001)
        v = np.where(t < 20e-6, 10.0, -10.0)
        self.assertLess(an.volt_second_imbalance(t, v), 0.1)

    def test_lopsided_square_shows_up(self):
        t = np.linspace(0, 40e-6, 4001)
        v = np.where(t < 20e-6, 11.0, -10.0)
        # 1 V of 21 V over half the cycle ≈ 4.8 %
        self.assertGreater(an.volt_second_imbalance(t, v), 4.0)


class TestCycleDetection(unittest.TestCase):

    def _train(self, f=50e3, n_half=8, amp=10.0, dt=None):
        T_half = 1.0 / (2 * f)
        dt = dt or T_half / 100
        t = np.arange(0, n_half * T_half, dt)
        k = np.floor(t / T_half).astype(int)
        return t, np.where(k % 2 == 0, amp, -amp)

    def test_finds_the_expected_number_of_cycles(self):
        t, v = self._train(n_half=8)
        cycles = an.find_cycles(t, v, 50e3)
        # 8 half-periods hold 4 cycles, but the record opens already high, so
        # only 3 rising edges are *transitions* and they bound 2 windows.
        # A real capture has pre-trigger samples at 0 V and sees all 4.
        self.assertEqual(len(cycles), 2)

    def test_window_is_one_real_period(self):
        t, v = self._train()
        i0, i1 = an.last_full_cycle(t, v, 50e3)
        self.assertAlmostEqual(t[i1] - t[i0], 1 / 50e3, delta=1e-7)

    def test_ringing_spikes_are_not_cycles(self):
        t, v = self._train()
        v = v.copy()
        v[1200:1203] = 30.0            # a narrow ringing spike mid-negative-half
        i0, i1 = an.last_full_cycle(t, v, 50e3)
        self.assertIsNotNone(i0)
        self.assertAlmostEqual(t[i1] - t[i0], 1 / 50e3, delta=1e-7)

    def test_flat_record_yields_nothing(self):
        t = np.linspace(0, 1e-4, 1000)
        self.assertEqual(an.find_cycles(t, np.zeros_like(t), 50e3), [])


class TestDrivePlanning(unittest.TestCase):

    def test_flux_voltage_roundtrip(self):
        v = flux_to_voltage(0.2, 10, 52.3e-6, 25e3)
        b = voltage_to_flux(v, 10, 52.3e-6, 25e3)
        self.assertAlmostEqual(b, 0.2, places=9)

    def test_deadtime_raises_the_required_voltage(self):
        with_dt = flux_to_voltage(0.1, 10, 52.3e-6, 200e3, deadtime_s=500e-9)
        without = flux_to_voltage(0.1, 10, 52.3e-6, 200e3, deadtime_s=0.0)
        self.assertGreater(with_dt, without)
        # 500 ns of a 2.5 us half-period is 20 %
        self.assertAlmostEqual(with_dt / without, 1 / 0.8, places=6)

    def test_bias_lengthens_only_the_opening_pulse(self):
        """Bias rides on top of the centring half-pulse, not on a full one.

        This asserted ``p[0] - p[1] == I_dc·L/V``, which silently required the
        opening pulse to be full length — the very thing that offset the minor
        loop and put a spurious H_dc on unbiased points.  The bias term is now
        measured against the centred opening pulse (T_half/2).
        """
        p = pulse_train(50e3, n_pulses=8, dc_bias_A=0.2, L_H=404e-6, voltage=10.0)
        self.assertEqual(len(p), 8)
        self.assertGreater(p[0], p[1])
        bias_dt = 0.2 * 404e-6 / 10.0
        self.assertAlmostEqual(p[0] - 1e-5 / 2.0, bias_dt, places=12)
        # everything after the opening pulse stays a clean symmetric cycle
        self.assertTrue(all(abs(x - 1e-5) < 1e-12 for x in p[1:]))

    def test_unbiased_train_opens_centred_then_runs_uniform(self):
        """Was ``test_unbiased_train_is_uniform`` — which asserted the bug.

        A uniform train starts from B=0 with a FULL first pulse, driving the
        flux 0 → +2·B̂ so the whole minor loop sits one B̂ high.  The opening
        pulse is half length precisely so the excursion straddles zero.
        """
        p = pulse_train(50e3, n_pulses=8)
        self.assertAlmostEqual(p[0], 1e-5 / 2.0, places=12)
        self.assertTrue(all(abs(x - 1e-5) < 1e-12 for x in p[1:]))


class TestClippingDetector(unittest.TestCase):

    def test_flat_top_is_clipping(self):
        a = np.concatenate([np.linspace(0, 1, 50), np.ones(20),
                            np.linspace(1, 0, 50)])
        self.assertTrue(qcmod.is_clipped(a))

    def test_clean_triangle_is_not(self):
        a = np.concatenate([np.linspace(-1, 1, 200), np.linspace(1, -1, 200)])
        self.assertFalse(qcmod.is_clipped(a))

    def test_reaching_full_scale_is_clipping(self):
        a = np.linspace(-0.99, 0.99, 500)
        self.assertTrue(qcmod.is_clipped(a, full_scale=1.0))
        self.assertFalse(qcmod.is_clipped(a, full_scale=10.0))


# ─── Golden: real captures from this bench ────────────────────────────────────

class GoldenCaptureMixin:
    """Loads a saved capture and reduces it with the production path."""

    CSV = None
    FREQ = None
    N1 = N2 = 10
    AE, LE = 52.3e-6, 63.5e-3

    @classmethod
    def setUpClass(cls):
        import pandas as pd
        path = golden(cls.CSV)
        if not os.path.exists(path):
            raise unittest.SkipTest(f"{cls.CSV} not present")
        df = pd.read_csv(path)
        cls.a = an.analyse_cycle(
            df["time"].to_numpy(), df["V_pri"].to_numpy(),
            df["V_sec"].to_numpy(), df["Current"].to_numpy(),
            frequency=cls.FREQ, N1=cls.N1, N2=cls.N2,
            Ae=cls.AE, le=cls.LE, Ve=cls.AE * cls.LE,
        )

    def test_a_cycle_was_found(self):
        self.assertIsNotNone(self.a, "no closed cycle in a known-good capture")

    def test_cycle_period_matches_the_drive(self):
        # Slightly longer than 1/f is correct — the firmware deadtime sits
        # between pulses.  More than a few per cent means the window has run
        # into the post-train ringdown.
        ratio = self.a.period_s * self.FREQ
        self.assertGreaterEqual(ratio, 0.98)
        self.assertLessEqual(ratio, 1.05)

    def test_sense_winding_polarity_is_detected_and_corrected(self):
        # V_sec is wired anti-phase with V_pri on this bench; the reduction
        # must resolve that from the energy integral, not paper over it.
        self.assertTrue(self.a.sense_polarity_inverted)
        self.assertGreater(self.a.mu_prime, 0)

    def test_turns_ratio_matches_the_winding(self):
        self.assertAlmostEqual(self.a.turns_ratio_measured,
                               self.N2 / self.N1, delta=0.15)

    def test_capture_passes_the_default_gates(self):
        v = qcmod.evaluate(self.a, qcmod.QCPolicy())
        self.assertTrue(v.passed, v.reason)

    def test_loss_is_positive(self):
        # A passive core cannot generate energy.  This is the check that caught
        # the reversed current clamp; it must never regress.
        self.assertGreater(self.a.Q_cycle_J, 0.0)
        self.assertGreater(self.a.Pv_kW_m3, 0.0)

    def test_permeability_is_physical_for_a_ferrite(self):
        self.assertGreater(self.a.mu_secant, 500)
        self.assertLess(self.a.mu_secant, 10000)
        self.assertGreater(self.a.mu_prime, 0)
        self.assertGreater(self.a.mu_second, 0)      # lossy
        self.assertLess(self.a.tan_delta, 1.0)

    def test_power_uses_the_measured_repetition_rate(self):
        # P = Q / period_measured, not Q * f_nominal — the deadtime makes the
        # real period longer, and using the nominal rate overstates Pv.
        Ve = self.AE * self.LE
        self.assertAlmostEqual(
            self.a.P_core_W, self.a.Q_cycle_J * self.a.frequency_measured_Hz,
            places=9)
        self.assertAlmostEqual(
            self.a.Pv_kW_m3, self.a.P_core_W / Ve / 1e3, places=6)


class TestGolden25kHz200mT(GoldenCaptureMixin, unittest.TestCase):
    CSV = "coreloss_25kHz_200mT.csv"
    FREQ = 25e3

    def test_measured_flux_is_near_the_200_mT_target(self):
        self.assertAlmostEqual(self.a.B_peak_T, 0.220, delta=0.015)

    def test_loss_density_regression(self):
        # Pinned to what this stored capture reduces to today.  This is a
        # *pipeline* pin — it catches a change in cycle detection, integration
        # or windowing.  It is not a claim about absolute watts: every Pv on
        # this rig scales linearly with the uncalibrated current_probe_scale.
        self.assertAlmostEqual(self.a.Pv_kW_m3, 144.3, delta=8.0)

    def test_amplitude_permeability_matches_the_3C90_curve(self):
        # 3C90 amplitude permeability peaks in the low thousands around
        # 200 mT; a reduction bug usually lands orders of magnitude away.
        self.assertAlmostEqual(self.a.mu_secant, 4400, delta=400)


class TestGolden100kHz100mT(GoldenCaptureMixin, unittest.TestCase):
    CSV = "coreloss_100kHz_100mT.csv"
    FREQ = 100e3

    def test_measured_flux_is_near_the_100_mT_target(self):
        self.assertAlmostEqual(self.a.B_peak_T, 0.107, delta=0.012)

    def test_the_consensus_filter_rejected_the_ringdown_cycle(self):
        """Regression for the bug the consensus filter exists to kill.

        The last rise-to-rise window in this record closes inside the
        post-train ringdown: it runs 11.2 % long and reduces to 5.07 %
        imbalance and 116 mT.  The clean cycle before it is 1.2 % imbalance
        and 107 mT.  Without the median-period filter the contaminated window
        wins simply by being last — and it is plausible enough to publish.
        """
        cycles = None
        import pandas as pd
        df = pd.read_csv(golden(self.CSV))
        t, vp = df["time"].to_numpy(), df["V_pri"].to_numpy()
        cycles = an.find_cycles(t, vp, self.FREQ)
        for i0, i1 in cycles:
            self.assertLess((t[i1] - t[i0]) * self.FREQ, 1.05)
        self.assertLess(self.a.imbalance_pct, 2.0)


class TestKnownBadCapturesAreRejected(unittest.TestCase):
    """The gates must reject the captures that produced the 16535x nonsense.

    ``magnet_25kHz_200mT.csv`` and ``magnet_100kHz_100mT.csv`` are saved from
    the run where ``measure_core_loss``'s balancing loop diverged: it trimmed
    the negative rail from a clipped current reading, drove V_neg to 31 V
    against a 23 V positive rail, saturated the core and railed the probe.  The
    recorded outcome is in ``magnet_comparison.csv`` — a 402x and a 16535x
    ratio against MagNet.

    Both files reduce to confident, entirely wrong numbers.  Nothing about them
    is obviously broken to the eye, which is exactly why the gates exist, and
    why this test is here: if a future change lets either of these through, the
    QC policy has stopped working.
    """

    CASES = [("magnet_25kHz_200mT.csv", 25e3), ("magnet_100kHz_100mT.csv", 100e3)]

    def test_rejected(self):
        import pandas as pd
        for name, f in self.CASES:
            path = golden(name)
            if not os.path.exists(path):
                continue
            with self.subTest(capture=name):
                df = pd.read_csv(path)
                a = an.analyse_cycle(
                    df["time"].to_numpy(), df["V_pri"].to_numpy(),
                    df["V_sec"].to_numpy(), df["Current"].to_numpy(),
                    frequency=f, N1=10, N2=10, Ae=52.3e-6, le=63.5e-3,
                    Ve=52.3e-6 * 63.5e-3)
                v = qcmod.evaluate(a, qcmod.QCPolicy())
                self.assertFalse(v.passed,
                                 f"{name} passed the gates — QC has regressed")
                self.assertIn("imbalance", [fl.gate for fl in v.failures])


if __name__ == "__main__":
    unittest.main(verbosity=2)
