from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response
import hashlib
import json
import pymupdf
from doc_understanding.human_review.store import GateInput
from doc_understanding.agent_contracts.models import StructureProposalBundle
from .store import DescriptionStore, DescriptionReview
from .numeric import numeric_checks
from .diagnostics import diagnose
from .guidance import supplement


def attach_routes(app, root, output=None):
    store = DescriptionStore(
        root / "data/semantic-understanding/cases",
        output or root / "data/semantic-understanding/reviews",
    )

    @app.get("/meaning", response_class=HTMLResponse)
    def screen():
        return Path(__file__).with_name("review.html").read_text(encoding="utf-8")

    @app.get("/api/meaning")
    def listing(include_mock: bool = False):
        # Isolated mock outputs, no import of old review events or approvals.
        store.paths.update({
            p.parent.name: p
            for p in (root / "data/agent-runner/cases").glob("mock-*/canonical.json")
        })
        return [
            dict(
                case_id=k,
                title=(c := store.case(k))["metadata"]["title"],
                pages=c["metadata"]["pages"],
                review_count=len(c["events"]),
                origin=c["descriptions"]["origin"],
            )
            for k in store.paths
            if include_mock or not k.startswith("mock-")
        ]

    @app.get("/api/meaning-review-pack")
    def review_pack():
        path = root / "reports/semantic-human-review/review-pack.json"
        pack = json.loads(path.read_text(encoding="utf-8"))
        pack["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        for item in pack["items"]:
            extra = supplement(root, item["case_id"], item["description_id"])
            if extra:
                item["supplement"] = extra
                item["questions"] = extra["questions"]
        return pack

    @app.get("/api/meaning-candidates")
    def candidates():
        return dict(items=store.candidates(), fact_published=False)

    @app.get("/api/meaning/{key}")
    def case(key):
        c = store.case(key)
        c["numeric_checks"] = numeric_checks(
            store._input(key).evidence,
            StructureProposalBundle.model_validate(c["current_proposal"]),
        )
        c["numeric_diagnostics"] = diagnose(
            store._input(key).evidence,
            StructureProposalBundle.model_validate(c["current_proposal"]),
        )
        return c

    @app.post("/api/meaning/{key}/review")
    def review(key: str, body: DescriptionReview):
        return store.review_description(key, body)

    @app.post("/api/meaning/{key}/gate")
    def gate(key: str, body: GateInput):
        return store.gate(key, body)

    @app.get("/api/meaning/{key}/source/{page}")
    def source(key: str, page: int):
        c = store._input(key)
        if page not in {i.page for i in c.evidence.items}:
            raise HTTPException(404)
        artifact = next(a for a in c.evidence.artifacts if a.kind == "pdf")
        p = Path(artifact.uri)
        if hashlib.sha256(p.read_bytes()).hexdigest() != artifact.sha256:
            raise HTTPException(409, "Source hash mismatch")
        with pymupdf.open(p) as doc:
            pg = doc[page - 1]
            image = pg.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5))
            return Response(
                image.tobytes("png"),
                media_type="image/png",
                headers={"X-Page-Width": str(pg.rect.width), "X-Page-Height": str(pg.rect.height)},
            )

    return store
