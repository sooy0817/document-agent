"""Read-only integration with the existing loopback review app."""
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response


def attach_routes(app, root):
    report=root/"reports/metric-mvp"

    def pages():
        manifest=root/"reports/staging-five/manifest.json"
        if not manifest.exists():
            raise HTTPException(404,"Metric review is not prepared")
        result={}
        for doc in json.loads(manifest.read_text(encoding="utf-8"))["documents"]:
            previous=json.loads((root/f"reports/staging-five/{doc['id']}/result.json").read_text(encoding="utf-8"))
            run=Path(previous["manifest"]["run_dir"])
            ir=json.loads((run/"document-ir.json").read_bytes())
            result[doc["id"]]=[(p["page_number"],run/p["image"]["artifact"]["path"],
                               p["image"]["artifact"]["sha256"]) for p in ir["pages"]]
        return result

    @app.get("/metrics",response_class=HTMLResponse)
    def metric_review():
        path=report/"index.html"
        if not path.exists():
            raise HTTPException(404,"Metric review is not prepared")
        text=path.read_text(encoding="utf-8")
        for doc,items in pages().items():
            for number,path,_ in items:
                text=text.replace(path.as_uri(),f"/api/metric-pages/{doc}/{number}")
        for name in ("sql-results.json","sql-final.json","evaluation.json","provisional-gold.json",
                     "mapping.schema.json","tests.txt"):
            text=text.replace(f'href="{name}"',f'href="/metrics-files/{name}"')
        return text

    @app.get("/api/metric-pages/{document}/{number}")
    def metric_page(document: str,number: int):
        match=next((x for x in pages().get(document,[]) if x[0]==number),None)
        if not match:
            raise HTTPException(404)
        _,path,sha=match
        if not path.resolve().is_relative_to(root.resolve()):
            raise HTTPException(403)
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=sha:
            raise HTTPException(409,"Source image hash mismatch")
        return Response(data,media_type="image/png")

    @app.get("/metrics-files/{name}")
    def metric_report_file(name: str):
        if name not in ("sql-results.json","sql-final.json","evaluation.json","provisional-gold.json",
                        "mapping.schema.json","tests.txt"):
            raise HTTPException(404)
        path=report/name
        if not path.is_file():
            raise HTTPException(404)
        return Response(path.read_bytes(),media_type="application/json" if name.endswith(".json") else "text/plain")
