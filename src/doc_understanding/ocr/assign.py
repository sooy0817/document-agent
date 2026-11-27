# 역할: 쪽 전체에서 읽은 줄을 칸에 배정하고, 원문과 비교할 때 쓰는 느슨한 같음을 정한다.
import re
import unicodedata

# 음수 표시 삼각형은 글꼴에 따라 여러 글자로 읽힌다. 저장하는 글자는 그대로 두고 비교할 때만 하나로 본다.
TRIANGLES = str.maketrans({"∆": "△", "Δ": "△", "▲": "△", "▵": "△"})
NUMERIC = re.compile(r"^[\d,.\-+()%△\s]*\d[\d,.\-+()%△\s]*$")


def comparable(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).translate(TRIANGLES)


def same_text(a, b):
    """공백·전각과 삼각형 모양의 차이를 무시하고 같은가. 판정에만 쓰고 글자를 바꾸지 않는다."""
    return comparable(a) == comparable(b)


# 줄 넓이의 이만큼 넘게 다른 칸에 걸치면 칸을 정하지 않는다. 선을 조금 넘는 읽기 오차는 허용한다.
# 스캔 문서로 잰 값이 아니라 정한 값이다.
SPILL = 0.2


def area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def shared(a, b):
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def holds(outer, inner):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def assign(lines, boxes):
    """줄(OcrLine)을 칸(bbox 목록)에 배정한다. 결과는 (칸마다 dict(text, confidence, lines, marks), 칸 밖의 줄, 보류한 줄).

    줄의 중심이 들어 있는 칸 가운데 가장 작은 칸에 넣는다(칸 안의 표가 있으면 안쪽 칸이 받는다).
    칸을 하나로 정할 수 없는 줄은 어느 칸에도 넣지 않고 셋째 반환값으로 돌려준다: 중심이 든 가장 작은 칸이
    둘 이상이거나(경계 위), 줄이 그 칸을 감싸지 않는 다른 칸에 `SPILL` 넘게 걸친 경우다. 글자를 읽은 신뢰도는
    칸 소속의 근거가 아니므로 이 판단에 쓰지 않는다.
    어느 칸에도 들지 않은 줄은 버리지 않고 둘째 반환값으로 돌려준다.
    한 칸의 여러 줄은 위에서 아래, 왼쪽에서 오른쪽 순서로 줄바꿈으로 잇는다.
    신뢰도는 그 칸에 든 줄 가운데 가장 낮은 값이다. 줄이 없는 칸은 text가 빈 문자열, confidence가 None이다.
    """
    held, loose, unsure = [[] for _ in boxes], [], []
    for line in lines:
        x0, y0, x1, y1 = line.bbox
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        inside = [n for n, (bx0, by0, bx1, by1) in enumerate(boxes) if bx0 <= cx <= bx1 and by0 <= cy <= by1]
        if not inside:
            loose.append(line)
            continue
        smallest = min(area(boxes[n]) for n in inside)
        chosen = [n for n in inside if area(boxes[n]) == smallest]
        limit = SPILL * area(line.bbox)
        if len(chosen) > 1 or any(shared(line.bbox, other) > limit for n, other in enumerate(boxes)
                                  if n != chosen[0] and not holds(other, boxes[chosen[0]])):
            unsure.append(line)
            continue
        held[chosen[0]].append(line)
    cells = []
    for got in held:
        got.sort(key=lambda line: line.bbox[1])
        rows = []
        for line in got:  # 세로로 절반 넘게 겹치면 같은 줄로 본다
            top, bottom = line.bbox[1], line.bbox[3]
            if rows and min(bottom, rows[-1]["bottom"]) - max(top, rows[-1]["top"]) > 0.5 * min(
                    bottom - top, rows[-1]["bottom"] - rows[-1]["top"]):
                rows[-1]["lines"].append(line)
                rows[-1]["bottom"] = max(bottom, rows[-1]["bottom"])
            else:
                rows.append(dict(top=top, bottom=bottom, lines=[line]))
        text = "\n".join(" ".join(line.text for line in sorted(row["lines"], key=lambda line: line.bbox[0])) for row in rows)
        marks = []
        if text and NUMERIC.match(comparable(text)) and any(c in text for c in "∆Δ▲▵"):
            marks.append("NEGATIVE_MARK_VARIANT")  # △가 다른 삼각형 글자로 읽혔다. 글자는 읽은 그대로 둔다
        cells.append(dict(text=text, confidence=min((line.confidence for line in got), default=None),
                          lines=len(got), marks=marks))
    return cells, loose, unsure
