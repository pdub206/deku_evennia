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


def schema_for(obj) -> dict[str, Field] | None:
    """Return the field schema for ``obj``, or ``None`` if it isn't buildable.

    Type detection is centralised here so the command and the exporter agree on
    what each object's editable fields are.
    """
    if inherits_from(obj, "evennia.objects.objects.DefaultRoom"):
        return ROOM_FIELDS
    # Future: DefaultCharacter -> NPC_FIELDS, item typeclasses -> ITEM_FIELDS.
    return None
