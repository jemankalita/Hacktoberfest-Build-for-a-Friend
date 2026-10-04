from handnotes.memory import (
    apply_known_fixes,
    build_prompt_hints,
    diff_words,
    empty_memory,
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


def test_build_prompt_hints_lists_vocabulary_and_misreads():
    memory = learn(empty_memory(), "The Peripheral Student", "The Perennial Student", NOW)
    hints = build_prompt_hints(memory)
    assert "Perennial" in hints
    assert '"Peripheral" → "Perennial"' in hints
