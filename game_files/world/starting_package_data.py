"""
Human-authored starting-equipment packages (ITEM-07A).

This module is content: staff write it by hand, and ``systems.starting_packages``
validates it into the immutable registry that chargen presents and ITEM-07B
grants.  Nothing else defines starting equipment.

Every selectable class and background needs exactly one package.  Until all of
them validate, the registry is incomplete, chargen shows starting equipment as
not yet available, and no package can be planned.  Run ``startpackages`` in
game (Builder) to see what is missing or invalid.

Package format (keys are lowercase identifiers: letters, digits, ``_``)::

    CLASS_PACKAGES = {
        "<Class name>": {
            "srd_reference": "SRD 5.2.1 <section>",   # required
            "adaptation": "<why this differs>",         # optional
            "items": [                                  # always granted
                {"prototype": "<prototype_key>", "quantity": 1,
                 "equip": ["<wear location>"]},         # equip is optional
            ],
            "coins": 0,                                 # always granted
            "choices": [
                {"key": "<choice_key>", "count": 1, "options": [
                    {"key": "a", "items": [...], "coins": 7},
                    {"key": "b", "coins": 110},
                ]},
            ],
        },
    }

``BACKGROUND_PACKAGES`` uses the same format keyed by background name.  An
option may hold its own ``choices`` one level deep (for example a tool kind
chosen only when that option is taken).  Every ``prototype`` must be a module
prototype in ``world/prototypes.py``; see ``help building starting packages``.
"""

# Bump when a published package changes so existing grants keep their version.
STARTING_PACKAGE_VERSION = 2

# Each option is labelled so its identity remains stable in a saved grant plan.
CLASS_PACKAGES: dict[str, dict] = {
    "Cleric": {
        "srd_reference": "SRD 5.2.1 Cleric: Starting Equipment",
        "choices": [
            {
                "key": "kit",
                "count": 1,
                "options": [
                    {
                        "key": "a",
                        "items": [
                            {"prototype": "chain_shirt", "equip": ["body"]},
                            {"prototype": "shield", "equip": ["shield"]},
                            {"prototype": "mace", "equip": ["wield"]},
                            {"prototype": "holy_symbol"},
                            {"prototype": "backpack"},
                            {"prototype": "blanket"},
                            {"prototype": "holy_water"},
                            {"prototype": "lamp"},
                            {"prototype": "rations", "quantity": 7},
                            {"prototype": "robe"},
                            {"prototype": "tinderbox"},
                        ],
                        "coins": 7,
                    },
                    {"key": "b", "coins": 110},
                ],
            }
        ],
    },
    "Fighter": {
        "srd_reference": "SRD 5.2.1 Fighter: Starting Equipment",
        "choices": [
            {
                "key": "kit",
                "count": 1,
                "options": [
                    {
                        "key": "a",
                        "items": [
                            {"prototype": "chain_mail", "equip": ["body"]},
                            {"prototype": "greatsword", "equip": ["wield"]},
                            {"prototype": "flail"},
                            {"prototype": "javelin", "quantity": 8},
                            {"prototype": "backpack"},
                            {"prototype": "caltrops"},
                            {"prototype": "crowbar"},
                            {"prototype": "oil_flask", "quantity": 2},
                            {"prototype": "rations", "quantity": 10},
                            {"prototype": "rope"},
                            {"prototype": "tinderbox"},
                            {"prototype": "torch", "quantity": 10},
                            {"prototype": "waterskin"},
                        ],
                        "coins": 4,
                    },
                    {
                        "key": "b",
                        "items": [
                            {"prototype": "studded_leather", "equip": ["body"]},
                            {"prototype": "scimitar", "equip": ["wield"]},
                            {"prototype": "shortsword"},
                            {"prototype": "longbow"},
                            {"prototype": "arrows", "quantity": 20},
                            {"prototype": "quiver"},
                            {"prototype": "backpack"},
                        ],
                        "coins": 11,
                    },
                    {"key": "c", "coins": 155},
                ],
            }
        ],
    },
    "Rogue": {
        "srd_reference": "SRD 5.2.1 Rogue: Starting Equipment",
        "choices": [
            {
                "key": "kit",
                "count": 1,
                "options": [
                    {
                        "key": "a",
                        "items": [
                            {"prototype": "leather_armor", "equip": ["body"]},
                            {"prototype": "dagger", "quantity": 2},
                            {"prototype": "shortsword"},
                            {"prototype": "shortbow"},
                            {"prototype": "arrows", "quantity": 20},
                            {"prototype": "quiver"},
                            {"prototype": "thieves_tools"},
                            {"prototype": "backpack"},
                            {"prototype": "ball_bearings"},
                            {"prototype": "bell"},
                            {"prototype": "candle", "quantity": 10},
                            {"prototype": "crowbar"},
                            {"prototype": "hooded_lantern"},
                            {"prototype": "oil_flask", "quantity": 7},
                            {"prototype": "rations", "quantity": 5},
                            {"prototype": "rope"},
                            {"prototype": "tinderbox"},
                            {"prototype": "waterskin"},
                        ],
                        "coins": 8,
                    },
                    {"key": "b", "coins": 100},
                ],
            }
        ],
    },
    "Wizard": {
        "srd_reference": "SRD 5.2.1 Wizard: Starting Equipment",
        "choices": [
            {
                "key": "kit",
                "count": 1,
                "options": [
                    {
                        "key": "a",
                        "items": [
                            {"prototype": "dagger", "quantity": 2},
                            {"prototype": "quarterstaff"},
                            {"prototype": "robe", "equip": ["body"]},
                            {"prototype": "spellbook"},
                            {"prototype": "backpack"},
                            {"prototype": "book"},
                            {"prototype": "ink"},
                            {"prototype": "ink_pen"},
                            {"prototype": "lamp"},
                            {"prototype": "oil_flask", "quantity": 10},
                            {"prototype": "parchment", "quantity": 10},
                            {"prototype": "tinderbox"},
                        ],
                        "coins": 5,
                    },
                    {"key": "b", "coins": 55},
                ],
            }
        ],
    },
}


