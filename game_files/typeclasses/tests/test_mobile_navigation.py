"""MOB-04 regression coverage for one-step mobile routing."""

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.areas import assign_area
from systems.combat import start_fight
from systems.mobile_navigation import (
    NavigationRequest,
    begin_pursuit,
    execute_navigation,
    navigation_state,
    wander,
)
from systems.mobile_policy import default_mobile_policy, set_mobile_policy
from systems.mobiles import process_mobile_pulse, set_mobile_profile
from systems.pulses import PulseEvent, PulseLane
from typeclasses.characters import Character
from typeclasses.exits import Exit
from typeclasses.rooms import Room


def _event(token: int) -> PulseEvent:
    """Make a valid deterministic mobiles-lane token."""
    return PulseEvent(token, PulseLane.MOBILES, token)


class TestMobileNavigation(EvenniaTest):
    """Navigation uses ordinary exits and persists only bounded route intent."""

    def setUp(self):
        super().setUp()
        self.npc = create_object(Character, key="Wanderer", location=self.room1)
        self.npc.db.is_player_character = False
        self.target = create_object(Character, key="Target", location=self.room1)
        self.target.db.is_player_character = True
        self.north = create_object(
            Exit, key="north", location=self.room1, destination=self.room2
        )

    def test_wander_profile_moves_one_exit_once_for_one_mobile_token(self):
        set_mobile_profile(self.npc, "wander")

        result = process_mobile_pulse(_event(1), mobiles=(self.npc,))
        duplicate = process_mobile_pulse(_event(1), mobiles=(self.npc,))

        self.assertEqual(result.outcomes[0].status, "acted")
        self.assertEqual(self.npc.location, self.room2)
        self.assertEqual(duplicate.outcomes[0].reason, "duplicate_token")
        self.assertEqual(navigation_state(self.npc)["last_successful_token"], 1)

    def test_wandering_rejects_sentinel_locked_hidden_and_forbidden_routes(self):
        set_mobile_policy(self.npc, {**default_mobile_policy(), "sentinel": True})
        self.assertEqual(wander(self.npc, 1).reason, "sentinel")
        set_mobile_policy(self.npc, default_mobile_policy())
        for exit_obj in self.room1.exits:
            exit_obj.attributes.add("hidden", True)
        self.assertEqual(wander(self.npc, 1).reason, "no_route")
        for exit_obj in self.room1.exits:
            exit_obj.attributes.add("hidden", False)
            exit_obj.destination.attributes.add("no_mobiles", True)
        self.room2.attributes.add("no_mobiles", True)
        self.assertEqual(wander(self.npc, 1).reason, "no_route")

    def test_area_constraint_requires_one_matching_authored_area(self):
        set_mobile_policy(self.npc, {**default_mobile_policy(), "stay_in_area": True})
        self.assertEqual(wander(self.npc, 1).reason, "no_route")
        assign_area(self.room1, "alpha")
        assign_area(self.room2, "beta")
        self.assertEqual(wander(self.npc, 1).reason, "no_route")
        assign_area(self.room2, "alpha")
        self.assertEqual(wander(self.npc, 1).status, "moved")

    def test_revalidation_prevents_duplicate_or_stale_direct_traversal(self):
        first = execute_navigation(NavigationRequest("wander", self.npc, 1), self.north)
        repeated = execute_navigation(
            NavigationRequest("wander", self.npc, 1), self.north
        )

        self.assertEqual(first.status, "moved")
        self.assertEqual(repeated.reason, "duplicate_token")

    def test_wander_selector_can_inject_a_stable_legal_choice(self):
        alternate = create_object(
            Exit, key="south", location=self.room1, destination=self.room2
        )

        result = wander(self.npc, 1, selector=lambda exits: alternate)

        self.assertEqual(result.status, "moved")
        self.assertEqual(result.exit_id, alternate.id)

    def test_pursuit_follows_one_shortest_step_then_rejoins_without_attacking(self):
        room3 = create_object(Room, key="Third room")
        create_object(Exit, key="east", location=self.room2, destination=room3)
        create_object(Exit, key="long", location=self.room1, destination=room3)
        self.assertTrue(start_fight(self.npc, self.target).accepted)
        self.assertTrue(self.target.move_to(room3, quiet=True, move_type="teleport"))

        result = process_mobile_pulse(_event(1), mobiles=(self.npc,))

        self.assertEqual(result.outcomes[0].action_key, "pursuit")
        self.assertEqual(self.npc.location, room3)
        self.assertIsNone(navigation_state(self.npc)["pursuit"])
        # Arrival can create an encounter, but its combat action is deferred to
        # the normal combat lane rather than granted by the route service.
        self.assertTrue(start_fight(self.npc, self.target).accepted)

    def test_malformed_pursuit_data_is_not_accepted(self):
        self.assertFalse(begin_pursuit(self.npc, self.target, step_limit=99))
