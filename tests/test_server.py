import json

import pytest
from fastapi.testclient import TestClient

import server
from handnotes import ocr
from handnotes.ocr import OllamaError


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MEMORY_PATH", tmp_path / "memory.json")
    return TestClient(server.app)


def _events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_landing_and_workspace_pages_are_served(client):
    assert "HandNotes" in client.get("/").text
    assert "HandNotes" in client.get("/app").text


def test_transcribe_streams_tokens_then_final_text(client, monkeypatch):
    monkeypatch.setattr(ocr, "transcribe_stream", lambda image, hints: iter(["The ", "Perennial"]))
    response = client.post("/api/transcribe", files={"image": ("p.jpg", b"img", "image/jpeg")})
    events = _events(response)
    assert [e["token"] for e in events if "token" in e] == ["The ", "Perennial"]
    assert events[-1] == {"done": True, "text": "The Perennial", "auto_fixes": []}


def test_transcribe_applies_confirmed_fixes_and_reports_them(client, monkeypatch):
    for _ in range(2):
        client.post("/api/learn", json={"model_text": "Possam", "corrected": "Possum"})
    monkeypatch.setattr(ocr, "transcribe_stream", lambda image, hints: iter(["Mr Possam"]))
    response = client.post("/api/transcribe", files={"image": ("p.jpg", b"img", "image/jpeg")})
    final = _events(response)[-1]
    assert final["text"] == "Mr Possum"
    assert final["auto_fixes"] == [["Possam", "Possum"]]


def test_transcribe_reports_model_errors_in_the_stream(client, monkeypatch):
    def failing(image, hints):
        raise OllamaError("Ollama is not running")
        yield  # makes this a generator

    monkeypatch.setattr(ocr, "transcribe_stream", failing)
    response = client.post("/api/transcribe", files={"image": ("p.jpg", b"img", "image/jpeg")})
    assert _events(response) == [{"error": "Ollama is not running"}]


def test_transcribe_rejects_non_images(client):
    response = client.post("/api/transcribe", files={"image": ("a.txt", b"hi", "text/plain")})
    assert response.status_code == 400


def test_learn_returns_accuracy_pairs_and_updated_memory(client):
    response = client.post(
        "/api/learn", json={"model_text": "The Peripheral Student", "corrected": "The Perennial Student"}
    )
    body = response.json()
    assert body["accuracy"] == pytest.approx(2 / 3)
    assert body["pairs"] == [["Peripheral", "Perennial"]]
    assert len(body["memory"]["history"]) == 1
    assert client.get("/api/memory").json()["vocabulary_count"] == 1


def test_summarize_maps_model_errors_to_502(client, monkeypatch):
    def failing(text):
        raise OllamaError("down")

    monkeypatch.setattr(ocr, "summarize", failing)
    response = client.post("/api/summarize", json={"text": "notes"})
    assert response.status_code == 502
    assert response.json()["detail"] == "down"


def test_export_formats_and_safe_filename(client):
    payload = {"title": '../evil"name', "text": "hello", "summary": "short"}
    pdf = client.post("/api/export/pdf", json=payload)
    assert pdf.content.startswith(b"%PDF")
    assert 'filename="evilname.pdf"' in pdf.headers["content-disposition"]
    assert client.post("/api/export/docx", json=payload).status_code == 200
    assert "Summary" in client.post("/api/export/txt", json=payload).text
    assert client.post("/api/export/exe", json=payload).status_code == 404
