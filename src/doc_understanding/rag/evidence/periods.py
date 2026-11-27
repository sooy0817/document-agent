"""Check duration lookups against the value's own physical row and column."""
import re


DURATION = re.compile(r"(?<![\d.,+\-])(\d+)\s*(개월|일|months?\b|days?\b)", re.I)
DURATION_VALUE = re.compile(r"\d+\s*(?:일|개월|년|주|days?\b|months?\b|years?\b|weeks?\b)", re.I)
CLOSED_OPEN = re.compile(r"(\d+)\s*(일|개월)\s*(?:이상)?\s*[~∼–]\s*(\d+)\s*\2\s*미만")
UPPER = re.compile(r"(\d+)\s*(일|개월)\s*미만")


def compact(text):
    return "".join((text or "").casefold().split())


def paired_headers(headers):
    labels = [compact(h) for h in headers]
    return len(labels) >= 4 and len(labels) % 2 == 0 and all(labels[:2]) and (
        labels[0] != labels[1] and labels == labels[:2] * (len(labels) // 2))


def interval_tables(question, evidence):
    """Old indexes can mark both panels eligible while assigning right values to left labels."""
    if not DURATION_VALUE.search(question):
        return []
    return list(dict.fromkeys((e.get("run_id"), e["table_id"]) for e in evidence
                              if e.get("table_id") and paired_headers(e.get("table_context") or [])
                              and any(UPPER.fullmatch(label.strip()) for label in e.get("row_path") or [] if label)))


def bounds(number, unit):
    # Month length is unknown without a calendar date. Keep the entire possible range.
    return (28 * number, 31 * number) if unit == "개월" else (number, number)


def compare(point, limit):
    n, unit = point
    m, other = limit
    if unit == other:
        return n < m
    low, high = bounds(n, unit)
    first, last = bounds(m, other)
    if high < first:
        return True
    if low >= last:
        return False
    return None


def duration_point(question):
    points = DURATION.findall(question)
    if (len(points) != 1 or len(DURATION_VALUE.findall(question)) != 1
            or re.search(r"\d\s*[~∼–-]\s*\d|(?:개월|일)\s*(?:미만|이상|이하|초과|이내|이전|이후|전후|[~∼–])", question)):
        return None
    number, unit = points[0]
    return int(number), "개월" if unit.casefold() in ("개월", "month", "months") else "일"


def physical_interval(question, grid):
    """Return None for unrelated grids, an empty list for ambiguity, or [label, header, value]."""
    cells = grid["cells"]
    columns = sorted((c for c in cells if c["r"] == 0), key=lambda c: c["c"])
    if not paired_headers([c["text"] for c in columns]):
        return None
    if not any(UPPER.fullmatch((c["text"] or "").strip()) for c in cells if c["r"] > 0 and c["text"]):
        return None
    point = duration_point(question)
    if point is None or any(c["cs"] != 1 or c["rs"] != 1 for c in cells):
        return []
    slots = {(c["r"], c["c"]): c for c in cells}
    height, width = grid.get("rows"), grid.get("columns")
    if (not height or width != len(columns) or len(slots) != len(cells)
            or set(slots) != {(r, c) for r in range(height) for c in range(width)}):
        return []
    bands = []
    trailing_blank = False
    for row in range(1, height):
        for col in range(0, width, 2):
            label, value = slots[row, col], slots[row, col + 1]
            if not (label["text"] or "").strip() and not (value["text"] or "").strip():
                trailing_blank = True
                continue
            bound = UPPER.fullmatch((label["text"] or "").strip())
            if trailing_blank or not bound or not (value["text"] or "").strip():
                return []
            limit = int(bound[1]), bound[2]
            if bands and compare(bands[-1][0], limit) is not True:
                return []
            bands.append((limit, [label["id"], columns[col + 1]["id"], value["id"]]))
    for limit, relation in bands:
        below = compare(point, limit)
        if below is None:
            return []
        if below:
            return relation
    return []


def period_conflict(question, chosen, available=()):
    """A literal half-open range on the cited path must contain a same-unit lookup point."""
    if mismatched_duration_axis(question, chosen):
        return "REQUESTED_AXIS_NOT_CITED"
    # A comparative lookup may select only one product and omit the opposing metric entirely.
    # Count only documents whose own source labels name the requested attribute.
    if "기간" in question and re.search(r"중.{0,32}더|보다.{0,32}(?:길|짧)|각각|비교", question):
        def named_documents(items, include_headers):
            found = set()
            for e in items:
                axes = [*(e.get("row_path") or []), *(e.get("column_path") or [])]
                if include_headers:
                    axes.extend(e.get("table_context") or [])
                if any(compact(a).endswith("기간") and compact(a) in compact(question) for a in axes):
                    ident = e.get("document_id") or e.get("run_id")
                    if ident:
                        found.add(ident)
            return found
        candidates = named_documents(available, True)
        supported = named_documents([e for e in chosen if DURATION_VALUE.search(e.get("value_raw") or "")], False)
        if len(candidates) >= 2 and len(candidates & supported) < 2:
            return "COMPARISON_DURATION_MISSING"
    point = duration_point(question)
    if point is None:
        return None
    number, unit = point
    for cell in chosen:
        for label in cell.get("row_path") or []:
            band = CLOSED_OPEN.fullmatch((label or "").strip())
            if band and unit == band[2] and not int(band[1]) <= number < int(band[3]):
                return "REQUESTED_INTERVAL_NOT_CITED"
    return None


def mismatched_duration_axis(question, chosen):
    """A duration must belong to the requested axis even when another column also contains durations.

    Detect a concrete contradiction: an explicitly named attribute labels another
    column of the same table, while the cited value is under a different heading.
    """
    requested = compact(question)
    for e in chosen:
        if not e.get("table_id") or not DURATION_VALUE.search(e.get("value_raw") or ""):
            continue
        axes = {compact(x) for k in ("row_path", "column_path") for x in e.get(k) or []}
        headers = {compact(h) for h in e.get("table_context") or []}
        named = {h for h in headers if len(h) >= 2 and h in requested and not any(c.isdigit() for c in h)}
        # When two period labels differ, do not infer that the other value column is a synonym.
        if not named and re.search(r"[가-힣]+기간", requested):
            named = {h for h in headers if h.endswith("기간")}
        if (not named and not DURATION_VALUE.search(question) and re.search(r"[가-힣]+기간", requested)
                and all(DURATION_VALUE.search(axis) for axis in axes)):
            return True
        if named and not named.intersection(axes):
            return True
    return False
