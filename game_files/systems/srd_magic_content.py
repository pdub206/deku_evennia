"""Released SRD 5.2.1 action definitions.

An entry is added here only after its P-04 checklist has a completed
progression grant, resource, executable handler, help entry, source citation,
and focused tests.  This module contains definitions only; it cannot execute
content.
"""

from __future__ import annotations

from systems.magic import (
    AccessMode,
    ClassAccess,
    DiceExpression,
    MagicDefinition,
    MagicKind,
    PlayerHelp,
    RangeCategory,
    ResourceCost,
    Targeting,
    TargetingMode,
)

SRD_MAGIC_DEFINITIONS = (
    MagicDefinition(
        key="fighter.second_wind",
        display_name="Second Wind",
        aliases=("second wind",),
        kind=MagicKind.ABILITY,
        school="martial",
        tags=("fighter", "class_feature", "healing"),
        class_access=(ClassAccess("Fighter", 1),),
        access_modes=(AccessMode.INNATE,),
        # The SRD calls this a Bonus Action. DEKU has no separate bonus-action
        # queue, so it uses the existing immediate ability-casting flow.
        action_category="combat",
        handler_key="healing",
        targeting=Targeting(TargetingMode.SELF, include_caster=True),
        range=RangeCategory.SELF,
        cost=ResourceCost("fighter.second_wind", 1),
        healing=DiceExpression(1, 10),
        healing_class_level_bonus=1,
        player_help=PlayerHelp(
            "second wind",
            "Regain 1d10 + your Fighter level Hit Points.",
            "In DEKU, bonus actions are not separately scheduled; this uses the normal immediate ability-casting flow.",
        ),
        srd_reference="SRD 5.2.1 pp.47-48: Fighter Features table; Second Wind",
    ),
)

SRD_MAGIC_HELP_KEYS = tuple(
    definition.player_help.key
    for definition in SRD_MAGIC_DEFINITIONS
    if definition.player_help is not None
)