def _background(
    items: list[dict], coins: int, *, choices: list[dict] | None = None
) -> dict:
    """Return a background package with its SRD-equivalent wealth alternative."""
    return {
        "srd_reference": "SRD 5.2.1 Background: Starting Equipment",
        "choices": [
            {
                "key": "equipment",
                "count": 1,
                "options": [
                    {
                        "key": "a",
                        "items": items,
                        "coins": coins,
                        "choices": choices or [],
                    },
                    {"key": "b", "coins": 50},
                ],
            }
        ],
    }


BACKGROUND_PACKAGES: dict[str, dict] = {
    "Acolyte": _background(
        [
            {"prototype": "calligraphers_supplies"},
            {"prototype": "book"},
            {"prototype": "holy_symbol"},
            {"prototype": "parchment", "quantity": 10},
            {"prototype": "robe"},
        ],
        8,
    ),
    "Artisan": _background(
        [
            {"prototype": "artisans_tools"},
            {"prototype": "pouch", "quantity": 2},
            {"prototype": "travelers_clothes"},
        ],
        32,
        choices=[],
    ),
    "Charlatan": _background(
        [
            {"prototype": "forgery_kit"},
            {"prototype": "costume"},
            {"prototype": "fine_clothes"},
        ],
        15,
    ),
    "Criminal": _background(
        [
            {"prototype": "dagger", "quantity": 2},
            {"prototype": "thieves_tools"},
            {"prototype": "crowbar"},
            {"prototype": "pouch", "quantity": 2},
            {"prototype": "travelers_clothes"},
        ],
        16,
    ),
    "Entertainer": _background(
        [
            {"prototype": "musical_instrument"},
            {"prototype": "costume", "quantity": 2},
            {"prototype": "mirror"},
            {"prototype": "perfume"},
            {"prototype": "travelers_clothes"},
        ],
        11,
    ),
    "Farmer": _background(
        [
            {"prototype": "artisans_tools"},
            {"prototype": "healers_kit"},
            {"prototype": "iron_pot"},
            {"prototype": "shovel"},
            {"prototype": "travelers_clothes"},
        ],
        30,
    ),
    "Guard": _background(
        [
            {"prototype": "hooded_lantern"},
            {"prototype": "manacles"},
            {"prototype": "quiver"},
            {"prototype": "arrows", "quantity": 20},
            {"prototype": "spear"},
            {"prototype": "travelers_clothes"},
        ],
        11,
    ),
    "Guide": _background(
        [
            {"prototype": "cartographers_tools"},
            {"prototype": "bedroll"},
            {"prototype": "quiver"},
            {"prototype": "arrows", "quantity": 20},
            {"prototype": "shortbow"},
            {"prototype": "travelers_clothes"},
        ],
        3,
    ),
    "Hermit": _background(
        [
            {"prototype": "herbalism_kit"},
            {"prototype": "bedroll"},
            {"prototype": "book"},
            {"prototype": "blanket"},
            {"prototype": "travelers_clothes"},
        ],
        16,
    ),
    "Merchant": _background(
        [
            {"prototype": "navigators_tools"},
            {"prototype": "pouch", "quantity": 2},
            {"prototype": "travelers_clothes"},
        ],
        22,
    ),
    "Noble": _background(
        [
            {"prototype": "fine_clothes"},
            {"prototype": "perfume"},
            {"prototype": "signet_ring"},
        ],
        30,
    ),
    "Sage": _background(
        [
            {"prototype": "quarterstaff"},
            {"prototype": "calligraphers_supplies"},
            {"prototype": "book"},
            {"prototype": "parchment", "quantity": 8},
            {"prototype": "robe"},
        ],
        8,
    ),
    "Sailor": _background(
        [
            {"prototype": "navigators_tools"},
            {"prototype": "dagger"},
            {"prototype": "rope"},
            {"prototype": "travelers_clothes"},
        ],
        20,
    ),
    "Scribe": _background(
        [
            {"prototype": "calligraphers_supplies"},
            {"prototype": "fine_clothes"},
            {"prototype": "parchment", "quantity": 12},
        ],
        23,
    ),
    "Soldier": _background(
        [
            {"prototype": "spear"},
            {"prototype": "shortbow"},
            {"prototype": "arrows", "quantity": 20},
            {"prototype": "healers_kit"},
            {"prototype": "quiver"},
            {"prototype": "travelers_clothes"},
        ],
        14,
        choices=[
            {
                "key": "gaming_set",
                "count": 1,
                "options": [
                    {"key": "dice", "items": [{"prototype": "gaming_dice"}]},
                    {
                        "key": "dragonchess",
                        "items": [{"prototype": "gaming_dragonchess"}],
                    },
                    {"key": "cards", "items": [{"prototype": "gaming_cards"}]},
                    {
                        "key": "three_dragon_ante",
                        "items": [{"prototype": "gaming_three_dragon_ante"}],
                    },
                ],
            }
        ],
    ),
    "Wayfarer": _background(
        [
            {"prototype": "thieves_tools"},
            {"prototype": "bedroll"},
            {"prototype": "dagger", "quantity": 2},
            {"prototype": "travelers_clothes"},
        ],
        16,
    ),
}
