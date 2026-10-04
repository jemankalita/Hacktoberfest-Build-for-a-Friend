"""Talks to a local Ollama server. Nothing leaves the machine."""

import base64
import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator

# 127.0.0.1 rather than localhost: on Windows, localhost tries IPv6 first and stalls ~2s.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("HANDNOTES_MODEL", "gemma3:4b")
REQUEST_TIMEOUT_SECONDS = 600
STATUS_TIMEOUT_SECONDS = 3
MAX_FLASHCARDS = 8

TRANSCRIBE_PROMPT = (
    "This is a photo of handwritten class notes. Transcribe the handwriting exactly "
    "as written, line by line, keeping the original line breaks.\n"
    "Rules:\n"
    "- Copy every line, including headings, dates, numbers and names. Skip nothing.\n"
    "- Copy literally. Do not fix grammar and do not change pronouns, names or "
    "numbers to what seems more likely.\n"
    "- Leave out words that are crossed out.\n"
    "- If a word is unclear, give your best guess followed by [?].\n"
    "Output only the transcription."
)

SUMMARY_PROMPT = (
    "Below are a student's class notes. Help them revise quickly:\n"
    "1. A short summary of the key points as bullet points.\n"
    "2. Three questions they could be asked about this in an exam.\n"
    "Use only what is in the notes.\n\nNotes:\n"
)

FLASHCARD_PROMPT = (
    "Make up to {count} revision flashcards from the class notes below. "
    "Each card has a short question and a one or two sentence answer, using only "
    'facts from the notes. Reply as JSON: {{"cards": [{{"question": "...", '
    '"answer": "..."}}]}}\n\nNotes:\n'
)


class OllamaError(RuntimeError):
    """The local model could not be reached or returned an error."""


def _request(path: str, body: dict | None = None, timeout: float = REQUEST_TIMEOUT_SECONDS):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        OLLAMA_BASE_URL + path, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise OllamaError(f"Ollama returned {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise OllamaError(
            "Could not reach Ollama at localhost:11434. Is the Ollama app running?"
        ) from error


def _generate_stream(
    prompt: str, model: str, images: list[str] | None = None, json_format: bool = False
) -> Iterator[str]:
    body = {"model": model, "prompt": prompt, "stream": True, "options": {"temperature": 0}}
    if images:
        body["images"] = images
    if json_format:
        body["format"] = "json"
    with _request("/api/generate", body) as response:
        for line in response:
            if not line.strip():
                continue
            chunk = json.loads(line)
            if "error" in chunk:
                raise OllamaError(chunk["error"])
            if chunk.get("response"):
                yield chunk["response"]
            if chunk.get("done"):
                return


def _generate(prompt: str, model: str, **options) -> str:
    return "".join(_generate_stream(prompt, model, **options)).strip()


def _transcribe_prompt(hints: str) -> str:
    return f"{TRANSCRIBE_PROMPT}\n\n{hints}" if hints else TRANSCRIBE_PROMPT


def transcribe_stream(
    image_bytes: bytes, hints: str = "", model: str = DEFAULT_MODEL
) -> Iterator[str]:
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    return _generate_stream(_transcribe_prompt(hints), model, images=[image_b64])


def transcribe(image_bytes: bytes, hints: str = "", model: str = DEFAULT_MODEL) -> str:
    return "".join(transcribe_stream(image_bytes, hints, model)).strip()


def summarize(text: str, model: str = DEFAULT_MODEL) -> str:
    return _generate(SUMMARY_PROMPT + text, model)


def parse_flashcards(raw: str) -> list[dict]:
    """Validate the model's JSON reply; keep only well-formed cards."""
    try:
        cards = json.loads(raw).get("cards", [])
    except (json.JSONDecodeError, AttributeError) as error:
        raise OllamaError("The model did not return valid flashcards. Try again.") from error
    if not isinstance(cards, list):
        return []
    return [
        {"question": card["question"].strip(), "answer": card["answer"].strip()}
        for card in cards[:MAX_FLASHCARDS]
        if isinstance(card, dict)
        and isinstance(card.get("question"), str)
        and isinstance(card.get("answer"), str)
        and card["question"].strip()
    ]


def flashcards(text: str, model: str = DEFAULT_MODEL) -> list[dict]:
    prompt = FLASHCARD_PROMPT.format(count=MAX_FLASHCARDS) + text
    return parse_flashcards(_generate(prompt, model, json_format=True))


def status(model: str = DEFAULT_MODEL) -> dict:
    """Is Ollama up, is the model installed, and how much of it sits on the GPU."""
    try:
        with _request("/api/tags", timeout=STATUS_TIMEOUT_SECONDS) as response:
            installed = [m["name"] for m in json.loads(response.read()).get("models", [])]
        with _request("/api/ps", timeout=STATUS_TIMEOUT_SECONDS) as response:
            running = json.loads(response.read()).get("models", [])
    except OllamaError as error:
        return {"ok": False, "model": model, "message": str(error)}

    loaded = next((m for m in running if m.get("name") == model), None)
    gpu_share = None
    if loaded and loaded.get("size"):
        gpu_share = round(100 * loaded.get("size_vram", 0) / loaded["size"])
    return {
        "ok": model in installed,
        "model": model,
        "installed": model in installed,
        "loaded": loaded is not None,
        "gpu_percent": gpu_share,
        "message": "" if model in installed else f"Run: ollama pull {model}",
    }
