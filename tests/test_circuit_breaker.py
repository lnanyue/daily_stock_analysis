# -*- coding: utf-8 -*-
import time
import unittest
from data_provider.realtime_types import CircuitBreaker


class TestCircuitBreaker(unittest.TestCase):
    """Test the CircuitBreaker state machine transitions."""

    def setUp(self):
        self.cb = CircuitBreaker(
            failure_threshold=3,
            cooldown_seconds=0.5,
            half_open_max_calls=1,
        )

    def test_initial_state_closed_and_available(self):
        self.assertTrue(self.cb.is_available("test_source"))
        self.assertEqual(self.cb.get_status().get("test_source"), "closed")

    def test_after_failures_below_threshold_stays_closed(self):
        self.cb.record_failure("test_source")
        self.cb.record_failure("test_source")
        self.assertTrue(self.cb.is_available("test_source"))
        self.assertEqual(self.cb.get_status()["test_source"], "closed")

    def test_after_threshold_failures_opens_circuit(self):
        self.cb.record_failure("test_source")
        self.cb.record_failure("test_source")
        self.cb.record_failure("test_source")
        self.assertFalse(self.cb.is_available("test_source"))
        self.assertEqual(self.cb.get_status()["test_source"], "open")

    def test_open_circuit_blocks_access(self):
        self.cb.record_failure("test_source")
        self.cb.record_failure("test_source")
        self.cb.record_failure("test_source")
        self.assertFalse(self.cb.is_available("test_source"))

    def test_after_cooldown_transitions_to_half_open(self):
        self.cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.01, half_open_max_calls=1)
        self.cb.record_failure("test_source")
        self.assertFalse(self.cb.is_available("test_source"))
        time.sleep(0.02)
        self.assertTrue(self.cb.is_available("test_source"))
        self.assertEqual(self.cb.get_status()["test_source"], "half_open")

    def test_half_open_success_resets_to_closed(self):
        self.cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.01, half_open_max_calls=1)
        self.cb.record_failure("test_source")
        time.sleep(0.02)
        self.assertTrue(self.cb.is_available("test_source"))
        self.cb.record_success("test_source")
        self.assertEqual(self.cb.get_status()["test_source"], "closed")
        self.assertTrue(self.cb.is_available("test_source"))

    def test_half_open_failure_goes_back_to_open(self):
        self.cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.01, half_open_max_calls=1)
        self.cb.record_failure("test_source")
        time.sleep(0.02)
        self.assertTrue(self.cb.is_available("test_source"))
        self.cb.record_failure("test_source")
        self.assertEqual(self.cb.get_status()["test_source"], "open")
        self.assertFalse(self.cb.is_available("test_source"))

    def test_half_open_only_allows_max_calls(self):
        self.cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.01, half_open_max_calls=1)
        self.cb.record_failure("test_source")
        time.sleep(0.02)
        self.assertTrue(self.cb.is_available("test_source"))
        self.assertFalse(self.cb.is_available("test_source"))

    def test_record_success_resets_failure_count(self):
        self.cb.record_failure("test_source")
        self.cb.record_failure("test_source")
        self.cb.record_success("test_source")
        self.assertTrue(self.cb.is_available("test_source"))
        self.assertEqual(self.cb.get_status()["test_source"], "closed")

    def test_get_status_returns_all_sources(self):
        self.cb.record_failure("src_a")
        self.cb.record_failure("src_a")
        self.cb.record_failure("src_a")
        self.cb.record_failure("src_b")
        status = self.cb.get_status()
        self.assertEqual(status["src_a"], "open")
        self.assertEqual(status["src_b"], "closed")

    def test_reset_specific_source(self):
        self.cb.record_failure("test_source")
        self.cb.record_failure("test_source")
        self.cb.record_failure("test_source")
        self.assertEqual(self.cb.get_status()["test_source"], "open")
        self.cb.reset("test_source")
        self.assertNotIn("test_source", self.cb.get_status())

    def test_reset_all_sources(self):
        self.cb.record_failure("src_a")
        self.cb.record_failure("src_a")
        self.cb.record_failure("src_a")
        self.cb.reset()
        self.assertEqual(self.cb.get_status(), {})

    def test_multiple_sources_independent(self):
        self.cb.record_failure("src_a")
        self.cb.record_failure("src_a")
        self.cb.record_failure("src_a")
        self.assertFalse(self.cb.is_available("src_a"))
        self.assertTrue(self.cb.is_available("src_b"))
        self.assertEqual(self.cb.get_status()["src_a"], "open")
        self.assertEqual(self.cb.get_status()["src_b"], "closed")

    def test_half_open_calls_increment(self):
        self.cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.01, half_open_max_calls=3)
        self.cb.record_failure("test_source")
        time.sleep(0.02)
        # transition: OPEN→HALF_OPEN (counts as 1 call)
        self.assertTrue(self.cb.is_available("test_source"))
        # call 2
        self.assertTrue(self.cb.is_available("test_source"))
        # call 3 (max reached)
        self.assertTrue(self.cb.is_available("test_source"))
        # call 4 — blocked
        self.assertFalse(self.cb.is_available("test_source"))


if __name__ == "__main__":
    unittest.main()
