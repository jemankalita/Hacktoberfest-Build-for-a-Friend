import pytest

from handnotes.ocr import MAX_FLASHCARDS, OllamaError, parse_flashcards


def test_parse_flashcards_keeps_well_formed_cards():
    raw = '{"cards": [{"question": " Who? ", "answer": "Possum"}, {"question": "x"}, "junk"]}'
    assert parse_flashcards(raw) == [{"question": "Who?", "answer": "Possum"}]


def test_parse_flashcards_caps_the_number_of_cards():
    cards = ",".join('{"question": "q", "answer": "a"}' for _ in range(MAX_FLASHCARDS + 5))
    assert len(parse_flashcards(f'{{"cards": [{cards}]}}')) == MAX_FLASHCARDS


def test_parse_flashcards_rejects_invalid_json():
    with pytest.raises(OllamaError):
        parse_flashcards("not json")


def test_parse_flashcards_handles_wrong_shape():
    assert parse_flashcards('{"cards": "nope"}') == []
