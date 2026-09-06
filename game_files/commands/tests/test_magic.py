"""MAGIC-02 command and instant-action integration coverage."""

from unittest.mock import patch

from commands.default_cmdsets import CharacterCmdSet
from commands.magic import CmdAbilities, CmdCast, CmdSpells
from evennia.utils.test_resources import EvenniaCommandTest
from systems.advancement import initialize_level_one
from systems.magic import (AccessMode, ClassAccess, MagicDefinition, MagicKind,
                           PlayerHelp, RangeCategory, ResourceCost, Targeting,
                           TargetingMode, build_magic_registry)
from systems.magic_actions import grant_action
from systems.magic_resources import resource_current


def _registry():
    """Build one released-shaped, safe utility action for command tests."""
    action = MagicDefinition(
        key="wizard.spark",
        display_name="Spark",
        aliases=("spark",),
        kind=MagicKind.SPELL,
        school="evocation",
        tags=("arcane",),
        class_access=(ClassAccess("Wizard", 1),),
        access_modes=(AccessMode.LEARNED,),
        action_category="manipulate",
        handler_key="utility",
        targeting=Targeting(TargetingMode.SELF, include_caster=True),
        range=RangeCategory.SELF,
        cost=ResourceCost("wizard.arcane_recovery", 1),
        player_help=PlayerHelp("spark", "A harmless spark."),
    )
    return build_magic_registry(
        (action,),
        class_keys=("Wizard",),
        resource_keys=("wizard.arcane_recovery",),
        help_keys=("spark",),
    )


class TestMagicCommands(EvenniaCommandTest):
    """Commands use one entitlement and resource service rather than strings."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.char1.db.constitution = 10
        initialize_level_one(self.char1, class_key="Wizard", hp_base=6)
        self.registry = _registry()

    def test_cmdset_registers_all_magic_commands(self):
        """The player command set exposes the three MAGIC-02 entry points."""
        cmdset = CharacterCmdSet()
        cmdset.at_cmdset_creation()
        keys = {command.key for command in cmdset.commands}
        self.assertTrue({"cast", "spells", "abilities"} <= keys)

    def test_listing_hides_unlearned_actions_and_cast_spends_once(self):
        """A known action lists and resolves through its declared cost once."""
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            self.assertIn("None.", self.call(CmdSpells(), ""))
            grant_action(self.char1, "wizard.spark", AccessMode.LEARNED)
            self.assertIn("Spark", self.call(CmdSpells(), ""))
            self.assertEqual(resource_current(self.char1, "wizard.arcane_recovery"), 1)
            self.assertIn("You cast", self.call(CmdCast(), "spark"))
            self.assertEqual(resource_current(self.char1, "wizard.arcane_recovery"), 0)
            self.assertIn("enough magical resources", self.call(CmdCast(), "spark"))

    def test_target_grammar_and_kind_specific_lists_fail_safely(self):
        """Target text is explicit, and spells never leak into ability listings."""
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            grant_action(self.char1, "wizard.spark", AccessMode.LEARNED)
            self.assertIn("only target you", self.call(CmdCast(), "spark at Char2"))
            self.assertIn("None.", self.call(CmdAbilities(), ""))
