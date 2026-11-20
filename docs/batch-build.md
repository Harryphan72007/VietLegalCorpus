# Batch build from explicit sources

Install with `pip install -e ".[dev]"`. Create a JSON plan with the exact sources
and metadata you have checked. For a local, **synthetic** example:

```json
{
  "schema_version": "1.0.0",
  "corpus_id": "corpus:synthetic-demo",
  "created_at": "2026-08-24T12:00:00Z",
  "review_date": "2026-08-24",
  "config_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "sources": [{
    "source": "sample.txt",
    "title": "Synthetic test document",
    "document_type": "law",
    "official_number": "SYNTHETIC/1",
    "status": "unknown"
  }]
}
```

Run `vlc batch-build plan.json out/snapshot out/batch-report.json`. Relative local
paths resolve beside the plan. Explicit HTTPS sources must match `allowed_hosts`;
the downloader does not crawl links. The workflow stores source bytes by checksum,
parses recognized text, extracts hierarchy and builds a snapshot only if all
sources were processed. Re-running with an already published output fails safely.
An interrupted run can reuse content-addressed acquired files, but does not
silently resume a partially published snapshot.

The report always lists processed documents, source failures, pending OCR or
parsing review, source-review readiness and G1 blockers when a snapshot is built.
No review approval is inferred. Supplying `source_review` points to a separately
completed review record relative to the plan. Use a real configuration digest
in place of the example string. Original source documents and local plans are
operator inputs; do not commit unapproved or private source material.
