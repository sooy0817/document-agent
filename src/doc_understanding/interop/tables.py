# 역할: 바깥 도구(표 구조 인식 모델·문서 파싱 모델)가 낸 HTML·마크다운 표를 칸 목록으로 풀고 우리 격자와 짝짓는다.
"""바깥에서 온 표를 우리 격자에 맞춘다.

바깥 표에서 받는 것은 배치에 대한 판단(머리글 줄 수, 라벨 열 수)과 채점용 비교뿐이다. 칸의 글자와 경계는
언제나 우리 격자(원문)가 기준이다. 이 모듈은 원문을 고치지 않는다.

    cells = html_tables(text)[0]          # 병합을 풀어 (행, 열, 세로, 가로, 글자, 머리글 여부)
    pairs = align(ours, cells)            # 우리 칸 번호 → 바깥 칸 번호
    layout(cells)                         # 바깥 표가 말하는 머리글 줄 수와 라벨 열 수
"""
import re
import unicodedata
from difflib import SequenceMatcher
from html.parser import HTMLParser


def norm(text):
    """비교용 글자: 폭·호환 문자를 맞추고 공백을 뺀다. 저장하는 글자가 아니다."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self.stack.append(dict(rows=[], row=None, cell=None, head=False))
        elif not self.stack:
            return
        table = self.stack[-1]
        if tag == "thead":
            table["head"] = True
        elif tag == "tr":
            table["row"] = []
            table["rows"].append(table["row"])
        elif tag in ("td", "th") and table["row"] is not None:
            def span(name):
                value = re.match(r"\d+", attrs.get(name) or "")
                return max(1, int(value.group())) if value else 1

            table["cell"] = dict(text=[], rowspan=span("rowspan"), colspan=span("colspan"),
                                 header=tag == "th" or table["head"])
            table["row"].append(table["cell"])
        elif tag == "br" and table["cell"] is not None:
            table["cell"]["text"].append("\n")

    def handle_endtag(self, tag):
        if not self.stack:
            return
        table = self.stack[-1]
        if tag == "thead":
            table["head"] = False
        elif tag in ("td", "th"):
            table["cell"] = None
        elif tag == "table":
            self.tables.append(_grid(self.stack.pop()["rows"]))

    def handle_data(self, data):
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"]["text"].append(data)


def _grid(rows):
    """행마다 나열된 칸을 병합을 풀어 격자 위치로 옮긴다(위 칸의 rowspan이 차지한 자리는 건너뛴다)."""
    taken, cells = set(), []
    for r, row in enumerate(rows):
        c = 0
        for cell in row:
            while (r, c) in taken:
                c += 1
            for dr in range(cell["rowspan"]):
                for dc in range(cell["colspan"]):
                    taken.add((r + dr, c + dc))
            cells.append(dict(row=r, col=c, rowspan=cell["rowspan"], colspan=cell["colspan"],
                              text="".join(cell["text"]).strip(), header=cell["header"]))
            c += cell["colspan"]
    return cells


def html_tables(text):
    """HTML 안의 표를 모두 칸 목록으로. 표 안의 표는 따로 한 개로 나온다."""
    parser = _Tables()
    parser.feed(text or "")
    return parser.tables


def markdown_tables(text):
    """마크다운 표를 칸 목록으로. 마크다운은 병합을 표현하지 못하므로 모든 칸이 1×1이다."""
    tables, block = [], []
    for line in (text or "").splitlines() + [""]:
        if line.strip().startswith("|"):
            block.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        if len(block) >= 2 and all(re.fullmatch(r":?-{2,}:?", c) for c in block[1] if c):
            cells = []
            for r, row in enumerate(block[:1] + block[2:]):
                cells += [dict(row=r, col=c, rowspan=1, colspan=1, text=t.replace("<br>", "\n"), header=r == 0)
                          for c, t in enumerate(row)]
            tables.append(cells)
        block = []
    return tables


OTSL = re.compile(r"<(fcel|ecel|lcel|ucel|xcel|ched|rhed|srow|nl)>")


def otsl_table(text):
    """OTSL 표기(칸마다 <fcel>글자, 빈칸 <ecel>, 왼쪽과 합침 <lcel>, 위와 합침 <ucel>, 둘 다 <xcel>, 줄 끝 <nl>)를 칸 목록으로.

    <ched>·<rhed>·<srow>는 머리글 칸으로 표시한다. 표기가 없으면 빈 목록이다.
    """
    parts = OTSL.split(text or "")
    grid, row = [], []
    for kind, body in zip(parts[1::2], parts[2::2]):
        if kind == "nl":
            grid.append(row)
            row = []
        else:
            row.append((kind, re.sub(r"<[^>]*>", "", body).strip()))
    if row:
        grid.append(row)
    cells, owner = [], {}
    for r, line in enumerate(grid):
        for c, (kind, body) in enumerate(line):
            if kind in ("lcel", "xcel") and (r, c - 1) in owner:
                cell = owner[(r, c - 1)]
                cell["colspan"] = max(cell["colspan"], c - cell["col"] + 1)
            elif kind == "ucel" and (r - 1, c) in owner:
                cell = owner[(r - 1, c)]
                cell["rowspan"] = max(cell["rowspan"], r - cell["row"] + 1)
            else:
                cell = dict(row=r, col=c, rowspan=1, colspan=1, text="" if kind == "ecel" else body,
                            header=kind in ("ched", "rhed", "srow"))
                cells.append(cell)
            owner[(r, c)] = cell
    return cells


def shape(cells):
    return (1 + max((c["row"] + c["rowspan"] - 1 for c in cells), default=-1),
            1 + max((c["col"] + c["colspan"] - 1 for c in cells), default=-1))


def layout(cells):
    """바깥 표가 말하는 배치: 위에서부터 머리글만 있는 줄 수, 왼쪽에서부터 머리글만 있는 열 수."""
    rows, cols = shape(cells)
    by_row = {r: [c for c in cells if c["row"] <= r < c["row"] + c["rowspan"]] for r in range(rows)}
    header_rows = 0
    while header_rows < rows and by_row[header_rows] and all(c["header"] for c in by_row[header_rows]):
        header_rows += 1
    body = [c for c in cells if c["row"] >= header_rows]
    label_columns = 0
    while label_columns < cols:
        column = [c for c in body if c["col"] <= label_columns < c["col"] + c["colspan"]]
        if not column or not all(c["header"] for c in column):
            break
        label_columns += 1
    return dict(rows=rows, columns=cols, header_rows=header_rows, label_columns=label_columns,
                marks_headers=any(c["header"] for c in cells))


def _lines(cells, axis, size):
    """행(또는 열)마다, 그 줄에서 시작하는 칸의 글자를 이어 붙인 것. 줄끼리 맞출 때 지문으로 쓴다."""
    other = "col" if axis == "row" else "row"
    return ["\x1f".join(norm(c["text"]) for c in sorted(cells, key=lambda c: c[other]) if c[axis] == n)
            for n in range(size)]


def _align_lines(ours, theirs, gap=0.35):
    """두 줄 목록을 순서를 유지하며 맞춘다(빠진 줄은 짝 없음). 비슷한 정도는 글자 일치 비율이다."""
    n, m = len(ours), len(theirs)
    like = [[SequenceMatcher(None, a, b, autojunk=False).ratio() if a or b else 0.5 for b in theirs] for a in ours]
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        best[i][0] = best[i - 1][0] - gap
    for j in range(1, m + 1):
        best[0][j] = best[0][j - 1] - gap
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best[i][j] = max(best[i - 1][j - 1] + like[i - 1][j - 1] - 0.5, best[i - 1][j] - gap, best[i][j - 1] - gap)
    pairs, i, j = {}, n, m
    while i > 0 and j > 0:
        if best[i][j] == best[i - 1][j - 1] + like[i - 1][j - 1] - 0.5:
            if like[i - 1][j - 1] >= 0.3:
                pairs[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif best[i][j] == best[i - 1][j] - gap:
            i -= 1
        else:
            j -= 1
    return pairs


def align(ours, theirs):
    """우리 칸 번호(목록에서의 위치) → 바깥 칸 번호. 글자로 행과 열을 맞춘 뒤, 맞춰진 자리에서 시작하는 칸끼리 짝짓는다.

    ours·theirs: row, col, rowspan, colspan, text를 가진 칸 목록. 짝이 없는 칸은 결과에 없다.
    """
    our_rows, our_cols = shape(ours)
    their_rows, their_cols = shape(theirs)
    rows = _align_lines(_lines(ours, "row", our_rows), _lines(theirs, "row", their_rows))
    kept = [c for c in ours if c["row"] in rows]
    other = [c for c in theirs if c["row"] in rows.values()]
    cols = _align_lines(_lines(kept, "col", our_cols), _lines(other, "col", their_cols))
    start = {(c["row"], c["col"]): n for n, c in enumerate(theirs)}
    pairs = {}
    for n, cell in enumerate(ours):
        target = (rows.get(cell["row"]), cols.get(cell["col"]))
        if target in start:
            pairs[n] = start[target]
    return pairs


def align_by_position(ours, theirs):
    """칸 좌표가 있을 때: 겹치는 넓이가 가장 큰 칸끼리 짝짓는다. 칸마다 bbox=(x0, y0, x1, y1), 같은 좌표계."""
    pairs = {}
    for n, cell in enumerate(ours):
        ax0, ay0, ax1, ay1 = cell["bbox"]
        best, area = None, 0.0
        for m, other in enumerate(theirs):
            bx0, by0, bx1, by1 = other["bbox"]
            overlap = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))
            if overlap > area:
                best, area = m, overlap
        if best is not None and area >= 0.3 * max(1e-9, (ax1 - ax0) * (ay1 - ay0)):
            pairs[n] = best
    return pairs


def compare(ours, theirs, pairs):
    """짝지은 결과의 채점. 분모는 우리 칸 전체다(짝이 없으면 틀린 것으로 센다)."""
    total = len(ours)
    matched = [(ours[a], theirs[b]) for a, b in pairs.items()]
    filled = [c for c in ours if norm(c["text"])]
    return dict(
        cells=total, their_cells=len(theirs), matched=len(matched),
        text_equal=sum(norm(a["text"]) == norm(b["text"]) for a, b in matched),
        text_equal_nonempty=sum(norm(a["text"]) == norm(b["text"]) for a, b in matched if norm(a["text"])),
        nonempty=len(filled),
        span_equal=sum((a["rowspan"], a["colspan"]) == (b["rowspan"], b["colspan"]) for a, b in matched),
        shape=shape(ours), their_shape=shape(theirs))
