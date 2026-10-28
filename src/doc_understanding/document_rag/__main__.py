"""Inspectable CLI: build, ingest, search, ask. All writes are local."""
import argparse
import json
from pathlib import Path
from time import perf_counter

from .document import build_from_run, validate_document


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
    for name in ('search', 'ask'):
        p = sub.add_parser(name)
        p.add_argument('--question', required=True)
        p.add_argument('--run-id')
        p.add_argument('--output', type=Path, required=True)
        if name == 'ask':
            p.add_argument('--allow-external', action='store_true')
            p.add_argument('--model', default='gpt-4.1')
    args = parser.parse_args()
    if args.command == 'build':
        start = perf_counter()
        nested_path = args.output.with_suffix('.nested-content.json')
        if args.output.exists() or nested_path.exists():
            raise FileExistsError('Build outputs must be new paths')
        doc = build_from_run(args.run)
        from .nested_content import build_nested_content
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
        from .nested_content import build_nested_content, validate_links
        doc = CanonicalDocument.model_validate_json(args.document.read_text(encoding='utf8'))
        links = build_nested_content(doc)
        checks = validate_links(doc, links)
        dump(args.output, links.model_dump(mode='json'))
        print(json.dumps(dict(output=str(args.output), checks=checks)))
        return
    from . import repository
    if args.command == 'ingest':
        result = repository.store(args.manifest, args.document)
        dump(args.output, result)
        print(json.dumps(result))
        return
    hits = repository.search(args.question, args.run_id)
    if args.command == 'search':
        dump(args.output, hits)
        print(json.dumps(dict(hits=len(hits), output=str(args.output))))
        return
    from .service import resolve_evidence, generate_answer
    evidence = resolve_evidence(hits, args.question)
    if not evidence:
        dump(args.output / 'answer.json', dict(question=args.question, answer='검색된 근거가 없습니다.',
                                              abstained=True, model_calls=0, facts_published=0))
        print('NO_EVIDENCE')
        return
    result = generate_answer(args.question, evidence, args.output, model=args.model, allow_external=args.allow_external)
    dump(args.output / 'retrieval.json', hits)
    print(json.dumps(dict(answer=result['answer'], usage=result['usage'], estimated_cost_usd=result['estimated_cost_usd']), ensure_ascii=False))


if __name__ == '__main__':
    main()
