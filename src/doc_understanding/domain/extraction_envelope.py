"""Draft raw-extraction storage envelope; no financial fact publication API."""

from typing import Literal
from pydantic import Field, model_validator
from .models import EvidenceModel


class StoredArtifact(EvidenceModel):
    artifact_id: str
    kind: Literal[
        "DocumentIR",
        "PhysicalTableIR",
        "StructuredTableIR",
        "SemanticOverlay",
        "PatchProposal",
        "SemanticRevision",
        "AggregationSidecar",
        "Coverage",
        "Trace",
    ]
    schema_version: str
    original_uri: str
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: dict
    validation_status: Literal[
        "not_evaluated", "partial", "review_required", "invalid", "extraction_valid"
    ]
    upstream_artifact_ids: tuple[str, ...] = ()
    source_page_numbers: tuple[int, ...] = ()
    source_node_ids: tuple[str, ...] = ()


class ExtractionEnvelope(EvidenceModel):
    schema_version: Literal["canonical-extraction-envelope/0.1-draft"] = (
        "canonical-extraction-envelope/0.1-draft"
    )
    source_document_id: str
    source_version_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_uri: str
    parsing_run_id: str
    parsing_run_status: Literal["failed", "partial", "completed"]
    configuration: dict
    artifacts: tuple[StoredArtifact, ...] = ()
    failures: tuple[dict, ...] = ()
    financial_facts: tuple[dict, ...] = Field(default=(), max_length=0)
    fact_publication_allowed: Literal[False] = False

    @model_validator(mode="after")
    def references(self):
        ids = {a.artifact_id for a in self.artifacts}
        if len(ids) != len(self.artifacts):
            raise ValueError("duplicate artifact IDs")
        graph = {a.artifact_id: a.upstream_artifact_ids for a in self.artifacts}
        for a in self.artifacts:
            if not set(a.upstream_artifact_ids) <= ids:
                raise ValueError("missing upstream artifact")
            todo = list(a.upstream_artifact_ids)
            seen = set()
            while todo:
                p = todo.pop()
                if p == a.artifact_id:
                    raise ValueError("artifact provenance cycle")
                if p not in seen:
                    seen.add(p)
                    todo.extend(graph[p])
        return self
