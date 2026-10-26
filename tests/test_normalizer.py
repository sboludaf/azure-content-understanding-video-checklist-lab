"""No Azure execution: normalization and honest uncertainty from recorded shapes."""
from copy import deepcopy
import unittest

from video_checklist.azure import evidence_field_name
from video_checklist.normalizer import normalize

STEPS = [{"id": "s1", "label": "Preparar"}, {"id": "s2", "label": "Formar"}, {"id": "s3", "label": "Ajustar"}]
CLIP = {"clip_id": "base", "duration_ms": 12000, "sha256": "a" * 64, "license": "CC BY-SA 4.0", "token": "private-token"}


def field(value):
    if isinstance(value, str):
        return {"type": "string", "valueString": value}
    if isinstance(value, int):
        return {"type": "integer", "valueInteger": value}
    if isinstance(value, list):
        return {"type": "array", "valueArray": [field(v) for v in value]}
    if isinstance(value, dict):
        return {"type": "object", "valueObject": {k: field(v) for k, v in value.items()}}
    return {"type": "integer", "valueInteger": None}


def raw(rows=None, segments=None):
    return {"status": "Succeeded", "result": {"apiVersion": "2025-11-01", "contents": [{
        "kind": "audioVisual", "startTimeMs": 0, "endTimeMs": 12000,
        "segments": segments or [], "fields": {"Segments": field(rows or [])}}]}}


def entry(step="s1", segment="seg1", evidence="observed", **extra):
    return {"StepId": step, "SegmentId": segment, "Action": "Acción visible",
            "Objects": ["cuerda"], "EvidenceStatus": evidence, **extra}


def independent_raw(rows_by_step, historical=False):
    data = raw()
    data["result"]["contents"][0]["fields"] = {
        ( "Evidence_" + step["id"] if historical else evidence_field_name(step)): field(rows_by_step.get(step["id"], []))
        for step in STEPS
    }
    return data


def run(data, source="azure"):
    return normalize(data, STEPS, CLIP, "manual-actions-v1", "2025-11-01", "gpt-4.1", source)


