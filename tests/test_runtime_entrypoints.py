"""User commands must not substitute replay/mock data for live work."""

import pytest

from doc_understanding.cli import main



def test_semantic_cli_cannot_select_mock_provider(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        main(['semantic-resolve', '--input-run', str(tmp_path), '--provider', 'mock'])
    assert error.value.code == 2
    assert 'invalid choice' in capsys.readouterr().err


def test_semantic_live_mode_requires_explicit_enable_before_input_reads(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        main(['semantic-resolve', '--input-run', str(tmp_path / 'missing'), '--mode', 'hybrid'])
    assert error.value.code == 2
    assert 'Live calls require' in capsys.readouterr().err


def test_meaning_list_does_not_load_retired_mock_cases(tmp_path):
    from fastapi import FastAPI
    from doc_understanding.review.semantic_description.routes import attach_routes

    # A malformed retired fixture must never be loaded by the real listing.
    old_case = tmp_path / 'data/agent-runner/cases/mock-old/canonical.json'
    old_case.parent.mkdir(parents=True)
    old_case.write_text('not a real document', encoding='utf8')
    app = FastAPI()
    attach_routes(app, tmp_path)
    listing = next(route.endpoint for route in app.routes if route.path == '/api/meaning')
    assert listing() == []
