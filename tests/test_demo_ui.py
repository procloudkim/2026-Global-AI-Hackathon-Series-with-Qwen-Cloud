from __future__ import annotations

from fastapi.testclient import TestClient

from librarian import main
from librarian.demo_ui import render_demo_home


def test_home_is_guided_dependency_free_and_provider_free(monkeypatch) -> None:
    def fail_if_called():
        raise AssertionError("GET / must not initialize or call a provider")

    monkeypatch.setattr(main, "get_router", fail_if_called)

    response = TestClient(main.app).get("/")

    assert response.status_code == 200
    html = response.text
    assert "Memory that can explain why it changed." in html
    assert "No Qwen call on page load" in html
    assert "judge-source-a" in html
    assert "judge-source-b" in html
    assert "crypto.randomUUID" in html
    assert "Unique namespace" in html
    assert "Explain memory" in html
    assert "/memory/explain?key=" in html
    assert 'id="valid_at"' in html
    assert 'id="known_at"' in html
    assert "Raw JSON receipt" in html
    assert "Calls Qwen" in html
    assert "Revision history" in html
    assert "Replacement proof" in html
    assert "Citations" in html
    assert "valid_at and known_at must be provided together" in html
    assert "<script src=" not in html
    assert "<link " not in html
    assert "url(http" not in html


def test_demo_html_is_stable_and_contains_no_hidden_provider_request() -> None:
    first = render_demo_home()
    second = render_demo_home()

    assert first == second
    assert 'fetch("/health/qwen"' not in first
    assert 'let url = "/memory/explain?key="' in first
    assert "initializeRun();" in first
    assert 'request("Ingest", "/ingest"' in first
    assert 'request("Query", "/query"' in first
    assert 'new RegExp("(^|\\\\D)" + expected + "(\\\\D|$)")' in first
