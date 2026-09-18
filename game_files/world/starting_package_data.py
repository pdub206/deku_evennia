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
STARTING_PACKAGE_VERSION = 1

CLASS_PACKAGES: dict[str, dict] = {}

BACKGROUND_PACKAGES: dict[str, dict] = {}
