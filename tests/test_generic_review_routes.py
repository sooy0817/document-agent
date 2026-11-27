"""Generic review must start without retired financial experiment modules."""
import asyncio

import httpx

from doc_understanding.review.human.app import create_app


def test_generic_review_registration_and_templates(tmp_path):
    cases = tmp_path / "cases"
    cases.mkdir()
    app = create_app(cases=cases, output=tmp_path / "events")
    routes = {route.path: route for route in app.routes}
    for path in ("/", "/advanced", "/meaning"):
        assert "<html" in routes[path].endpoint().lower()
    assert routes["/api/cases"].endpoint() == []
    assert not any(path.startswith(("/metrics", "/api/metric-pages")) for path in routes)


def test_generic_review_serves_pages_without_financial_routes(tmp_path):
    cases = tmp_path / "cases"
    cases.mkdir()
    app = create_app(cases=cases, output=tmp_path / "events")

    async def check():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            for path in ("/", "/advanced", "/meaning"):
                response = await client.get(path)
                assert response.status_code == 200
                assert "text/html" in response.headers["content-type"]
            assert (await client.get("/api/cases")).json() == []
            for path in ("/metrics", "/api/metric-pages/D1/1",
                         "/metrics-files/evaluation.json"):
                assert (await client.get(path)).status_code == 404

    asyncio.run(check())
