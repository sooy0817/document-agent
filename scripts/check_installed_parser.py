"""설치된 wheel의 nh-doc 명령을 새 작업 폴더에서 합성 PDF로 검사한다."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sysconfig
from tempfile import TemporaryDirectory

import pymupdf

import doc_understanding


def main():
    library = Path(sysconfig.get_path("purelib")).resolve()
    if not Path(doc_understanding.__file__).resolve().is_relative_to(library):
        raise SystemExit("Install the project wheel in a fresh virtual environment first.")
    cli = Path(sysconfig.get_path("scripts")) / ("nh-doc.exe" if os.name == "nt" else "nh-doc")
    with TemporaryDirectory() as directory:
        root = Path(directory)
        pdf = root / "sample.pdf"
        phrase = "Installed wheel evidence check"
        with pymupdf.open() as document:
            document.new_page().insert_text((60, 70), phrase)
            document.save(pdf)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        subprocess.run([str(cli), "extract-ir", "--pdf", pdf.name, "--output-dir", "runs"],
                       cwd=root, env=env, check=True, timeout=120)
        artifact, = (root / "runs").glob("*/document-ir.json")
        ir = json.loads(artifact.read_text(encoding="utf-8"))
        page, = ir["pages"]
        if page["text_status"] != "native" or page["page_number"] != 1:
            raise SystemExit("The installed CLI did not extract the expected native PDF page.")
        if not any(span["text_raw"] == phrase for block in page["blocks"] for span in block["spans"]):
            raise SystemExit("The source text is missing from the extracted spans.")
        digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        if ir["source_sha256"] != digest:
            raise SystemExit("The extracted document does not identify the input PDF.")
    print("Installed nh-doc extracted one page with the expected source text and document hash.")


if __name__ == "__main__":
    main()
