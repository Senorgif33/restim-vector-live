import unittest

from vector1a.routing import AuthoredAxisRouter
from vector1a.tcode import TCodeCommand


class AuthoredAxisRouterTests(unittest.TestCase):
    def test_l0_can_be_enabled_for_manual_passthrough(self):
        router = AuthoredAxisRouter(clock=lambda: 0.0)
        router.set_enabled("L0", True)
        self.assertEqual(router.enabled_axes(), {"L0"})

    def test_enabled_axis_is_sampled_on_original_timeline(self):
        router = AuthoredAxisRouter(clock=lambda: 0.0)
        router.set_enabled("L1", True)
        router.receive(TCodeCommand("L1", .2), 1.0)
        router.receive(TCodeCommand("L1", .8), 2.0)
        self.assertAlmostEqual(router.snapshot(1.5)["L1"], .2)
        self.assertAlmostEqual(router.snapshot(2.0)["L1"], .8)

    def test_interval_axis_interpolates_until_target(self):
        router = AuthoredAxisRouter(clock=lambda: 0.0)
        router.set_enabled("R0", True)
        router.receive(TCodeCommand("R0", .2), 1.0)
        router.receive(TCodeCommand("R0", .8, 1000), 2.0)
        self.assertAlmostEqual(router.snapshot(2.0)["R0"], .2)
        self.assertAlmostEqual(router.snapshot(2.5)["R0"], .5)
        self.assertAlmostEqual(router.snapshot(3.0)["R0"], .8)

    def test_unchecked_axis_is_observed_but_not_routed(self):
        router = AuthoredAxisRouter(clock=lambda: 0.0)
        router.receive(TCodeCommand("P1", .7), 1.0)
        self.assertIn("P1", router.available_axes())
        self.assertEqual(router.snapshot(1.0), {})

    def test_auto_mode_stays_vector_generated_for_plain_l0_motion(self):
        router = AuthoredAxisRouter(clock=lambda: 0.0)
        router.receive(TCodeCommand("L0", .25), 1.0)
        self.assertFalse(router.auto_authored_active(1.0))
        self.assertEqual(router.snapshot_auto(1.0), {})

    def test_auto_mode_routes_complete_authored_set_when_restim_signature_present(self):
        router = AuthoredAxisRouter(clock=lambda: 0.0)
        for axis, value in (("L0", .2), ("L1", .8), ("V0", .45), ("C0", .6), ("R0", .3)):
            router.receive(TCodeCommand(axis, value), 1.0)
        self.assertTrue(router.auto_authored_active(1.0))
        snap = router.snapshot_auto(1.0)
        self.assertAlmostEqual(snap["L0"], .2)
        self.assertAlmostEqual(snap["L1"], .8)
        self.assertAlmostEqual(snap["V0"], .45)
        self.assertAlmostEqual(snap["R0"], .3)

    def test_auto_mode_keeps_generated_fallback_for_missing_axes(self):
        router = AuthoredAxisRouter(clock=lambda: 0.0)
        router.receive(TCodeCommand("L0", .2), 1.0)
        router.receive(TCodeCommand("V0", .45), 1.0)
        snap = router.snapshot_auto(1.0)
        self.assertEqual(set(snap), {"L0", "V0"})
        self.assertNotIn("P1", snap)

    def test_auto_mode_routes_s1_with_restim_signature(self):
        router = AuthoredAxisRouter(clock=lambda: 0.0)
        router.receive(TCodeCommand("V0", .45), 1.0)
        router.receive(TCodeCommand("S1", .9), 1.0)
        snap = router.snapshot_auto(1.0)
        self.assertAlmostEqual(snap["S1"], .9)

    def test_manual_mode_routes_enabled_s1(self):
        router = AuthoredAxisRouter(clock=lambda: 0.0)
        router.set_enabled("S1", True)
        router.receive(TCodeCommand("S1", .75), 1.0)
        self.assertAlmostEqual(router.snapshot(1.0)["S1"], .75)

    def test_auto_mode_drops_stale_axis_and_uses_vector_fallback(self):
        router = AuthoredAxisRouter(clock=lambda: 0.0)
        router.receive(TCodeCommand("L0", .2), 1.0)
        router.receive(TCodeCommand("V0", .45), 1.0)
        self.assertIn("V0", router.snapshot_auto(1.5))
        self.assertEqual(router.snapshot_auto(2.1), {})


if __name__ == "__main__":
    unittest.main()


def test_auto_snapshot_uses_historical_freshness_despite_newer_packets():
    """Regression: a 2 s delayed sample must not be invalidated by future history."""
    from vector1a.routing import AuthoredAxisRouter
    from vector1a.tcode import TCodeCommand
    router = AuthoredAxisRouter(clock=lambda: 10.0)
    # Commands around the original sample time.
    router.receive(TCodeCommand("L0", .80), 5.00)
    router.receive(TCodeCommand("V0", .45), 5.00)
    router.receive(TCodeCommand("L0", .82), 5.10)
    router.receive(TCodeCommand("V0", .50), 5.10)
    # Newer packets arrive during Vector's look-ahead delay. Alpha 45 used these
    # timestamps for freshness and therefore rejected the historical snapshot.
    router.receive(TCodeCommand("L0", .20), 7.00)
    router.receive(TCodeCommand("V0", .90), 7.00)
    snap = router.snapshot_auto(5.10)
    assert abs(snap["L0"] - .82) < 1e-9
    assert abs(snap["V0"] - .50) < 1e-9

def test_auto_snapshot_falls_back_when_axis_was_stale_at_historical_time():
    from vector1a.routing import AuthoredAxisRouter
    from vector1a.tcode import TCodeCommand
    router = AuthoredAxisRouter(clock=lambda: 10.0)
    router.receive(TCodeCommand("V0", .40), 2.0)
    router.receive(TCodeCommand("L0", .70), 4.0)
    # A future V0 must not make V0 appear live at t=4.0.
    router.receive(TCodeCommand("V0", .90), 7.0)
    snap = router.snapshot_auto(4.0)
    assert snap == {}
