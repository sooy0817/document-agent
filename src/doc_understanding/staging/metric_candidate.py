"""Proposal-only metric contract; no automatic alias matching or Fact publication."""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from doc_understanding.domain.models import BBox, EvidenceModel, SHA256


class MetricEvidence(EvidenceModel):
    artifact_sha256: SHA256
    page_number: int = Field(gt=0)
    block_id: str | None = None
    table_id: str | None = None
    cell_id: str | None = None
    bbox: BBox
    raw_text: str
    role: Literal["value", "label", "institution", "period", "unit", "accounting_scope", "note"]

    @model_validator(mode="after")
    def source_anchor(self):
        if not self.block_id and not self.cell_id:
            raise ValueError("Original block or cell anchor is required")
        return self


class MetricCandidate(EvidenceModel):
    schema_version: Literal["metric-candidate/0.1-draft"] = "metric-candidate/0.1-draft"
    candidate_id: str
    source_sha256: SHA256
    processing_run_id: str
    metric: Literal["total_assets", "total_liabilities", "total_equity",
                    "operating_revenue", "net_income"]
    raw_value: str | None
    parsed_value: Decimal | None = None
    sign_policy: str | None = None
    institution: str | None = None
    period_kind: Literal["instant", "duration", "unresolved"] = "unresolved"
    period_start: date | None = None
    period_end: date | None = None
    displayed_unit: str | None = None
    multiplier: Decimal | None = None
    currency: str | None = None
    accounting_scope: Literal["integrated", "credit", "general", "unresolved"] = "unresolved"
    evidence: tuple[MetricEvidence, ...] = ()
    unresolved: tuple[str, ...] = ()
    status: Literal["proposed", "review_required"] = "review_required"
    semantic_validation: Literal["not_reviewed"] = "not_reviewed"
    numeric_validation: Literal["not_checked", "exact", "approximate", "mismatch"] = "not_checked"
    human_review_event_refs: tuple[str, ...] = ()
    fact_publication_allowed: Literal[False] = False

    @model_validator(mode="after")
    def supported_dimensions(self):
        roles = {e.role for e in self.evidence}
        required = {"label"}
        if self.raw_value is not None:
            required.add("value")
        if self.institution:
            required.add("institution")
        if self.period_kind != "unresolved":
            required.add("period")
        if self.displayed_unit or self.multiplier or self.currency:
            required.add("unit")
        if self.accounting_scope != "unresolved":
            required.add("accounting_scope")
        if not required <= roles:
            raise ValueError(f"Missing dimension evidence: {sorted(required - roles)}")
        if self.raw_value is not None and not any(
            e.role == "value" and e.raw_text == self.raw_value for e in self.evidence
        ):
            raise ValueError("Raw value must match the original value evidence")
        if self.parsed_value is not None and (self.raw_value in (None, "") or not self.sign_policy):
            raise ValueError("Parsed value requires original text and explicit sign policy")
        if self.period_kind == "instant" and (not self.period_end or self.period_start):
            raise ValueError("Instant metric requires only period_end")
        if self.period_kind == "duration" and (
            not self.period_start or not self.period_end or self.period_start > self.period_end
        ):
            raise ValueError("Duration requires ordered start/end")
        return self
