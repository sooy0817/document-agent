# 역할: 인용 근거를 원문과 대조해 보여 주는 데 필요한 것(원본 파일 찾기, 쪽 그림, 쪽 크기, 표 격자, 앞뒤 본문)을 읽는다.
"""원문 보기(읽기 전용).

검색·답변 화면과 매핑 검토 화면이 함께 쓴다. 문서를 어떤 경로로 적재했든(화면 업로드, 스크립트, 일괄 적재) 문서 ID가
원본 파일의 내용 해시이므로, 원본은 내용 해시로 찾는다. 파일 이름은 찾는 순서에만 쓴다.

- 표 격자와 쪽 크기는 색인한 문서 저장본에서 그 표·그 쪽만 꺼낸다(문서 전체를 풀어 검증하지 않는다).
- 좌표를 쓸 수 있는 조건: 원본이 PDF이고, 저장된 쪽 크기가 지금 그 파일의 쪽 크기와 같다. HTML처럼 좌표가 원문 위치가
  아닌 문서(합성 좌표)나 크기가 다른 파일에서는 위치를 강조하지 않는다(추정한 위치를 정확한 위치처럼 보이지 않게 한다).
- 여기서 읽는 표의 역할·경로는 분석 후보이며 사람 검증을 거친 사실이 아니다.
"""
import hashlib
import os
import re
import threading
import time
from pathlib import Path

from doc_understanding.storage.staging.local import query_params

HTML_SUFFIXES = (".html", ".htm")
SUFFIXES = (".pdf", *HTML_SUFFIXES)
# 원문 HTML은 격리해서만 보여 준다(스크립트·외부 요청 차단, 문서 안의 글꼴 크기·표 모양만 허용).
SOURCE_POLICY = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
SCALE = 1.35  # 쪽 그림 배율(화면 업로드 문서의 쪽 그림과 같다)
# 쪽 그림 보관 폴더는 매핑 검토 화면과 검색 화면이 함께 쓴다. 같은 쪽을 동시에 그리지 않도록 잠금도 하나만 둔다.
RENDER_LOCK = threading.Lock()

ARTIFACT = ("FROM rag.document_snapshot s JOIN staging.artifact a ON a.run_id=s.run_id AND a.artifact_id=s.artifact_id "
            "WHERE s.run_id=%s")


def source_kind(path):
    if path is None:
        return None
    suffix = Path(path).suffix.lower()
    return "pdf" if suffix == ".pdf" else "html" if suffix in HTML_SUFFIXES else None


class SourceFiles:
    """문서 ID(내용 해시) → 원본 파일. 주어진 폴더들에서 PDF·HTML을 찾아 내용 해시로 대조한다.

    파일 해시는 (경로, 크기, 수정 시각)별로 한 번만 계산한다. 못 찾은 문서가 있을 때만 폴더를 다시 훑고,
    다시 훑는 간격은 rescan_seconds로 제한한다(없는 문서를 열 때마다 폴더 전체를 읽지 않는다).
    """

    def __init__(self, roots, rescan_seconds=30):
        self.roots = [Path(r) for r in roots]
        self.rescan_seconds = rescan_seconds
        self.lock = threading.Lock()
        self.hashes = {}   # (경로, 크기, 수정 시각) → 문서 ID
        self.found = {}    # 문서 ID → 경로
        self.scanned_at = None

    def _files(self):
        out = []
        for base in self.roots:
            for folder, _, names in os.walk(base):
                out += [Path(folder) / n for n in names if n.lower().endswith(SUFFIXES)]
        return out

    def _digest(self, path):
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if key not in self.hashes:
            self.hashes[key] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        return self.hashes[key]

    def _valid(self, document_id):
        path = self.found.get(document_id)
        try:
            return path is not None and self._digest(path) == document_id
        except OSError:
            return False

    def find(self, document_ids, names=()):
        """{문서 ID: 경로}. 못 찾은 문서는 결과에 없다. names: 먼저 볼 파일 이름(있으면 해시 계산이 줄어든다)."""
        wanted = set(document_ids)
        with self.lock:
            missing = {d for d in wanted if not self._valid(d)}
            for d in missing:
                self.found.pop(d, None)
            due = self.scanned_at is None or time.monotonic() - self.scanned_at >= self.rescan_seconds
            if missing and due:
                names = set(names)
                files = sorted(self._files(), key=lambda p: p.name not in names)
                for path in files:
                    if not missing:
                        break
                    try:
                        digest = self._digest(path)
                    except OSError:
                        continue
                    self.found.setdefault(digest, path)
                    missing.discard(digest)
                else:
                    self.scanned_at = time.monotonic()  # 끝까지 훑었는데도 없으면 잠시 다시 훑지 않는다.
            return {d: self.found[d] for d in wanted if d in self.found}


