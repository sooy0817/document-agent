"""A named model role; transport, billing and durable replay belong to runtime."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel


class RoleTransport(Protocol):
    def call(self, role: str, packet: dict, images: list, run_dir: Path) -> dict: ...


@dataclass(frozen=True)
class AgentRole:
    name: str
    prompt: str
    response_model: type[BaseModel]

    @property
    def profile(self) -> tuple[str, type[BaseModel]]:
        return self.prompt, self.response_model

    def invoke(
        self, calls: RoleTransport, packet: dict[str, Any], images: list, run_dir: Path
    ) -> dict:
        """One delegated call. Never loop, enable a provider, or approve a result."""
        return calls.call(self.name, packet, images, run_dir)
