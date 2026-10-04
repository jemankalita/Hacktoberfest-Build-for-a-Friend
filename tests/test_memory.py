import pytest

from handnotes.memory import (
    add_abbreviation,
    apply_known_fixes,
    build_prompt_hints,
    diff_abbreviations,
    diff_words,
    empty_memory,
    expand_abbreviations,
    learn,
    word_accuracy,
)

NOW = "2026-10-05T03:30:00"


def test_diff_words_finds_single_word_misreads():
    pairs = diff_words("The Peripheral Student by Coles", "The Perennial Student by Coles")
    assert pairs == [("Peripheral", "Perennial")]


def test_diff_words_ignores_trailing_punctuation():
    pairs = diff_words("a spatialical story.", "a satirical story.")
    assert pairs == [("spatialical", "satirical")]


def test_diff_words_treats_curly_and_straight_apostrophes_as_equal():
    assert diff_words("other student’s work", "other student's work") == []
    assert word_accuracy("Denise’s ‘avant-garde’", "Denise's 'avant-garde'") == 1.0


def test_diff_words_returns_nothing_for_identical_text():
    assert diff_words("same words here", "same words here") == []


def test_word_accuracy_is_one_for_perfect_transcription():
    assert word_accuracy("hello world", "hello world") == 1.0


def test_word_accuracy_counts_missed_words():
    # model dropped two of the five correct words
    assert word_accuracy("footage from pm", "footage from 17 September pm") == 0.6


def test_word_accuracy_handles_empty_correction():
    assert word_accuracy("anything", "") == 0.0


def test_learn_records_corrections_vocabulary_and_history_without_mutating():
    original = empty_memory()
    updated = learn(original, "The Peripheral Student", "The Perennial Student", NOW)

    assert original == empty_memory()
    assert updated["corrections"]["peripheral→perennial"]["count"] == 1
    assert "Perennial" in updated["vocabulary"]
    assert updated["history"] == [
        {"timestamp": NOW, "accuracy": 2 / 3, "words": 3, "errors": 1}
    ]


def test_learn_increments_repeated_corrections():
    memory = learn(empty_memory(), "Mr Possam said", "Mr Possum said", NOW)
    memory = learn(memory, "Possam left", "Possum left", NOW)
    assert memory["corrections"]["possam→possum"]["count"] == 2
    assert memory["vocabulary"].count("Possum") == 1


def test_learn_skips_short_words_for_vocabulary():
    memory = learn(empty_memory(), "in his class", "in her class", NOW)
    assert memory["vocabulary"] == []


def test_apply_known_fixes_only_uses_confirmed_long_corrections():
    memory = learn(empty_memory(), "Possam", "Possum", NOW)
    assert apply_known_fixes("Possam again", memory) == "Possam again"

    memory = learn(memory, "Possam", "Possum", NOW)
    assert apply_known_fixes("Possam again, Possam.", memory) == "Possum again, Possum."


def test_apply_known_fixes_never_touches_short_words():
    memory = learn(empty_memory(), "his", "her", NOW)
    memory = learn(memory, "his", "her", NOW)
    assert apply_known_fixes("his class", memory) == "his class"


def test_build_prompt_hints_is_empty_for_new_memory():
    assert build_prompt_hints(empty_memory()) == ""


def test_diff_abbreviations_finds_single_and_multi_word_short_forms():
    pairs = diff_abbreviations("acc: to the text, as gitq", "according to the text, as given in the question")
    assert pairs == [("acc", "according"), ("gitq", "given in the question")]


def test_diff_abbreviations_ignores_ordinary_misreads():
    assert diff_abbreviations("in his class, Possam said", "in her class, Possum said") == []


def test_learn_records_short_forms_separately_from_misreads():
    memory = learn(empty_memory(), "acc to Possum", "according to Possum", NOW)
    assert memory["abbreviations"] == {"acc": "according"}
    assert memory["corrections"] == {}


def test_expand_abbreviations_replaces_whole_words_and_reports_them():
    memory = add_abbreviation(empty_memory(), "gitq", "given in the question")
    memory = add_abbreviation(memory, "acc", "according")
    text, expanded = expand_abbreviations("Acc: the author, as gitq. Accent stays.", memory)
    assert text == "according the author, as given in the question. Accent stays."
    assert expanded == [["gitq", "given in the question"], ["acc", "according"]]


def test_add_abbreviation_validates_and_does_not_mutate():
    original = empty_memory()
    updated = add_abbreviation(original, " Wrt ", "with respect to")
    assert original["abbreviations"] == {}
    assert updated["abbreviations"] == {"wrt": "with respect to"}
    with pytest.raises(ValueError):
        add_abbreviation(original, "two words", "x")


def test_build_prompt_hints_mentions_short_forms():
    memory = add_abbreviation(empty_memory(), "gitq", "given in the question")
    assert "gitq" in build_prompt_hints(memory)


def test_build_prompt_hints_lists_vocabulary_and_misreads():
    memory = learn(empty_memory(), "The Peripheral Student", "The Perennial Student", NOW)
    hints = build_prompt_hints(memory)
    assert "Perennial" in hints
    assert '"Peripheral" → "Perennial"' in hints
