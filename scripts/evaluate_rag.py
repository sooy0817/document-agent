"""RAG 평가 실행기: 평가셋 질문을 설정별로 실행해 저장하고 evaluation.rag 지표로 채점한다.

설정
- lexical: 키워드 검색 → 근거 복원 (모델 호출 없음)
- hybrid: 키워드+벡터 RRF → 근거 복원 (질문 임베딩만)
- agent: 질문 이해 계획(gpt-4.1-mini) → 검색어별 하이브리드 → 구조 일치 → 근거 (답변 없음)
- agent_rerank: agent + LLM 목록 리랭커(gpt-4.1-mini, 상위 30개 → 12개 근거)
- agent_answer: agent + 리랭커 + 답변(gpt-4.1, 화면과 같은 선택형·답변 문장) — --answer로만 실행(유료)
결과: work/rag-eval/<평가셋>-<시각>/ 아래 설정별 항목 결과, report.json, report.md.

집계: 답변 설정은 전체 대상 기준(실행 오류를 분모에 포함)으로 대상·정상 완료·정답·보류·실행 오류 수를 함께 적는다.
검색만 실행한 설정은 답변 집계를 만들지 않는다. --rescore는 저장된 결과를 지금 기준으로 다시 채점해 새 폴더에 쓰고
실행 폴더의 원래 기록(report.json, report.md, failures.json)은 그대로 둔다.
"""
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

from doc_understanding.evaluation import failure_log
from doc_understanding.evaluation.rag import failures as rag_failures
from doc_understanding.evaluation.rag.anchors import resolve_item
from doc_understanding.evaluation.rag.dataset import load
from doc_understanding.evaluation.rag.metrics import METRIC_VERSION, mode_of, score_item, summarize
from doc_understanding.evaluation.rag.snapshot import digest, read_snapshot, write_snapshot
from doc_understanding.rag import repository
from doc_understanding.rag.agentic import directory as agent_directory
from doc_understanding.rag.agentic.plan import PlanCache
from doc_understanding.rag.embedding.client import EmbeddingClient
from doc_understanding.rag.embedding.models import EmbeddingConfig
from doc_understanding.rag.retrieval.runtime import Retriever
from doc_understanding.rag.service import resolve_evidence
from doc_understanding.runtime.structured_call import CallPolicy, Ledger
from doc_understanding.services.corpus_rag import INDEX_VERSION, latest_runs
from doc_understanding.services import source_view
from doc_understanding.services.web_rag import scoped

EMBEDDING = EmbeddingConfig(model="text-embedding-3-small", dimensions=1536, input_usd_per_million=0.02,
                            max_cost_usd=0.5, max_calls=200)
PLAN = CallPolicy(model="gpt-4.1-mini", input_usd_per_million=0.4, output_usd_per_million=1.6, max_cost_usd=0.05)
RERANK = CallPolicy(model="gpt-4.1-mini", input_usd_per_million=0.4, output_usd_per_million=1.6, max_cost_usd=0.05)
ANSWER = dict(model="gpt-4.1", input_usd_per_million=2.0, output_usd_per_million=8.0, max_cost_usd=0.15)
# 같은 질문은 실행마다 같은 계획(검색어)을 쓴다. 전후 비교에서 계획 모델의 흔들림이 섞이지 않게 한다.
PLAN_CACHE = PlanCache(Path("work/rag-eval/plan-cache"))


def commit():
    """현재 코드 버전(짧은 커밋 해시, 작업 중 변경이 있으면 +). 장부에서 어느 코드의 결과인지 구분한다."""
    import subprocess
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True,
                               timeout=10).stdout.strip()
        return head + ("+" if dirty else "") if head else None
    except Exception:
        return None


