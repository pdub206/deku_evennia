"""Durable known, prepared, and spellbook ownership coverage."""

from unittest.mock import patch

from evennia.utils.test_resources import EvenniaTest
from systems.magic import (
    AccessMode,
    ClassAccess,
    MagicDefinition,
    MagicKind,
    PlayerHelp,
    RangeCategory,
    Targeting,
    TargetingMode,
    build_magic_registry,
)
from systems.magic_actions import (
    MAGIC_ACTION_STATE_ATTRIBUTE,
    MagicActionError,
    available_actions,
    grant_action,
    grant_spellbook_entry,
    has_action_entitlement,
    mark_preparation_window,
    prepare_action,
    replace_learned_action,
    revoke_action,
)


def _spell(number: int) -> MagicDefinition:
    """Build one prepared Wizard spell without adding executable content."""
    return MagicDefinition(
        key=f"wizard.test_spell_{number}",
        display_name=f"Test Spell {number}",
        aliases=(f"test {number}",),
        kind=MagicKind.SPELL,
        school="abjuration",
        tags=("arcane",),
        class_access=(ClassAccess("Wizard", 1),),
        access_modes=(AccessMode.PREPARED,),
        action_category="manipulate",
        handler_key="utility",
        targeting=Targeting(TargetingMode.SELF, include_caster=True),
        range=RangeCategory.SELF,
        spell_level=1,
        player_help=PlayerHelp(f"test spell {number}", "A test spell."),
    )


def _cantrip(number: int) -> MagicDefinition:
    """Build a learned Wizard cantrip for capacity and replacement tests."""
    return MagicDefinition(
        key=f"wizard.test_cantrip_{number}",
        display_name=f"Test Cantrip {number}",
        aliases=(f"cantrip {number}",),
        kind=MagicKind.SPELL,
        school="evocation",
        tags=("arcane",),
        class_access=(ClassAccess("Wizard", 1),),
        access_modes=(AccessMode.LEARNED,),
        action_category="manipulate",
        handler_key="utility",
        targeting=Targeting(TargetingMode.SELF, include_caster=True),
        range=RangeCategory.SELF,
        player_help=PlayerHelp(f"test cantrip {number}", "A test cantrip."),
    )


def _registry():
    """Build more spells than level-one preparation capacity permits."""
    definitions = tuple(_spell(number) for number in range(1, 8)) + tuple(
        _cantrip(number) for number in range(1, 5)
    )
    return build_magic_registry(
        definitions,
        class_keys=("Wizard",),
        help_keys=tuple(f"test spell {number}" for number in range(1, 8))
        + tuple(f"test cantrip {number}" for number in range(1, 5)),
    )


class TestSpellOwnership(EvenniaTest):
    """Spellbooks and prepared spells have different durable semantics."""

    def setUp(self):
        super().setUp()
        self.char1.db.char_class = "Wizard"
        self.char1.db.level = 1
        self.registry = _registry()

    def test_spellbook_entry_must_precede_wizard_preparation(self):
        """A Wizard cannot cast a prepared spell absent from their spellbook."""
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            mark_preparation_window(self.char1, 1)
            with self.assertRaisesRegex(MagicActionError, "not in your spellbook"):
                prepare_action(self.char1, "wizard.test_spell_1")
            grant_spellbook_entry(self.char1, "wizard.test_spell_1")
            prepare_action(self.char1, "wizard.test_spell_1")

            state = self.char1.attributes.get(MAGIC_ACTION_STATE_ATTRIBUTE)
            self.assertEqual(state["version"], 2)
            self.assertEqual(state["spellbook"], ["wizard.test_spell_1"])
            self.assertEqual(state[AccessMode.PREPARED], ["wizard.test_spell_1"])
            self.assertEqual(
                [
                    action.key
                    for action in available_actions(self.char1, MagicKind.SPELL)
                ],
                ["wizard.test_spell_1"],
            )

    def test_preparation_capacity_and_legacy_state_fail_closed(self):
        """Preparation respects the class table and upgrades v1 state on write."""
        self.char1.attributes.add(
            MAGIC_ACTION_STATE_ATTRIBUTE,
            {"version": 1, "learned": [], "prepared": [], "innate": []},
        )
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            mark_preparation_window(self.char1, 1)
            for number in range(1, 6):
                key = f"wizard.test_spell_{number}"
                grant_spellbook_entry(self.char1, key)
                if number < 5:
                    grant_action(self.char1, key, AccessMode.PREPARED)
            with self.assertRaisesRegex(MagicActionError, "cannot prepare another"):
                prepare_action(self.char1, "wizard.test_spell_5")
            self.assertEqual(
                self.char1.attributes.get(MAGIC_ACTION_STATE_ATTRIBUTE)["version"], 2
            )

    def test_preparation_requires_completed_long_rest(self):
        """A spellbook entry alone cannot bypass the class preparation timing."""
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            grant_spellbook_entry(self.char1, "wizard.test_spell_1")
            with self.assertRaisesRegex(MagicActionError, "only after a Long Rest"):
                prepare_action(self.char1, "wizard.test_spell_1")
            mark_preparation_window(self.char1, 80)
            prepare_action(self.char1, "wizard.test_spell_1")

            self.char1.attributes.remove("magic_preparation_window")
            with self.assertRaisesRegex(MagicActionError, "only after a Long Rest"):
                revoke_action(self.char1, "wizard.test_spell_1", AccessMode.PREPARED)
            self.assertTrue(
                has_action_entitlement(
                    self.char1, "wizard.test_spell_1", AccessMode.PREPARED
                )
            )

    def test_cantrip_capacity_and_atomic_learned_replacement(self):
        """Cantrips use their own class-table capacity and replace safely."""
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            for number in range(1, 4):
                grant_action(
                    self.char1, f"wizard.test_cantrip_{number}", AccessMode.LEARNED
                )
            with self.assertRaisesRegex(MagicActionError, "cannot learn another"):
                grant_action(self.char1, "wizard.test_cantrip_4", AccessMode.LEARNED)

            replace_learned_action(
                self.char1, "wizard.test_cantrip_1", "wizard.test_cantrip_4"
            )

        learned = self.char1.attributes.get(MAGIC_ACTION_STATE_ATTRIBUTE)[
            AccessMode.LEARNED
        ]
        self.assertEqual(
            learned,
            [
                "wizard.test_cantrip_2",
                "wizard.test_cantrip_3",
                "wizard.test_cantrip_4",
            ],
        )
