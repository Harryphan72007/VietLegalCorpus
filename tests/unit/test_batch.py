from __future__ import annotations

import json
from pathlib import Path

from vietlegalcorpus.batch import run_batch
from vietlegalcorpus.snapshot import validate_snapshot


def test_batch_builds_unreviewed_synthetic_source_and_reports_pending(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("Điều 1. Quy định thử nghiệm\n", encoding="utf-8")
    plan = {
        "corpus_id": "corpus:synthetic-batch",
        "created_at": "2026-08-24T12:00:00Z",
        "review_date": "2026-08-24",
        "config_sha256": "a" * 64,
        "sources": [
            {
                "source": "sample.txt",
                "title": "Synthetic test only",
                "document_type": "law",
                "official_number": "SYNTHETIC/1",
                "effective_from": "2020-01-01",
                "status": "unknown",
            }
        ],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    snapshot = tmp_path / "snapshot"
    result = run_batch(path, snapshot, tmp_path / "report.json")
    assert result["status"] == "built"
    assert result["source_review_ready"] is False
    assert validate_snapshot(snapshot).snapshot_sha256 == result["snapshot_sha256"]


def test_batch_reports_missing_source_without_publishing_snapshot(tmp_path: Path) -> None:
    plan = {
        "corpus_id": "corpus:synthetic-batch",
        "created_at": "2026-08-24T12:00:00Z",
        "review_date": "2026-08-24",
        "config_sha256": "a" * 64,
        "sources": [
            {
                "source": "missing.txt",
                "title": "Synthetic",
                "document_type": "law",
                "official_number": "SYNTHETIC/1",
            }
        ],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    result = run_batch(path, tmp_path / "snapshot", tmp_path / "report.json")
    assert result["status"] == "failed" and result["failures"]
    assert not (tmp_path / "snapshot").exists()
