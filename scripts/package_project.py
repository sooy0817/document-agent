"""Package source/design/test evidence; exclude environments, private PDFs and secrets."""
import hashlib
import json
import zipfile
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".git", "data", "dist", "build"}


def included(path):
    rel = path.relative_to(ROOT)
    return (not any(p in EXCLUDED or p.endswith(".egg-info") for p in rel.parts)
            and path.name != ".env" and not path.name.endswith(".pyc")
            and "private" not in rel.parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name",default="NH_Document_Agent_Phase01.zip")
    args = parser.parse_args()
    if Path(args.name).name != args.name or not args.name.endswith(".zip"):
        raise ValueError("archive name must be a ZIP basename")
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    source_zip = ROOT.parent / "nh-document-agent-design-v2.zip"
    integrity = []
    with zipfile.ZipFile(source_zip) as z:
        for item in z.infolist():
            if item.is_dir():
                continue
            relative = Path(item.filename).relative_to("nh-document-agent")
            original, current = z.read(item), (ROOT / relative).read_bytes()
            integrity.append({"path": relative.as_posix(),
                              "original_sha256": hashlib.sha256(original).hexdigest(),
                              "current_sha256": hashlib.sha256(current).hexdigest(),
                              "byte_identical": current == original,
                              "intentional_change": relative.as_posix() == "README.md"})
    (reports / "design-integrity.json").write_text(json.dumps(integrity, indent=2), encoding="utf-8")
    # Include the inventory/report files in their own listings.
    for name in ["PROJECT_TREE.txt", "IMPLEMENTATION_FILES.json"]:
        (reports / name).touch(exist_ok=True)
    paths = sorted((p for p in ROOT.rglob("*") if included(p)), key=lambda p: p.as_posix())
    tree = ["nh-document-agent/ (runtime environments, caches, data and secrets excluded)"]
    tree.extend("  " * len(p.relative_to(ROOT).parts) + p.name + ("/" if p.is_dir() else "") for p in paths)
    (reports / "PROJECT_TREE.txt").write_text("\n".join(tree) + "\n", encoding="utf-8")
    files = [p.relative_to(ROOT).as_posix() for p in paths if p.is_file()]
    (reports / "IMPLEMENTATION_FILES.json").write_text(json.dumps(files, indent=2), encoding="utf-8")
    output = ROOT.parent / "outputs" / args.name
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        for relative in files:
            z.write(ROOT / relative, f"nh-document-agent/{relative}")
    print(json.dumps({"archive": str(output), "files": len(files),
                      "original_unchanged": sum(x["byte_identical"] for x in integrity),
                      "original_count": len(integrity)}, indent=2))


if __name__ == "__main__":
    main()
