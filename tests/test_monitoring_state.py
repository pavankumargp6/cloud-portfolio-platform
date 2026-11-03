"""Unit tests for app.monitoring.state — pure, no DB, no network."""

import unittest

from app.monitoring.state import (
    BLOCKED,
    DOWN,
    FAILURE,
    SUCCESS,
    UNKNOWN,
    UP,
    HealthState,
    apply_result,
    apply_threshold_change,
    outcome_to_kind,
)


class TestOutcomeToKind(unittest.TestCase):

    def test_up_is_success(self):
        self.assertEqual(outcome_to_kind("up"), SUCCESS)

    def test_blocked_ssrf_is_blocked(self):
        self.assertEqual(outcome_to_kind("blocked_ssrf"), BLOCKED)

    def test_down_is_failure(self):
        self.assertEqual(outcome_to_kind("down"), FAILURE)

    def test_timeout_is_failure(self):
        self.assertEqual(outcome_to_kind("timeout"), FAILURE)

    def test_tls_error_is_failure(self):
        self.assertEqual(outcome_to_kind("tls_error"), FAILURE)

    def test_dns_error_is_failure(self):
        self.assertEqual(outcome_to_kind("dns_error"), FAILURE)


class TestApplyResultThreshold3(unittest.TestCase):
    """Default threshold 3."""

    def test_unknown_first_failure_stays_unknown(self):
        s = apply_result(HealthState(UNKNOWN, 0), FAILURE, threshold=3)
        self.assertEqual(s, HealthState(UNKNOWN, 1))

    def test_unknown_second_failure_stays_unknown(self):
        s = apply_result(HealthState(UNKNOWN, 1), FAILURE, threshold=3)
        self.assertEqual(s, HealthState(UNKNOWN, 2))

    def test_unknown_third_failure_goes_down(self):
        s = apply_result(HealthState(UNKNOWN, 2), FAILURE, threshold=3)
        self.assertEqual(s, HealthState(DOWN, 3))

    def test_down_additional_failure_stays_down(self):
        s = apply_result(HealthState(DOWN, 3), FAILURE, threshold=3)
        self.assertEqual(s, HealthState(DOWN, 4))

    def test_down_first_success_goes_up(self):
        s = apply_result(HealthState(DOWN, 5), SUCCESS, threshold=3)
        self.assertEqual(s, HealthState(UP, 0))

    def test_up_first_failure_stays_up_cf1(self):
        s = apply_result(HealthState(UP, 0), FAILURE, threshold=3)
        self.assertEqual(s, HealthState(UP, 1))

    def test_up_second_failure_stays_up_cf2(self):
        s = apply_result(HealthState(UP, 1), FAILURE, threshold=3)
        self.assertEqual(s, HealthState(UP, 2))

    def test_up_third_failure_goes_down(self):
        s = apply_result(HealthState(UP, 2), FAILURE, threshold=3)
        self.assertEqual(s, HealthState(DOWN, 3))


class TestApplyResultThresholdVariants(unittest.TestCase):

    def test_threshold_1_first_failure_from_up_goes_down(self):
        s = apply_result(HealthState(UP, 0), FAILURE, threshold=1)
        self.assertEqual(s, HealthState(DOWN, 1))

    def test_threshold_1_first_failure_from_unknown_goes_down(self):
        s = apply_result(HealthState(UNKNOWN, 0), FAILURE, threshold=1)
        self.assertEqual(s, HealthState(DOWN, 1))

    def test_threshold_5_fourth_failure_stays_up(self):
        s = apply_result(HealthState(UP, 3), FAILURE, threshold=5)
        self.assertEqual(s, HealthState(UP, 4))

    def test_threshold_5_fifth_failure_goes_down(self):
        s = apply_result(HealthState(UP, 4), FAILURE, threshold=5)
        self.assertEqual(s, HealthState(DOWN, 5))


class TestBlockedLeavesStateUntouched(unittest.TestCase):

    def test_blocked_in_up(self):
        s = apply_result(HealthState(UP, 2), BLOCKED, threshold=3)
        self.assertEqual(s, HealthState(UP, 2))

    def test_blocked_in_unknown(self):
        s = apply_result(HealthState(UNKNOWN, 1), BLOCKED, threshold=3)
        self.assertEqual(s, HealthState(UNKNOWN, 1))

    def test_blocked_in_down(self):
        s = apply_result(HealthState(DOWN, 5), BLOCKED, threshold=3)
        self.assertEqual(s, HealthState(DOWN, 5))


class TestTransitionCallback(unittest.TestCase):

    def _capture(self):
        events = []
        return events, lambda f, t: events.append((f, t))

    def test_callback_fires_on_up_to_down(self):
        events, cb = self._capture()
        apply_result(HealthState(UP, 2), FAILURE, threshold=3, on_transition=cb)
        self.assertEqual(events, [(UP, DOWN)])

    def test_callback_fires_on_down_to_up(self):
        events, cb = self._capture()
        apply_result(HealthState(DOWN, 4), SUCCESS, threshold=3, on_transition=cb)
        self.assertEqual(events, [(DOWN, UP)])

    def test_callback_not_fired_when_state_unchanged(self):
        events, cb = self._capture()
        apply_result(HealthState(UP, 1), FAILURE, threshold=3, on_transition=cb)
        self.assertEqual(events, [])

    def test_callback_not_fired_on_blocked(self):
        events, cb = self._capture()
        apply_result(HealthState(UP, 1), BLOCKED, threshold=3, on_transition=cb)
        self.assertEqual(events, [])

    def test_callback_none_is_valid(self):
        s = apply_result(HealthState(UP, 2), FAILURE, threshold=3, on_transition=None)
        self.assertEqual(s, HealthState(DOWN, 3))


class TestThresholdChange(unittest.TestCase):

    def test_drop_threshold_below_current_cf_triggers_down(self):
        s = apply_threshold_change(HealthState(UP, 2), new_threshold=2)
        self.assertEqual(s, HealthState(DOWN, 2))

    def test_drop_threshold_above_current_cf_no_change(self):
        s = apply_threshold_change(HealthState(UP, 1), new_threshold=3)
        self.assertEqual(s, HealthState(UP, 1))

    def test_raise_threshold_does_not_auto_recover_down(self):
        # A down state stays down until success, regardless of threshold change.
        s = apply_threshold_change(HealthState(DOWN, 3), new_threshold=5)
        self.assertEqual(s, HealthState(DOWN, 3))

    def test_unknown_with_sufficient_cf_triggers_down(self):
        s = apply_threshold_change(HealthState(UNKNOWN, 1), new_threshold=1)
        self.assertEqual(s, HealthState(DOWN, 1))

    def test_threshold_change_fires_callback(self):
        events, cb = self._capture() if False else ([], lambda f, t: events.append((f, t)))
        # (if False above prevents NameError; use direct construction)
        events = []
        cb = lambda f, t: events.append((f, t))
        apply_threshold_change(HealthState(UP, 2), new_threshold=2, on_transition=cb)
        self.assertEqual(events, [(UP, DOWN)])

    def test_threshold_change_no_transition_no_callback(self):
        events = []
        cb = lambda f, t: events.append((f, t))
        apply_threshold_change(HealthState(UP, 1), new_threshold=3, on_transition=cb)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
