"""
Language utility functions.

Centralised here so that say, whisper, shout, and any future verbal mechanic
can share the same garbling logic without duplicating it.
"""

import random

_VOWELS = "aeiou"
_CONSONANTS = "bcdfghjklmnpqrstvwxyz"
# Characters treated as punctuation and stripped from word edges before garbling.
_PUNCT = frozenset('.,!?;:\'"()-–—')


def garble(text: str) -> str:
    """Return a garbled version of *text* for listeners who don't know the language.

    Per-word rules:
    - Leading/trailing punctuation is preserved in place.
    - Letters are shuffled and the word length randomly extended or shortened.
    - Capitalisation is randomised per word.
    Occasionally a garbled word is split at a random point to insert an extra space.
    """
    words = text.split()
    garbled = [_garble_word(w) for w in words]

    result: list[str] = []
    for w in garbled:
        if len(w) > 4 and random.random() < 0.15:
            mid = random.randint(2, len(w) - 2)
            result.extend([w[:mid], w[mid:]])
        else:
            result.append(w)

    return " ".join(result)


def _garble_word(word: str) -> str:
    lead = ""
    trail = ""

    while word and word[0] in _PUNCT:
        lead += word[0]
        word = word[1:]
    while word and word[-1] in _PUNCT:
        trail = word[-1] + trail
        word = word[:-1]

    if not word:
        return lead + trail

    letters = list(word.lower())
    random.shuffle(letters)

    target = max(1, len(letters) + random.randint(-2, 3))
    while len(letters) < target:
        ch = (
            random.choice(_VOWELS)
            if random.random() < 0.4
            else random.choice(_CONSONANTS)
        )
        letters.insert(random.randint(0, len(letters)), ch)
    letters = letters[:target]

    body = "".join(letters)

    r = random.random()
    if r < 0.30:
        body = body.upper()
    elif r < 0.55:
        body = body.capitalize()
    # else: leave lowercase

    return lead + body + trail


def is_sign_language(language: str) -> bool:
    """Return True if *language* is a signed (gestural) language."""
    return "sign" in language.lower()


def hand_pronoun(gender: str) -> str:
    """Return the possessive pronoun for the hand-gesture message."""
    return {"male": "his", "female": "her"}.get(gender or "", "their")
