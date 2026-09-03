"""Command tests for staff effect inspection."""

from commands.default_cmdsets import CharacterCmdSet
from commands.effects import CmdEffects
from evennia.utils.test_resources import EvenniaCommandTest
from systems.effects import (EFFECT_REGISTRY, EffectDefinition, SaveRule,
                             SaveSuccess, SaveTiming, StackingPolicy)

_INSPECT_EFFECT = EffectDefinition(
    key="test.rules03.inspect",
    name="Inspected Effect",
    duration=7,
    stacking=StackingPolicy.STACK,
    max_stacks=2,
    modifiers={"armor_class": 1},
    conditions=frozenset({"inspected"}),
    save=SaveRule("Wisdom", 14, timing=SaveTiming.ON_PULSE, success=SaveSuccess.END),
    removal_categories=frozenset({"magic"}),
)
if EFFECT_REGISTRY.get(_INSPECT_EFFECT.key) is None:
    EFFECT_REGISTRY.register(_INSPECT_EFFECT)


class TestEffectCommand(EvenniaCommandTest):
    """The inspection path is registered, permissioned, and useful."""

    def test_command_is_registered_and_staff_locked(self):
        cmdset = CharacterCmdSet()
        cmdset.at_cmdset_creation()

        self.assertTrue(any(command.key == "@effects" for command in cmdset.commands))
        self.assertTrue(CmdEffects().access(self.char1, "cmd"))
        self.char2.permissions.clear()
        self.assertFalse(CmdEffects().access(self.char2, "cmd"))

    def test_empty_target_is_reported(self):
        output = self.call(CmdEffects(), f"#{self.char2.id}")

        self.assertIn("has no active effects", output)

    def test_staff_can_inspect_full_effect_state(self):
        result = self.char2.effects.add(
            _INSPECT_EFFECT.key,
            source=self.char1,
            source_key="test_spell",
            stacks=2,
            quiet=True,
        )

        output = self.call(CmdEffects(), f"#{self.char2.id}")

        self.assertIn("Inspected Effect", output)
        self.assertIn(result.effect.instance_id, output)
        self.assertIn("stacks: 2", output)
        self.assertIn("7 pulse(s) remaining", output)
        self.assertIn("test_spell", output)
        self.assertIn("inspected", output)
        self.assertIn("armor_class +2", output)
        self.assertIn("Wisdom DC 14", output)
        self.assertIn("removable by: magic", output)

    def test_invalid_storage_reports_safe_diagnostic(self):
        self.char2.db.active_effects = "broken"

        output = self.call(CmdEffects(), f"#{self.char2.id}")

        self.assertIn("effect data is invalid", output)
        self.assertNotIn("Traceback", output)
