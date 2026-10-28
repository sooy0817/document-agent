from typing import Literal
from pydantic import Field
from doc_understanding.domain.models import EvidenceModel
from doc_understanding.staging.metric_candidate import MetricCandidate


class MappingRow(EvidenceModel):
    row_id: str
    label: str
    metric: str
    baseline: Literal["substring_match"] = "substring_match"
    decision: Literal["PROPOSE", "OTHER_METRIC", "ABSTAIN"]
    alternative_metric: str | None = None
    reasons: tuple[str, ...]
    unresolved: tuple[str, ...]
    context: dict
    bindings: tuple[dict, ...]
    candidates: tuple[MetricCandidate, ...] = ()
    review_status: Literal["unreviewed"] = "unreviewed"


class MetricMappingBundle(EvidenceModel):
    schema_version: Literal["metric-mapping/0.1"] = "metric-mapping/0.1"
    source_sha256: str
    source_run_id: str
    source_ir_sha256: str
    strategy: Literal["contextual_rule"] = "contextual_rule"
    policy_version: str = "metric-context/0.1"
    input_hashes: dict[str, str]
    rows: tuple[MappingRow, ...]
    coverage: dict
    fact_publication_allowed: Literal[False] = False
    external_calls: Literal[0] = 0
    elapsed_seconds: float = Field(ge=0)
