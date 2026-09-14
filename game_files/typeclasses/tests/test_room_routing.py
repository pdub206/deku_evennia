"""INTERACT-02C start, respawn, and recall routing coverage."""

from django.test import override_settings
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest
from commands.generic import CmdRecall
from systems.action_queue import inspect_action, process_action_pulse
from systems.areas import AREA_TAG_CATEGORY, ROOM_KEY_CATEGORY
from systems.combat import start_fight
from systems.injury import apply_damage
from systems.pulses import PulseEvent, PulseLane
from systems.room_roles import RoomRoleError, resolve_room_role, validate_room_roles
from typeclasses.rooms import Room


@override_settings(
    CHARACTER_START_ROOM="routes:start",
    COMBAT_RESPAWN_SANCTUARY="routes:sanctuary",
    RECALL_DESTINATION="routes:recall",
    INTERACTION_INTERVAL_ACTIONS=1,
)
class TestRoomRouting(EvenniaCommandTest):
    """Every route uses a unique stable reference and its own policy."""

    def setUp(self):
        super().setUp()
        self.start = self._role_room("Start", "start")
        self.sanctuary = self._role_room("Sanctuary", "sanctuary")
        self.recall = self._role_room("Recall", "recall")
        self.char1.db.is_player_character = True
        self.char1.permissions.clear()
        self.char1.db.position = "standing"

    def _role_room(self, name, key):
        room = create_object(Room, key=name)
        room.tags.add("routes", category=AREA_TAG_CATEGORY)
        room.tags.add(key, category=ROOM_KEY_CATEGORY)
        return room

    def test_distinct_and_shared_roles_resolve_without_home_fallback(self):
        roles = validate_room_roles()
        self.assertEqual(
            [role.room for role in roles], [self.start, self.sanctuary, self.recall]
        )
        self.char1.home = self.start
        self.assertIs(resolve_room_role("RECALL_DESTINATION").room, self.recall)
        with override_settings(RECALL_DESTINATION="routes:start"):
            self.assertIs(resolve_room_role("RECALL_DESTINATION").room, self.start)

    def test_missing_malformed_stale_and_duplicate_references_fail(self):
        for value in (None, "bad", "routes:missing", "routes:"):
            with override_settings(RECALL_DESTINATION=value):
                with self.assertRaises(RoomRoleError):
                    resolve_room_role("RECALL_DESTINATION")
        duplicate = self._role_room("Duplicate", "recall")
        with self.assertRaises(RoomRoleError):
            resolve_room_role("RECALL_DESTINATION")
        duplicate.delete()

    def test_recall_and_home_alias_wait_then_arrive_without_hazard(self):
        self.char1.db.hp_current = 10
        self.recall.db.room_environment = {"entry_hazard": "unknown"}
        self.call(CmdRecall(), "", "You begin recalling to safety.")
        self.assertIs(self.char1.location, self.room1)
        self.assertIsNotNone(inspect_action(self.char1))
        process_action_pulse(PulseEvent(1, PulseLane.ACTIONS, 1))
        self.assertIs(self.char1.location, self.recall)
        self.assertEqual(self.char1.db.hp_current, 10)
        self.char1.move_to(self.room1, quiet=True, move_type="forced")
        self.call(CmdRecall(), "", "You begin recalling to safety.", cmdstring="home")

    def test_request_denials_and_every_runtime_interrupt(self):
        self.char1.db.position = "sitting"
        self.call(CmdRecall(), "", "You need to stand before you can do that.")
        self.char1.db.position = "standing"
        self.room1.locks.add("recall:false()")
        self.call(
            CmdRecall(), "", "You cannot recall right now (source_denies_recall)."
        )
        self.room1.locks.remove("recall")
        self.call(CmdRecall(), "", "You begin recalling to safety.")
        apply_damage(self.char1, 1)
        self.assertIsNone(inspect_action(self.char1))
        self.call(CmdRecall(), "", "You begin recalling to safety.")
        self.char1.move_to(self.room2, quiet=True, move_type="forced")
        self.assertIsNone(inspect_action(self.char1))
        self.char1.move_to(self.room1, quiet=True, move_type="forced")
        self.call(CmdRecall(), "", "You begin recalling to safety.")
        start_fight(self.char1, self.char2)
        self.assertIsNone(inspect_action(self.char1))

    def test_destination_is_revalidated_at_execution(self):
        self.call(CmdRecall(), "", "You begin recalling to safety.")
        self.recall.db.room_policy = {"occupant_capacity": 0}
        process_action_pulse(PulseEvent(1, PulseLane.ACTIONS, 1))
        self.assertIs(self.char1.location, self.room1)
        self.assertIsNone(inspect_action(self.char1))
