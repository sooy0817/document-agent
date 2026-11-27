# 역할: 문서 조립·적재·검색·질의 CLI를 제공한다. 모델 호출은 명시적 허용이 필요하다.
"""Inspectable CLI: build, ingest, search, ask. All writes are local."""
import argparse
import json
from pathlib import Path
from time import perf_counter

from doc_understanding.parser.assembly.document import build_from_run, validate_document


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('build')
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('link-nested')
    p.add_argument('--document', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('ingest')
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--document', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    sub.add_parser('embedding-migrate')
    p = sub.add_parser('embed')
    p.add_argument('--run-id', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--allow-external', action='store_true')
    from doc_understanding.rag.embedding.options import add_arguments as embedding_arguments
    embedding_arguments(p)
    for name in ('search', 'ask'):
        p = sub.add_parser(name)
        p.add_argument('--question', required=True)
        p.add_argument('--run-id')
        p.add_argument('--output', type=Path, required=True)
        p.add_argument('--limit', type=int, default=8)
        p.add_argument('--retrieval-mode', choices=['lexical', 'dense', 'hybrid'], default='lexical')
        p.add_argument('--allow-external', action='store_true')
        embedding_arguments(p)
        if name == 'ask':
            from doc_understanding.rag.generation.model_policy import add_arguments
            add_arguments(p)
            p.add_argument('--required-source', action='append', default=[])
            p.add_argument('--max-documents', type=int, default=3)
            p.add_argument('--max-cells-per-document', type=int, default=8)
            p.add_argument('--answer-mode', choices=['selection','narrative'], default='selection')
            p.add_argument('--planner', action='store_true',
                           help='질문 이해 에이전트(ADR-079): 계획 모델 1회 추가, 구조 일치·1회 재검색')
            p.add_argument('--langfuse-config', type=Path,
                           help='에이전트 실행과 모든 모델 호출을 이 Langfuse 설정으로 기록')
    args = parser.parse_args()
    if args.command == 'embedding-migrate':
        from doc_understanding.rag.embedding.repository import migrate
        migrate()
        print('EMBEDDING_SCHEMA_READY')
        return
    if args.command == 'embed':
        from doc_understanding.rag.embedding.commands import run_embed
        run_embed(args)
        return
    if args.command == 'build':
        start = perf_counter()
        nested_path = args.output.with_suffix('.nested-content.json')
        if args.output.exists() or nested_path.exists():
            raise FileExistsError('Build outputs must be new paths')
        doc = build_from_run(args.run)
        from doc_understanding.parser.assembly.nested_content import build_nested_content
        links = build_nested_content(doc)
        dump(args.output, doc.model_dump(mode='json'))
        dump(nested_path, links.model_dump(mode='json'))
        print(json.dumps(dict(output=str(args.output), contents=len(doc.contents),
                              sections=len(doc.sections), tables=len(doc.table_structure),
                              nested_content=str(nested_path), nested_candidates=len(links.candidates),
                              checks=validate_document(doc), seconds=perf_counter()-start)))
        return
    if args.command == 'link-nested':
        from doc_understanding.domain.pipeline_contracts import CanonicalDocument
        from doc_understanding.parser.assembly.nested_content import build_nested_content, validate_links
        doc = CanonicalDocument.model_validate_json(args.document.read_text(encoding='utf8'))
        links = build_nested_content(doc)
        checks = validate_links(doc, links)
        dump(args.output, links.model_dump(mode='json'))
        print(json.dumps(dict(output=str(args.output), checks=checks)))
        return
    from doc_understanding.rag import repository
    if args.command == 'ingest':
        result = repository.store(args.manifest, args.document)
        dump(args.output, result)
        print(json.dumps(result))
        return
    from doc_understanding.rag.embedding.options import retriever_from_args
    retriever = retriever_from_args(args)
    audit_path = (args.output.with_suffix('.retrieval-audit.json') if args.command == 'search'
                  else args.output / 'retrieval-audit.json')
    if args.output.exists() or (retriever and audit_path.exists()):
        raise ValueError('OUTPUT_ALREADY_EXISTS')
    if args.command == 'ask' and args.planner:
        from doc_understanding.rag.agentic.run import answer as agent_answer
        from doc_understanding.rag.generation.model_policy import options, rates
        from doc_understanding.runtime.structured_call import CallPolicy
        opts = options(args)
        prices = rates(opts['model'], opts['input_usd_per_million'], opts['output_usd_per_million'])
        policy = CallPolicy(model=opts['model'], input_usd_per_million=prices[0],
                            output_usd_per_million=prices[1], max_cost_usd=opts['max_cost_usd'])
        trace = None
        if args.langfuse_config:
            from doc_understanding.runtime.telemetry import Trace, load_config
            trace = Trace(load_config(args.langfuse_config))
        try:
            report = agent_answer(args.question, args.output, trace=trace, run_id=args.run_id, retriever=retriever,
                                  plan_policy=policy, allow_external=args.allow_external,
                                  required_sources=args.required_source, answer_mode=args.answer_mode, **opts)
        finally:
            if retriever and args.output.exists():
                dump(audit_path, retriever.report())
        print(json.dumps(dict(answer=report['result'].get('answer'), retried=report['retried'],
                              plan_fallback=report['plan_check'].get('fallback'),
                              trace_id=report.get('trace_id')), ensure_ascii=False))
        return
    try:
        hits = (retriever or repository.search)(args.question, args.run_id, args.limit)
    except Exception:
        if retriever:
            dump(audit_path, retriever.report())
        raise
    if args.command == 'search':
        dump(args.output, hits)
        if retriever:
            dump(audit_path, retriever.report())
        print(json.dumps(dict(hits=len(hits), output=str(args.output))))
        return
    from doc_understanding.rag.service import resolve_evidence, generate_answer
    try:
        evidence = resolve_evidence(hits, args.question, max_documents=args.max_documents,
                                    max_cells_per_document=args.max_cells_per_document)
    except Exception:
        if retriever:
            dump(audit_path, retriever.report())
        raise
    if not evidence:
        dump(args.output / 'answer.json', dict(question=args.question, answer='검색된 근거가 없습니다.',
                                              abstained=True, model_calls=0, facts_published=0))
        print('NO_EVIDENCE')
        if retriever:
            dump(audit_path, retriever.report())
        return
    from doc_understanding.rag.generation.model_policy import options
    try:
        result = generate_answer(args.question, evidence, args.output, **options(args),
                                 required_sources=args.required_source,
                                 allow_external=args.allow_external, answer_mode=args.answer_mode,verify_sql=True)
    finally:
        if retriever:
            dump(audit_path, retriever.report())
    if not (args.output/'answer.json').exists():dump(args.output/'answer.json',result)
    dump(args.output / 'retrieval.json', hits)
    print(json.dumps(dict(answer=result['answer'], usage=result.get('usage'), estimated_cost_usd=result.get('estimated_cost_usd',0)), ensure_ascii=False))


if __name__ == '__main__':
    main()
