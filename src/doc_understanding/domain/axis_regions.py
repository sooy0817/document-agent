# 역할: 행·열 관계를 볼 때 쓰는 근거 영역(셀 묶음)과 좌표 계산. 파서와 에이전트가 함께 쓰는 자료형이며 판단 로직은 없다.
"""Source-anchored cell regions and box geometry shared by the parser and the agents."""
import hashlib

from doc_understanding.domain.models import BBox, EvidenceModel


class CellEvidence(EvidenceModel):
    id: str
    table_id: str
    text: str
    bbox: BBox
    spans: tuple[str, ...]
    physical_usable: bool
    row: int | None
    col: int | None
    rs: int | None
    cs: int | None
    filled: bool
    bold: bool


class AxisRegion(EvidenceModel):
    id: str
    document_id: str
    page: int
    bbox: BBox
    source_table_id: str | None
    cells: tuple[CellEvidence, ...]


def area(b):
    return max(0, b.x1-b.x0)*max(0, b.y1-b.y0)


def encloses(a, b, tolerance=1.5):
    return (a.x0-tolerance <= b.x0 and a.y0-tolerance <= b.y0
            and a.x1+tolerance >= b.x1 and a.y1+tolerance >= b.y1)


def overlap(a, b, axis):
    lo, hi = ('x0', 'x1') if axis == 'x' else ('y0', 'y1')
    return max(0, min(getattr(a, hi), getattr(b, hi))-max(getattr(a, lo), getattr(b, lo)))


def digest(value):
    return hashlib.sha256(value.model_dump_json().encode()).hexdigest()
