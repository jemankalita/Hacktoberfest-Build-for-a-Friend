"""HandNotes web server: a local API around the handnotes package plus the static UI.

Run with:  python server.py   then open http://127.0.0.1:8000
Binds to 127.0.0.1 only, so nothing is reachable from outside this laptop.
"""

import json
import re
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from handnotes import ocr
from handnotes.exporters import to_docx, to_pdf
from handnotes.memory import (
    add_abbreviation,
    apply_known_fixes,
    build_prompt_hints,
    diff_words,
    expand_abbreviations,
    learn,
    load_memory,
    save_memory,
    word_accuracy,
)
from handnotes.ocr import OllamaError

ROOT = Path(__file__).parent
STATIC_DIR = ROOT / "static"
MEMORY_PATH = ROOT / "memory.json"
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
TOP_MISREADS_SHOWN = 12
HOST, PORT = "127.0.0.1", 8000

app = FastAPI(title="HandNotes")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
_memory_lock = threading.Lock()


class LearnRequest(BaseModel):
    model_text: str = Field(max_length=MAX_TEXT_CHARS)
    corrected: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class AbbreviationRequest(BaseModel):
    short: str = Field(min_length=1, max_length=20)
    expansion: str = Field(min_length=1, max_length=120)


class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class ExportRequest(BaseModel):
    title: str = Field(default="notes", max_length=120)
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    summary: str = Field(default="", max_length=MAX_TEXT_CHARS)


def _memory_view(memory: dict) -> dict:
    misreads = sorted(memory["corrections"].values(), key=lambda e: e["count"], reverse=True)
    return {
        "history": memory["history"],
        "vocabulary_count": len(memory["vocabulary"]),
        "top_misreads": misreads[:TOP_MISREADS_SHOWN],
        "abbreviations": memory.get("abbreviations", {}),
        "samples": memory.get("samples"),
        "personal_model": memory.get("personal_model"),
    }


def _load_memory_or_500() -> dict:
    try:
        return load_memory(MEMORY_PATH)
    except ValueError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


def _event(**fields) -> str:
    return json.dumps(fields, ensure_ascii=False) + "\n"


def _transcribe_events(image_bytes: bytes, memory: dict, use_memory: bool) -> Iterator[str]:
    hints = build_prompt_hints(memory) if use_memory else ""
    parts = []
    try:
        for token in ocr.transcribe_stream(image_bytes, hints):
            parts.append(token)
            yield _event(token=token)
    except OllamaError as error:
        yield _event(error=str(error))
        return
    raw = "".join(parts).strip()
    if not use_memory:
        yield _event(done=True, text=raw, auto_fixes=[], expanded=[])
        return
    fixed = apply_known_fixes(raw, memory)
    text, expanded = expand_abbreviations(fixed, memory)
    yield _event(done=True, text=text, auto_fixes=diff_words(raw, fixed), expanded=expanded)


@app.get("/", include_in_schema=False)
def landing() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/app", include_in_schema=False)
def workspace() -> FileResponse:
    return FileResponse(STATIC_DIR / "app.html")


@app.get("/api/status")
def api_status() -> dict:
    return ocr.status()


@app.get("/api/memory")
def api_memory() -> dict:
    return _memory_view(_load_memory_or_500())


@app.post("/api/transcribe")
def api_transcribe(image: UploadFile = File(...), use_memory: bool = Form(True)):
    if not (image.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Please upload an image (JPG, PNG or WebP).")
    image_bytes = image.file.read(MAX_IMAGE_BYTES + 1)
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image is larger than 15 MB.")
    if not image_bytes:
        raise HTTPException(status_code=400, detail="The image is empty.")
    memory = _load_memory_or_500()
    return StreamingResponse(
        _transcribe_events(image_bytes, memory, use_memory), media_type="application/x-ndjson"
    )


@app.post("/api/learn")
def api_learn(request: LearnRequest) -> dict:
    with _memory_lock:
        memory = _load_memory_or_500()
        timestamp = datetime.now().isoformat(timespec="seconds")
        updated = learn(memory, request.model_text, request.corrected, timestamp)
        save_memory(MEMORY_PATH, updated)
    return {
        "accuracy": word_accuracy(request.model_text, request.corrected),
        "pairs": diff_words(request.model_text, request.corrected),
        "memory": _memory_view(updated),
    }


@app.post("/api/abbreviations")
def api_add_abbreviation(request: AbbreviationRequest) -> dict:
    with _memory_lock:
        memory = _load_memory_or_500()
        try:
            updated = add_abbreviation(memory, request.short, request.expansion)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        save_memory(MEMORY_PATH, updated)
    return _memory_view(updated)


@app.post("/api/summarize")
def api_summarize(request: TextRequest) -> dict:
    try:
        return {"summary": ocr.summarize(request.text)}
    except OllamaError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/api/flashcards")
def api_flashcards(request: TextRequest) -> dict:
    try:
        return {"cards": ocr.flashcards(request.text)}
    except OllamaError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def _safe_filename(title: str) -> str:
    cleaned = re.sub(r"[^\w\- ]", "", title).strip()[:60]
    return cleaned or "notes"


@app.post("/api/export/{fmt}")
def api_export(fmt: str, request: ExportRequest) -> Response:
    name = _safe_filename(request.title)
    if fmt == "pdf":
        content, media = to_pdf(name, request.text, request.summary), "application/pdf"
    elif fmt == "docx":
        content = to_docx(name, request.text, request.summary)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif fmt == "txt":
        body = request.text + (f"\n\nSummary\n\n{request.summary}" if request.summary else "")
        content, media = body.encode("utf-8"), "text/plain; charset=utf-8"
    else:
        raise HTTPException(status_code=404, detail="Unknown format. Use pdf, docx or txt.")
    headers = {"Content-Disposition": f'attachment; filename="{name}.{fmt}"'}
    return Response(content=content, media_type=media, headers=headers)


if __name__ == "__main__":
    import uvicorn

    print(f"HandNotes running at http://{HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)
