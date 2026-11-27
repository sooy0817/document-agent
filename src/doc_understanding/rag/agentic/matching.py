# 역할: 요구 사항 문구와 표 라벨을 의미 층별로 비교한다. 표기 정규화 · 숫자 구간 · 임베딩 유사도. 원문은 바꾸지 않는다.
"""Semantic structure matching (계획 4단계).

L0 표기 정규화: NFKC + 물결표·대시 변형과 공백(비교용). 한자 독음 변환은 하지 않는다(모델 단계가 해석).
L1 문자 포함: 정규화한 문자열 포함.
L2 숫자 구간: '이상·이하·초과·미만·이내·~·-'와 단위(일·주·개월·년, %)를 구간으로 해석해 질문 값이 라벨 구간에
    속하면 강한 가점, 같은 차원인데 겹치지 않으면 감점. 단어 사전이 아니라 표기 형식 해석이다.
    기준 단위는 개월(1년=12개월 정확), 일·주는 30일=1개월 근사이며 섞이면 approx로 표시한다.
L3 의미 유사도: 같은 모델로 만든 임베딩의 코사인 유사도(약한 가점, L2 배제를 덮지 않음).
    문자·구간 일치가 없을 때만 쓴다. 임베딩은 반대말을 구분하지 못한다(실측 '장점~단점' 0.604,
    '중도해지 이율~일반중도해지 이자율' 0.610, text-embedding-3-small). 그래서 기준 0.6·가점 0.5~1.0으로 약하게 두고
    의미 판별(장점/단점, 고정/변동, 만기 후/예치 후)은 리랭커·답변 모델에 맡긴다.
"""
import math
import re
import unicodedata
from dataclasses import dataclass

# 비교에만 쓰는 기호 대응. NFKC가 합치지 않는 물결표·대시 변형만 둔다.
# 한자 독음(前→전 등)은 규칙으로 늘리지 않는다. 열린 집합이라 사전으로 끝낼 수 없고, 실측에서 라벨 임베딩과
# 원문을 읽는 리랭커가 前/後 가지를 구분했다(2026-11-11, KB 2·3쪽). 해석은 모델 단계가 맡는다.
VARIANTS = {"∼": "~", "〜": "~", "～": "~", "–": "-", "—": "-"}
# 기준 단위는 개월이다(1년=12개월은 정확). 일·주는 30일=1개월 근사라 섞이면 approx로 표시한다.
UNITS = {"일": ("time", 1 / 30), "주": ("time", 7 / 30), "개월": ("time", 1.0), "월": ("time", 1.0),
         "년": ("time", 12.0), "%": ("percent", 1.0)}
EXACT_TIME = {"개월", "월", "년"}
NUMBER = r"(\d+(?:[.,]\d+)?)\s*(개월|일|주|년|월|%)?"


def normalize(text):
    text = unicodedata.normalize("NFKC", text or "")
    for src, dst in VARIANTS.items():
        text = text.replace(src, dst)
    return text


def compact(text):
    return re.sub(r"[\s()\[\]·,:/]+", "", normalize(text)).lower()


@dataclass(frozen=True)
class Interval:
    low: float = -math.inf
    low_incl: bool = True
    high: float = math.inf
    high_incl: bool = True
    dimension: str = "time"
    approx: bool = False
    day_based: bool = False    # 일·주 단위가 쓰였는가
    month_based: bool = False  # 개월·월·년 단위가 쓰였는가

    def contains(self, other):
        lo = other.low > self.low or (other.low == self.low and (self.low_incl or not other.low_incl))
        hi = other.high < self.high or (other.high == self.high and (self.high_incl or not other.high_incl))
        return lo and hi

    def overlaps(self, other):
        left = self.high > other.low or (self.high == other.low and self.high_incl and other.low_incl)
        right = other.high > self.low or (other.high == self.low and other.high_incl and self.low_incl)
        return left and right


def _value(number, unit, default_unit):
    unit = unit or default_unit
    if unit not in UNITS:
        return None, None
    dimension, scale = UNITS[unit]
    return float(number.replace(",", "")) * scale, dimension


LOW_WORDS = {"이상": True, "초과": False}
HIGH_WORDS = {"이하": True, "미만": False, "이내": True}


