# Corpus snapshot workflow

A snapshot packages corpus records, original source bytes, decoded evidence, and
the evidence used to report G1 readiness. It is reproducible for the same inputs,
including the creation timestamp, review record, and configuration checksum.

## Build and validate a synthetic example

Install the package with `pip install -e ".[dev]"`, then run:

```sh
vlc evaluate data/samples/golden-corpus
vlc build-snapshot data/samples/golden-corpus out/example-snapshot \
  --corpus-id corpus:example:synthetic \
  --created-at 2026-08-24T12:00:00Z \
  --review-date 2026-08-24 \
  --config-sha256 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
vlc validate-snapshot out/example-snapshot
vlc validate-snapshot out/example-snapshot --require-ready
```

The example checksum is a placeholder. For production builds, supply the SHA-256
of the actual configuration bytes. Creation timestamps must include a timezone.
The final command exits with status 1 because synthetic fixtures do not have an
approved official-source review. Plain validation succeeds for a valid snapshot
even when G1 readiness is incomplete; its JSON output includes the blockers.

The output directory must be absent or empty. Construction happens in a sibling
temporary directory, and the complete result is validated before publication.
Failed builds clean up their staging directory. Existing snapshots are never
overwritten. Keep snapshots outside the input corpus directory.

## Source bytes and review evidence

Use `--artifact-source-root PATH` when original bytes differ from decoded UTF-8
evidence, such as PDF, DOCX, or HTML. Each artifact's `storage_path` is resolved
beneath that root, and its bytes must match its declared checksum and length.
Without this option, source bytes are taken from the corresponding UTF-8 evidence.
Symlinks and artifact paths colliding with snapshot metadata are rejected.

Pass `--source-review PATH` to preserve a validated review record. An approved
record must explicitly approve storage/reuse and every document, identify the
reviewer and review timestamp, and cover the exact document metadata and official
source locators at the manifest's review date. Pending reviews remain pending;
the builder never creates approval. The current review template is
`data/source-reviews/land-law-pilot.pending.json`.

The default G1 technical target is five documents. Override it with
`--target-document-count N` (a positive integer). Technical readiness also requires
zero quality errors and warnings. The separate experiment-scale indicator still
requires 100 documents. These are corpus workflow gates, not legal conclusions.

## Files and validation

| File | Purpose |
| --- | --- |
| `manifest.json` | Sorted inventory, provenance metadata, evidence/review/readiness hashes |
| `snapshot.sha256` | SHA-256 of the exact manifest bytes |
| `records/*.jsonl` | Six typed record families plus decoded `source_evidence.jsonl` |
| Artifact `storage_path` files | Original source bytes listed in the manifest |
| `source_review.json` | Optional supplied review record |
| `g1_readiness.json` | Recomputed technical status and official-review blockers |

Validation checks the required record inventory and types, JSON record schemas,
sorted unique IDs, file hashes/counts, artifact checksums/lengths, corpus quality,
review evidence, and readiness consistency. Missing, extra, or corrupt files fail.
Evidence, review, and readiness checksums are carried in dedicated manifest fields;
decoded evidence is not treated as another legal record family. Raw artifacts
whose filename ends in `.jsonl` are counted as single artifacts, not parsed records.

Snapshots built before these evidence integrity fields were introduced must be
rebuilt from their original bundle and review inputs. Optional fields remain
readable by the manifest model, but the validator requires evidence and readiness
hashes before accepting a snapshot for downstream use.

Snapshot hashes detect corruption and bind the package's contents. They do not
authenticate the reviewer or publisher. Verify the expected snapshot hash through
a trusted channel when accepting a package from another party.

Build and validation failures print an error to stderr and exit with status 1.
`validate-snapshot --require-ready` also exits with status 1 for a valid snapshot
whose G1 gate is incomplete, while retaining its machine-readable JSON report.
