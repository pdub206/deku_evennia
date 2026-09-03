"""
Tests for the unified build command and the area export/import round-trip.

Run from the game/ directory:
    evennia test --settings settings.py commands.tests.test_building
"""

import importlib.util
import tempfile
from unittest.mock import MagicMock

from commands.building import (_BUILD_PROMPT, CmdAreas, CmdBuild, CmdBuildArea,
                               CmdBuildDel, CmdBuildDig, CmdBuildDone,
                               CmdBuildFields, CmdBuildSet, CmdItems,
                               CmdLoadArea, CmdNpcs, CmdRooms,
                               _enter_build_mode, _exit_build_mode)
from commands.command import CmdNoInput
from commands.default_cmdsets import CharacterCmdSet
from django.conf import settings
from evennia import create_object
from evennia.prototypes.prototypes import save_prototype, search_prototype
from evennia.prototypes.spawner import spawn
from evennia.utils.test_resources import EvenniaCommandTest
from evennia.utils.utils import inherits_from
from systems.areas import build_area_data, export_area, load_area_data
from world.build_schema import ITEM_TYPES, schema_for_prototype


def _proto(key):
    """Return the saved prototype (flattened) with this exact key, or None.

    Evennia stores arbitrary fields under an ``attrs`` list; flatten them back to
    plain keys so tests can read them the way the editor does.
    """
    for proto in search_prototype(key):
        if proto.get("prototype_key") == key:
            flat = {k: v for k, v in proto.items() if k != "attrs"}
            for attr in proto.get("attrs", []):
                flat[attr[0]] = attr[1]
            return flat
    return None


class TestBuildAccess(EvenniaCommandTest):
    """The build tools are lock-gated to Builder permission."""

    def test_builder_can_access(self):
        # char1 is given Developer in the test base, which outranks Builder.
        self.assertTrue(CmdBuild().access(self.char1, "cmd"))
        self.assertTrue(CmdLoadArea().access(self.char1, "cmd"))

    def test_plain_player_denied(self):
        self.char2.permissions.clear()
        self.assertFalse(CmdBuild().access(self.char2, "cmd"))
        self.assertFalse(CmdLoadArea().access(self.char2, "cmd"))


class TestBuildEditing(EvenniaCommandTest):
    """Entering the edit context and setting validated fields."""

    def setUp(self):
        super().setUp()
        self.char1.permissions.add("Builder")
        # Enter the editing context bound to room1.
        self.call(CmdBuild(), "here")

    def test_set_desc_and_name(self):
        self.call(CmdBuildSet(), "desc A quiet, dusty hall.")
        self.assertEqual(self.room1.db.desc, "A quiet, dusty hall.")
        self.call(CmdBuildSet(), "name The Quiet Hall")
        self.assertEqual(self.room1.key, "The Quiet Hall")

    def test_fields_lists_room_fields(self):
        out = self.call(CmdBuildFields(), "")
        for field_name in ("name", "desc", "area"):
            self.assertIn(field_name, out)

    def test_unknown_field_rejected(self):
        self.call(CmdBuildSet(), "bogus whatever", "Unknown field 'bogus'")

    def test_bad_value_rejected(self):
        # Punctuation-only slugifies to empty and must be refused, not stored.
        self.call(CmdBuildSet(), "area @@@", "Invalid value for 'area'")
        self.assertFalse(self.room1.tags.get(category="area", return_list=True))


class TestBuildDig(EvenniaCommandTest):
    """Digging creates a connected room with two-way exits."""

    def test_dig_creates_two_way_exits(self):
        self.char1.permissions.add("Builder")
        self.call(CmdBuild(), "here")
        self.call(CmdBuildDig(), "north = Armory")

        north = [ex for ex in self.room1.exits if ex.key == "north"]
        self.assertEqual(len(north), 1)
        armory = north[0].destination
        self.assertEqual(armory.key, "Armory")
        self.assertIn("n", north[0].aliases.all())

        south = [ex for ex in armory.exits if ex.key == "south"]
        self.assertEqual(len(south), 1)
        self.assertEqual(south[0].destination, self.room1)


