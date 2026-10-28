"""Replaceable PDF engine and artifact interfaces (no third-party SDK imports)."""
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pydantic import Field

from .models import ArtifactRef, DocumentIR, DocumentMetadata, EvidenceModel, ExtractionRun


class ExtractionLimits(EvidenceModel):
    max_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    max_pages: int = Field(default=200, gt=0)
    render_dpi: int = Field(default=120, ge=36, le=300)
    max_pixels: int = Field(default=20_000_000, gt=0)


@dataclass(frozen=True)
class ExtractionRequest:
    pdf_bytes: bytes
    source_name: str
    metadata: DocumentMetadata = field(default_factory=DocumentMetadata)
    limits: ExtractionLimits = field(default_factory=ExtractionLimits)


class ExtractionError(Exception):
    """Sanitized code; raw PDF text and paths are never included in API errors."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@runtime_checkable
class ArtifactStore(Protocol):
    def write(self, key: str, data: bytes, media_type: str) -> ArtifactRef: ...


@runtime_checkable
class PDFExtractor(Protocol):
    name: str
    version: str

    def extract(self, request: ExtractionRequest, store: ArtifactStore,
                run: ExtractionRun) -> DocumentIR: ...
