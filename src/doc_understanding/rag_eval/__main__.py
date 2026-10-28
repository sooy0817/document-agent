"""Offline CLI: imported vectors/responses only; no external API implementation."""
import argparse
import json
import time
from pathlib import Path

from .corpus import read, sha
from .engine import Chunk, Question, answer_metrics, retrieval_metrics, retrieve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--vectors", type=Path, required=True,
                        help="model_id, corpus_sha256, questions_sha256, chunks, queries")
    parser.add_argument("--answers", type=Path, help="Optional stored answer objects by question ID")
    parser.add_argument("--hybrid", action="store_true")
    parser.add_argument("--pages", type=int, nargs="+")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.k < 1:
        parser.error("k must be positive")
    corpus = [Chunk.model_validate(c) for c in read(args.corpus)]
    questions = [Question.model_validate(q) for q in read(args.questions)]
    vectors = read(args.vectors)
    if vectors.get("corpus_sha256") != sha(args.corpus) or vectors.get("questions_sha256") != sha(args.questions) or not vectors.get("model_id"):
        raise ValueError("Frozen input hash/model mismatch")
    if args.pages:
        corpus = [c for c in corpus if c.page in args.pages]
        questions = [q for q in questions if q.evidence and {e['page'] for e in q.evidence} <= set(args.pages)]
    embeddings = {c.chunk_id: vectors["chunks"][c.chunk_id] for c in corpus}
    responses = read(args.answers) if args.answers else {}
    results = []
    for q in questions:
        started = time.perf_counter()
        hits = retrieve(corpus, q.question, embeddings, vectors["queries"][q.question_id], hybrid=args.hybrid, k=args.k)
        results.append({"question_id":q.question_id,"review_status":q.review_status,
                        "retrieval":retrieval_metrics(hits,[e["evidence_id"] for e in q.evidence],corpus),
                        "hit_ids":[c.chunk_id for c in hits],"retrieval_seconds":time.perf_counter()-started,
                        "answer":answer_metrics(q,responses[q.question_id]) if q.question_id in responses else None})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({"model_id":vectors["model_id"],"k":args.k,"results":results,
        "answer_usage":responses.get("_usage"),"embedding_usage":vectors.get("usage"),
        "note":"Offline imported outputs. nDCG graded by fraction of reference evidence per chunk; marginal evidence nDCG also reported. Missing usage is N/A."},ensure_ascii=False,indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()
