"""
Editable-field schemas for the |wbuild|n command.

A schema maps the friendly field name a builder types (``name``, ``desc``,
``area``) to how that field is validated, where it is stored, and a one-line
blurb describing it.  This module is the single source of truth for "what is
editable on a buildable thing":

* the build command renders the blurbs (``fields``) and validates ``set`` input
  through the validators here, and
* the area exporter walks the ``attr`` fields to decide what to serialise.

Add a new buildable type (NPC, Item, ...) by defining its ``*_FIELDS`` dict and
wiring it into :func:`schema_for`.  Validators raise ``ValueError`` with a short,
player-safe reason on bad input; callers turn that into a friendly message.
"""

import re
from typing import Callable, NamedTuple

from evennia.utils.utils import inherits_from
from systems.equipment import WEAR_LOCATIONS
from world.chargen_data import (ABILITY_NAMES, ALIGNMENTS, BACKGROUNDS,
                                CLASSES, MAX_AGE, MIN_AGE, SKILLS, SPECIES,
                                STANDARD_LANGUAGES)


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


def as_int_range(minimum: int, maximum: int) -> Callable[[str], int]:
    """Return a validator accepting whole numbers in an inclusive range."""

    def validate(raw: str) -> int:
        try:
            value = int(raw.strip())
        except ValueError:
            raise ValueError("expected a whole number.")
        if not minimum <= value <= maximum:
            raise ValueError(f"must be between {minimum} and {maximum}.")
        return value

    return validate


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


def as_named_choice(*options: str) -> Callable[[str], str]:
    """Return a case-insensitive validator preserving each option's spelling."""
    allowed = {option.lower(): option for option in options}

    def validate(raw: str) -> str:
        value = raw.strip().lower()
        if value not in allowed:
            raise ValueError(f"must be one of: {', '.join(options)}.")
        return allowed[value]

    return validate


def as_choice_list(*options: str) -> Callable[[str], list[str]]:
    """Return a validator for a comma-separated list of canonical choices."""
    validate_choice = as_named_choice(*options)

    def validate(raw: str) -> list[str]:
        values = [part.strip() for part in raw.split(",") if part.strip()]
        if not values:
            raise ValueError("expected one or more comma-separated values.")
        canonical = [validate_choice(value) for value in values]
        return list(dict.fromkeys(canonical))

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

# Item types supported by the builder. These follow the classic Diku/Circle/tbaMUD
# names, with the existing container type retained. Most are classifications only
# for now; TYPE_FIELDS adds extra builder fields where mechanics already exist.
ITEM_TYPES: tuple[str, ...] = (
    "light",
    "scroll",
    "wand",
    "staff",
    "weapon",
    "furniture",
    "treasure",
    "armor",
    "potion",
    "worn",
    "other",
    "trash",
    "container",
    "note",
    "drinkcon",
    "key",
    "food",
    "money",
    "pen",
    "boat",
    "fountain",
)

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
    "wear_locations": Field(
        "attr",
        as_choice_list(*WEAR_LOCATIONS),
        "comma-separated equipment slots where this can be worn",
        target="wear_locations",
    ),
    "type": Field(
        "type",
        as_choice(*ITEM_TYPES, "none"),
        f"item type ({', '.join(ITEM_TYPES)}, or none) — adds type-specific fields",
        target="type",
    ),
}


_ALIGNMENT_NAMES = tuple(name for name, _abbr, _desc in ALIGNMENTS)
_ABILITY_DB_NAMES = {name: name.lower() for name in ABILITY_NAMES}
_PC_LANGUAGES = ("Common", *STANDARD_LANGUAGES)

