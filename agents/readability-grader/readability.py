"""Readability scoring logic.

Standard library only. No knowledge of JSON, stdin/stdout, or the
agentcall/v1 protocol lives here — this module is pure functions that take
a string and return numbers. `agent_main.py` is the only thing that knows
about the wire format.

Formulas implemented:
  - Flesch Reading Ease  (0-100, higher = easier)
  - Flesch-Kincaid Grade Level (approximate US school grade)

Syllable counting is a vowel-group heuristic, not a dictionary lookup
(e.g. CMUdict). It is accurate on most everyday English words and wrong
on some irregular ones (e.g. "queue"). This is a deliberate, documented
trade-off — see README.md — that keeps this agent dependency-free.
"""

from __future__ import annotations

import re

_VOWEL_GROUPS = re.compile(r"[aeiouy]+", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+")
_ABBREVIATIONS = frozenset(
    {
        "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "rev",
        "vs", "etc", "approx", "eg", "ie",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
        "mon", "tue", "wed", "thu", "fri", "sat", "sun",
    }
)
_TRAILING_WORD_RE = re.compile(r"[A-Za-z]+$")

# Flesch Reading Ease -> plain verdict. Descending, first match wins.
# Standard 6-band table used by most readability tools.
_VERDICT_BANDS: tuple[tuple[float, str], ...] = (
    (90.0, "very easy"),
    (80.0, "easy"),
    (70.0, "fairly easy"),
    (60.0, "standard"),
    (50.0, "fairly difficult"),
    (30.0, "difficult"),
    (float("-inf"), "very confusing"),
)


def count_syllables(word: str) -> int:
    """Estimate syllables in a single word using a vowel-group heuristic.

    Rule: count groups of consecutive vowels (a, e, i, o, u, y), then
    subtract one if the word ends in a silent "e" (but not if that "e"
    is the word's only vowel group, e.g. "the"). Every word has at
    least one syllable.
    """
    word = word.strip().lower()
    if not word:
        return 0

    groups = _VOWEL_GROUPS.findall(word)
    count = len(groups)

    if word.endswith("e") and not word.endswith(("le", "ee", "ye")) and count > 1:
        count -= 1

    return max(count, 1)


def split_words(text: str) -> list[str]:
    """Extract words (letters only, with internal apostrophes allowed)."""
    return _WORD_RE.findall(text)


def split_sentences(text: str) -> list[str]:
    """Split text into sentences on ., !, ? — drops empty fragments.

    A punctuation run is only treated as a sentence boundary when:
      1. it is followed by whitespace + a capital letter, or by the end
         of the text (rules out decimal numbers like "2.5", where the
         next character is a digit); and
      2. the word immediately before it is not a single letter (rules
         out initials like "U.S.", "U.N.") and is not in the documented
         abbreviation list (rules out "Dr.", "Mr.", "p.m.", "Jan.", ...).

    This is a heuristic, not a dictionary of every abbreviation in
    English — see README.md.
    """
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_SPLIT_RE.finditer(text):
        end = match.end()
        after = text[end:].lstrip()
        is_boundary = not after or after[0].isupper()

        if is_boundary:
            preceding = _TRAILING_WORD_RE.search(text[: match.start()])
            if preceding is not None:
                word = preceding.group().lower()
                if len(word) == 1 or word in _ABBREVIATIONS:
                    is_boundary = False

        if is_boundary:
            sentences.append(text[start:end])
            start = end

    if start < len(text):
        sentences.append(text[start:])

    return [s.strip() for s in sentences if s.strip()]


def verdict_for(reading_ease: float) -> str:
    for threshold, label in _VERDICT_BANDS:
        if reading_ease >= threshold:
            return label
    return _VERDICT_BANDS[-1][1]  # pragma: no cover - unreachable, -inf always matches


def analyze(text: str, target_grade: float | None = None) -> dict[str, object]:
    """Compute readability metrics for `text`.

    Returns a plain dict — the caller (agent_main.py) is responsible for
    wrapping it in a protocol envelope. Raises ValueError if `text` has
    no usable words or sentences; the caller turns that into
    invalid_request.
    """
    words = split_words(text)
    words = split_words(text)

    if not words:
        raise ValueError("text contains no words")
    if not _SENTENCE_SPLIT_RE.search(text):
        raise ValueError("text contains no detectable sentences (no '.', '!' or '?')")

    sentences = split_sentences(text)

    word_count = len(words)
    sentence_count = len(sentences)
    syllable_count = sum(count_syllables(w) for w in words)

    avg_sentence_length = word_count / sentence_count
    avg_syllables_per_word = syllable_count / word_count

    reading_ease = (
        206.835 - 1.015 * avg_sentence_length - 84.6 * avg_syllables_per_word
    )
    grade_level = (
        0.39 * avg_sentence_length + 11.8 * avg_syllables_per_word - 15.59
    )

    meets_target: bool | None = None
    if target_grade is not None:
        meets_target = grade_level <= target_grade

    return {
        "word_count": word_count,
        "sentence_count": sentence_count,
        "avg_sentence_length": round(avg_sentence_length, 2),
        "avg_syllables_per_word": round(avg_syllables_per_word, 2),
        "flesch_reading_ease": round(reading_ease, 2),
        "flesch_kincaid_grade": round(grade_level, 2),
        "verdict": verdict_for(reading_ease),
        "meets_target": meets_target,
    }
