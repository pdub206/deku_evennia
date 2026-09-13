"""MAGIC-03 concentration relationship coverage."""

from unittest.mock import patch

from evennia.utils.test_resources import EvenniaTest
from systems.advancement import initialize_level_one
from systems.effects import EFFECT_REGISTRY, EffectDefinition, StackingPolicy
from systems.injury import apply_damage
from systems.injury import InjuryState
from systems.magic import (
    AccessMode,
    ClassAccess,
    MagicDefinition,
    MagicKind,
    PlayerHelp,
    RangeCategory,
    ResourceCost,
    Targeting,
    TargetingMode,
    build_magic_registry,
)
from systems.magic_actions import (
    CONCENTRATION_ATTRIBUTE,
    MAGIC_ACTION_STATE_ATTRIBUTE,
    MagicActionError,
    cast_action,
    grant_action,
    maintain_concentration,
    replace_learned_action,
)
from systems.magic_resources import restore_resource

_FIRST_EFFECT = EffectDefinition(
    key="test.magic.sustained_first",
    name="First Sustained Test",
    duration=9,
    stacking=StackingPolicy.INDEPENDENT,
)
_SECOND_EFFECT = EffectDefinition(
    key="test.magic.sustained_second",
    name="Second Sustained Test",
    duration=9,
    stacking=StackingPolicy.INDEPENDENT,
)
for _effect in (_FIRST_EFFECT, _SECOND_EFFECT):
    if EFFECT_REGISTRY.get(_effect.key) is None:
        EFFECT_REGISTRY.register(_effect)


def _sustained_action(key: str, name: str, effect_key: str) -> MagicDefinition:
    """Build a complete test-only sustained effect action."""
    return MagicDefinition(
        key=key,
        display_name=name,
        aliases=(name.casefold(),),
        kind=MagicKind.SPELL,
        school="abjuration",
        tags=("test",),
        class_access=(ClassAccess("Wizard", 1),),
        access_modes=(AccessMode.LEARNED,),
        action_category="manipulate",
        handler_key="effect",
        targeting=Targeting(TargetingMode.SELF, include_caster=True),
        range=RangeCategory.SELF,
        cost=ResourceCost("wizard.arcane_recovery", 1),
        duration=3,
        concentration=True,
        maintenance="concentration",
        effect_keys=(effect_key,),
        player_help=PlayerHelp(name.casefold(), "A sustained test effect."),
    )


def _registry():
    """Return two independent effects so replacement is observable."""
    first = _sustained_action(
        "wizard.test_sustain_first", "First Sustain", _FIRST_EFFECT.key
    )
    second = _sustained_action(
        "wizard.test_sustain_second", "Second Sustain", _SECOND_EFFECT.key
    )
    return build_magic_registry(
        (first, second),
        class_keys=("Wizard",),
        resource_keys=("wizard.arcane_recovery",),
        effect_keys=(_FIRST_EFFECT.key, _SECOND_EFFECT.key),
        help_keys=("first sustain", "second sustain"),
    )


class TestConcentration(EvenniaTest):
    """A caster owns exactly one durable, removable concentration source."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = True
        self.char1.db.constitution = 10
        initialize_level_one(self.char1, class_key="Wizard", hp_base=6)
        self.char1.db.hp_current = 10
        self.registry = _registry()

    def _cast(self, action_key: str) -> None:
        """Grant and resolve one action with its test resource restored as needed."""
        grant_action(self.char1, action_key, AccessMode.LEARNED)
        result = cast_action(
            self.char1, self.registry.definition_for(action_key).display_name
        )
        self.assertTrue(result.accepted)

    def test_new_concentration_removes_the_previous_linked_effect(self):
        """Starting a later concentration effect cleans up the old source."""
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            self._cast("wizard.test_sustain_first")
            first = next(
                effect
                for effect in self.char1.effects.all()
                if effect.key == _FIRST_EFFECT.key
            )
            with self.assertRaisesRegex(MagicActionError, "active effect"):
                replace_learned_action(
                    self.char1,
                    "wizard.test_sustain_first",
                    "wizard.test_sustain_second",
                )
            restore_resource(self.char1, "wizard.arcane_recovery", 1)
            self._cast("wizard.test_sustain_second")

        self.assertIsNone(self.char1.effects.get(first.instance_id))
        record = self.char1.attributes.get(CONCENTRATION_ATTRIBUTE)
        self.assertEqual(record["source_key"], "wizard.test_sustain_second")
        self.assertEqual(len(record["effects"]), 1)

    def test_replacement_scans_effect_sources_beyond_concentration_state(self):
        """An active effect blocks replacement even if its link record is absent."""
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            self._cast("wizard.test_sustain_first")
            self.char1.attributes.remove(CONCENTRATION_ATTRIBUTE)
            with self.assertRaisesRegex(MagicActionError, "active effect"):
                replace_learned_action(
                    self.char1,
                    "wizard.test_sustain_first",
                    "wizard.test_sustain_second",
                )

        learned = self.char1.attributes.get(MAGIC_ACTION_STATE_ATTRIBUTE)[
            AccessMode.LEARNED
        ]
        self.assertEqual(learned, ["wizard.test_sustain_first"])

    def test_removal_or_incapacitation_clears_concentration(self):
        """Independent removal and canonical injury cleanup cannot leave it stuck."""
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            self._cast("wizard.test_sustain_first")
            effect = next(
                active
                for active in self.char1.effects.all()
                if active.key == _FIRST_EFFECT.key
            )
            self.char1.effects.remove(effect.instance_id)
            self.assertIsNone(self.char1.attributes.get(CONCENTRATION_ATTRIBUTE))

            restore_resource(self.char1, "wizard.arcane_recovery", 1)
            self._cast("wizard.test_sustain_second")

        with patch("systems.injury._is_staff_immune", return_value=False):
            injury = apply_damage(self.char1, 10, emit_messages=False)
        self.assertEqual(injury.state, InjuryState.DYING)
        self.assertIsNone(self.char1.attributes.get(CONCENTRATION_ATTRIBUTE))
        self.assertFalse(
            any(active.key == _SECOND_EFFECT.key for active in self.char1.effects.all())
        )

    def test_damage_maintenance_uses_srd_dc_and_cleans_up_on_failure(self):
        """A failed Constitution save ends every effect linked to concentration."""
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            self._cast("wizard.test_sustain_first")
        result = maintain_concentration(self.char1, 40, roller=lambda _: 1)

        self.assertTrue(result.attempted)
        self.assertFalse(result.maintained)
        self.assertEqual(result.dc, 20)
        self.assertEqual(result.reason, "failed")
        self.assertIsNone(self.char1.attributes.get(CONCENTRATION_ATTRIBUTE))
        self.assertFalse(self.char1.effects.has(_FIRST_EFFECT.key))

    def test_canonical_damage_invokes_concentration_maintenance(self):
        """Every non-terminal injury path delegates maintenance to MAGIC-03."""
        with patch("systems.magic.MAGIC_REGISTRY", self.registry):
            self._cast("wizard.test_sustain_first")
        with (
            patch("systems.injury._is_staff_immune", return_value=False),
            patch("systems.magic_actions.maintain_concentration") as maintained,
        ):
            apply_damage(self.char1, 1, emit_messages=False)
        maintained.assert_called_once_with(self.char1, 1)
