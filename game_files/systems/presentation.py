"""INTERACT-05 persistent player presentation preferences."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PROFILE_ATTRIBUTE = "presentation_profile"
PROFILE_VERSION = 1
ROOM_MODES = frozenset({"full", "brief"})
SPACING_MODES = frozenset({"normal", "compact"})
DEFAULT_PROFILE = {
    "version": PROFILE_VERSION,
    "room_mode": "full",
    "spacing": "normal",
    "auto_exits": True,
    "prompt": True,
}
ORDINARY_PROMPT = "> "


@dataclass(frozen=True)
class PresentationProfile:
    """Validated, immutable view of one character's display preferences."""

    room_mode: str
    spacing: str
    auto_exits: bool
    prompt: bool


def get_presentation_profile(character: Any) -> PresentationProfile:
    """Read the profile, repairing missing, old, or malformed stored data."""
    stored = character.attributes.get(PROFILE_ATTRIBUTE)
    repaired = _repair_profile(stored)
    if stored != repaired:
        character.attributes.add(PROFILE_ATTRIBUTE, repaired)
    return PresentationProfile(
        room_mode=repaired["room_mode"],
        spacing=repaired["spacing"],
        auto_exits=repaired["auto_exits"],
        prompt=repaired["prompt"],
    )


def set_presentation_preference(character: Any, field: str, value: Any) -> None:
    """Persist one validated field without replacing the other preferences."""
    profile = get_presentation_profile(character)
    values = {
        "version": PROFILE_VERSION,
        "room_mode": profile.room_mode,
        "spacing": profile.spacing,
        "auto_exits": profile.auto_exits,
        "prompt": profile.prompt,
    }
    validators = {
        "room_mode": lambda item: item in ROOM_MODES,
        "spacing": lambda item: item in SPACING_MODES,
        "auto_exits": lambda item: isinstance(item, bool),
        "prompt": lambda item: isinstance(item, bool),
    }
    if field not in validators or not validators[field](value):
        raise ValueError("Invalid presentation preference.")
    values[field] = value
    character.attributes.add(PROFILE_ATTRIBUTE, values)


def presentation_summary(character: Any) -> str:
    """Return the single player-readable summary of all presentation settings."""
    profile = get_presentation_profile(character)
    return (
        "Display preferences:\n"
        f"  Brief: {'on' if profile.room_mode == 'brief' else 'off'}\n"
        f"  Compact: {'on' if profile.spacing == 'compact' else 'off'}\n"
        f"  Auto-exits: {'on' if profile.auto_exits else 'off'}\n"
        f"  Prompt: {'on' if profile.prompt else 'off'}"
    )


def active_prompt(character: Any) -> str | None:
    """Select editor, combat, then ordinary prompt in strict priority order."""
    ndb = getattr(character, "ndb", None)
    editor = getattr(ndb, "_prompt", None)
    if editor:
        return editor
    combat = getattr(ndb, "_combat_prompt", None)
    if combat:
        return combat
    is_typeclass = getattr(character, "is_typeclass", None)
    if not callable(is_typeclass) or not is_typeclass(
        "typeclasses.characters.Character", exact=False
    ):
        return None
    return ORDINARY_PROMPT if get_presentation_profile(character).prompt else None


def _repair_profile(stored: Any) -> dict[str, Any]:
    """Normalize any legacy payload to the current exact primitive schema."""
    # Evennia wraps persisted dicts in a mapping proxy, so accepting only a
    # literal ``dict`` would silently reset valid profiles on every read.
    source = stored if isinstance(stored, Mapping) else {}
    return {
        "version": PROFILE_VERSION,
        "room_mode": (
            source.get("room_mode")
            if source.get("room_mode") in ROOM_MODES
            else DEFAULT_PROFILE["room_mode"]
        ),
        "spacing": (
            source.get("spacing")
            if source.get("spacing") in SPACING_MODES
            else DEFAULT_PROFILE["spacing"]
        ),
        "auto_exits": (
            source.get("auto_exits")
            if isinstance(source.get("auto_exits"), bool)
            else DEFAULT_PROFILE["auto_exits"]
        ),
        "prompt": (
            source.get("prompt")
            if isinstance(source.get("prompt"), bool)
            else DEFAULT_PROFILE["prompt"]
        ),
    }
