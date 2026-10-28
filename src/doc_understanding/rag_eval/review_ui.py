"""Self-contained offline review UI; annotations never overwrite benchmark answers."""
import hashlib
import json
from pathlib import Path

from .review_guidance import review_guidance


def render_review(output: Path, document: dict) -> None:
    question_path = output / "questions.provisional.json"
    payload = {
        "guidance": review_guidance(),
        "questions": json.loads(question_path.read_text(encoding="utf-8")),
        "question_sha256": hashlib.sha256(question_path.read_bytes()).hexdigest(),
        "source_sha256": document["source_sha256"],
        "pages": {str(p["page_number"]): {"width": p["width"], "height": p["height"]}
                  for p in document["pages"] if p["page_number"] in (42, 43, 45, 46, 51)},
    }
    template = Path(__file__).with_name("review_template.html").read_text(encoding="utf-8")
    encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    output.joinpath("index.html").write_text(template.replace("/*REVIEW_DATA*/null", encoded), encoding="utf-8")
