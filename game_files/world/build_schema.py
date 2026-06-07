"""
Editable-field schemas for the |wbuild|n command.

A schema maps the friendly field name a builder types (``name``, ``desc``,
``area``) to how that field is validated, where it is stored, and a one-line
blurb describing it.  This module is the single source of truth for "what is
editable on a buildable thing":

* the build command renders the blurbs (``fields``) and validates ``set`` input
  through the validators here, and
* the area exporter walks the ``attr`` fields to decide what to serialise.

Add a new buildable type (Mob, Item, ...) by defining its ``*_FIELDS`` dict and
wiring it into :func:`schema_for`.  Validators raise ``ValueError`` with a short,
player-safe reason on bad input; callers turn that into a friendly message.
"""

import re
from typing import Callable, NamedTuple

from evennia.utils.utils import inherits_from


class Field(NamedTuple):
    """One editable field on a buildable object.

    Attributes:
        kind: ``"key"`` (the object's name), ``"attr"`` (a ``db`` attribute) or
            ``"tag"`` (a tag in a category).
        validate: Turns raw player text into the stored value, or raises
            ``ValueError`` with a short reason.
        blurb: Human description shown by ``fields``.
        target: Attribute/tag name to store under; defaults to the field name.
    """

    kind: str
    validate: Callable[[str], object]
    blurb: str
    target: str | None = None


# ---------------------------------------------------------------------------
# Validators — each returns the cleaned value or raises ValueError(<reason>).
# ---------------------------------------------------------------------------


def as_text(raw: str) -> str:
    """Any non-empty text."""
    text = raw.strip()
    if not text:
        raise ValueError("expected some text.")
    return text


def as_slug(raw: str) -> str:
    """A lowercase identifier safe for dict keys, tags, and module filenames.

    Collapses any run of non-alphanumerics to a single underscore.  This also
    makes the value injection-safe: an area name can never contain ``/`` or
    ``..`` and so cannot escape the areas directory on export.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")
    if not slug:
        raise ValueError("use letters, numbers, spaces, or hyphens.")
    return slug


def as_nonneg_int(raw: str) -> int:
    """A whole number that is zero or greater."""
    try:
        value = int(raw.strip())
    except ValueError:
        raise ValueError("expected a whole number.")
    if value < 0:
        raise ValueError("cannot be negative.")
    return value


def as_weight(raw: str) -> float:
    """A weight in pounds: a number that is zero or greater."""
    try:
        value = float(raw.strip())
    except ValueError:
        raise ValueError("expected a number of pounds.")
    if value < 0:
        raise ValueError("weight cannot be negative.")
    return value


_DICE_RE = re.compile(r"^[1-9]\d*d[1-9]\d*([+-]\d+)?$")


def as_dice(raw: str) -> str:
    """Dice notation like ``1d8``, ``2d6``, or ``1d8+1`` (stored lowercased)."""
    value = raw.strip().lower().replace(" ", "")
    if not _DICE_RE.match(value):
        raise ValueError("expected dice like 1d8 or 2d6.")
    return value


def as_choice(*options: str) -> Callable[[str], str]:
    """Return a validator accepting only one of ``options`` (case-insensitive).

    The accepted value is returned lowercased so storage is consistent.
    """
    allowed = tuple(opt.lower() for opt in options)

    def validate(raw: str) -> str:
        value = raw.strip().lower()
        if value not in allowed:
            raise ValueError(f"must be one of: {', '.join(allowed)}.")
        return value

    return validate


# ---------------------------------------------------------------------------
# Per-type field schemas.
# ---------------------------------------------------------------------------

ROOM_FIELDS: dict[str, Field] = {
    "name": Field("key", as_text, "the room's name"),
    "desc": Field(
        "attr",
        as_text,
        "the room's description (type 'desc' with no value for the editor)",
        target="desc",
    ),
    "area": Field("tag", as_slug, "the area this room belongs to (drives export)"),
}

# Extra fields a builder can set once an item's ``type`` is chosen. The "type"
# field itself drives this: setting it makes the matching group below appear in
# `fields`/`show`, and clearing/changing it swaps the group out. Both weapon and
# armor expose a ``subtype`` field — same name, different allowed values — which
# is why the build command clears an item's type-specific attributes whenever
# the type changes (so a weapon's "slashing" can't linger on a later armor).
TYPE_FIELDS: dict[str, dict[str, Field]] = {
    "weapon": {
        "damage": Field(
            "attr",
            as_dice,
            "damage dice the system can roll, e.g. 1d8",
            target="damage",
        ),
        "subtype": Field(
            "attr",
            as_choice("bludgeoning", "piercing", "slashing"),
            "damage type: bludgeoning, piercing, or slashing",
            target="subtype",
        ),
    },
    "armor": {
        "base_ac": Field(
            "attr",
            as_nonneg_int,
            "base Armor Class this grants (a shield's bonus)",
            target="base_ac",
        ),
        "subtype": Field(
            "attr",
            as_choice("light", "medium", "heavy", "shield"),
            "armor category: light, medium, heavy, or shield",
            target="subtype",
        ),
    },
    "container": {
        "capacity": Field(
            "attr", as_weight, "max weight in pounds it can hold", target="capacity"
        ),
    },
}

ITEM_TYPES: tuple[str, ...] = tuple(TYPE_FIELDS)

ITEM_FIELDS: dict[str, Field] = {
    "name": Field("key", as_text, "the item's name"),
    "desc": Field(
        "attr",
        as_text,
        "the item's description (type 'desc' with no value for the editor)",
        target="desc",
    ),
    "weight": Field(
        "attr",
        as_weight,
        "weight in pounds; counts against a character's carry capacity",
        target="weight",
    ),
    "value": Field("attr", as_nonneg_int, "worth in coins", target="value"),
    "type": Field(
        "type",
        as_choice(*ITEM_TYPES, "none"),
        f"item type ({', '.join(ITEM_TYPES)}, or none) — adds type-specific fields",
        target="type",
    ),
}


def schema_for(obj) -> dict[str, Field] | None:
    """Return the field schema for ``obj``, or ``None`` if it isn't buildable.

    For an item the schema is dynamic: the always-present fields plus the extra
    fields for whatever ``type`` the item currently has (none for a generic
    item).  Centralising this keeps the command and the exporter in agreement.
    """
    if inherits_from(obj, "evennia.objects.objects.DefaultRoom"):
        return ROOM_FIELDS
    if inherits_from(obj, "typeclasses.objects.Item"):
        extra = TYPE_FIELDS.get(obj.db.type, {})
        return {**ITEM_FIELDS, **extra}
    # Future: DefaultCharacter -> NPC_FIELDS.
    return None
