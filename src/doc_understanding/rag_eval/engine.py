"""Retrieval and scoring independent of source documents and answer labels."""
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    chunk_id: str
    text: str
    page: int
    table_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    expansion_ids: list[str] = Field(default_factory=list)
    tree_status: str = "none"


class Question(BaseModel):
    question_id: str
    category: str
    question: str
    answer: str
    answer_kind: Literal["text", "number", "unanswerable"] = "text"
    unit: str | None = None
    period: str | None = None
    evidence: list[dict] = Field(default_factory=list)
    calculation: str | None = None
    review_status: Literal["provisional", "independently_reviewed"] = "provisional"
    authoring: str = "agent visual source inspection; independent human review pending"


def cosine(a, b):
    if len(a) != len(b) or not a or any(not math.isfinite(x) for x in [*a, *b]):
        raise ValueError("Invalid embedding dimensions or values")
    den = math.sqrt(sum(x*x for x in a) * sum(x*x for x in b))
    return sum(x*y for x, y in zip(a, b)) / den if den else 0.0


def terms(text):
    return re.findall(r"[\w]+", text.lower())


def retrieve(chunks, query, vectors, query_vector, *, hybrid=False, k=5):
    """Exact local cosine; D adds BM25 via RRF. Vectors supplied by caller."""
    ids = {c.chunk_id for c in chunks}
    if ids != set(vectors):
        raise ValueError("Embedding corpus mismatch")
    dense = sorted(chunks, key=lambda c: (-cosine(vectors[c.chunk_id], query_vector), c.chunk_id))
    if not hybrid:
        return dense[:k]
    tokens = {c.chunk_id: terms(c.text) for c in chunks}
    avg = sum(map(len, tokens.values())) / max(len(chunks), 1)
    query_terms = set(terms(query))
    df = {t: sum(t in ts for ts in tokens.values()) for t in query_terms}
    def bm25(c):
        ts = tokens[c.chunk_id]
        return sum(math.log(1+(len(chunks)-df[t]+.5)/(df[t]+.5)) *
                   (ts.count(t)*2.2)/(ts.count(t)+1.2*(.25+.75*len(ts)/max(avg, 1)))
                   for t in query_terms if t in ts)
    lexical = sorted(chunks, key=lambda c: (-bm25(c), c.chunk_id))
    score = {c.chunk_id: 1/(60+i) for i, c in enumerate(dense, 1)}
    for i, c in enumerate(lexical, 1):
        score[c.chunk_id] += 1/(60+i)
    return sorted(chunks, key=lambda c: (-score[c.chunk_id], c.chunk_id))[:k]


def context_pack(hits, corpus, token_count, *, budget=3000, expand=False):
    """One shared real-token budget, full chunks only, bounded 2-hop-free expansion."""
    by_id = {c.chunk_id: c for c in corpus}
    candidates = list(hits)
    if expand:
        candidates += [by_id[i] for c in hits for i in c.expansion_ids[:2] if i in by_id]
    out, seen, used = [], set(), 0
    for c in candidates:
        if c.chunk_id in seen:
            continue
        seen.add(c.chunk_id)
        n = token_count(c.text)
        if used+n <= budget:
            out.append(c)
            used += n
    return out, used


def retrieval_metrics(hits, required_ids, corpus=None):
    """Atom recall plus conventional graded chunk nDCG when corpus is supplied."""
    required = set(required_ids)
    if not required:
        return {key: None for key in ("recall", "mrr", "ndcg", "evidence_coverage")}
    seen, dcg, first = set(), 0.0, None
    for rank, hit in enumerate(hits, 1):
        relevant = set(hit.evidence_ids) & required
        if relevant and first is None:
            first = rank
        new = relevant-seen
        dcg += len(new)/math.log2(rank+1)
        seen |= relevant
    # All required evidence in the first result is the fixed ideal.
    coverage = len(seen)/len(required)
    marginal = dcg/len(required)
    ndcg = None
    if corpus is not None:
        gain = lambda c: len(set(c.evidence_ids) & required)/len(required)
        ideal = sorted((gain(c) for c in corpus), reverse=True)[:len(hits)]
        idcg = sum(g/math.log2(i+2) for i,g in enumerate(ideal))
        ndcg = sum(gain(c)/math.log2(i+2) for i,c in enumerate(hits))/idcg if idcg else 0.0
    return {"recall": coverage, "mrr": 1/first if first else 0.0,
            "ndcg": ndcg, "marginal_evidence_ndcg": marginal, "evidence_coverage": coverage}


def answer_metrics(question, response):
    """Strict answer object; unsupported free-text numerical guessing is disallowed."""
    abstained = response.get("abstained") is True
    expected_abstain = question.answer_kind == "unanswerable"
    result = {"abstention_accuracy": int(abstained == expected_abstain)}
    if expected_abstain:
        return {**result, "exact_match": None, "numerical_accuracy": None,
                "unit_accuracy": None, "period_accuracy": None, "evidence_attribution": None}
    value = str(response.get("answer", ""))
    result["exact_match"] = int(value.strip() == question.answer.strip() and not abstained)
    result["numerical_accuracy"] = None
    if question.answer_kind == "number":
        try:
            result["numerical_accuracy"] = int(not abstained and Decimal(value.replace(",", "")) == Decimal(question.answer.replace(",", "")))
        except InvalidOperation:
            result["numerical_accuracy"] = 0
    result["unit_accuracy"] = int(response.get("unit") == question.unit) if question.unit else None
    result["period_accuracy"] = int(response.get("period") == question.period) if question.period else None
    required = {e["evidence_id"] for e in question.evidence}
    cited = set(response.get("evidence_ids", []))
    result["evidence_attribution"] = int(bool(required) and required <= cited and cited <= required)
    return result
