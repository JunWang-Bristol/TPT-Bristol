"""How the DC operating point is established — and the two bugs that hid there.

`first_pulse` is the classical TPT method and stays the default; `distributed`
is the opt-in alternative.  The tests that matter most here are not about
choosing between them, but about properties that held silently wrong before:

* the burst must start CENTRED, or every nominally unbiased point carries a
  spurious H_dc;
* a NEGATIVE bias request must actually bias negative.

No hardware: these are pure functions over the pulse list.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from opentpt.drive import (                       # noqa: E402
    BIAS_METHODS, DEFAULT_BIAS_METHOD, bias_volt_seconds, pulse_train,
    ramp_cycles,
)

F = 50e3
T_HALF = 1.0 / (2 * F)
L, V = 404e-6, 11.0


def train(**kw):
    kw.setdefault("n_pulses", 8)
    kw.setdefault("L_H", L)
    kw.setdefault("voltage", V)
    return pulse_train(F, **kw)


class TestCentring(unittest.TestCase):
    """The burst starts at B=0, so the opening pulse is HALF a half-period."""

    def test_unbiased_train_opens_with_a_half_pulse(self):
        p = train(dc_bias_A=0.0)
        self.assertAlmostEqual(p[0], T_HALF / 2.0, places=12)

    def test_every_later_pulse_is_a_full_half_period(self):
        for k, dt in enumerate(train(dc_bias_A=0.0)[1:], start=1):
            with self.subTest(pulse=k):
                self.assertAlmostEqual(dt, T_HALF, places=12)

    def test_an_unbiased_train_swings_symmetrically_about_zero(self):
        """Centring means the EXCURSION straddles zero, not that it ends there.

        The train legitimately finishes at -B-hat; what must be symmetric is
        the flux excursion.  With the opening half-pulse the flux alternates
        between +B-hat and -B-hat.  A full-length opening pulse instead runs
        0 -> +2B-hat and then swings 0..+2B-hat: the right amplitude with the
        whole minor loop offset by one B-hat, which is the spurious H_dc.
        """
        flux, running = [], 0.0
        for k, dt in enumerate(train(dc_bias_A=0.0)):
            running += dt if k % 2 == 0 else -dt
            flux.append(running)
        self.assertAlmostEqual(max(flux) + min(flux), 0.0, places=12)

    def test_a_full_opening_pulse_would_offset_the_loop(self):
        """The bug this centring fixes, pinned so it cannot come back."""
        p = train(dc_bias_A=0.0)
        p[0] = T_HALF                      # the old, uncentred behaviour
        flux, running = [], 0.0
        for k, dt in enumerate(p):
            running += dt if k % 2 == 0 else -dt
            flux.append(running)
        # offset by half a swing, i.e. one B-hat — not centred
        self.assertAlmostEqual(max(flux) + min(flux), T_HALF, places=12)


class TestBiasDirection(unittest.TestCase):

    def test_a_negative_request_lengthens_a_NEGATIVE_half_period(self):
        """Regression: abs() turned a request for -100 mA into +100 mA."""
        pos = train(dc_bias_A=+0.1)
        neg = train(dc_bias_A=-0.1)
        dt = bias_volt_seconds(0.1, L, V)
        # positive bias extends entry 0 (a high-side pulse)
        self.assertAlmostEqual(pos[0] - T_HALF / 2.0, dt, places=12)
        # negative bias leaves entry 0 alone and extends entry 1 (low side)
        self.assertAlmostEqual(neg[0], T_HALF / 2.0, places=12)
        self.assertAlmostEqual(neg[1] - T_HALF, dt, places=12)

    def test_the_two_directions_are_mirror_images_about_the_unbiased_train(self):
        """Compared against the unbiased baseline, not against zero.

        The centred train carries its own net offset (it ends at -B-hat), so
        the two directions mirror each other only once that common baseline is
        removed.
        """
        def net(p):
            return sum(dt if k % 2 == 0 else -dt for k, dt in enumerate(p))
        base = net(train(dc_bias_A=0.0))
        self.assertAlmostEqual(net(train(dc_bias_A=+0.1)) - base,
                               -(net(train(dc_bias_A=-0.1)) - base), places=12)


class TestMethodsAgree(unittest.TestCase):
    """Same volt-seconds, same end state — they differ only in the path."""

    def test_both_methods_deliver_identical_net_volt_seconds(self):
        def net(p):
            return sum(dt if k % 2 == 0 else -dt for k, dt in enumerate(p))
        a = train(dc_bias_A=0.1, bias_method="first_pulse")
        b = train(dc_bias_A=0.1, bias_method="distributed", n_pulses=16)
        self.assertAlmostEqual(net(a), net(b), places=12)

    def test_distributed_keeps_the_opening_pulse_short(self):
        """The point of the method: conduction stays pinned near T_half."""
        a = train(dc_bias_A=0.2, bias_method="first_pulse", n_pulses=16)
        b = train(dc_bias_A=0.2, bias_method="distributed", n_pulses=16)
        self.assertGreater(max(a), max(b))

    def test_bias_lead_one_reproduces_first_pulse_exactly(self):
        a = train(dc_bias_A=0.1, bias_method="first_pulse", n_pulses=16)
        b = train(dc_bias_A=0.1, bias_method="distributed", bias_lead=1.0,
                  n_pulses=16)
        for k, (x, y) in enumerate(zip(a, b)):
            with self.subTest(pulse=k):
                self.assertAlmostEqual(x, y, places=12)

    def test_default_is_the_classical_method(self):
        self.assertEqual(DEFAULT_BIAS_METHOD, "first_pulse")
        self.assertIn("distributed", BIAS_METHODS)

    def test_an_unknown_method_is_refused_not_guessed(self):
        with self.assertRaises(ValueError):
            train(dc_bias_A=0.1, bias_method="staircase")


class TestShortTrainCannotRampGently(unittest.TestCase):
    """Why `distributed` is not the default.

    The ramp must leave one full symmetric cycle behind it — the last cycle is
    the one analysed, and a ramp running to the end would be measured
    mid-climb.  At the default 8 pulses that leaves only 3 cycles for the ramp,
    which is why `distributed` needs n_pulses >= 16 to be worth choosing.
    """

    def test_default_train_caps_the_ramp_at_three_cycles(self):
        self.assertEqual(ramp_cycles("distributed", bias_cycles=4, n_pulses=8), 3)

    def test_a_longer_train_honours_the_request(self):
        self.assertEqual(ramp_cycles("distributed", bias_cycles=4, n_pulses=16), 4)

    def test_first_pulse_always_ramps_in_one(self):
        for n in (8, 16, 64):
            self.assertEqual(ramp_cycles("first_pulse", 4, n), 1)

    def test_the_ramp_never_consumes_the_final_cycle(self):
        for n_pulses in (8, 12, 16, 24):
            for req in (1, 4, 100):
                with self.subTest(n_pulses=n_pulses, bias_cycles=req):
                    self.assertLessEqual(
                        ramp_cycles("distributed", req, n_pulses),
                        n_pulses // 2 - 1)


if __name__ == "__main__":
    unittest.main()