class TestBuildDelete(EvenniaCommandTest):
    """Deletion requires a second confirming 'del'."""

    def test_two_step_delete(self):
        self.char1.permissions.add("Builder")
        victim = create_object(settings.BASE_ROOM_TYPECLASS, key="Doomed")
        self.call(CmdBuild(), f"#{victim.id}")

        # First del only arms the confirmation.
        self.call(CmdBuildDel(), "", "Delete Doomed?")
        self.assertTrue(victim.pk)

        # Second del actually deletes and drops the editing context.
        self.call(CmdBuildDel(), "")
        self.assertFalse(victim.pk)


class TestAreaRoundTrip(EvenniaCommandTest):
    """build -> export-data -> load reproduces rooms and the exit graph."""

    def test_roundtrip_rebuilds_graph(self):
        self.char1.permissions.add("Builder")
        self.call(CmdBuild(), "here")
        self.call(CmdBuildArea(), "testarea")
        self.call(CmdBuildDig(), "north = Armory")

        rooms, exits = build_area_data("testarea")
        # room1's key "Room" slugs to "room"; the dug room to "armory".
        self.assertIn("room", rooms)
        self.assertIn("armory", rooms)
        edges = {(frm, direction, to) for (frm, direction, to, _attrs) in exits}
        self.assertIn(("room", "north", "armory"), edges)
        self.assertIn(("armory", "south", "room"), edges)

        # Load the captured data under a *new* area name to force fresh spawns.
        loaded = load_area_data("imported", rooms, exits)
        self.assertEqual(len(loaded), 2)
        new_room, new_armory = loaded["room"], loaded["armory"]
        self.assertNotEqual(new_room, self.room1)
        # Description carried through the prototype.
        self.assertEqual(new_room.db.desc, self.room1.db.desc)
        # Exit graph rebuilt by key reference, not dbref.
        self.assertTrue(
            any(
                ex.key == "north" and ex.destination == new_armory
                for ex in new_room.exits
            )
        )
        self.assertTrue(
            any(
                ex.key == "south" and ex.destination == new_room
                for ex in new_armory.exits
            )
        )

    def test_load_is_idempotent(self):
        self.char1.permissions.add("Builder")
        self.call(CmdBuild(), "here")
        self.call(CmdBuildArea(), "testarea")
        self.call(CmdBuildDig(), "north = Armory")
        rooms, exits = build_area_data("testarea")

        first = load_area_data("imported", rooms, exits)
        second = load_area_data("imported", rooms, exits)
        # Same room objects reused, and no duplicate exits created.
        self.assertEqual(first["room"], second["room"])
        north_exits = [ex for ex in second["room"].exits if ex.key == "north"]
        self.assertEqual(len(north_exits), 1)


