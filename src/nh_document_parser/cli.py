"""단일 PDF를 읽고 검증된 문서 JSON을 새 파일에 쓴다."""
import argparse
import json
from pathlib import Path

from nh_document_parser import parse_pdf, render_text


def main():
    parser = argparse.ArgumentParser(description="Parse a PDF into a tabledoc/0.5 JSON document.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True, help="New JSON file")
    parser.add_argument("--evidence-dir", type=Path, help="Keep the source copy and parser artifacts")
    parser.add_argument("--text-output", type=Path, help="Also save readable UTF-8 text to a new file")
    args = parser.parse_args()
    paths = [args.output]
    if args.text_output:
        paths.append(args.text_output)
        if args.output.resolve() == args.text_output.resolve():
            parser.error("JSON and text output require different paths")
    for path in paths:
        if path.exists():
            parser.error(f"output already exists: {path}")
    result = parse_pdf(args.pdf, evidence_dir=args.evidence_dir)
    texts = [json.dumps(result, ensure_ascii=False, indent=2) + "\n"]
    if args.text_output:
        texts.append(render_text(result))
    created = []
    try:
        for path, text in zip(paths, texts, strict=True):
            with path.open("x", encoding="utf-8") as output:
                created.append(path)
                output.write(text)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
