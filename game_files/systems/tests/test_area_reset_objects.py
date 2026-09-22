"""AREA-03D reset-object root reconciliation coverage."""

from evennia.prototypes.prototypes import save_prototype
from evennia.utils.test_resources import EvenniaTest
from systems.areas import AREA_TAG_CATEGORY, ROOM_KEY_CATEGORY
from systems.object_spawning import (
    object_population_snapshot,
    object_spawn_identity,
    reconcile_object_placement,
)


class TestAreaResetObjects(EvenniaTest):
    """Reset-created roots persist their source identity wherever they travel."""

    key = "area03d_test_stone"

    def setUp(self):
        super().setUp()
        self.room1.tags.add("alpha", category=AREA_TAG_CATEGORY)
        self.room1.tags.add("entry", category=ROOM_KEY_CATEGORY)
        self.room2.tags.add("beta", category=AREA_TAG_CATEGORY)
        self.room2.tags.add("road", category=ROOM_KEY_CATEGORY)
        save_prototype(
            {
                "prototype_key": self.key,
                "key": "AREA-03D stone",
                "typeclass": "typeclasses.objects.Item",
                "type": "item",
                "weight": 1,
            }
        )

    def _placement(self, **overrides):
        return {
            "room_key": "entry",
            "prototype_key": self.key,
            "desired": 2,
            "room_max": 3,
            "area_max": 3,
            "contents": [],
            **overrides,
        }

    def test_wandering_managed_root_satisfies_placement_without_replacement(self):
        first = reconcile_object_placement(
            "area_reset_one",
            "alpha",
            "stones",
            self._placement(),
            {"entry": self.room1},
        )
        roots = [item for item in self.room1.contents if item.key == "AREA-03D stone"]
        roots[0].move_to(self.room2, quiet=True)
        later = reconcile_object_placement(
            "area_reset_two",
            "alpha",
            "stones",
            self._placement(),
            {"entry": self.room1},
        )

        self.assertEqual(first.created, 2)
        self.assertEqual(later.created, 0)
        self.assertEqual(object_spawn_identity(roots[0]).placement_key, "stones")
        self.assertEqual(
            object_population_snapshot("alpha").placement_counts["stones"], 2
        )

    def test_manual_local_root_uses_capacity_but_does_not_satisfy_placement(self):
        from systems.encumbrance import spawn_with_capacity
        from systems.object_spawning import _prototype

        manual = spawn_with_capacity(_prototype(self.key), self.room1)[0]
        result = reconcile_object_placement(
            "area_reset_three",
            "alpha",
            "stones",
            self._placement(desired=1, room_max=1, area_max=1),
            {"entry": self.room1},
        )

        self.assertIsNone(object_spawn_identity(manual))
        self.assertEqual(result.reason, "room_maximum")
        self.assertNotIn("stones", object_population_snapshot("alpha").placement_counts)
