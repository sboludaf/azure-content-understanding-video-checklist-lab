from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("evaluation", ROOT / "scripts/evaluate.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
GOLD = json.loads((ROOT / "data/gold.json").read_text())


def report_for(clip_id="base"):
    clip = next(c for c in GOLD["clips"] if c["clip_id"] == clip_id)
    return {
        "source": "azure",  # Unit-test payload only, not service evidence.
        "clip": {"sha256": clip["sha256"], "duration_ms": clip["duration_ms"]},
        "events": [{**event, "evidence_status": "observed"} for event in clip["observed_events"]],
        "checklist": [],
    }


class EvaluationTests(unittest.TestCase):
    def test_exact_unit_payload_counts_once(self):
        actual = MODULE.evaluate(GOLD, "base", report_for())
        self.assertTrue(actual["passed"])
        self.assertEqual((actual["metrics"]["tp"], actual["metrics"]["fp"], actual["metrics"]["fn"]), (4, 0, 0))
        self.assertEqual(actual["metrics"]["max_start_error_ms"], 0)

    def test_duplicate_claim_is_false_positive(self):
        report = report_for()
        report["events"].append(deepcopy(report["events"][0]))
        actual = MODULE.evaluate(GOLD, "base", report)
        self.assertEqual(actual["metrics"]["tp"], 4)
        self.assertEqual(actual["metrics"]["fp"], 1)
        self.assertFalse(actual["passed"])

    def test_maximum_matching_avoids_greedy_loss(self):
        predicted = [{"step_id": "S1", "start_ms": 3000, "end_ms": 4000},
                     {"step_id": "S1", "start_ms": 1000, "end_ms": 2000}]
        gold_events = [{"step_id": "S1", "start_ms": 2000, "end_ms": 3000},
                       {"step_id": "S1", "start_ms": 4500, "end_ms": 5500}]
        self.assertEqual(len(MODULE.match_events(predicted, gold_events, 9000)), 2)

    def test_tolerance_boundary_is_inclusive(self):
        report = report_for()
        report["events"][0]["start_ms"] += 3000
        self.assertTrue(MODULE.evaluate(GOLD, "base", report)["passed"])
        report["events"][0]["start_ms"] += 1
        actual = MODULE.evaluate(GOLD, "base", report)
        self.assertFalse(actual["passed"])
        self.assertEqual(actual["metrics"]["fn"], 1)

    def test_empty_predictions_fail_zero_denominator(self):
        report = report_for()
        report["events"] = []
        actual = MODULE.evaluate(GOLD, "base", report)
        self.assertIsNone(actual["metrics"]["precision"])
        self.assertFalse(actual["passed"])
        self.assertIn("zero_denominator_is_failure", actual["reasons"])

    def test_empty_gold_is_failure(self):
        empty = deepcopy(GOLD)
        empty["clips"][0]["observed_events"] = []
        actual = MODULE.evaluate(empty, "base", report_for())
        self.assertIsNone(actual["metrics"]["recall"])
        self.assertFalse(actual["passed"])

    def test_fixture_never_yields_acceptance_metrics(self):
        report = report_for()
        report["source"] = "fixture"
        actual = MODULE.evaluate(GOLD, "base", report)
        self.assertFalse(actual["passed"])
        self.assertFalse(actual["eligible_for_acceptance_metrics"])
        self.assertIsNone(actual["metrics"])

    def test_removed_action_claim_is_failure(self):
        report = report_for("omitted")
        report["checklist"] = [{"id": "S1", "status": "observed"}]
        actual = MODULE.evaluate(GOLD, "omitted", report)
        self.assertFalse(actual["passed"])
        self.assertEqual(actual["metrics"]["removed_action_checklist_claims"], 1)

    def test_invalid_interval_fails_even_when_start_matches(self):
        report = report_for()
        report["events"][0]["end_ms"] = 37001
        actual = MODULE.evaluate(GOLD, "base", report)
        self.assertFalse(actual["passed"])
        self.assertEqual(actual["metrics"]["invalid_timed_event_count"], 1)
        self.assertEqual(actual["metrics"]["fn"], 1)

    def test_boolean_or_fractional_timestamp_is_invalid(self):
        for value in (True, 5200.5, float("nan")):
            event = {"step_id": "S1", "start_ms": value, "end_ms": 12800}
            self.assertFalse(MODULE.valid_interval(event, 37000))

    def test_wrong_input_hash_blocks_metrics(self):
        report = report_for()
        report["clip"]["sha256"] = "0" * 64
        actual = MODULE.evaluate(GOLD, "base", report)
        self.assertFalse(actual["eligible_for_acceptance_metrics"])

    def test_uncertain_is_not_an_observed_claim(self):
        report = report_for()
        report["events"][0]["evidence_status"] = "uncertain"
        actual = MODULE.evaluate(GOLD, "base", report)
        self.assertEqual(actual["metrics"]["tp"], 3)
        self.assertEqual(actual["metrics"]["fn"], 1)
        self.assertFalse(actual["passed"])


if __name__ == "__main__":
    unittest.main()
