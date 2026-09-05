"""COMBAT-05 corpse lifecycle regression coverage."""

from django.test import override_settings
from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.corpses import (can_withdraw, corpse_record, create_corpse,
                             process_corpse_pulse, withdraw)
from systems.injury import InjuryState, apply_damage
from systems.pulses import PulseEvent, PulseLane
from typeclasses.objects import Corpse


class TestCorpses(EvenniaTest):
    """Persistent bodies retain ordinary contents and enforce loot ownership."""

    def setUp(self):
        """Use a non-staff observer because EvenniaTest's first character is staff."""
        super().setUp()
        self.looter = create_object(
            "typeclasses.characters.Character", key="looter", location=self.room1
        )

    def item(self, key: str, owner):
        """Create one physical carried item through the normal typeclass path."""
        return create_object("typeclasses.objects.Item", key=key, location=owner)

    def test_creation_is_idempotent_and_unequips_every_carried_item(self):
        """One death identity creates one body and transfers actual possessions."""
        sword = self.item("sword", self.char2)
        sword.db.wear_locations = ["wield"]
        self.char2.equipment.equip(sword, "wield")
        self.char2.db.currency = 7

        corpse = create_corpse(self.char2, "death-one")
        same_corpse = create_corpse(self.char2, "death-one")

        self.assertEqual(corpse.id, same_corpse.id)
        self.assertIs(sword.location, corpse)
        self.assertIsNone(sword.db.worn_location)
        self.assertEqual(corpse_record(corpse).currency, 7)
        self.assertEqual(self.char2.db.currency, 0)

    def test_final_injury_consumes_its_death_identity_once(self):
        """COMBAT-04 final death creates the downstream corpse automatically."""
        item = self.item("dropped blade", self.char2)
        self.char2.db.is_player_character = False
        self.char2.db.hp_current = 10

        result = apply_damage(self.char2, 10, emit_messages=False)
        corpses = [
            corpse
            for corpse in Corpse.objects.filter_family()
            if corpse_record(corpse).death_id == result.death_id
        ]

        self.assertEqual(result.state, InjuryState.DEAD)
        self.assertEqual(len(corpses), 1)
        self.assertIs(item.location, corpses[0])

    def test_npc_is_public_but_pc_requires_the_exact_character(self):
        """PC ownership uses character identity rather than account identity."""
        npc_item = self.item("token", self.char2)
        self.char2.db.is_player_character = False
        npc_corpse = create_corpse(self.char2, "npc-death")
        self.assertTrue(can_withdraw(npc_corpse, self.looter))
        self.assertTrue(withdraw(npc_corpse, self.looter, npc_item).moved)

        pc_item = self.item("ring", self.char2)
        self.char2.db.is_player_character = True
        pc_corpse = create_corpse(self.char2, "pc-death")
        self.assertFalse(can_withdraw(pc_corpse, self.looter))
        denied = withdraw(pc_corpse, self.looter, pc_item)
        self.assertFalse(denied.moved)
        self.assertIs(pc_item.location, pc_corpse)
        self.assertTrue(withdraw(pc_corpse, self.char2, pc_item).moved)

    @override_settings(PC_CORPSE_DECAY_MINUTES=1)
    def test_decay_consumes_a_lane_token_once_then_spills_contents(self):
        """Expired bodies spill room loot and replaying a token does nothing."""
        item = self.item("keepsake", self.char2)
        corpse = create_corpse(self.char2, "decay-death")
        corpse_id = corpse.id

        result = process_corpse_pulse(PulseEvent(60, PulseLane.CORPSES, 1))
        replay = process_corpse_pulse(PulseEvent(60, PulseLane.CORPSES, 1))

        self.assertEqual(result.processed, 1)
        self.assertEqual(result.decayed, 1)
        self.assertEqual(replay.decayed, 0)
        self.assertIs(item.location, self.room1)
        self.assertFalse(Corpse.objects.filter(id=corpse_id).exists())

    def test_direct_corpse_movement_and_unmediated_removal_are_rejected(self):
        """The typeclass boundary protects corpses outside the player command."""
        item = self.item("proof", self.char2)
        corpse = create_corpse(self.char2, "protected-death")

        self.assertFalse(corpse.move_to(self.char1, quiet=True))
        self.assertFalse(item.move_to(self.looter, quiet=True))
        self.assertFalse(withdraw(corpse, self.looter, item).moved)
        self.assertIs(item.location, corpse)
