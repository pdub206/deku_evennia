"""AREA-03C integration coverage for MOB-05 reset reconciliation."""

from evennia.prototypes.prototypes import save_prototype
from evennia.utils.test_resources import EvenniaTest
from systems.area_resets import reconcile_mobile_resets
from systems.areas import AREA_TAG_CATEGORY, ROOM_KEY_CATEGORY, compile_area_load_plan
from systems.mob_spawning import mobile_population_snapshot


class TestAreaResetMobiles(EvenniaTest):
    """AREA-03C supplies scheduling identity, while MOB-05 owns population."""

    prototype_key = "area03c_test_guard"

    def setUp(self):
        super().setUp()
        self._tag_room(self.room1, "alpha", "entry")
        self._tag_room(self.room2, "beta", "road")
        save_prototype(
            {
                "prototype_key": self.prototype_key,
                "key": "AREA-03C guard",
                "typeclass": "typeclasses.characters.Character",
                "is_player_character": False,
                "mobile_behavior_profile": "idle",
            }
        )

    @staticmethod
    def _tag_room(room, area, key):
        for category in (AREA_TAG_CATEGORY, ROOM_KEY_CATEGORY):
            for old in room.tags.get(category=category, return_list=True):
                room.tags.remove(old, category=category)
        room.tags.add(area, category=AREA_TAG_CATEGORY)
        room.tags.add(key, category=ROOM_KEY_CATEGORY)

    @staticmethod
    def _room(name):
        return {
            "name": name,
            "description": "",
            "extra_descriptions": [],
            "sector": "inside",
            "policy": {},
            "environment": {},
            "weather_profile": None,
        }

    def _plan(self, placements, rooms=None):
        rooms = rooms or {"entry": self._room("Entry")}
        manifest = {
            "key": "alpha",
            "display_name": "Alpha",
            "schema_version": 1,
            "dependencies": [],
            "credits": [],
            "srd_references": [],
            "reset_policy": "always",
            "lifespan_pulses": 1,
            "rooms": rooms,
            "exits": {},
            "mobiles": placements,
            "objects": {},
        }
        return compile_area_load_plan(
            manifests={"alpha": manifest},
            prototype_catalogs={
                "mobiles": {
                    self.prototype_key: {
                        "prototype_key": self.prototype_key,
                        "typeclass": "typeclasses.characters.Character",
                    }
                }
            },
        )

    def _placement(self, key="entry_guard", room="entry", desired=2):
        return {
            "area_key": "alpha",
            "room_key": room,
            "placement_key": key,
            "prototype_key": self.prototype_key,
            "desired": desired,
            "room_max": 3,
            "area_max": 3,
        }

    def test_reset_uses_mob05_identity_and_never_replaces_wandering_survivors(self):
        plan = self._plan([self._placement()])

        reconcile_mobile_resets("alpha", "area-reset:alpha:1", plan)
        guards = [
            item
            for item in self.room1.contents
            if item.attributes.get("is_player_character") is False
        ]
        guards[0].move_to(self.room2, quiet=True)
        reconcile_mobile_resets("alpha", "area-reset:alpha:2", plan)

        population = mobile_population_snapshot("alpha")
        self.assertEqual(len(guards), 2)
        self.assertEqual(population.placement_counts["entry_guard"], 2)
        self.assertEqual(
            len(
                [
                    item
                    for item in self.room1.contents
                    if item.attributes.get("is_player_character") is False
                ]
            ),
            1,
        )

    def test_one_missing_live_room_does_not_starve_another_placement(self):
        plan = self._plan(
            [self._placement(), self._placement("lost_guard", "missing", 1)],
            {"entry": self._room("Entry"), "missing": self._room("Missing")},
        )

        reconcile_mobile_resets("alpha", "area-reset:alpha:3", plan)

        population = mobile_population_snapshot("alpha")
        self.assertEqual(population.placement_counts["entry_guard"], 2)
        self.assertNotIn("lost_guard", population.placement_counts)
