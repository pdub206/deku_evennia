"""Immutable, display-safe definitions for the released nonverbal socials."""

from __future__ import annotations

import re
from string import Formatter
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from evennia.utils.ansi import strip_ansi

_KEY_PATTERN = re.compile(r"^[a-z]+$")
_SLOTS = frozenset({"actor", "target"})


@dataclass(frozen=True)
class SocialTemplates:
    """The actor, target, self, and observer text for one fixed social."""

    actor: str
    observer: str
    target_actor: str
    target_target: str
    target_observer: str
    self_actor: str
    self_observer: str


@dataclass(frozen=True)
class SocialDefinition:
    """A validated registry entry with no executable or database-owned prose."""

    key: str
    aliases: tuple[str, ...]
    templates: SocialTemplates


def _validate_template(template: str) -> None:
    """Allow only the two display-aware placeholders in fixed social prose."""
    if not isinstance(template, str) or not template or "|" in template:
        raise ValueError("social templates must be non-empty plain text")
    try:
        fields = [
            (field, format_spec, conversion)
            for _literal, field, format_spec, conversion in Formatter().parse(template)
            if field is not None
        ]
    except ValueError as error:
        raise ValueError("social templates contain invalid braces") from error
    if any(
        field not in _SLOTS or format_spec or conversion
        for field, format_spec, conversion in fields
    ):
        raise ValueError("social templates contain an unsupported placeholder")


def validate_social(definition: SocialDefinition) -> SocialDefinition:
    """Reject malformed keys, aliases, and templates before a social is released."""
    names = (definition.key, *definition.aliases)
    if not all(
        isinstance(name, str) and _KEY_PATTERN.fullmatch(name) for name in names
    ) or len(set(names)) != len(names):
        raise ValueError("social keys and aliases must be unique lowercase words")
    for template in definition.templates.__dict__.values():
        _validate_template(template)
    return definition


def _definition(
    key: str, verb: str, target_preposition: str = " to"
) -> SocialDefinition:
    """Build deliberately fixed prose while keeping the registry compact to audit."""
    return validate_social(
        SocialDefinition(
            key=key,
            aliases=(),
            templates=SocialTemplates(
                actor=f"You {verb}.",
                observer=f"{{actor}} {verb}s.",
                target_actor=f"You {verb}{target_preposition} {{target}}.",
                target_target=f"{{actor}} {verb}s{target_preposition} you.",
                target_observer=f"{{actor}} {verb}s{target_preposition} {{target}}.",
                self_actor=f"You {verb}{target_preposition} yourself.",
                self_observer=f"{{actor}} {verb}s{target_preposition} themselves.",
            ),
        )
    )


_DEFINITIONS = (
    _definition("bow", "bow"),
    _definition("grin", "grin"),
    _definition("laugh", "laugh", " at"),
    _definition("nod", "nod"),
    _definition("salute", "salute", ""),
    _definition("shake", "shake", " at"),
    _definition("shrug", "shrug", " at"),
    _definition("sigh", "sigh", " at"),
    _definition("smile", "smile", " at"),
    _definition("thank", "thank", ""),
    _definition("wave", "wave", " at"),
    _definition("wink", "wink", " at"),
)


def _registry(
    definitions: tuple[SocialDefinition, ...],
) -> Mapping[str, SocialDefinition]:
    """Create a registry only when no released key or alias can collide."""
    entries = tuple(validate_social(definition) for definition in definitions)
    names = [
        name for definition in entries for name in (definition.key, *definition.aliases)
    ]
    if len(names) != len(set(names)):
        raise ValueError("social registry keys and aliases must not collide")
    return MappingProxyType({definition.key: definition for definition in entries})


SOCIALS = _registry(_DEFINITIONS)


def social_for(name: str) -> SocialDefinition | None:
    """Resolve one released social by its stable key or a validated alias."""
    query = name.casefold()
    for definition in SOCIALS.values():
        if query == definition.key or query in definition.aliases:
            return definition
    return None


def safe_display_name(subject: Any, viewer: Any) -> str:
    """Return one plain display name so character names cannot forge output."""
    try:
        name = subject.get_display_name(viewer)
    except (AttributeError, TypeError):
        name = getattr(subject, "key", "someone")
    text = unicodedata.normalize("NFC", str(name))
    text = strip_ansi(text).replace("|", "")
    return "".join(
        char for char in text if not unicodedata.category(char).startswith("C")
    )


def render(template: str, actor: Any, viewer: Any, target: Any | None = None) -> str:
    """Fill only trusted, observer-specific display slots in fixed template text."""
    values = {"actor": safe_display_name(actor, viewer)}
    if target is not None:
        values["target"] = safe_display_name(target, viewer)
    return template.format(**values)
