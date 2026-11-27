"""README 그림을 파서의 실제 출력에서 다시 그린다.

입력은 저장소에 들어 있는 합성 PDF(examples/rate-table-ko.pdf)와 그 파서 출력
(examples/rate-table-ko.json, tabledoc 형식)뿐이다. 먼저 examples/rate_table_ko.py를 실행한다.
그림 속 값·경로·좌표·상태는 모두 그 JSON에서 읽으며, 이 스크립트 안에 정답을 적어 두지 않는다.

실행(저장소 루트):
    python examples/make_readme_figures.py
필요: pymupdf, Pillow(기본 의존성), 한글 글꼴(--font / --font-bold로 지정).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "figures"
KO_PDF = ROOT / "examples" / "rate-table-ko.pdf"
KO_JSON = ROOT / "examples" / "rate-table-ko.json"

INK, SUB, MUTE = "#0b0b0b", "#52514e", "#8a8984"
BG, PANEL, LINE = "#ffffff", "#f6f6f4", "#d9d8d3"
BLUE, BLUE_BG = "#2a78d6", "#dbe9fb"
ORANGE, ORANGE_BG = "#eb6834", "#fde3d8"
GREEN, GREEN_BG = "#1baf7a", "#d8f3e9"
STATE_KO = {"observed": "원문에서 확인", "inferred": "추론 후보", "unresolved": "미확정",
            "ambiguous": "모호", "partial": "일부 미확정", "review_required": "검토 필요"}
W = 1600
ZOOM = 2.0  # PDF point → pixel


class Fonts:
    def __init__(self, regular: str, bold: str) -> None:
        self.regular, self.bold, self._cache = regular, bold, {}

    def __call__(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (size, bold)
        if key not in self._cache:
            self._cache[key] = ImageFont.truetype(self.bold if bold else self.regular, size)
        return self._cache[key]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            trial = word if not line else f"{line} {word}"
            if draw.textlength(trial, font=font) <= width:
                line = trial
                continue
            if line:
                lines.append(line)
            line = word
            while draw.textlength(line, font=font) > width:  # 공백 없는 긴 토막
                cut = len(line)
                while cut > 1 and draw.textlength(line[:cut], font=font) > width:
                    cut -= 1
                lines.append(line[:cut])
                line = line[cut:]
        lines.append(line)
    return lines


def text_block(draw, xy, text, font, width, fill=INK, gap=8) -> int:
    """줄바꿈해 그리고 다음 y를 돌려준다."""
    x, y = xy
    step = font.size + gap
    for line in wrap(draw, text, font, width):
        draw.text((x, y), line, font=font, fill=fill)
        y += step
    return y


def canvas(height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, height), BG)
    return img, ImageDraw.Draw(img, "RGBA")


def header(draw, f, title: str, subtitle: str) -> int:
    draw.text((48, 36), title, font=f(38, True), fill=INK)
    y = text_block(draw, (48, 92), subtitle, f(22), W - 96, fill=SUB)
    return y + 16


def render(pdf: Path, clip: tuple[float, float, float, float], pad: float = 6) -> Image.Image:
    page = pymupdf.open(pdf)[0]
    rect = pymupdf.Rect(clip[0] - pad, clip[1] - pad, clip[2] + pad, clip[3] + pad)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM), clip=rect, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def to_px(bbox, clip, pad: float = 6, origin=(0, 0), k: float = 1.0) -> tuple[int, int, int, int]:
    ox, oy = origin
    z = ZOOM * k
    return (
        int(ox + (bbox[0] - clip[0] + pad) * z), int(oy + (bbox[1] - clip[1] + pad) * z),
        int(ox + (bbox[2] - clip[0] + pad) * z), int(oy + (bbox[3] - clip[1] + pad) * z),
    )


def tint(draw, box, color: str, width: int = 5, alpha: int = 46) -> None:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    draw.rectangle(box, fill=(r, g, b, alpha), outline=color, width=width)


def chip(draw, f, xy, text: str, color: str, bg: str) -> int:
    font = f(20, True)
    w = int(draw.textlength(text, font=font)) + 24
    x, y = xy
    draw.rounded_rectangle((x, y, x + w, y + 34), 8, fill=bg, outline=color, width=2)
    draw.text((x + 12, y + 4), text, font=font, fill=INK)
    return x + w


def card(draw, box, fill=PANEL) -> None:
    draw.rounded_rectangle(box, 14, fill=fill, outline=LINE, width=2)


def state(value: str) -> str:
    return f"{STATE_KO.get(value, value)}({value})"


# ---------------------------------------------------------------- 파서 출력 읽기
class Table:
    """tabledoc 표 한 개. 경로 라벨과 칸 좌표를 출력 JSON에서만 조회한다."""

    def __init__(self) -> None:
        self.document = load(KO_JSON)
        self.table = max(self.document["tables"], key=lambda t: len(t["grid"]))
        self.clip = tuple(self.table["bbox"])
        self.cells = {c["cell_id"]: c for c in self.table["grid"]}
        self.rows = {n["node_id"]: n for n in self.table["row_nodes"]}
        self.columns = {n["node_id"]: n for n in self.table["column_nodes"]}
        # 값이 적힌 칸만, 원문 위치 순서로.
        self.paths = sorted((p for p in self.table["paths"] if self.cells[p["cell_id"]]["text"].strip()),
                            key=lambda p: (self.cells[p["cell_id"]]["row"], self.cells[p["cell_id"]]["col"]))

    @staticmethod
    def chain(nodes: dict, node_id) -> list[dict]:
        out = []
        while node_id is not None:
            out.append(nodes[node_id])
            node_id = nodes[node_id]["parent"]
        return out[::-1]

    def row_path(self, path) -> list[dict]:
        return self.chain(self.rows, path["row_node"])

    def column_path(self, path) -> list[dict]:
        return self.chain(self.columns, path["column_node"])

    def value(self, path) -> str:
        return self.cells[path["cell_id"]]["text"].strip()

    def bbox(self, cell_id):
        return self.cells[cell_id]["bbox"]


def labels(nodes: list[dict]) -> str:
    return " › ".join(n["label_raw"].strip() for n in nodes)


# ---------------------------------------------------------------- 그림 1: 문제
def fig1(f: Fonts) -> None:
    t = Table()
    table = render(KO_PDF, t.clip)
    img, d = canvas(1700)
    y = header(d, f, "같은 표를 읽는 두 가지 방법",
               "검색·답변 시스템이 표를 글자 줄로만 받으면, 숫자가 어느 상품·어느 기간의 값인지가 사라집니다.")
    tw = 620
    th = int(table.height * tw / table.width)
    card(d, (40, y, 40 + tw + 32, y + th + 84), fill=BG)
    d.text((56, y + 14), "원문 PDF의 표 (합성 금리표)", font=f(22, True), fill=INK)
    img.paste(table.resize((tw, th), Image.LANCZOS), (56, y + 58))

    # 왼쪽 아래: 일반 텍스트 추출(실제 get_text 결과에서 표 영역의 줄만)
    page = pymupdf.open(KO_PDF)[0]
    flat = [ln for ln in page.get_text(clip=pymupdf.Rect(*t.clip)).split("\n") if ln.strip()]
    by = y + th + 108
    first = t.paths[0]
    kinds = sorted({t.column_path(p)[-1]["label_raw"].strip() for p in t.paths})
    warn = (f"→ “{t.value(first)}”이 어느 해·어느 대상의 값인지, {'인지 '.join(kinds)}인지 알 수 없습니다. "
            "빈칸은 흔적 없이 사라져 뒤의 숫자가 어느 열인지 밀리고, 같은 글자의 행 라벨이 어느 상품에 속하는지도 "
            "구분되지 않습니다.")

    def left_card(dr) -> int:
        dr.text((56, by + 14), "① 글자만 순서대로 뽑으면", font=f(24, True), fill=INK)
        dr.text((56, by + 50), "PDF 기본 텍스트 추출의 실제 출력", font=f(18), fill=MUTE)
        ty = text_block(dr, (56, by + 88), " · ".join(flat), f(20), tw, fill=SUB, gap=9)
        return text_block(dr, (56, ty + 12), warn, f(20, True), tw, fill=ORANGE)

    bottom = left_card(ImageDraw.Draw(Image.new("RGB", (W, 1700)))) + 24  # 높이만 잰다
    card(d, (40, by, 40 + tw + 32, bottom))
    left_card(d)

    # 오른쪽: 파서 출력(paths)에서 행마다 값 하나씩
    rx = 40 + tw + 32 + 40
    rw = W - rx - 40
    card(d, (rx, y, rx + rw, bottom))
    d.text((rx + 20, y + 14), "② 이 파서의 결과", font=f(24, True), fill=INK)
    d.text((rx + 20, y + 50), "값마다 행 경로 · 열 경로 · 원문 칸 위치를 함께 기록 (실행 결과 발췌)",
           font=f(18), fill=MUTE)
    cols = [("값", 0), ("행 경로", 100), ("열 경로", 400), ("출처", 650)]
    hy = y + 96
    for name, dx in cols:
        d.text((rx + 24 + dx, hy), name, font=f(20, True), fill=SUB)
    d.line((rx + 20, hy + 34, rx + rw - 20, hy + 34), fill=LINE, width=2)
    groups: dict = {}
    for p in t.paths:
        groups.setdefault(p["row_node"], []).append(p)
    # 행마다 값 하나씩, 행 순서대로 다른 열에서 고른다(여러 열 경로가 보이도록).
    rows = [members[(i * 3) % len(members)] for i, members in enumerate(groups.values())]
    ry = hy + 50
    for p in rows:
        d.text((rx + 24, ry), t.value(p), font=f(24, True), fill=INK)
        d.text((rx + 24 + 100, ry + 3), labels(t.row_path(p)), font=f(20), fill=INK)
        d.text((rx + 24 + 400, ry + 3), labels(t.column_path(p)), font=f(20), fill=INK)
        d.text((rx + 24 + 650, ry + 5), f"{t.table['page_no']}쪽 cell {p['cell_id']}", font=f(18), fill=MUTE)
        ry += 52
        d.line((rx + 20, ry - 10, rx + rw - 20, ry - 10), fill=LINE, width=1)
    leaf = t.column_path(first)[-1]["label_raw"].strip()
    repeats = sum(1 for n in t.columns.values() if n["label_raw"].strip() == leaf)
    text_block(d, (rx + 24, ry + 14),
               f"→ “{leaf}”이라는 같은 이름의 열이 {repeats}번 나와도 위쪽 머리글이 달라 서로 다른 경로가 됩니다. "
               f"경로가 붙은 칸 {len(t.table['paths'])}개 모두 원문 칸의 번호와 좌표로 되돌아갈 수 있습니다.",
               f(20, True), rw - 48, fill=BLUE)
    states = sorted({p["state"] for p in rows})
    text_block(d, (rx + 24, bottom - 62),
               f"이 경로의 상태는 {', '.join(state(x) for x in states)}입니다. 규칙으로 추론한 결과이며 사람이 확인한 사실이 아닙니다.",
               f(18), rw - 48, fill=MUTE)
    img.crop((0, 0, W, bottom + 30)).save(OUT / "fig1-problem.png", optimize=True)


# ---------------------------------------------------------------- 그림 2: 트리와 경로
def fig2(f: Fonts) -> None:
    t = Table()
    target = t.paths[0]
    row_path, column_path = t.row_path(target), t.column_path(target)
    full = render(KO_PDF, t.clip)
    k = 860 / full.width
    table = full.resize((860, int(full.height * k)), Image.LANCZOS)
    img, d = canvas(1500)
    y = header(d, f, "복합표를 두 개의 트리로 읽습니다",
               "병합된 머리글과 행 묶음을 열 트리·행 트리로 만들고, 값 칸마다 두 트리의 경로를 붙입니다.")
    ox, oy = 48, y + 50
    d.text((ox, y + 6), "원문 표 위에 표시한 실제 결과", font=f(22, True), fill=INK)
    img.paste(table, (ox, oy))
    for node in column_path:
        tint(d, to_px(t.bbox(node["cell_id"]), t.clip, origin=(ox, oy), k=k), BLUE)
    for node in row_path:
        tint(d, to_px(t.bbox(node["cell_id"]), t.clip, origin=(ox, oy), k=k), ORANGE)
    tint(d, to_px(t.bbox(target["cell_id"]), t.clip, origin=(ox, oy), k=k), GREEN, width=7, alpha=70)
    ly = oy + table.height + 16
    x = chip(d, f, (ox, ly), "열 경로의 머리글 칸", BLUE, BLUE_BG)
    x = chip(d, f, (x + 14, ly), "행 경로의 라벨 칸", ORANGE, ORANGE_BG)
    chip(d, f, (x + 14, ly), "값 칸", GREEN, GREEN_BG)

    def draw_tree(tree, title, color, bg, x0, y0, path_ids) -> int:
        d.text((x0, y0), title, font=f(24, True), fill=INK)

        def order(n):
            box = t.bbox(n["cell_id"])
            return box[1], box[0]

        kids: dict = {}
        for n in tree:
            kids.setdefault(n["parent"], []).append(n)
        cursor = dict(y=y0 + 46)

        def walk(node, depth):
            bx, yy = x0 + depth * 40, cursor["y"]
            on = node["node_id"] in path_ids
            font = f(20, on)
            label = (node["label_raw"] or "").strip()
            w = int(d.textlength(label, font=font)) + 26
            if depth:
                d.line((bx - 22, yy - 10, bx - 22, yy + 17, bx - 4, yy + 17), fill=LINE, width=3)
            d.rounded_rectangle((bx, yy, bx + w, yy + 34), 8, fill=bg if on else BG,
                                outline=color if on else LINE, width=3 if on else 2)
            d.text((bx + 13, yy + 3), label, font=font, fill=INK)
            cursor["y"] += 42
            for child in sorted(kids.get(node["node_id"], []), key=order):
                walk(child, depth + 1)

        for root in sorted(kids.get(None, []), key=order):
            walk(root, 0)
            cursor["y"] += 8
        return cursor["y"]

    tx = ox + table.width + 60
    end_a = draw_tree(t.table["column_nodes"], "열 트리 (머리글)", BLUE, BLUE_BG, tx, y + 6,
                      {n["node_id"] for n in column_path})
    end_b = draw_tree(t.table["row_nodes"], "행 트리 (행 라벨)", ORANGE, ORANGE_BG, tx + 270, y + 6,
                      {n["node_id"] for n in row_path})

    by = max(ly + 70, end_a + 20, end_b + 20)
    card(d, (40, by, W - 40, by + 250))
    d.text((64, by + 18), "값 칸 하나에 대해 저장되는 것", font=f(24, True), fill=INK)
    items = [
        ("원문 값", t.value(target), GREEN, GREEN_BG),
        ("행 경로", labels(row_path), ORANGE, ORANGE_BG),
        ("열 경로", labels(column_path), BLUE, BLUE_BG),
    ]
    cx = 64
    for label, value, color, bg in items:
        d.text((cx, by + 66), label, font=f(18), fill=MUTE)
        cx = chip(d, f, (cx, by + 94), value, color, bg) + 36
    box = t.bbox(target["cell_id"])
    d.text((cx, by + 66), "출처", font=f(18), fill=MUTE)
    d.text((cx, by + 98),
           f"{t.table['page_no']}쪽 · 표 {t.table['table_id']} · cell {target['cell_id']} · "
           f"좌표 ({box[0]:.0f}, {box[1]:.0f})–({box[2]:.0f}, {box[3]:.0f})", font=f(20), fill=INK)
    text_block(d, (64, by + 152),
               f"상태: 경로 = {state(target['state'])} · 표 구조 = {state(t.table['state'])} · "
               f"문서 = {state(t.document['document']['validation_status'])}. "
               f"경로는 라벨 글자가 아니라 트리의 위치로 저장하므로, 같은 글자({column_path[-1]['label_raw'].strip()})가 "
               "반복돼도 위쪽 머리글이 다르면 다른 경로입니다. 근거가 부족한 연결은 채우지 않고 미확정으로 남깁니다.",
               f(20), W - 128, fill=SUB)
    img.crop((0, 0, W, by + 280)).save(OUT / "fig2-tree-paths.png", optimize=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--font", default="C:/Windows/Fonts/NotoSansKR-Regular.ttf")
    ap.add_argument("--font-bold", default="C:/Windows/Fonts/NotoSansKR-Bold.ttf")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    f = Fonts(args.font, args.font_bold)
    for fig in (fig1, fig2):
        fig(f)
        print("wrote", fig.__name__)


if __name__ == "__main__":
    main()
