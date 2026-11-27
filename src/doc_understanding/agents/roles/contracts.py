"""agents / contracts."""

from pydantic import BaseModel, ConfigDict


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(Contract):
    source_id: str
    quote: str
