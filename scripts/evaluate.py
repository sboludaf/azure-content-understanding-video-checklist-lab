#!/usr/bin/env python3
"""Evaluate a normalized Azure report against frozen visual gold, never a fixture.

Metrics are evidence for one clip, not an independent QA verdict. No cloud calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics


def valid_interval(event: dict, duration: int) -> bool:
    start, end = event.get("start_ms"), event.get("end_ms")
    return (
        isinstance(start, int) and not isinstance(start, bool)
        and isinstance(end, int) and not isinstance(end, bool)
        and 0 <= start < end <= duration
    )


def match_events(predicted: list[dict], expected: list[dict], duration: int,
                 tolerance: int = 3000) -> list[tuple[int, int, int]]:
    """Maximum one-to-one matching; minimize total start error among ties.

    Frozen gold has at most six events. A bitmask dynamic program prevents
    duplicate claims consuming the same gold event and avoids greedy misses.
    """
    states: dict[int, tuple[int, list[tuple[int, int, int]]]] = {0: (0, [])}
    for prediction_index, prediction in enumerate(predicted):
        next_states = dict(states)
        if not valid_interval(prediction, duration):
            continue
        for mask, (cost, pairs) in states.items():
            for gold_index, gold_event in enumerate(expected):
                if mask & (1 << gold_index) or prediction.get("step_id") != gold_event["step_id"]:
                    continue
                error = abs(prediction["start_ms"] - gold_event["start_ms"])
                if error > tolerance:
                    continue
                new_mask, new_cost = mask | (1 << gold_index), cost + error
                if new_mask not in next_states or new_cost < next_states[new_mask][0]:
                    next_states[new_mask] = (new_cost, pairs + [(prediction_index, gold_index, error)])
        states = next_states
    best_mask = min(states, key=lambda mask: (-bin(mask).count("1"), states[mask][0], mask))
    return states[best_mask][1]


def evaluate(gold: dict, clip_id: str, report: dict) -> dict:
    clip = next((c for c in gold.get("clips", []) if c.get("clip_id") == clip_id), None)
    if clip is None:
        raise ValueError("Unknown gold clip")
    answer = {
        "clip_id": clip_id, "dataset_revision": gold.get("dataset_revision"),
        "evaluation_scope": "single_clip_metrics_only_not_independent_QA",
        "eligible_for_acceptance_metrics": False, "passed": False,
        "reasons": [], "metrics": None,
    }
    if report.get("source") != "azure":
        answer["reasons"].append("fixture_or_non_azure_source_rejected_for_acceptance_metrics")
        return answer
    reported_clip = report.get("clip", {})
    if not isinstance(reported_clip, dict):
        answer["reasons"].append("invalid_report_clip")
        return answer
    reported_hash = reported_clip.get("sha256", reported_clip.get("hash"))
    if reported_hash != clip.get("sha256") or reported_clip.get("duration_ms") != clip["duration_ms"]:
        answer["reasons"].append("report_input_hash_or_duration_does_not_match_frozen_gold")
        return answer
    events = report.get("events")
    if not isinstance(events, list) or any(not isinstance(e, dict) for e in events):
        answer["reasons"].append("invalid_event_list")
        return answer
    expected = clip["observed_events"]
    duration = clip["duration_ms"]
    if len(expected) > 6 or any(not valid_interval(e, duration) for e in expected):
        raise ValueError("Gold events violate the frozen contract")
    predicted = [e for e in events if e.get("evidence_status") == "observed"]
    pairs = match_events(predicted, expected, duration)
    tp, fp, fn = len(pairs), len(predicted) - len(pairs), len(expected) - len(pairs)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    errors = [pair[2] for pair in pairs]
    timed_events = [e for e in events if e.get("start_ms") is not None or e.get("end_ms") is not None]
    invalid_times = sum(not valid_interval(e, duration) for e in timed_events)
    removed = set(clip.get("not_observed_steps", []))
    removed_claims = sum(e.get("step_id") in removed for e in predicted)
    checklist = report.get("checklist", [])
    if isinstance(checklist, list):
        removed_checklist_claims = sum(
            isinstance(step, dict) and step.get("id") in removed and step.get("status") == "observed"
            for step in checklist
        )
    else:
        removed_checklist_claims = 0
    answer["eligible_for_acceptance_metrics"] = True
    answer["metrics"] = {
        "tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall,
        "observed_event_count": len(predicted), "gold_event_count": len(expected),
        "matched_start_errors_ms": errors,
        "median_start_error_ms": statistics.median(errors) if errors else None,
        "max_start_error_ms": max(errors) if errors else None,
        "matched_starts_within_3000ms_fraction": sum(e <= 3000 for e in errors) / len(errors) if errors else None,
        "valid_interval_for_all_timed_events": invalid_times == 0,
        "invalid_timed_event_count": invalid_times,
        "removed_action_event_claims": removed_claims,
        "removed_action_checklist_claims": removed_checklist_claims,
        "matches": [{"step_id": expected[j]["step_id"], "start_error_ms": error,
                     "predicted_start_ms": predicted[i]["start_ms"],
                     "gold_start_ms": expected[j]["start_ms"]} for i, j, error in pairs],
    }
    if precision is None or recall is None:
        answer["reasons"].append("zero_denominator_is_failure")
    else:
        if precision < 0.9:
            answer["reasons"].append("precision_below_0_90")
        if recall < 0.8:
            answer["reasons"].append("recall_below_0_80")
    if not errors or sum(e <= 3000 for e in errors) / len(errors) < 0.8:
        answer["reasons"].append("insufficient_matched_starts_within_3000ms")
    if invalid_times:
        answer["reasons"].append("invalid_temporal_interval")
    if removed_claims or removed_checklist_claims:
        answer["reasons"].append("removed_action_claimed_observed")
    answer["passed"] = not answer["reasons"]
    return answer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=Path("data/gold.json"))
    parser.add_argument("--clip", required=True, choices=("base", "omitted", "reordered"))
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        gold_bytes = args.gold.read_bytes()
        gold, report = json.loads(gold_bytes), json.loads(args.result.read_bytes())
        if not isinstance(gold, dict) or not isinstance(report, dict):
            raise ValueError("Invalid JSON root")
        manifest = json.loads((Path(__file__).resolve().parent.parent / "data/manifest.json").read_bytes())
        if hashlib.sha256(gold_bytes).hexdigest() != manifest["evaluation"]["gold_sha256"]:
            raise ValueError("Gold differs from the frozen manifest")
        output = evaluate(gold, args.clip, report)
        output["gold_sha256"] = hashlib.sha256(gold_bytes).hexdigest()
    except (OSError, ValueError, KeyError, TypeError):
        output = {"clip_id": args.clip, "passed": False,
                  "eligible_for_acceptance_metrics": False,
                  "reasons": ["cannot_read_or_validate_gold_and_normalized_report"], "metrics": None}
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if output["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