class NormalizerTests(unittest.TestCase):
    def test_independent_empty_arrays_never_manufacture_events(self):
        result = run(independent_raw({}))
        self.assertEqual(result["events"], [])
        self.assertEqual([row["status"] for row in result["checklist"]], ["not_observed"] * 3)
        self.assertEqual(result["issues"], [])

    def test_all_candidate_fields_missing_means_uncertain_not_absent(self):
        data = independent_raw({})
        data["result"]["contents"][0]["fields"] = {}
        result = run(data)
        self.assertEqual(result["events"], [])
        self.assertEqual([row["status"] for row in result["checklist"]], ["uncertain"] * 3)

    def test_candidate_field_identity_cannot_be_overridden_by_row(self):
        occurrence = entry(step="invented", EstimatedStartMs=1000, EstimatedEndMs=3000)
        result = run(independent_raw({"s2": [occurrence]}))
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["step_id"], "s2")
        self.assertEqual([row["status"] for row in result["checklist"]], ["not_observed", "observed", "not_observed"])

    def test_mixed_historic_and_candidate_fields_do_not_duplicate_claims(self):
        occurrence = entry(EstimatedStartMs=1000, EstimatedEndMs=3000)
        data = independent_raw({"s1": [occurrence]})
        data["result"]["contents"][0]["fields"]["Segments"] = field([occurrence])
        result = run(data)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["step_id"], "s1")
        self.assertEqual(len(result["checklist"][0]["events"]), 1)

    def test_independent_field_assigns_id_only_to_returned_occurrences(self):
        occurrence = entry(EstimatedStartMs=6000, EstimatedEndMs=8000)
        del occurrence["StepId"]
        early = deepcopy(occurrence)
        early.update(EstimatedStartMs=1000, EstimatedEndMs=3000)
        data = independent_raw({"s1": [occurrence], "s3": [early]})
        original = deepcopy(data)
        result = run(data)
        self.assertEqual(data, original)
        self.assertEqual([event["step_id"] for event in result["events"]], ["s1", "s3"])
        self.assertTrue(result["order_changed"])
        self.assertEqual(result["checklist"][1]["status"], "not_observed")
        self.assertEqual(result["checklist"][1]["events"], [])
        self.assertTrue(all(event["time_source"] == "model_estimate" for event in result["events"]))

    def test_historic_per_id_fields_still_normalize_identically(self):
        occurrence = entry(EstimatedStartMs=1000, EstimatedEndMs=3000)
        del occurrence["StepId"]
        rows = {"s2": [occurrence]}
        self.assertEqual(run(independent_raw(rows)), run(independent_raw(rows, historical=True)))

    def test_mixed_opaque_and_historic_candidate_fields_do_not_duplicate_claims(self):
        occurrence = entry(step="invented", EstimatedStartMs=1000, EstimatedEndMs=3000)
        data = independent_raw({"s2": [occurrence]})
        data["result"]["contents"][0]["fields"]["Evidence_s2"] = field([occurrence])
        result = run(data)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["step_id"], "s2")

    def test_opaque_field_for_unauthorized_label_does_not_supply_evidence(self):
        occurrence = entry(EstimatedStartMs=1000, EstimatedEndMs=3000)
        data = independent_raw({})
        fields = data["result"]["contents"][0]["fields"]
        del fields[evidence_field_name(STEPS[1])]
        wrong_key = evidence_field_name(dict(STEPS[1], label="A different unauthorized action"))
        fields[wrong_key] = field([occurrence])
        result = run(data)
        self.assertEqual(result["events"], [])
        self.assertEqual([row["status"] for row in result["checklist"]], ["not_observed", "uncertain", "not_observed"])

    def test_independent_uncertain_occurrence_stays_uncertain(self):
        occurrence = entry(evidence="uncertain", EstimatedStartMs=1000, EstimatedEndMs=3000)
        del occurrence["StepId"]
        result = run(independent_raw({"s2": [occurrence]}))
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["step_id"], "s2")
        self.assertEqual(result["events"][0]["evidence_status"], "uncertain")
        self.assertEqual([row["status"] for row in result["checklist"]], ["not_observed", "uncertain", "not_observed"])

    def test_independent_missing_or_malformed_field_is_not_negative_evidence(self):
        for replacement in (None, field("incorrectly not an array")):
            data = independent_raw({})
            fields = data["result"]["contents"][0]["fields"]
            if replacement is None:
                del fields[evidence_field_name(STEPS[1])]
            else:
                fields[evidence_field_name(STEPS[1])] = replacement
            with self.subTest(replacement=replacement):
                result = run(data)
                self.assertEqual(result["events"], [])
                self.assertEqual([row["status"] for row in result["checklist"]], ["not_observed", "uncertain", "not_observed"])
                self.assertIn("content_0_missing_or_invalid_" + evidence_field_name(STEPS[1]), result["issues"])

    def test_independent_invalid_interval_cannot_become_observed(self):
        occurrence = entry(EstimatedStartMs=3000, EstimatedEndMs=1000)
        del occurrence["StepId"]
        result = run(independent_raw({"s2": [occurrence]}))
        event = result["events"][0]
        self.assertIsNone(event["start_ms"])
        self.assertIsNone(event["end_ms"])
        self.assertEqual(event["step_id"], "s2")
        self.assertEqual(event["evidence_status"], "uncertain")
        self.assertEqual(event["time_source"], "unavailable")

    def test_independent_malformed_occurrence_affects_only_its_candidate(self):
        result = run(independent_raw({"s2": [None]}))
        self.assertEqual(result["events"], [])
        self.assertEqual([row["status"] for row in result["checklist"]], ["not_observed", "uncertain", "not_observed"])
        self.assertIn("event-1-1_malformed", result["issues"])

    def test_unrecognized_candidate_field_never_becomes_an_event(self):
        data = independent_raw({})
        data["result"]["contents"][0]["fields"]["Evidence_invented"] = field([
            entry(EstimatedStartMs=1000, EstimatedEndMs=3000)])
        result = run(data)
        self.assertEqual(result["events"], [])
        self.assertEqual([row["status"] for row in result["checklist"]], ["not_observed"] * 3)

    def test_service_segment_times_keep_milliseconds(self):
        data = raw([entry()], [{"segmentId": "seg1", "startTimeMs": 1234, "endTimeMs": 5678}])
        result = run(data)
        event = result["events"][0]
        self.assertEqual((event["start_ms"], event["end_ms"]), (1234, 5678))
        self.assertEqual(event["time_source"], "service_segment")
        self.assertEqual(event["evidence_status"], "observed")
        self.assertEqual(result["checklist"][0]["events"], [event["id"]])
        self.assertNotIn("token", result["clip"])

    def test_model_generated_time_is_explicitly_estimated(self):
        result = run(raw([entry(EstimatedStartMs=1000, EstimatedEndMs=3000)]))
        self.assertEqual(result["events"][0]["time_source"], "model_estimate")
        self.assertEqual(result["events"][0]["start_ms"], 1000)

    def test_invalid_or_null_time_is_uncertain_without_seek(self):
        for start, end in ((None, 3000), (-1, 3000), (3000, 1000), (1000, 13000), (1000.5, 3000), (True, 3000)):
            data = raw([entry()], [{"segmentId": "seg1", "startTimeMs": start, "endTimeMs": end}])
            with self.subTest(start=start, end=end):
                result = run(data)
                event = result["events"][0]
                self.assertIsNone(event["start_ms"])
                self.assertIsNone(event["end_ms"])
                self.assertEqual(event["evidence_status"], "uncertain")
                self.assertEqual(result["checklist"][0]["status"], "uncertain")

    def test_never_uses_whole_clip_bounds_for_an_action(self):
        result = run(raw([entry()]))
        self.assertIsNone(result["events"][0]["start_ms"])
        self.assertEqual(result["events"][0]["time_source"], "unavailable")

    def test_empty_recorded_event_list_does_not_invent_observation(self):
        result = run(raw([]))
        self.assertEqual(result["events"], [])
        self.assertEqual([row["status"] for row in result["checklist"]], ["not_observed"] * 3)

    def test_malformed_or_failed_response_is_not_negative_evidence(self):
        for data in ({"status": "Failed"}, {"status": "Succeeded", "result": {}},
                     {"status": "Succeeded", "result": {"apiVersion": "2025-11-01", "contents": [{"kind": "audioVisual", "fields": {}}]}}):
            with self.subTest(data=data):
                result = run(data)
                self.assertEqual([row["status"] for row in result["checklist"]], ["uncertain"] * 3)

    def test_omitted_step_and_order_change_remain_distinct(self):
        data = raw([entry("s3", "seg3"), entry("s1", "seg1")], [
            {"segmentId": "seg3", "startTimeMs": 1000, "endTimeMs": 3000},
            {"segmentId": "seg1", "startTimeMs": 6000, "endTimeMs": 8000}])
        result = run(data)
        self.assertTrue(result["order_changed"])
        self.assertEqual(result["checklist"][1]["status"], "not_observed")

    def test_unknown_step_is_never_observed(self):
        result = run(raw([entry("invented", EstimatedStartMs=1000, EstimatedEndMs=3000)]))
        self.assertEqual(result["events"][0]["step_id"], "unknown")
        self.assertEqual(result["events"][0]["evidence_status"], "uncertain")

    def test_missing_evidence_status_not_promoted_to_observed(self):
        row = entry(EstimatedStartMs=1000, EstimatedEndMs=3000)
        del row["EvidenceStatus"]
        result = run(raw([row]))
        self.assertEqual(result["checklist"][0]["status"], "uncertain")

    def test_ai_and_human_review_stay_separate_and_input_untouched(self):
        data = raw([entry(EstimatedStartMs=1000, EstimatedEndMs=3000)])
        original = deepcopy(data)
        result = run(data, source="fixture")
        result["review"]["notes"] = "Revisión humana"
        self.assertEqual(data, original)
        self.assertEqual(result["source"], "fixture")
        self.assertEqual(result["events"][0]["evidence_status"], "observed")


if __name__ == "__main__":
    unittest.main()