def run_item(config, item, run_ids, out_dir):
    started = time.perf_counter()
    client = EmbeddingClient(EMBEDDING, allow_external=True) if config != "lexical" else None
    base = Retriever("hybrid", client) if client else None
    retriever = scoped(base, run_ids)
    cost = 0.0
    record = dict(question=item.question)
    if config in ("lexical", "hybrid"):
        hits = retriever(item.question, None, 10)
        record.update(hits=hits, evidence=resolve_evidence(hits, item.question, max_documents=3))
    else:
        from doc_understanding.rag.agentic.plan import make_plan
        from doc_understanding.rag.agentic.rerank import LLMListwiseReranker
        from doc_understanding.rag.agentic.run import answer, gather, rerank_evidence
        ledger = Ledger(max_calls=2, max_total_usd=0.1)
        rerank_ledger = Ledger(max_calls=2, max_total_usd=0.1)
        reranker = LLMListwiseReranker(RERANK, allow_external=True, ledger=rerank_ledger)
        if config in ("agent", "agent_rerank"):
            plan, check = make_plan(item.question, PLAN, out_dir / item.id / "plan", allow_external=True, ledger=ledger,
                                    cache=PLAN_CACHE)
            evidence, trace = gather(item.question, plan, None, retriever, max_documents=3)
            if config == "agent_rerank":
                evidence = rerank_evidence(item.question, plan, evidence, trace, reranker, 12,
                                           out_dir / item.id / "rerank")
                record.update(rerank={k: v for k, v in trace["rerank"].items() if k != "scores"})
            record.update(hits=trace["hits"], evidence=evidence, plan=plan.model_dump(), plan_check=check)
        else:
            report = answer(item.question, out_dir / item.id, run_id=None, retriever=retriever, plan_policy=PLAN,
                            plan_cache=PLAN_CACHE,
                            # 화면의 전체 검색과 같게: 질문의 대상을 문서와 맞춰 검색 범위를 정한다.
                            directory=lambda: agent_directory.documents(INDEX_VERSION, run_ids),
                            narrow=lambda ids: scoped(base, ids), mentions=agent_directory.mentions(INDEX_VERSION, run_ids),
                            grid_loader=source_view.table_grid, grid_shapes=source_view.table_shapes,
                            grid_policy=PLAN.model_copy(update=dict(max_output_tokens=600)),
                            allow_external=True, ledger=ledger, max_documents=3, answer_mode="selection",
                            reranker=reranker, max_evidence=12,
                            presentation=True, **ANSWER)
            record.update(hits=report["attempts"][-1]["trace"]["hits"] if report["attempts"] else [],
                          evidence=report["evidence"], result=report["result"], plan=report["plan"],
                          retried=report["retried"], documents=report.get("documents"))
            cost += report["result"].get("estimated_cost_usd") or 0
            if report["result"].get("generation_mode") != "raw_grid_reading":
                cost += report.get("grid_estimated_cost_usd") or 0
        cost += ledger.estimated_usd + rerank_ledger.estimated_usd
    if client:
        cost += sum(c.get("estimated_cost_usd", 0) for c in client.audit)
    record.update(seconds=round(time.perf_counter() - started, 2), cost_usd=round(cost, 6))
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", type=Path, default=Path("config/evaluation/rag-dev-v1.json"))
    parser.add_argument("--configs", default="lexical,hybrid,agent")
    parser.add_argument("--answer", action="store_true", help="agent_answer(유료 답변 모델)도 실행")
    parser.add_argument("--only", help="쉼표로 구분한 항목 ID만 실행")
    parser.add_argument("--corpus-from-set", action="store_true",
                        help="모든 문항을 평가셋에 등록된 동일 문서 목록에서 실행")
    parser.add_argument("--rescore", type=Path, help="저장된 실행 폴더를 다시 채점(검색·모델 호출 없음)")
    args = parser.parse_args()
    data = load(args.set)
    if args.rescore:
        return rescore(data, args.rescore)
    configs = args.configs.split(",") + (["agent_answer"] if args.answer else [])
    if configs != ["lexical"] and not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("API_KEY_UNAVAILABLE")
    items = [i for i in data.items if not args.only or i.id in args.only.split(",")]
    corpus = [r for r in latest_runs() if r["index_version"] == INDEX_VERSION]
    wanted = set(data.documents.values())
    if args.corpus_from_set:
        corpus = [r for r in corpus if r["document_id"] in wanted]
        if {r["document_id"] for r in corpus} != wanted:
            raise SystemExit("EVALUATION_DOCUMENT_NOT_INDEXED")
    run_ids = [r["run_id"] for r in corpus]
    # 정답 위치를 찾는 데는 평가셋이 가리키는 문서만 필요하다(코퍼스 전체 문서를 읽지 않는다).
    docs = {r["document_id"]: repository.fetch_document(r["run_id"]) for r in corpus if r["document_id"] in wanted}
    resolved = {i.id: resolve_item(i, data.documents, docs) for i in items}
    out = Path("work/rag-eval") / f"{data.name}-{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True)
    import hashlib
    code_files = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(Path("src/doc_understanding").rglob("*"))
                  if p.is_file() and p.suffix in (".py", ".sql")}
    for p in (Path(__file__), Path("pyproject.toml")):
        code_files[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    reference_hash = write_snapshot(out, data, items, resolved, provenance=dict(
        corpus_runs=run_ids, index_version=INDEX_VERSION, configs=configs, commit=commit(), code_files=code_files,
        documents={key: digest(doc.model_dump(mode="json")) for key, doc in docs.items()},
        settings=dict(embedding=EMBEDDING.model_dump(mode="json"), plan=PLAN.model_dump(mode="json"),
                      rerank=RERANK.model_dump(mode="json"), answer=ANSWER, plan_cache=str(PLAN_CACHE.folder))))
    report = dict(eval_set=data.name, reviewed_by_human=data.reviewed_by_human, corpus_runs=run_ids,
                  index_version=INDEX_VERSION, metric_version=METRIC_VERSION, reference_sha256=reference_hash,
                  items=len(items), configs={})
    failed = []
    for config in configs:
        rows = []
        for item in items:
            started = time.perf_counter()
            try:
                run = run_item(config, item, run_ids, out / config)
            except Exception as error:  # 한 항목 실패는 기록하고 계속한다.
                run = dict(error=type(error).__name__, error_message=str(error)[:300], hits=[], evidence=[],
                           seconds=round(time.perf_counter() - started, 2), cost_usd=None)
            (out / config).mkdir(parents=True, exist_ok=True)
            (out / config / f"{item.id}.json").write_text(json.dumps(run, ensure_ascii=False, indent=1, default=str),
                                                          encoding="utf-8")
            rows.append(dict(score_item(item, resolved[item.id], run, mode=mode_of(config)), error=run.get("error")))
            failure = rag_failures.item_failure(item, rows[-1], run, config, data.name)
            if failure:
                failed.append(failure)
        report["configs"][config] = dict(summary=summarize(rows, mode_of(config)), items=rows)
        print(config, json.dumps({k: v for k, v in report["configs"][config]["summary"].items() if k != "by_type"},
                                 ensure_ascii=False))
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    failure_log.record("rag", data.name, "+".join(configs), failed, rag_failures.summarize(failed), out, commit=commit())
    (out / "report.md").write_text(markdown(data, report["configs"]), encoding="utf-8")
    print("report:", out / "report.md")


def markdown(data, configs, title=None):
    """설정별 요약 표. 답변 설정은 전체 대상 기준 집계 표를 따로 적는다(검색만 실행한 설정은 그 표에 없다)."""
    lines = [f"# {title or data.name} ({'사람 검토 전 초안' if not data.reviewed_by_human else '검토됨'})", "",
             "이 세트는 개발용이거나 개선 방향 결정에 쓴 세트다. 아래 수치는 독립된 최종 성능이 아니다.", "",
             "답변은 기대·금지 문자열로 채점한다. 여러 문서 비교는 답변이 선택한 근거가 각 필수 원본 위치를 포함해야 정답이다.", "",
             "| 설정 | 모드 | 문항 | 실행 오류 | hit@1 | hit@5 | MRR | 근거 하나 포함 | 문자열 일치(완료 중) | 보류 정확 | 모호 감지 | 평균 초 | 전체 비용 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for config, block in configs.items():
        s = block["summary"]
        cell = lambda k: "" if s[k]["rate"] is None else f"{s[k]['rate']:.2f} ({s[k]['n']})"  # noqa: E731
        lines.append(f"| {config} | {s['mode']} | {s['items']} | {s['run_errors']} | {cell('hit@1')} | {cell('hit@5')} | "
                     f"{cell('mrr')} | {cell('evidence_recall')} | "
                     f"{cell('answer_string_match')} | {cell('abstain_correct')} | {cell('ambiguity_flagged')} | "
                     f"{s['mean_seconds']} | {s['total_cost_usd']} |")
    lines += ["", "| 설정 | 필수 근거 충족률 | 필수 근거 전체 충족 | P95 초 | 시간 누락 | 비용 누락 | 관측 비용 USD |",
              "|---|---|---|---|---|---|---|"]
    for config, block in configs.items():
        s = block["summary"]
        lines.append(f"| {config} | {s['required_evidence_coverage']} | {s['all_required_evidence']} | "
                     f"{s['latency_seconds']['p95']} | {len(s['timing_missing_items'])} | "
                     f"{len(s['cost_missing_items'])} | {s['observed_cost_usd']} |")
    answered = {c: b["summary"]["answer_totals"] for c, b in configs.items() if b["summary"].get("answer_totals")}
    if answered:
        lines += ["", "## 답변 집계 (비교 문항은 선택 근거까지 검사하며 실행 오류도 분모에 포함)", "",
                  "| 설정 | 전체 대상 | 정상 완료 | 실행 오류 | 보류 | 답할 문항 | 정답 | 불필요한 보류 | 오답 | 답할 문항의 오류 | "
                  "정답률(전체 대상) | 정답률(완료 중) | 기대 결과 충족(전체) |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for config, t in answered.items():
            a, e = t["answerable"], t["expected_outcome"]
            lines.append(f"| {config} | {t['total']} | {t['completed']} | {t['run_errors']} | {t['abstained']} | {a['total']} | "
                         f"{a['correct']} | {a['abstained']} | {a['wrong']} | {a['run_errors']} | "
                         f"{a['correct']}/{a['total']} = {a['rate_of_total']} | "
                         f"{a['correct']}/{a['completed']} = {a['rate_of_completed']} | "
                         f"{e['met']}/{e['total']} = {e['rate_of_total']} |")
        lines += ["", "기대 결과 충족은 정답, 타당한 보류, 또는 모호한 문항에서 필요한 조건을 되묻거나 원문에 근거한 조건부 답을 낸 문항 수다."]
    return "\n".join(lines) + "\n"


def rescore(data, folder):
    """저장 당시 문항·정답 ID로 재채점한다. 스냅샷이 없는 옛 실행은 명시적으로 거부한다.

    실행 폴더는 읽기만 한다. 다시 채점한 보고서(report.json, report.md)와 실패 기록은 새 폴더에 쓴다.
    그 설정에서 실행하지 않은 문항(--only로 일부만 실행)은 대상에 넣지 않고, 실행했는데 실패한 문항은 넣는다."""
    snapshot = read_snapshot(folder, data)
    items = [i for i in data.items if i.id in snapshot["item_ids"]]
    failed, configs = [], {}
    for config in snapshot["provenance"]["configs"]:
        config_dir = Path(folder) / config
        runs = {}
        for item in items:
            path = config_dir / f"{item.id}.json"
            if path.is_file():
                runs[item.id] = json.loads(path.read_text(encoding="utf-8"))
            else:
                runs[item.id] = dict(error="MISSING_RESULT", hits=[], evidence=[])
        mode = mode_of(config_dir.name, runs.values())
        rows = []
        for item in items:
            run = runs[item.id]
            rows.append(dict(score_item(item, snapshot["resolved"][item.id], run, mode=mode),
                             error=run.get("error")))
            failure = rag_failures.item_failure(item, rows[-1], run, config_dir.name, data.name)
            if failure:
                failed.append(failure)
        s = summarize(rows, mode)
        configs[config_dir.name] = dict(summary=s, items=rows)
        print(config_dir.name, mode, {k: s[k]["rate"] for k in ("hit@5", "evidence_recall", "evidence@1", "evidence@3",
                                                                "evidence_mrr", "answer_correct")},
              "run_errors", s["run_errors"], "answer_totals", json.dumps(s["answer_totals"], ensure_ascii=False))
    out = Path("work/failures") / f"rescore-{Path(folder).name}-{datetime.now():%Y%m%d-%H%M%S-%f}"
    out.mkdir(parents=True)
    report = dict(eval_set=data.name, reviewed_by_human=data.reviewed_by_human, rescored_from=str(folder),
                  rescored_at=datetime.now().isoformat(timespec="seconds"),
                  index_version=snapshot["provenance"]["index_version"], metric_version=METRIC_VERSION,
                  reference_sha256=digest(snapshot), items=len(items), configs=configs)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.md").write_text(markdown(data, configs, f"{data.name}: {Path(folder).name} 다시 채점"),
                                   encoding="utf-8")
    failure_log.record("rag", data.name, "rescore:" + Path(folder).name, failed, rag_failures.summarize(failed),
                       out, commit=commit())
    print("report:", out / "report.md")
    return out


if __name__ == "__main__":
    main()
