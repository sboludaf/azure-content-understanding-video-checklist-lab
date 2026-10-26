#!/usr/bin/env python3
"""Generate a reviewable local package. This script never grants consent or calls Azure."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from video_checklist.azure import build_analyzer_definition
from video_checklist.config import LAB_ID, PROPOSAL_HASH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-4.1-mini", help="Candidate, not authorized or provisioned")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.expanduser().resolve()
    if ROOT == out or ROOT in out.parents or "botti-oro" in out.parts:
        parser.error("Use a private directory outside Git and the vault")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = json.loads((ROOT / "data/manifest.json").read_text())
    steps = manifest["expected_steps"]
    prepared = build_analyzer_definition(steps, args.model, "geography")
    draft = json.loads((ROOT / "docs/cloud-authorization.template.json").read_text())
    draft.update(model=args.model, model_version="2025-04-14" if args.model == "gpt-4.1-mini" else "", analyzer_id=prepared["analyzer_id"], schema_hash=prepared["schema_hash"], shared_ledger_path=str(out / "shared-ledger.sqlite3"), manifest_path=str(out / "resources.private.json"))
    files = {
        "steps.json": steps,
        "analyzer-proposed.json": prepared,
        "authorization-NOT-GRANTED.private.json": draft,
        "resources.private.json": {"lab_id": LAB_ID, "proposal_hash": PROPOSAL_HASH, "status": "planned_not_created", "resources": [], "proposed_analyzer_id": prepared["analyzer_id"], "schema_hash": prepared["schema_hash"], "cleanup_due_at": "2026-10-19T10:00:00+02:00"},
    }
    for name, value in files.items():
        path = out / name
        with path.open("x") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        path.chmod(0o600)
    print(json.dumps({"lab_id": LAB_ID, "status": "planned_not_authorized", "model_candidate": args.model, "schema_hash": prepared["schema_hash"], "analyzer_id_proposed": prepared["analyzer_id"], "authorized_budget_eur": 0, "files": list(files)}))


if __name__ == "__main__":
    main()
