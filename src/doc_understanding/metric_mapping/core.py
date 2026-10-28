import re
from decimal import Decimal, InvalidOperation

ALIASES = {
    "total_assets": ("자산총계", "자산총액", "총자산"),
    "total_liabilities": ("부채총계", "총부채"),
    "total_equity": ("자본총계", "자본총액"),
    "operating_revenue": ("영업수익",),
    "net_income": ("당기순이익", "당기순손실", "당기순손익"),
}
NAMES = dict(total_assets="총자산", total_liabilities="총부채", total_equity="자본총계",
             operating_revenue="영업수익", net_income="당기순손익")


def compact(text):
    return re.sub(r"\s+", "", text or "")


def label_identity(label):
    return re.sub(r"^(?:[Ⅰ-Ⅿⅰ-ⅿIVX\d.()]+|[가-하][.)])", "", compact(label)).strip("()")


def classify(metric, label, family, structure, description_conflict=False, parent_labels=()):
    """Multiple semantic dimensions; lexical exclusions alone cannot yield a positive."""
    text = compact(label)
    if description_conflict:
        return "ABSTAIN", None, ["STRUCTURE_DESCRIPTION_CONFLICT"]
    if metric == "total_equity" and "부채" in text and "자본" in text:
        return "OTHER_METRIC", "liabilities_and_equity", ["COMBINED_BALANCE_NOT_EQUITY"]
    if any(x in text for x in ("이익률", "수익률", "비율")):
        return "OTHER_METRIC", "ratio", ["RATIO_NOT_MONETARY_LEVEL"]
    if any(x in text for x in ("1좌당", "주당", "좌당", "1주당")):
        return "OTHER_METRIC", "per_unit_income", ["PER_UNIT_NOT_TOTAL"]
    if metric == "net_income" and any(x in text for x in ("차감전", "세전")):
        return "OTHER_METRIC", "pretax_income", ["PRETAX_NOT_AFTER_TAX"]
    identity = label_identity(label)
    if metric == "net_income":
        identity = re.sub(r"^(?:법인세차감후|세후)", "", identity)
    if identity not in ALIASES[metric]:
        return "ABSTAIN", None, ["COMPONENT_OR_NONCANONICAL_LABEL", "NEEDS_SEMANTIC_REVIEW"]
    if any(label_identity(p) in ALIASES[metric] for p in parent_labels if p):
        return "ABSTAIN", None, ["PARENT_PATH_SUGGESTS_COMPONENT", "NEEDS_SEMANTIC_REVIEW"]
    expected = "balance" if metric.startswith("total_") else "income"
    if family != expected:
        return "ABSTAIN", None, ["STATEMENT_CONTEXT_MISSING_OR_DIFFERENT"]
    if not structure:
        return "ABSTAIN", None, ["NO_PHYSICAL_STRUCTURED_ROW_LINK"]
    return "PROPOSE", None, ["LABEL_DEFINITION_COMPATIBLE", "STATEMENT_FAMILY_SUPPORTED",
                             "PHYSICAL_STRUCTURED_ROW_ANCHORED", "UNREVIEWED_PROPOSAL"]


def number(raw):
    text = compact(raw).replace(",", "")
    text = re.sub(r"=+$", "", text)
    if text in ("", "-", "—", "△", "N/A"):
        return None
    if text.startswith(("△", "▲")):
        text = "-" + text[1:]
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    try:
        return str(Decimal(text))
    except InvalidOperation:
        return None


def overlap(a, b):
    intersection = max(0,min(a["x1"],b["x1"])-max(a["x0"],b["x0"])) * max(
        0,min(a["y1"],b["y1"])-max(a["y0"],b["y0"]))
    union = (a["x1"]-a["x0"])*(a["y1"]-a["y0"])+(b["x1"]-b["x0"])*(b["y1"]-b["y0"])-intersection
    return intersection/union if union else 0


def header_candidates(table, value, label):
    box = value["source"]["bbox"]
    x = (box["x0"]+box["x1"])/2
    # Only cells preceding the row, with explicit header semantics and containing its center.
    return [c for c in table["cells"] if c["row_index"] < label["row_index"]
            and c["source"]["bbox"]["x0"] <= x <= c["source"]["bbox"]["x1"]
            and re.search(r"20\d{2}|제\s*\d+.*기|금\s*액|구성비|증감|잔액|비율|평잔",
                          c.get("text_raw") or "")
            and c["row_index"] < 8]


def period_binding(headers, blocks):
    # Prefer a scoped year/term header, not a full-width report date line.
    direct = [h for h in headers if "부터" not in (h["text_raw"] or "")]
    years = {int(y) for h in direct for y in re.findall(r"20\d{2}", h["text_raw"] or "")}
    terms = {int(t) for h in direct for t in re.findall(r"제\s*(\d+)", h["text_raw"] or "")}
    matched = []
    for b in blocks:
        raw = b["text_raw"]
        found = {int(t) for t in re.findall(r"제\s*(\d+)", raw)}
        if terms and found.intersection(terms):
            matched.append(b)
    dates = set()
    for b in matched:
        dates.update(tuple(map(int,d)) for d in re.findall(
            r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일",b["text_raw"]))
        years.update(int(y) for y in re.findall(r"20\d{2}",b["text_raw"]))
    if len(years) != 1:
        return dict(state="unresolved", year=None, start=None, end=None,
                    evidence=matched, reason="period header absent or conflicting")
    from datetime import date
    parsed = []
    for d in sorted(dates):
        try:
            parsed.append(date(*d).isoformat())
        except ValueError:
            return dict(state="unresolved", year=None, start=None, end=None,
                        evidence=matched, reason="invalid original date")
    return dict(state="inferred", year=next(iter(years)),
                start=parsed[0] if len(parsed)==2 else None,
                end=parsed[-1] if parsed else None, evidence=matched,
                reason="explicit header year; dates only when explicitly present")
