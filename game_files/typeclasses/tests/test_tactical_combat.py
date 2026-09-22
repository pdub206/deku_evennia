"""Focused COMBAT-08 regression coverage for queued physical tactics."""

from types import SimpleNamespace
from unittest.mock import patch

from evennia import create_object
from evennia.server.models import ServerConfig
from evennia.utils.test_resources import EvenniaTest
from systems.attacks import AttackOutcome, AttackResult
from systems.combat import (
    COMBAT_CONFIG_KEY,
    assist_fight,
    get_target,
    process_combat_pulse,
    rescue_retarget,
    schedule_tactical_action,
    set_combat_action_hook,
    start_fight,
)
from systems.dice import RollResult
from systems.pulses import PulseEvent, PulseLane
from systems.tactical_combat import (
    HIDDEN_EFFECT_KEY,
    PRONE_EFFECT_KEY,
    STEADY_AIM_EFFECT_KEY,
    _aim,
    _backstab,
    _bash,
    _hide,
    _kick,
    _steady_aim,
    consume_prone_action,
    execute_tactical_intent,
    resolve_combat_action,
)
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

    def test_tactical_intent_cannot_target_an_ally_after_joining_a_mobile(self):
        """Two combatants attacking one target never become tactical PvP targets."""
        ally = create_object(Character, key="Ally", location=self.room1)
        start_fight(ally, self.char2)

        result = schedule_tactical_action(self.char1, "kick", ally)

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "invalid_target")


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
        contest = SimpleNamespace(
            actor=RollResult(20, 0, 20, 0, True),
            opponent=RollResult(1, 0, 1, 0, True),
            actor_wins=True,
        )
        with patch(
            "systems.tactical_combat.resolve_opposed_check", return_value=contest
        ):
            result = _bash(self.char1, self.char2, self.event, {})

        self.assertEqual(result.effect_applied, PRONE_EFFECT_KEY)
        self.assertTrue(self.char2.effects.has(PRONE_EFFECT_KEY))
        self.assertTrue(consume_prone_action(self.char2))
        self.assertFalse(self.char2.effects.has(PRONE_EFFECT_KEY))

    def test_tactical_mind_spends_second_wind_only_when_it_changes_bash(self):
        """The explicit intent applies 1d10 after failure and pays on success."""
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
        self.char1.db.char_class = "Fighter"
        self.char1.db.level = 2
        self.char1.db.class_progression = {
            "grants": ["fighter.second_wind", "fighter.tactical_mind"]
        }
        actor_roll = RollResult(5, 0, 5, 5, True)
        defender_roll = RollResult(10, 0, 10, 5, True)
        contest = SimpleNamespace(
            actor=SimpleNamespace(total=actor_roll.total),
            opponent=SimpleNamespace(total=defender_roll.total),
            actor_wins=False,
        )
        with (
            patch(
                "systems.tactical_combat.resolve_opposed_check", return_value=contest
            ),
            patch("systems.dice.roll", return_value=6),
        ):
            result = _bash(self.char1, self.char2, self.event, {"tactical_mind": True})

        self.assertTrue(result.accepted)
        from systems.magic_resources import resource_current

        self.assertEqual(resource_current(self.char1, "fighter.second_wind"), 1)

    def test_hide_uses_adv04_and_backstab_consumes_observer_specific_state(self):
        """Stealth succeeds against one target and is spent by its backstab."""
        self.char1.db.char_class = "Rogue"
        create_object(
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
        contest = SimpleNamespace(
            actor=RollResult(20, 0, 20, 0, True),
            opponent=RollResult(10, 0, 10, 0, False),
            actor_wins=True,
        )
        with patch(
            "systems.tactical_combat.stealth_against_passive", return_value=contest
        ) as check:
            hidden = _hide(self.char1, self.char2, self.event, {})

        self.assertTrue(hidden.accepted)
        self.assertTrue(self.char1.effects.has(HIDDEN_EFFECT_KEY))
        check.assert_called_once_with(self.char1, self.char2)

        attack = AttackResult(acted=True, accepted=True, outcome=AttackOutcome.MISS)
        with patch("systems.tactical_combat.resolve_basic_attack", return_value=attack):
            _backstab(self.char1, self.char2, self.event, {})
        self.assertFalse(self.char1.effects.has(HIDDEN_EFFECT_KEY))

    def test_steady_aim_requires_its_grant_and_prepares_one_advantage(self):
        """The Rogue feature creates one combat-scoped, non-stacking effect."""
        denied = _steady_aim(self.char1, self.char2, self.event, {})
        self.assertEqual(denied.reason, "feature_required")
        self.char1.db.class_progression = {"grants": ["rogue.steady_aim"]}

        with patch("systems.combat.accelerate_next_action") as accelerate:
            applied = _steady_aim(self.char1, self.char2, self.event, {})

        self.assertTrue(applied.accepted)
        self.assertEqual(applied.effect_applied, STEADY_AIM_EFFECT_KEY)
        self.assertTrue(self.char1.effects.has(STEADY_AIM_EFFECT_KEY))
        accelerate.assert_called_once_with(self.char1)

    def test_cunning_action_accelerates_a_rogue_hide_without_replaying_it(self):
        """The alpha Bonus Action adapter advances cadence after one hide result."""
        self.char1.db.class_progression = {"grants": ["rogue.cunning_action"]}
        contest = SimpleNamespace(
            actor=RollResult(20, 0, 20, 0, True),
            opponent=RollResult(10, 0, 10, 0, False),
            actor_wins=True,
        )
        with (
            patch(
                "systems.tactical_combat.stealth_against_passive", return_value=contest
            ),
            patch("systems.combat.accelerate_next_action") as accelerate,
        ):
            result = _hide(self.char1, self.char2, self.event, {})

        self.assertTrue(result.accepted)
        accelerate.assert_called_once_with(self.char1)

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

    def test_assist_joins_an_ally_side_without_an_immediate_attack(self):
        """Assist preserves the caller's normal first-ready-action cadence."""
        from systems import groups

        ally = create_object(Character, key="Ally", location=self.room1)
        groups.invite(self.char1, ally)
        groups.accept(ally, self.char1)
        start_fight(ally, self.char2)
        before = self.char2.stats.hp_current

        result = assist_fight(self.char1, ally)

        self.assertTrue(result.accepted)
        self.assertIs(get_target(self.char1), self.char2)
        self.assertEqual(self.char2.stats.hp_current, before)

    def test_rescue_intent_retargets_only_after_its_contest(self):
        """Rescue stores both target ids and has no attack or damage result."""
        ally = create_object(Character, key="Ally", location=self.room1)
        start_fight(ally, self.char2)
        assist_fight(self.char1, ally)
        from systems.combat import change_target

        change_target(self.char2, ally)
        scheduled = schedule_tactical_action(
            self.char1, "rescue", self.char2, protected=ally.id
        )
        intent = ServerConfig.objects.conf(COMBAT_CONFIG_KEY)["encounters"][
            str(scheduled.encounter_id)
        ]["participants"][str(self.char1.id)]["pending_intent"]
        rolls = iter((RollResult(20, 0, 20, 0, True), RollResult(1, 0, 1, 0, True)))

        with patch("systems.dice.roll_check", side_effect=lambda *_: next(rolls)):
            result = execute_tactical_intent(self.char1, self.char2, self.event, intent)

        self.assertTrue(result.retargeted)
        self.assertIs(get_target(self.char2), self.char1)
