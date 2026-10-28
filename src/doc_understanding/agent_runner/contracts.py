from typing import Literal
from pydantic import Field
from doc_understanding.agent_contracts.models import (
    Draft,
    LogicalNode,
    RelationClaim,
    Operation,
    TreeProjection,
    CellBinding,
    Ambiguity,
    Coverage,
)
from doc_understanding.semantic_description.models import Description


class WireResponse(Draft):
    action: Literal["PROPOSE", "ABSTAIN"]
    reason: str
    nodes: tuple[LogicalNode, ...]
    relations: tuple[RelationClaim, ...]
    operations: tuple[Operation, ...]
    trees: tuple[TreeProjection, ...]
    bindings: tuple[CellBinding, ...]
    ambiguities: tuple[Ambiguity, ...]
    coverage: tuple[Coverage, ...]
    descriptions: tuple[Description, ...]


class RunConfig(Draft):
    version: Literal["offline-agent/0.1"] = "offline-agent/0.1"
    model: Literal["mock-evidence/0.1"] = "mock-evidence/0.1"
    max_input_tokens: int = Field(default=200000, gt=0)
    max_output_tokens: int = Field(default=100000, gt=0)
    max_requests: Literal[1] = 1
    external_calls_allowed: Literal[False] = False


def response_schema():
    """Closed objects, all properties required for strict structured-output dialect."""
    schema = WireResponse.model_json_schema()

    def strict(obj):
        if isinstance(obj, dict):
            obj.pop("default", None)
            if obj.get("type") == "object":
                obj["additionalProperties"] = False
                # Free-form reuse_context is disallowed on the wire.
                obj["required"] = list(obj.get("properties", {}))
            for v in obj.values():
                strict(v)
        elif isinstance(obj, list):
            for v in obj:
                strict(v)

    strict(schema)
    return schema