def parse_interval(text):
    """문구에서 구간 하나를 해석한다. 숫자·단위가 없거나 해석이 모호하면 None."""
    t = normalize(text)
    found = [(m.group(1), m.group(2), m.end()) for m in re.finditer(NUMBER, t)]
    found = [f for f in found if f[1] or len(found) > 1]
    if not found:
        return None
    units = [u for _, u, _ in found if u]
    default = units[-1] if units else None
    # 일·주와 개월·년이 함께 쓰이면 환산이 근사다.
    day_based = bool({u for u in units if u in ("일", "주")})
    month_based = bool({u for u in units if u in EXACT_TIME})
    approx = day_based and month_based
    flags = dict(approx=approx, day_based=day_based, month_based=month_based)

    def word_after(end):
        tail = t[end:end + 4]
        for word in list(LOW_WORDS) + list(HIGH_WORDS):
            if tail.lstrip().startswith(word):
                return word
        return None

    first = _value(found[0][0], found[0][1], default)
    if first[0] is None:
        return None
    if len(found) >= 2 and re.search(r"[~\-]|부터|에서", t[found[0][2]:found[1][2]]):
        second = _value(found[1][0], found[1][1], default)
        if second[0] is None or second[1] != first[1]:
            return None
        w1, w2 = word_after(found[0][2]), word_after(found[1][2])
        return Interval(first[0], LOW_WORDS.get(w1, True), second[0], HIGH_WORDS.get(w2, True), first[1], **flags)
    word = word_after(found[0][2])
    if word in LOW_WORDS:
        return Interval(first[0], LOW_WORDS[word], math.inf, True, first[1], **flags)
    if word in HIGH_WORDS:
        return Interval(-math.inf, True, first[0], HIGH_WORDS[word], first[1], **flags)
    if len(found) == 1:
        return Interval(first[0], True, first[0], True, first[1], **flags)  # 값 하나(예: '2개월', '6개월')
    return None


def interval_relation(term, label):
    """('contains'|'disjoint'|'overlap'|None, approx). 같은 차원일 때만 비교한다."""
    q, r = parse_interval(term), parse_interval(label)
    if not q or not r or q.dimension != r.dimension:
        return None, False
    approx = q.approx or r.approx or (q.day_based and r.month_based) or (q.month_based and r.day_based)
    if r.contains(q):
        return "contains", approx
    if not r.overlaps(q):
        return "disjoint", approx
    return "overlap", approx


def text_match(term, label):
    a, b = compact(term), compact(label)
    return len(a) >= 2 and bool(b) and (a in b or (len(b) >= 2 and b in a))


def cosine(a, b):
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


WEIGHTS = dict(text=1.0, interval=2.0, interval_exclusion=-2.0, semantic=0.5)


def match_term(term, labels, similarity=None, threshold=0.6):
    """한 요구 문구를 셀의 라벨들과 비교해 가장 강한 일치 하나를 고른다. 배제가 있으면 배제가 우선한다."""
    best = None
    for label in labels:
        relation, approx = interval_relation(term, label)
        if relation == "disjoint":
            return dict(term=term, label=label, kind="interval_exclusion", weight=WEIGHTS["interval_exclusion"],
                        approx=approx)
        if relation == "contains":
            candidate = dict(term=term, label=label, kind="interval", weight=WEIGHTS["interval"], approx=approx)
        elif text_match(term, label):
            candidate = dict(term=term, label=label, kind="text", weight=WEIGHTS["text"])
        elif similarity is not None:
            s = similarity(term, label)
            if s is None or s < threshold:
                continue
            candidate = dict(term=term, label=label, kind="semantic", similarity=round(s, 3),
                             weight=round(min(1.0, WEIGHTS["semantic"] + (s - threshold)), 3))
        else:
            continue
        if best is None or candidate["weight"] > best["weight"]:
            best = candidate
    return best


_VECTORS = {}  # (profile_id, text) → 벡터. 같은 라벨·문구를 다시 임베딩하지 않는다(프로세스 안 캐시).


def similarity_from(embed, profile_id, texts, limit=5000):
    """texts 중 캐시에 없는 것만 한 번에 임베딩하고 코사인 유사도 함수를 돌려준다. 실패하면 None."""
    missing = [t for t in dict.fromkeys(texts) if t and (profile_id, t) not in _VECTORS]
    try:
        for i in range(0, len(missing), 32):
            chunk = missing[i:i + 32]
            for text, vector in zip(chunk, embed(chunk)):
                _VECTORS[(profile_id, text)] = vector
    except Exception:
        return None  # 의미 유사도 없이 문자·구간 일치만 쓴다.
    if len(_VECTORS) > limit:
        for key in list(_VECTORS)[: len(_VECTORS) - limit]:
            _VECTORS.pop(key, None)

    def similarity(a, b):
        va, vb = _VECTORS.get((profile_id, a)), _VECTORS.get((profile_id, b))
        return cosine(va, vb) if va is not None and vb is not None else None
    return similarity
