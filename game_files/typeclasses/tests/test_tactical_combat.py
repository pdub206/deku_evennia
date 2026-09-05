"""Focused COMBAT-08 regression coverage for queued physical tactics."""

from unittest.mock import patch

from evennia import create_object
from evennia.server.models import ServerConfig
from evennia.utils.test_resources import EvenniaTest
from systems.attacks import AttackOutcome, AttackResult
from systems.combat import (COMBAT_CONFIG_KEY, get_target,
                            process_combat_pulse, rescue_retarget,
                            schedule_tactical_action, set_combat_action_hook,
                            start_fight)
from systems.dice import RollResult
from systems.pulses import PulseEvent, PulseLane
from systems.tactical_combat import (PRONE_EFFECT_KEY, _aim, _backstab, _bash,
                                     _kick, consume_prone_action,
                                     resolve_combat_action)
from typeclasses.characters import Character


class TestTacticalIntentStorage(EvenniaTest):
    """Tactical action persistence is replacement-safe and pulse-safe."""

    def setUp(self):
        super().setUp()
        ServerConfig.objects.conf(COMBAT_CONFIG_KEY, delete=True)
        self.char1.db.is_player_character = False
        self.char2.db.is_player_character = False
        start_fight(self.char1, self.char2)

    def test_queue_replaces_instead_of_resolving_and_survives_a_state_read(self):
        before = self.char2.stats.hp_current
        aim = schedule_tactical_action(self.char1, "aim", self.char2, location="body")
        kick = schedule_tactical_action(self.char1, "kick", self.char2)
        state = ServerConfig.objects.conf(COMBAT_CONFIG_KEY)
        intent = state["encounters"][str(aim.encounter_id)]["participants"][
            str(self.char1.id)
        ]["pending_intent"]

        self.assertTrue(aim.accepted)
        self.assertTrue(kick.changed)
        self.assertEqual(
            intent, {"kind": "tactical", "action": "kick", "target": self.char2.id}
        )
        self.assertEqual(self.char2.stats.hp_current, before)

    def test_tactical_intent_is_consumed_once_by_the_combat_pulse(self):
        schedule_tactical_action(self.char1, "aim", self.char2, location="body")
        set_combat_action_hook(resolve_combat_action)
        self.addCleanup(set_combat_action_hook, None)

        first = process_combat_pulse(PulseEvent(2, PulseLane.COMBAT, 1))
        replay = process_combat_pulse(PulseEvent(2, PulseLane.COMBAT, 1))
        state = ServerConfig.objects.conf(COMBAT_CONFIG_KEY)
        record = state["encounters"]["1"]["participants"][str(self.char1.id)]

        self.assertGreaterEqual(first.actions, 1)
        self.assertFalse(replay.processed)
        self.assertIsNone(record["pending_intent"])


class TestPhysicalTactics(EvenniaTest):
    """Each physical tactic delegates to the canonical shared services."""

    def setUp(self):
        super().setUp()
        self.char1.db.is_player_character = False
        self.char2.db.is_player_character = False
        start_fight(self.char1, self.char2)
        self.event = PulseEvent(2, PulseLane.COMBAT, 1)

    def test_aim_passes_disadvantage_and_the_selected_location_to_attack(self):
        attack = AttackResult(acted=True, accepted=True, outcome=AttackOutcome.MISS)
        with patch(
            "systems.tactical_combat.resolve_basic_attack", return_value=attack
        ) as resolver:
            result = _aim(self.char1, self.char2, self.event, {"location": "head"})

        self.assertTrue(result.acted)
        self.assertTrue(resolver.call_args.kwargs["has_disadvantage"])
        self.assertEqual(
            resolver.call_args.kwargs["location_selector"](None, None), "head"
        )

    def test_kick_uses_the_unarmed_profile_and_one_and_a_half_delay(self):
        attack = AttackResult(acted=True, accepted=True, outcome=AttackOutcome.MISS)
        with patch(
            "systems.tactical_combat.resolve_basic_attack", return_value=attack
        ) as resolver:
            result = _kick(self.char1, self.char2, self.event, {})

        profile = resolver.call_args.kwargs["profile"]
        self.assertEqual(profile.damage_dice, "1d4")
        self.assertEqual(profile.damage_type, "bludgeoning")
        self.assertEqual(result.delay_multiplier, 1.5)

    def test_backstab_requires_rogue_finesse_and_records_successful_damage_use(self):
        self.char1.db.char_class = "Rogue"
        weapon = create_object(
            "typeclasses.objects.Item",
            key="knife",
            location=self.char1,
            attributes=(
                ("type", "weapon"),
                ("subtype", "piercing"),
                ("damage", "1d4"),
                ("wear_locations", ["wield"]),
                ("worn_location", "wield"),
                ("finesse", True),
            ),
        )
        self.assertIs(self.char1.equipment.wielded_weapon, weapon)
        # The multi-participant rule exercises the ADV-04 seam without
        # recreating a second hidden-state implementation.
        ally = create_object(Character, key="Ally", location=self.room1)
        start_fight(ally, self.char2)
        attack = AttackResult(
            acted=True, accepted=True, outcome=AttackOutcome.HIT, final_damage=1
        )
        with patch(
            "systems.tactical_combat.resolve_basic_attack", return_value=attack
        ) as resolver:
            result = _backstab(self.char1, self.char2, self.event, {})

        self.assertTrue(result.accepted)
        self.assertEqual(resolver.call_args.kwargs["extra_damage_dice"], "1d6")
        self.assertEqual(self.char1.db.combat_sneak_attack_round, self.event.sequence)

    def test_bash_applies_one_prone_effect_then_consumes_one_target_action(self):
        create_object(
            "typeclasses.objects.Item",
            key="shield",
            location=self.char1,
            attributes=(
                ("type", "armor"),
                ("subtype", "shield"),
                ("wear_locations", ["shield"]),
                ("worn_location", "shield"),
            ),
        )
        rolls = iter((RollResult(20, 0, 20, 0, True), RollResult(1, 0, 1, 0, True)))
        with patch(
            "systems.tactical_combat.roll_check", side_effect=lambda *_: next(rolls)
        ):
            result = _bash(self.char1, self.char2, self.event, {})

        self.assertEqual(result.effect_applied, PRONE_EFFECT_KEY)
        self.assertTrue(self.char2.effects.has(PRONE_EFFECT_KEY))
        self.assertTrue(consume_prone_action(self.char2))
        self.assertFalse(self.char2.effects.has(PRONE_EFFECT_KEY))

    def test_rescue_primitive_retargets_only_the_requested_enemy(self):
        ally = create_object(Character, key="Ally", location=self.room1)
        start_fight(ally, self.char2)
        from systems.combat import change_target

        change_target(self.char2, ally)
        rolls = iter((RollResult(20, 0, 20, 0, True), RollResult(1, 0, 1, 0, True)))
        with patch("systems.dice.roll_check", side_effect=lambda *_: next(rolls)):
            result = rescue_retarget(self.char1, ally, self.char2)

        self.assertTrue(result.changed)
        self.assertIs(get_target(self.char2), self.char1)
        self.assertIsNotNone(get_target(ally))
