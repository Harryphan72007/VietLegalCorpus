"""Deterministic JSON Schema export for downstream consumers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel

from vietlegalcorpus.schemas.models import (
    CORPUS_SCHEMA_VERSION,
    CorpusManifest,
    DocumentVersion,
    LegalDocument,
    Provision,
    ProvisionVersion,
    RelationEdge,
    SourceArtifact,
)

SCHEMA_MODELS: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        "corpus_manifest": CorpusManifest,
        "document_version": DocumentVersion,
        "legal_document": LegalDocument,
        "provision": Provision,
        "provision_version": ProvisionVersion,
        "relation_edge": RelationEdge,
        "source_artifact": SourceArtifact,
    }
)


def export_json_schemas(output_dir: Path) -> tuple[Path, ...]:
    """Write stable, UTF-8 JSON Schema files and return them in name order."""
    output_dir.mkdir(parents=True, exist_ok=True)
    exported: list[Path] = []
    for name, model in SCHEMA_MODELS.items():
        output_path = output_dir / f"{name}.schema.json"
        schema = model.model_json_schema(mode="validation")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:pio1:vietlegalcorpus:{CORPUS_SCHEMA_VERSION}:{name}"
        serialized = json.dumps(
            schema,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        output_path.write_text(f"{serialized}\n", encoding="utf-8", newline="\n")
        exported.append(output_path)
    return tuple(exported)