def render_page(source, number, target, lock=None):
    """원본 PDF의 한 쪽을 그림으로 그려 target에 둔다(있으면 그대로 쓴다). 쪽이 없으면 None.
    lock 인자는 예전 호출과의 호환용이며 쓰지 않는다(모든 호출이 RENDER_LOCK 하나를 쓴다)."""
    import fitz
    target = Path(target)
    if target.exists():
        return target
    with RENDER_LOCK:
        if target.exists():
            return target
        with fitz.open(source) as pdf:
            if not 1 <= number <= pdf.page_count:
                return None
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(f"{target.stem}.{os.getpid()}.{threading.get_ident()}.part")
            pdf[number - 1].get_pixmap(matrix=fitz.Matrix(SCALE, SCALE)).save(str(partial), output="png")
            os.replace(partial, target)
    return target


def page_folder(cache, document_id):
    return Path(cache) / re.sub(r"[^a-f0-9]", "", document_id.split(":")[-1])[:64]


def pdf_page_size(source, number):
    """지금 파일의 그 쪽 크기(점 단위, 회전 반영). 쪽이 없으면 None."""
    import fitz
    with fitz.open(source) as pdf:
        if not 1 <= number <= pdf.page_count:
            return None
        rect = pdf[number - 1].rect
        return rect.width, rect.height


def stored_page(run_id, number):
    """색인한 문서의 그 쪽 크기와 좌표 종류. 쪽이 없으면 None.
    synthetic: 좌표가 원문 위치가 아니라 읽은 순서로 만든 값이다(HTML 등)."""
    rows = query_params(
        "SELECT p->>'width' AS width, p->>'height' AS height, p->'issues' AS issues FROM ("
        "SELECT jsonb_path_query_first(a.payload, '$.document_ir.pages[*] ? (@.page_number == $n)', "
        "jsonb_build_object('n', %s::int)) AS p " + ARTIFACT + ") x WHERE p IS NOT NULL", [number, run_id])
    if not rows:
        return None
    codes = [i.get("code", "") for i in rows[0]["issues"] or []]
    return dict(width=float(rows[0]["width"]), height=float(rows[0]["height"]),
                synthetic=any("SYNTHETIC" in c for c in codes))


def positions(kind, stored, actual, tolerance=0.01):
    """좌표로 위치를 강조해도 되는지와 안 되는 이유. (가능 여부, 이유 코드)"""
    if kind != "pdf":
        return False, "NO_PAGE_COORDINATES" if kind == "html" else "SOURCE_NOT_AVAILABLE"
    if stored is None or actual is None:
        return False, "PAGE_NOT_FOUND"
    if stored["synthetic"]:
        return False, "SYNTHETIC_COORDINATES"
    close = lambda a, b: abs(a - b) <= tolerance * max(a, b, 1)  # noqa: E731
    if not (close(stored["width"], actual[0]) and close(stored["height"], actual[1])):
        return False, "PAGE_SIZE_MISMATCH"
    return True, None