class TestAreaExportFile(EvenniaCommandTest):
    """export_area writes a valid, importable area module."""

    def test_export_writes_importable_module(self):
        self.char1.permissions.add("Builder")
        self.call(CmdBuild(), "here")
        self.call(CmdBuildArea(), "tmptest")
        self.call(CmdBuildDig(), "east = Cellar")

        with tempfile.TemporaryDirectory() as tmpdir:
            path, rooms, exits = export_area("tmptest", directory=tmpdir)

            spec = importlib.util.spec_from_file_location("area_under_test", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            self.assertIn("room", module.ROOMS)
            self.assertIn("cellar", module.ROOMS)
            edges = {(frm, direction, to) for (frm, direction, to, _a) in module.EXITS}
            self.assertIn(("room", "east", "cellar"), edges)
            self.assertIn(("cellar", "west", "room"), edges)


class TestEditExitRedirect(EvenniaCommandTest):
    """'edit <direction>' binds the room the exit leads to, not the exit."""

    def test_edit_direction_edits_destination_room(self):
        self.char1.permissions.add("Builder")
        self.call(CmdBuild(), "here")
        self.call(CmdBuildDig(), "north = Armory")

        # 'edit north' finds the north exit and should redirect to Armory.
        self.call(CmdBuild(), "north")
        target = self.char1.ndb._build_target
        self.assertEqual(target.key, "Armory")
        self.assertIsNone(target.destination)  # it's the room, not the exit


class TestRoomListing(EvenniaCommandTest):
    """The areas/rooms browse commands surface what's been built."""

    def setUp(self):
        super().setUp()
        self.char1.permissions.add("Builder")
        self.call(CmdBuild(), "here")
        self.call(CmdBuildArea(), "testarea")
        self.call(CmdBuildDig(), "north = Armory")

    def test_rooms_lists_area_members(self):
        out = self.call(CmdRooms(), "testarea")
        self.assertIn("room", out)  # room1's key
        self.assertIn("armory", out)  # the dug room's key

    def test_rooms_no_arg_uses_current_area(self):
        # char1 is standing in room1, which is in 'testarea'.
        out = self.call(CmdRooms(), "")
        self.assertIn("testarea", out)  # the header names the current area
        self.assertIn("room", out)
        self.assertIn("armory", out)

    def test_rooms_no_arg_without_area(self):
        # Stand in a room that has no area assigned.
        bare = create_object(settings.BASE_ROOM_TYPECLASS, key="Bare")
        self.char1.location = bare
        self.call(CmdRooms(), "", "This room has no area assigned yet.")

    def test_rooms_unknown_area_reports_known(self):
        self.call(CmdRooms(), "nowhere", "No area")

    def test_areas_lists_area_with_count(self):
        out = self.call(CmdAreas(), "")
        self.assertIn("testarea", out)
        self.assertIn("2 room(s)", out)


class TestEditNew(EvenniaCommandTest):
    """'edit new room' creates a standalone room and teleports the builder in."""

    def test_edit_new_room_creates_and_teleports(self):
        self.char1.permissions.add("Builder")
        origin = self.char1.location
        self.call(CmdBuild(), "new room Hidden Vault")

        target = self.char1.ndb._build_target
        self.assertEqual(target.key, "Hidden Vault")
        self.assertTrue(inherits_from(target, "evennia.objects.objects.DefaultRoom"))
        # The builder is moved inside the new room...
        self.assertEqual(self.char1.location, target)
        self.assertNotEqual(self.char1.location, origin)
        # ...and the room is genuinely standalone (no exits in or out).
        self.assertEqual(list(target.exits), [])

    def test_edit_new_room_without_name_uses_default(self):
        self.char1.permissions.add("Builder")
        self.call(CmdBuild(), "new room")
        self.assertEqual(self.char1.ndb._build_target.key, "An Unnamed Room")

    def test_edit_new_requires_a_type_keyword(self):
        # A bare name (no room/item keyword) is rejected, not silently a room.
        self.char1.permissions.add("Builder")
        self.call(CmdBuild(), "new Misty Cave", "Usage: edit new")
        self.assertIsNone(self.char1.ndb._build_target)

    def test_edit_new_bare_shows_usage(self):
        self.char1.permissions.add("Builder")
        self.call(CmdBuild(), "new", "Usage: edit new")


class TestEditNewItem(EvenniaCommandTest):
    """'edit new item' authors an item *template* (prototype), not a world object."""

    ITEM = "typeclasses.objects.Item"

    def setUp(self):
        super().setUp()
        self.char1.permissions.add("Builder")

    def test_edit_new_item_creates_prototype(self):
        from typeclasses.objects import Item

        self.call(CmdBuild(), "new item Iron Sword")
        proto = self.char1.ndb._build_target
        self.assertIsInstance(proto, dict)
        self.assertEqual(proto["prototype_key"], "iron_sword")
        self.assertEqual(proto["key"], "Iron Sword")
        self.assertEqual(proto["typeclass"], self.ITEM)
        # A template, not a placed object...
        self.assertEqual(Item.objects.all_family().count(), 0)
        # ...that persisted immediately.
        self.assertIsNotNone(_proto("iron_sword"))

    def test_defaults_and_fields(self):
        self.call(CmdBuild(), "new item Rock")
        proto = self.char1.ndb._build_target
        self.assertEqual(proto["weight"], 0.0)
        self.assertEqual(proto["value"], 0)
        out = self.call(CmdBuildFields(), "")
        for field_name in (
            "name",
            "desc",
            "weight",
            "value",
            "wear_locations",
            "type",
        ):
            self.assertIn(field_name, out)

    def test_set_wear_locations_persists_to_prototype(self):
        self.call(CmdBuild(), "new item Bracelet")

        self.call(CmdBuildSet(), "wear_locations left wrist, right wrist")

        self.assertEqual(
            self.char1.ndb._build_target["wear_locations"],
            ["left wrist", "right wrist"],
        )
        self.assertEqual(
            _proto("bracelet")["wear_locations"], ["left wrist", "right wrist"]
        )

    def test_invalid_wear_location_rejected(self):
        self.call(CmdBuild(), "new item Bracelet")

        self.call(
            CmdBuildSet(),
            "wear_locations nose",
            "Invalid value for 'wear_locations'",
        )

        self.assertEqual(self.char1.ndb._build_target["wear_locations"], [])

    def test_set_persists_to_prototype(self):
        self.call(CmdBuild(), "new item Rock")
        self.call(CmdBuildSet(), "weight 2.5")
        self.call(CmdBuildSet(), "value 12")
        self.assertEqual(self.char1.ndb._build_target["weight"], 2.5)
        saved = _proto("rock")
        self.assertEqual(saved["weight"], 2.5)
        self.assertEqual(saved["value"], 12)

    def test_negative_weight_rejected(self):
        self.call(CmdBuild(), "new item Rock")
        self.call(CmdBuildSet(), "weight -3", "Invalid value for 'weight'")
        self.assertEqual(self.char1.ndb._build_target["weight"], 0.0)

    def test_new_item_requires_name(self):
        self.call(CmdBuild(), "new item", "Usage: edit new item")

    def test_duplicate_prototype_rejected(self):
        self.call(CmdBuild(), "new item Rock")
        self.call(CmdBuild(), "new item Rock", "An item prototype")

    def test_edit_existing_prototype(self):
        self.call(CmdBuild(), "new item Rock")
        self.call(CmdBuildSet(), "value 5")
        self.call(CmdBuildDone(), "")
        self.call(CmdBuild(), "item Rock")
        proto = self.char1.ndb._build_target
        self.assertEqual(proto["prototype_key"], "rock")
        self.assertEqual(proto["value"], 5)

    def test_edit_missing_prototype(self):
        self.call(CmdBuild(), "item ghost", "No item prototype")


class TestItemType(EvenniaCommandTest):
    """'set type <kind>' reshapes an item *prototype's* fields dynamically."""

    def setUp(self):
        super().setUp()
        self.char1.permissions.add("Builder")
        self.call(CmdBuild(), "new item Thing")
        self.proto = self.char1.ndb._build_target  # the prototype dict being edited

    def test_type_fields_hidden_until_type_set(self):
        fields = set(schema_for_prototype(self.proto))
        self.assertIn("type", fields)  # the type field itself is always offered
        self.assertEqual(fields & {"damage", "subtype", "base_ac", "capacity"}, set())

    def test_set_type_weapon_reveals_fields(self):
        self.call(CmdBuildSet(), "type weapon")
        self.assertEqual(self.proto["type"], "weapon")
        out = self.call(CmdBuildFields(), "")
        self.assertIn("damage", out)
        self.assertIn("subtype", out)

    def test_weapon_damage_and_subtype(self):
        self.call(CmdBuildSet(), "type weapon")
        self.call(CmdBuildSet(), "damage 1d8")
        self.call(CmdBuildSet(), "subtype slashing")
        self.assertEqual(self.proto["damage"], "1d8")
        self.assertEqual(self.proto["subtype"], "slashing")

    def test_invalid_damage_rejected(self):
        self.call(CmdBuildSet(), "type weapon")
        self.call(CmdBuildSet(), "damage sharp", "Invalid value for 'damage'")
        self.assertIsNone(self.proto.get("damage"))

    def test_subtype_validated_per_type(self):
        self.call(CmdBuildSet(), "type weapon")
        # 'light' is an armor value, not valid for a weapon's subtype.
        self.call(CmdBuildSet(), "subtype light", "Invalid value for 'subtype'")
        self.call(CmdBuildSet(), "subtype piercing")
        self.assertEqual(self.proto["subtype"], "piercing")

    def test_set_type_armor_fields(self):
        self.call(CmdBuildSet(), "type armor")
        self.call(CmdBuildSet(), "base_ac 16")
        self.call(CmdBuildSet(), "subtype heavy")
        self.assertEqual(self.proto["base_ac"], 16)
        self.assertEqual(self.proto["subtype"], "heavy")
        self.call(CmdBuildSet(), "subtype slashing", "Invalid value for 'subtype'")

    def test_changing_type_clears_old_fields(self):
        self.call(CmdBuildSet(), "type weapon")
        self.call(CmdBuildSet(), "subtype slashing")
        self.call(CmdBuildSet(), "damage 2d6")
        # Switching type must drop the weapon's stale damage/subtype keys.
        self.call(CmdBuildSet(), "type armor")
        self.assertIsNone(self.proto.get("subtype"))
        self.assertIsNone(self.proto.get("damage"))
        out = self.call(CmdBuildFields(), "")
        self.assertIn("base_ac", out)
        self.assertNotIn("damage", out)

    def test_set_type_container_capacity(self):
        self.call(CmdBuildSet(), "type container")
        self.call(CmdBuildSet(), "capacity 30")
        self.assertEqual(self.proto["capacity"], 30.0)

    def test_classic_diku_item_types_are_available(self):
        classic_types = (
            "light",
            "scroll",
            "wand",
            "staff",
            "weapon",
            "furniture",
            "treasure",
            "armor",
            "potion",
            "worn",
            "other",
            "trash",
            "note",
            "drinkcon",
            "key",
            "food",
            "money",
            "pen",
            "boat",
            "fountain",
        )
        self.assertTrue(set(classic_types).issubset(ITEM_TYPES))
        for item_type in classic_types:
            with self.subTest(item_type=item_type):
                self.call(CmdBuildSet(), f"type {item_type}")
                self.assertEqual(self.proto["type"], item_type)

    def test_classification_only_type_has_shared_fields(self):
        self.call(CmdBuildSet(), "type wand")
        fields = set(schema_for_prototype(self.proto))
        self.assertEqual(
            fields,
            {"name", "desc", "weight", "value", "wear_locations", "type"},
        )

    def test_set_type_none_reverts_to_generic(self):
        self.call(CmdBuildSet(), "type weapon")
        self.call(CmdBuildSet(), "type none")
        self.assertIsNone(self.proto.get("type"))
        out = self.call(CmdBuildFields(), "")
        self.assertNotIn("damage", out)

    def test_invalid_type_rejected(self):
        self.call(CmdBuildSet(), "type vehicle", "Invalid value for 'type'")
        self.assertIsNone(self.proto.get("type"))

    def test_edit_new_kind_keyword_is_not_creation(self):
        # 'weapon' is no longer a creation keyword; only room/item are.
        self.call(CmdBuild(), "new weapon Sword", "Usage: edit new")


class TestItemsListing(EvenniaCommandTest):
    """'items' catalogues item *templates* (prototypes), grouped/filtered by type."""

    ITEM = "typeclasses.objects.Item"

    def setUp(self):
        super().setUp()
        self.char1.permissions.add("Builder")
        save_prototype(
            {
                "prototype_key": "sword",
                "key": "Sword",
                "typeclass": self.ITEM,
                "type": "weapon",
            }
        )
        save_prototype({"prototype_key": "bag", "key": "Bag", "typeclass": self.ITEM})

    def test_items_lists_all_with_type_groups(self):
        out = self.call(CmdItems(), "")
        self.assertIn("sword", out)
        self.assertIn("bag", out)
        self.assertIn("weapon", out)  # the group header

    def test_items_filtered_by_type(self):
        out = self.call(CmdItems(), "weapon")
        self.assertIn("sword", out)
        self.assertNotIn("bag", out)

    def test_items_generic_filter(self):
        out = self.call(CmdItems(), "item")
        self.assertIn("bag", out)
        self.assertNotIn("sword", out)

    def test_items_unknown_type(self):
        self.call(CmdItems(), "vehicle", "Unknown type")

    def test_items_recognizes_empty_classic_type(self):
        self.call(CmdItems(), "wand", "No items of type")

    def test_items_lists_directly_created_item_as_untemplated(self):
        boots = create_object(
            self.ITEM,
            key="a pair of leather boots",
            location=self.char1,
            attributes=(("type", "armor"),),
        )

        out = self.call(CmdItems(), "")

        self.assertIn("Untemplated live items", out)
        self.assertIn(f"#{boots.id}", out)
        self.assertIn("a pair of leather boots", out)

    def test_items_type_filter_includes_untemplated_item(self):
        boots = create_object(
            self.ITEM,
            key="a pair of leather boots",
            attributes=(("type", "armor"),),
        )

        out = self.call(CmdItems(), "armor")

        self.assertIn(f"#{boots.id}", out)
        self.assertIn("Untemplated live items of type", out)
        self.assertNotIn("sword", out)

    def test_items_lists_legacy_item_with_unknown_type(self):
        wand = create_object(
            self.ITEM,
            key="an old wand",
            attributes=(("type", "wand"),),
        )

        out = self.call(CmdItems(), "")

        self.assertIn("wand", out)
        self.assertIn(f"#{wand.id}", out)
        self.assertIn("an old wand", out)

    def test_spawned_item_is_counted_but_not_listed_as_untemplated(self):
        sword = spawn("sword")[0]

        out = self.call(CmdItems(), "")

        self.assertIn("sword", out)
        self.assertIn("(1 in world)", out)
        self.assertNotIn(f"#{sword.id} —", out)


class TestEditNewNpc(EvenniaCommandTest):
    """NPCs are Character prototypes with one initial copy spawned in-room."""

    NPC = "typeclasses.characters.Character"

    def setUp(self):
        super().setUp()
        self.char1.permissions.add("Builder")

    def test_create_npc_template_and_spawn_copy_here(self):
        self.call(CmdBuild(), "new npc City Guard")

        proto = self.char1.ndb._build_target
        self.assertIsInstance(proto, dict)
        self.assertEqual(proto["prototype_key"], "city_guard")
        self.assertEqual(proto["typeclass"], self.NPC)
        self.assertIsNotNone(_proto("city_guard"))

        spawned = [obj for obj in self.room1.contents if obj.key == "City Guard"]
        self.assertEqual(len(spawned), 1)
        self.assertTrue(
            inherits_from(spawned[0], "evennia.objects.objects.DefaultCharacter")
        )
        self.assertIsNone(spawned[0].account)
        self.assertEqual(spawned[0].db.position, "standing")

    def test_npc_defaults_match_finished_character_attributes(self):
        self.call(CmdBuild(), "new npc City Guard")
        proto = self.char1.ndb._build_target

        expected = {
            "gender": "unspecified",
            "age": 18,
            "char_class": "Fighter",
            "background": "",
            "species": "Human",
            "size": "Medium",
            "alignment": "Neutral",
            "languages": ["Common"],
            "active_language": "Common",
            "skill_proficiencies": [],
            "strength": 8,
            "dexterity": 8,
            "constitution": 8,
            "intelligence": 8,
            "wisdom": 8,
            "charisma": 8,
            "level": 1,
            "xp": 0,
            "hp_base": 10,
            "hp_current": 9,
            "hit_die": 10,
            "speed": 30,
        }
        for name, value in expected.items():
            self.assertEqual(proto[name], value, name)

    def test_fields_clone_finished_pc_sheet(self):
        self.call(CmdBuild(), "new npc City Guard")
        fields = set(schema_for_prototype(self.char1.ndb._build_target))

        self.assertEqual(
            fields,
            {
                "name",
                "desc",
                "gender",
                "species",
                "class",
                "age",
                "alignment",
                "background",
                "size",
                "languages",
                "active_language",
                "skills",
                "strength",
                "dexterity",
                "constitution",
                "intelligence",
                "wisdom",
                "charisma",
                "level",
                "xp",
                "proficiency_bonus",
                "hp_base",
                "hp_max",
                "hp_current",
                "hit_die",
                "reaction",
                "armor_class",
                "passive_perception",
                "speed",
            },
        )

    def test_set_npc_fields_persists_canonical_values(self):
        self.call(CmdBuild(), "new npc City Guard")
        self.call(CmdBuildSet(), "gender female")
        self.call(CmdBuildSet(), "species elf")
        self.call(CmdBuildSet(), "class wizard")
        self.call(CmdBuildSet(), "age 240")
        self.call(CmdBuildSet(), "alignment lawful neutral")
        self.call(CmdBuildSet(), "background sage")
        self.call(CmdBuildSet(), "size medium")
        self.call(CmdBuildSet(), "languages Common, Elvish, Draconic")
        self.call(CmdBuildSet(), "active_language elvish")
        self.call(CmdBuildSet(), "skills Arcana, History")
        self.call(CmdBuildSet(), "intelligence 18")

        saved = _proto("city_guard")
        self.assertEqual(saved["gender"], "female")
        self.assertEqual(saved["species"], "Elf")
        self.assertEqual(saved["char_class"], "Wizard")
        self.assertEqual(saved["age"], 240)
        self.assertEqual(saved["alignment"], "Lawful Neutral")
        self.assertEqual(saved["background"], "Sage")
        self.assertEqual(saved["languages"], ["Common", "Elvish", "Draconic"])
        self.assertEqual(saved["active_language"], "Elvish")
        self.assertEqual(saved["skill_proficiencies"], ["Arcana", "History"])
        self.assertEqual(saved["intelligence"], 18)

    def test_invalid_npc_value_rejected(self):
        self.call(CmdBuild(), "new npc City Guard")
        self.call(CmdBuildSet(), "class commoner", "Invalid value for 'class'")
        self.call(CmdBuildSet(), "strength 21", "Invalid value for 'strength'")
        self.assertEqual(self.char1.ndb._build_target["char_class"], "Fighter")
        self.assertEqual(self.char1.ndb._build_target["strength"], 8)

    def test_derived_stat_fields_store_explicit_overrides(self):
        self.call(CmdBuild(), "new npc City Guard")
        self.call(CmdBuildSet(), "proficiency_bonus 4")
        self.call(CmdBuildSet(), "hp_base 20")
        self.call(CmdBuildSet(), "hp_max 30")
        self.call(CmdBuildSet(), "reaction 3")
        self.call(CmdBuildSet(), "armor_class 17")
        self.call(CmdBuildSet(), "passive_perception 15")

        saved = _proto("city_guard")
        self.assertEqual(saved["proficiency_bonus_override"], 4)
        self.assertEqual(saved["hp_base"], 20)
        self.assertEqual(saved["hp_max_override"], 30)
        self.assertEqual(saved["reaction_modifier_override"], 3)
        self.assertEqual(saved["armor_class_override"], 17)
        self.assertEqual(saved["passive_perception_override"], 15)

    def test_edit_existing_npc_template(self):
        self.call(CmdBuild(), "new npc City Guard")
        self.call(CmdBuildSet(), "level 3")
        self.call(CmdBuildDone(), "")
        self.call(CmdBuild(), "npc City Guard")
        self.assertEqual(self.char1.ndb._build_target["level"], 3)

    def test_edit_live_npc_copy_without_changing_template(self):
        self.call(CmdBuild(), "new npc City Guard")
        npc = next(obj for obj in self.room1.contents if obj.key == "City Guard")

        self.call(CmdBuild(), f"#{npc.id}")
        self.call(CmdBuildSet(), "level 4")

        self.assertEqual(npc.db.level, 4)
        self.assertEqual(_proto("city_guard")["level"], 1)

    def test_new_npc_requires_name_and_room(self):
        self.call(CmdBuild(), "new npc", "Usage: edit new npc")
        self.char1.location = None
        self.call(CmdBuild(), "new npc Wanderer", "You must be in a room")
        self.assertIsNone(_proto("wanderer"))

    def test_duplicate_npc_prototype_rejected(self):
        self.call(CmdBuild(), "new npc City Guard")
        self.call(CmdBuild(), "new npc City Guard", "An NPC prototype")


class TestNpcsListing(EvenniaCommandTest):
    """The NPC catalogue shows templates and their spawned-copy counts."""

    def test_npcs_lists_created_template_and_copy(self):
        self.char1.permissions.add("Builder")
        self.call(CmdBuild(), "new npc City Guard")

        out = self.call(CmdNpcs(), "")

        self.assertIn("city_guard", out)
        self.assertIn("City Guard", out)
        self.assertIn("(1 in world)", out)

    def test_npcs_empty_state(self):
        self.char1.permissions.add("Builder")
        self.call(CmdNpcs(), "", "There are no NPC templates yet")


class TestEditPrompt(EvenniaCommandTest):
    """The 'editing>' prompt is armed on enter, re-sent every command, cleared
    on exit by the generic ndb._prompt mechanism in commands.command."""

    def test_enter_arms_prompt_and_exit_clears_it(self):
        _enter_build_mode(self.char1, self.room1)
        self.assertEqual(self.char1.ndb._prompt, _BUILD_PROMPT)

        self.char1.msg = MagicMock()
        _exit_build_mode(self.char1)
        self.assertIsNone(self.char1.ndb._prompt)
        self.char1.msg.assert_any_call(prompt="")  # client prompt cleared too

    def test_prompt_resent_after_any_command_while_editing(self):
        # The persistence mixin re-sends ndb._prompt from at_post_cmd, so even a
        # non-build command (here CmdRooms) keeps the prompt at the input line.
        self.char1.ndb._prompt = _BUILD_PROMPT
        self.char1.msg = MagicMock()
        cmd = CmdRooms()
        cmd.caller = self.char1
        cmd.at_post_cmd()
        self.char1.msg.assert_any_call(prompt=_BUILD_PROMPT)

    def test_no_prompt_when_not_editing(self):
        self.char1.ndb._prompt = None
        self.char1.msg = MagicMock()
        cmd = CmdRooms()
        cmd.caller = self.char1
        cmd.at_post_cmd()
        for mock_call in self.char1.msg.mock_calls:
            self.assertNotIn("prompt", mock_call.kwargs)

    def test_bare_enter_redraws_prompt(self):
        # Empty input runs CMD_NOINPUT, not a normal command, so it needs its
        # own redraw of the sticky prompt.
        self.char1.ndb._prompt = _BUILD_PROMPT
        self.char1.msg = MagicMock()
        cmd = CmdNoInput()
        cmd.caller = self.char1
        cmd.func()
        self.char1.msg.assert_any_call(prompt=_BUILD_PROMPT)

    def test_bare_enter_silent_when_not_editing(self):
        self.char1.ndb._prompt = None
        self.char1.msg = MagicMock()
        cmd = CmdNoInput()
        cmd.caller = self.char1
        cmd.func()
        self.char1.msg.assert_not_called()

    def test_noinput_registered_in_character_cmdset(self):
        cmdset = CharacterCmdSet()
        cmdset.at_cmdset_creation()
        self.assertTrue(any(isinstance(c, CmdNoInput) for c in cmdset.commands))
