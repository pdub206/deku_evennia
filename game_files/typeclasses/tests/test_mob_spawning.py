"""MOB-05 identity, population, and reset-placement regression coverage."""

from unittest.mock import patch

from evennia import create_object
from evennia.prototypes.prototypes import save_prototype
from evennia.prototypes.spawner import spawn
from evennia.utils.test_resources import EvenniaTest
from systems.areas import assign_area, load_area_data, room_key_of
from systems.mob_spawning import (MOBILE_SPAWN_IDENTITY_ATTRIBUTE,
                                  MobileSpawnError, mobile_population_snapshot,
                                  mobile_spawn_identity,
                                  reconcile_mobile_placement, spawn_mobile,
                                  validate_mobile_placement)


class TestMobileSpawning(EvenniaTest):
    """Every fresh mobile has durable source identity without a second timer."""

    prototype_key = "mob05_test_guard"

    def setUp(self):
        super().setUp()
        assign_area(self.room1, "alpha")
        assign_area(self.room2, "beta")
        self.room_key = room_key_of(self.room1)
        save_prototype(
            {
                "prototype_key": self.prototype_key,
                "key": "MOB-05 guard",
                "typeclass": "typeclasses.characters.Character",
                "is_player_character": False,
                "mobile_behavior_profile": "idle",
            }
        )

    def placement(self, **overrides):
        """Build a valid placement tied to the test area's stable room key."""
        return {
            "area_key": "alpha",
            "room_key": self.room_key,
            "placement_key": "yard_guard",
            "prototype_key": self.prototype_key,
            "desired": 2,
            "room_max": 3,
            "area_max": 3,
            **overrides,
        }

    def test_manual_copy_has_primitive_unmanaged_source_identity(self):
        result = spawn_mobile(self.prototype_key, self.room1)

        self.assertEqual(result.status, "created")
        identity = mobile_spawn_identity(result.npc)
        self.assertEqual(identity.prototype_key, self.prototype_key)
        self.assertFalse(identity.managed)
        self.assertEqual(
            set(result.npc.attributes.get(MOBILE_SPAWN_IDENTITY_ATTRIBUTE)),
            {
                "version",
                "prototype_key",
                "reset_owner",
                "area_key",
                "placement_key",
                "reset_room_key",
            },
        )

    def test_explicit_unpublished_npc_class_fails_before_object_creation(self):
        """An NPC source class cannot bypass the release manifest at spawn."""
        save_prototype(
            {
                "prototype_key": "mob05_unpublished_class",
                "key": "unreleased guard",
                "typeclass": "typeclasses.characters.Character",
                "is_player_character": False,
                "char_class": "Fighter",
                "level": 1,
                "mobile_behavior_profile": "idle",
            }
        )
        with patch("systems.mob_spawning.is_level_published", return_value=False):
            result = spawn_mobile("mob05_unpublished_class", self.room1)

        self.assertEqual(
            (result.status, result.reason), ("failed", "unavailable_class")
        )
        self.assertFalse(
            any(obj.key == "unreleased guard" for obj in self.room1.contents)
        )

    def test_legacy_npc_is_not_guessed_from_its_display_name(self):
        legacy = create_object(
            "typeclasses.characters.Character", key="MOB-05 guard", location=self.room1
        )
        legacy.db.is_player_character = False

        snapshot = mobile_population_snapshot("alpha")

        self.assertIsNone(mobile_spawn_identity(legacy))
        self.assertNotIn(self.prototype_key, snapshot.area_prototype_counts)

    def test_managed_survivor_counts_after_wandering_and_reset_is_idempotent(self):
        first = reconcile_mobile_placement(
            "reset_one", self.placement(), {self.room_key: self.room1}
        )
        created = [obj for obj in self.room1.contents if obj.key == "MOB-05 guard"]
        self.assertEqual(first.created, 2)
        self.assertEqual(len(created), 2)
        self.assertTrue(created[0].move_to(self.room2, quiet=True))

        snapshot = mobile_population_snapshot("alpha")
        replay = reconcile_mobile_placement(
            "reset_one", self.placement(), {self.room_key: self.room1}
        )
        later = reconcile_mobile_placement(
            "reset_two", self.placement(), {self.room_key: self.room1}
        )

        self.assertEqual(snapshot.placement_counts["yard_guard"], 2)
        self.assertEqual(replay.status, "duplicate")
        self.assertEqual(later.status, "unchanged")
        self.assertEqual(later.created, 0)

    def test_unmanaged_template_copy_applies_physical_area_ceiling_only(self):
        unmanaged = spawn(self.prototype_key)[0]
        unmanaged.db.is_player_character = False
        unmanaged.move_to(self.room1, quiet=True)

        result = reconcile_mobile_placement(
            "reset_limit",
            self.placement(desired=1, room_max=1, area_max=1),
            {self.room_key: self.room1},
        )

        self.assertEqual(result.status, "unchanged")
        self.assertEqual(result.reason, "room_maximum")
        self.assertEqual(mobile_population_snapshot("alpha").placement_counts, {})

    def test_manual_copy_counts_against_local_ceiling_but_not_a_placement(self):
        manual = spawn_mobile(self.prototype_key, self.room1).npc

        snapshot = mobile_population_snapshot("alpha")

        self.assertFalse(mobile_spawn_identity(manual).managed)
        self.assertEqual(snapshot.area_prototype_counts[self.prototype_key], 1)
        self.assertEqual(snapshot.placement_counts, {})

    def test_area_load_uses_the_same_managed_spawn_path(self):
        rooms = load_area_data(
            "gamma",
            {
                "entry": {
                    "prototype_key": "gamma_entry",
                    "key": "Gamma entry",
                    "typeclass": "typeclasses.rooms.Room",
                }
            },
            [],
            [
                {
                    "area_key": "gamma",
                    "room_key": "entry",
                    "placement_key": "entry_guard",
                    "prototype_key": self.prototype_key,
                    "desired": 1,
                    "room_max": 1,
                    "area_max": 1,
                }
            ],
        )

        guard = next(
            obj for obj in rooms["entry"].contents if obj.key == "MOB-05 guard"
        )
        identity = mobile_spawn_identity(guard)
        self.assertEqual(identity.area_key, "gamma")
        self.assertEqual(identity.placement_key, "entry_guard")

    def test_invalid_limits_and_missing_prototype_fail_before_spawning(self):
        with self.assertRaises(MobileSpawnError):
            validate_mobile_placement(self.placement(desired=4))
        with self.assertRaises(MobileSpawnError):
            validate_mobile_placement(self.placement(prototype_key="missing_mob"))