def table_grid(run_id, table_id):
    """표 하나의 격자(모든 칸의 위치·원문·좌표·역할)와, 값 칸의 행·열 경로와 근거 사용 가능 여부.

    격자는 색인한 문서의 물리 표에서, 경로는 색인 때 저장한 근거 항목에서 읽는다(질문 때 쓰는 것과 같은 저장본).
    표가 없으면 None."""
    rows = query_params(
        "SELECT jsonb_path_query_first(a.payload, '$.physical_tables[*] ? (@.table_id == $t)', "
        "jsonb_build_object('t', %s::text)) AS physical, "
        "jsonb_path_query_first(a.payload, '$.table_structure[*] ? (@.table_id == $t).cell_roles', "
        "jsonb_build_object('t', %s::text)) AS roles " + ARTIFACT, [table_id, table_id, run_id])
    if not rows or not rows[0]["physical"]:
        return None
    physical = rows[0]["physical"]
    roles = {r["cell_id"]: r["role"] for r in rows[0]["roles"] or []}
    stored = query_params("SELECT payload FROM rag.evidence_item WHERE run_id=%s AND kind='table' AND item_id=%s",
                          [run_id, table_id])
    item = stored[0]["payload"] if stored else {}
    mapped = {c["cell_id"]: c for c in item.get("cells") or []}
    cells = []
    for c in physical.get("cells") or []:
        if c.get("row_index") is None or c.get("column_index") is None:
            continue
        m = mapped.get(c["cell_id"])
        bbox = (c.get("source") or {}).get("bbox") or c.get("source_bbox")
        cells.append(dict(
            id=c["cell_id"], r=c["row_index"], c=c["column_index"], rs=c.get("rowspan") or 1, cs=c.get("colspan") or 1,
            text=c.get("original_text") or "", bbox=bbox, role=roles.get(c["cell_id"], "unknown"),
            row_path=m["row_path"] if m else None, column_path=m["column_path"] if m else None,
            eligible=bool(m and m.get("lookup_eligible"))))
    nested = item.get("nested_context") or None
    # 같은 표의 셀에 반복 저장된 단위 문맥은 원문 위치별로 한 번만 보낸다.
    units = {u["content_id"]: u for m in mapped.values() for u in m.get("unit_context") or []}
    return dict(table_id=table_id, page=physical.get("page_number"),
                rows=1 + max((x["r"] + x["rs"] - 1 for x in cells), default=-1),
                columns=1 + max((x["c"] + x["cs"] - 1 for x in cells), default=-1),
                scope=item.get("scope") or [], nested=dict(
                    parent_table_id=nested.get("parent_table_id"), parent_cell_id=nested.get("parent_cell_id"),
                    path=[p.get("label") for p in nested.get("path") or []]) if nested else None,
                cells=cells, unit_context=list(units.values()))


def table_shapes(run_id):
    """이 실행의 물리 표 목록: (표 ID, 쪽, 열 수), 쪽·표 순서. 앞쪽에서 이어진 표의 머리글 후보를 찾을 때 쓴다."""
    rows = query_params(
        "SELECT t->>'table_id' AS table_id, (t->>'page_number')::int AS page, "
        "(SELECT max((c->>'column_index')::int + coalesce((c->>'colspan')::int, 1)) FROM jsonb_array_elements(t->'cells') c "
        " WHERE c->>'column_index' IS NOT NULL) AS columns "
        "FROM (SELECT jsonb_array_elements(a.payload->'physical_tables') AS t " + ARTIFACT + ") x ORDER BY 2, 1", [run_id])
    return [(r["table_id"], r["page"], r["columns"] or 0) for r in rows]


def neighbours(run_id, content_id, around=2):
    """본문 근거의 앞뒤 본문(문서 순서로 around개씩). 근거 항목이 없으면 None."""
    here = query_params("SELECT (payload->>'position')::int AS position FROM rag.evidence_item "
                        "WHERE run_id=%s AND kind='content' AND item_id=%s", [run_id, content_id])
    if not here:
        return None
    position = here[0]["position"]
    rows = query_params(
        "SELECT item_id, (payload->>'position')::int AS position, payload->>'text_raw' AS text, "
        "(payload->'source'->>'page_number')::int AS page FROM rag.evidence_item "
        "WHERE run_id=%s AND kind='content' AND (payload->>'position')::int BETWEEN %s AND %s "
        "ORDER BY (payload->>'position')::int", [run_id, position - around, position + around])
    return [dict(content_id=r["item_id"], text=r["text"], page=r["page"], cited=r["item_id"] == content_id)
            for r in rows]
