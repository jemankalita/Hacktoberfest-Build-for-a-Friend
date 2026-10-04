"""Per-writer correction memory.

Instead of retraining the model, every correction the user makes is stored and
fed back on the next page: as hints in the prompt, and as automatic fixes for
misreads that have been confirmed more than once.
"""

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

# Words shorter than this (his/her, in/on) depend on context, so they are never
# learned as vocabulary or auto-fixed.
MIN_LEARNABLE_LENGTH = 4
AUTO_FIX_MIN_COUNT = 2
MAX_HINT_WORDS = 60
MAX_HINT_MISREADS = 30
_PUNCTUATION = ".,;:!?\"'()[]"
# Typographic quotes are not misreads: "student’s" and "student's" are the same word.
_QUOTE_TABLE = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def empty_memory() -> dict:
    return {"vocabulary": [], "corrections": {}, "history": []}


def _clean_words(text: str) -> list[str]:
    words = (raw.strip(_PUNCTUATION) for raw in text.translate(_QUOTE_TABLE).split())
    return [word for word in words if word]


def diff_words(model_text: str, corrected_text: str) -> list[tuple[str, str]]:
    """Word-for-word misreads (wrong, right) between the model output and the fix."""
    model = _clean_words(model_text)
    fixed = _clean_words(corrected_text)
    matcher = SequenceMatcher(a=model, b=fixed, autojunk=False)
    pairs = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace" or (i2 - i1) != (j2 - j1):
            continue
        pairs.extend(
            (wrong, right)
            for wrong, right in zip(model[i1:i2], fixed[j1:j2])
            if wrong.lower() != right.lower()
        )
    return pairs


def _matched_word_count(model_text: str, corrected_text: str) -> tuple[int, int]:
    model = [word.lower() for word in _clean_words(model_text)]
    fixed = [word.lower() for word in _clean_words(corrected_text)]
    matcher = SequenceMatcher(a=model, b=fixed, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks()), len(fixed)


def word_accuracy(model_text: str, corrected_text: str) -> float:
    """Share of the corrected words the model got right (missed words count as wrong)."""
    matched, total = _matched_word_count(model_text, corrected_text)
    return matched / total if total else 0.0


def learn(memory: dict, model_text: str, corrected_text: str, timestamp: str) -> dict:
    """Return a new memory that includes this page's corrections and accuracy."""
    corrections = {key: dict(entry) for key, entry in memory["corrections"].items()}
    vocabulary = list(memory["vocabulary"])

    for wrong, right in diff_words(model_text, corrected_text):
        key = f"{wrong.lower()}→{right.lower()}"
        previous = corrections.get(key, {"wrong": wrong, "right": right, "count": 0})
        corrections[key] = {**previous, "count": previous["count"] + 1}
        if len(right) >= MIN_LEARNABLE_LENGTH and right not in vocabulary:
            vocabulary.append(right)

    matched, total = _matched_word_count(model_text, corrected_text)
    session = {
        "timestamp": timestamp,
        "accuracy": matched / total if total else 0.0,
        "words": total,
        "errors": total - matched,
    }
    return {
        "vocabulary": vocabulary,
        "corrections": corrections,
        "history": [*memory["history"], session],
    }


def _learnable(entry: dict) -> bool:
    return len(entry["wrong"]) >= MIN_LEARNABLE_LENGTH


def apply_known_fixes(text: str, memory: dict) -> str:
    """Replace misreads the user has already corrected at least twice."""
    for entry in memory["corrections"].values():
        if entry["count"] >= AUTO_FIX_MIN_COUNT and _learnable(entry):
            pattern = rf"(?<!\w){re.escape(entry['wrong'])}(?!\w)"
            text = re.sub(pattern, entry["right"], text)
    return text


def build_prompt_hints(memory: dict) -> str:
    vocabulary = memory["vocabulary"][-MAX_HINT_WORDS:]
    misreads = sorted(
        (entry for entry in memory["corrections"].values() if _learnable(entry)),
        key=lambda entry: entry["count"],
        reverse=True,
    )[:MAX_HINT_MISREADS]
    if not vocabulary and not misreads:
        return ""

    lines = ["This writer's notes have been corrected before. Use what was learned:"]
    if vocabulary:
        lines.append("Words and names that appear in their notes: " + ", ".join(vocabulary))
    if misreads:
        pairs = "; ".join(f'"{entry["wrong"]}" → "{entry["right"]}"' for entry in misreads)
        lines.append("Past misreads to avoid (wrong → right): " + pairs)
    return "\n".join(lines)


def load_memory(path: Path) -> dict:
    if not path.exists():
        return empty_memory()
    try:
        return {**empty_memory(), **json.loads(path.read_text(encoding="utf-8"))}
    except json.JSONDecodeError as error:
        raise ValueError(f"{path.name} is corrupted ({error}); fix or delete it.") from error


def save_memory(path: Path, memory: dict) -> None:
    path.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