# NPCs use the Character typeclass and the same canonical stat inputs written by
# chargen. Builder-facing derived fields target explicit overrides so changing a
# base ability or level cannot silently leave an ordinary NPC with stale values.
NPC_FIELDS: dict[str, Field] = {
    "name": Field("key", as_text, "the NPC's name"),
    "desc": Field(
        "attr",
        as_text,
        "the NPC's description (type 'desc' with no value for the editor)",
        target="desc",
    ),
    "gender": Field(
        "attr",
        as_choice("male", "female", "nonbinary", "unspecified"),
        "male, female, nonbinary, or unspecified",
    ),
    "species": Field(
        "attr", as_named_choice(*SPECIES), f"species ({', '.join(SPECIES)})"
    ),
    "class": Field(
        "attr", as_named_choice(*CLASSES), f"class ({', '.join(CLASSES)})", "char_class"
    ),
    "age": Field(
        "attr",
        as_int_range(MIN_AGE, MAX_AGE),
        f"age in years ({MIN_AGE}-{MAX_AGE})",
    ),
    "alignment": Field(
        "attr",
        as_named_choice(*_ALIGNMENT_NAMES),
        f"alignment ({', '.join(_ALIGNMENT_NAMES)})",
    ),
    "background": Field(
        "attr",
        as_named_choice(*BACKGROUNDS),
        f"background ({', '.join(BACKGROUNDS)})",
    ),
    "size": Field("attr", as_named_choice("Small", "Medium"), "Small or Medium"),
    "languages": Field(
        "attr",
        as_choice_list(*_PC_LANGUAGES),
        "known languages, comma-separated",
    ),
    "active_language": Field(
        "attr",
        as_named_choice(*_PC_LANGUAGES),
        "the language the NPC currently speaks",
    ),
    "skills": Field(
        "attr",
        as_choice_list(*SKILLS),
        "skill proficiencies, comma-separated",
        "skill_proficiencies",
    ),
    **{
        ability.lower(): Field(
            "attr",
            as_int_range(3, 20),
            f"{ability} ability score (3-20)",
            _ABILITY_DB_NAMES[ability],
        )
        for ability in ABILITY_NAMES
    },
    "level": Field("attr", as_int_range(1, 20), "character level (1-20)"),
    "xp": Field("attr", as_nonneg_int, "experience points (zero or greater)"),
    "proficiency_bonus": Field(
        "attr",
        as_nonneg_int,
        "optional proficiency bonus override",
        "proficiency_bonus_override",
    ),
    "hp_base": Field(
        "attr",
        as_nonneg_int,
        "accumulated HP before Constitution",
        "hp_base",
    ),
    "hp_max": Field(
        "attr", as_nonneg_int, "optional maximum HP override", "hp_max_override"
    ),
    "hp_current": Field("attr", as_nonneg_int, "current hit points", "hp_current"),
    "hit_die": Field("attr", as_int_range(1, 100), "hit die size, e.g. 10", "hit_die"),
    "reaction": Field(
        "attr",
        as_int_range(-20, 20),
        "optional combat Reaction override",
        "reaction_modifier_override",
    ),
    "armor_class": Field(
        "attr", as_nonneg_int, "optional Armor Class override", "armor_class_override"
    ),
    "passive_perception": Field(
        "attr",
        as_nonneg_int,
        "optional passive Perception override",
        "passive_perception_override",
    ),
    "speed": Field("attr", as_nonneg_int, "movement speed in feet", "speed"),
}


def _item_schema(item_type) -> dict[str, Field]:
    """Item fields plus the extra fields for ``item_type`` (None = generic)."""
    return {**ITEM_FIELDS, **TYPE_FIELDS.get(item_type, {})}


def schema_for(obj) -> dict[str, Field] | None:
    """Return the field schema for a live ``obj``, or ``None`` if not buildable.

    For an item the schema is dynamic: the always-present fields plus the extra
    fields for whatever ``type`` the item currently has (none for a generic
    item).  Centralising this keeps the command and the exporter in agreement.
    """
    if inherits_from(obj, "evennia.objects.objects.DefaultRoom"):
        return ROOM_FIELDS
    if inherits_from(obj, "evennia.objects.objects.DefaultCharacter"):
        return NPC_FIELDS
    if inherits_from(obj, "typeclasses.objects.Item"):
        return _item_schema(obj.db.type)
    return None


def schema_for_prototype(proto: dict) -> dict[str, Field] | None:
    """Return the field schema for a prototype dict, or ``None`` if unknown.

    Mirrors :func:`schema_for` but reads from a prototype's ``typeclass``/``type``
    keys instead of a live object, so the build editor can drive item templates
    with the same fields as their instances.
    """
    if proto.get("typeclass") == "typeclasses.objects.Item":
        return _item_schema(proto.get("type"))
    if proto.get("typeclass") == "typeclasses.characters.Character":
        return NPC_FIELDS
    return None
