"""
Tests for the unified build command and the area export/import round-trip.

Run from the game/ directory:
    evennia test --settings settings.py commands.tests.test_building
"""

import importlib.util
import tempfile
from unittest.mock import MagicMock

from commands.building import (_BUILD_PROMPT, CmdAreas, CmdBuild, CmdBuildArea,
                               CmdBuildDel, CmdBuildDig, CmdBuildFields,
                               CmdBuildSet, CmdLoadArea, CmdRooms,
                               _enter_build_mode, _exit_build_mode)
from commands.command import CmdNoInput
from commands.default_cmdsets import CharacterCmdSet
from django.conf import settings
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest
from evennia.utils.utils import inherits_from
from systems.areas import build_area_data, export_area, load_area_data
from world.build_schema import schema_for


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
    """'edit new item' creates an item in the room and edits it in place."""

    ITEM = "typeclasses.objects.Item"

    def setUp(self):
        super().setUp()
        self.char1.permissions.add("Builder")

    def test_create_item_in_current_room(self):
        origin = self.char1.location
        self.call(CmdBuild(), "new item Iron Sword")
        item = self.char1.ndb._build_target
        self.assertEqual(item.key, "Iron Sword")
        self.assertTrue(inherits_from(item, self.ITEM))
        # Created in the room, and the builder did NOT teleport.
        self.assertEqual(item.location, origin)
        self.assertEqual(self.char1.location, origin)

    def test_item_defaults_and_fields(self):
        self.call(CmdBuild(), "new item Rock")
        item = self.char1.ndb._build_target
        self.assertEqual(item.db.weight, 0.0)
        self.assertEqual(item.db.value, 0)
        out = self.call(CmdBuildFields(), "")
        for field_name in ("name", "desc", "weight", "value"):
            self.assertIn(field_name, out)

    def test_set_weight_and_value(self):
        self.call(CmdBuild(), "new item Rock")
        self.call(CmdBuildSet(), "weight 2.5")
        self.call(CmdBuildSet(), "value 12")
        item = self.char1.ndb._build_target
        self.assertEqual(item.db.weight, 2.5)
        self.assertEqual(item.db.value, 12)

    def test_negative_weight_rejected(self):
        self.call(CmdBuild(), "new item Rock")
        self.call(CmdBuildSet(), "weight -3", "Invalid value for 'weight'")
        self.assertEqual(self.char1.ndb._build_target.db.weight, 0.0)

    def test_non_numeric_value_rejected(self):
        self.call(CmdBuild(), "new item Rock")
        self.call(CmdBuildSet(), "value lots", "Invalid value for 'value'")

    def test_edit_existing_item_by_name(self):
        item = create_object(self.ITEM, key="Lantern", location=self.room1)
        self.call(CmdBuild(), "Lantern")
        self.assertEqual(self.char1.ndb._build_target, item)


class TestItemType(EvenniaCommandTest):
    """'set type <kind>' reshapes an item's editable fields dynamically."""

    def setUp(self):
        super().setUp()
        self.char1.permissions.add("Builder")
        self.call(CmdBuild(), "new item Thing")
        self.item = self.char1.ndb._build_target

    def test_type_fields_hidden_until_type_set(self):
        # Check schema keys directly (blurb text can contain field-like words).
        fields = set(schema_for(self.item))
        self.assertIn("type", fields)  # the type field itself is always offered
        self.assertEqual(fields & {"damage", "subtype", "base_ac", "capacity"}, set())

    def test_set_type_weapon_reveals_fields(self):
        self.call(CmdBuildSet(), "type weapon")
        self.assertEqual(self.item.db.type, "weapon")
        out = self.call(CmdBuildFields(), "")
        self.assertIn("damage", out)
        self.assertIn("subtype", out)

    def test_weapon_damage_and_subtype(self):
        self.call(CmdBuildSet(), "type weapon")
        self.call(CmdBuildSet(), "damage 1d8")
        self.call(CmdBuildSet(), "subtype slashing")
        self.assertEqual(self.item.db.damage, "1d8")
        self.assertEqual(self.item.db.subtype, "slashing")

    def test_invalid_damage_rejected(self):
        self.call(CmdBuildSet(), "type weapon")
        self.call(CmdBuildSet(), "damage sharp", "Invalid value for 'damage'")
        self.assertIsNone(self.item.db.damage)

    def test_subtype_validated_per_type(self):
        self.call(CmdBuildSet(), "type weapon")
        # 'light' is an armor value, not valid for a weapon's subtype.
        self.call(CmdBuildSet(), "subtype light", "Invalid value for 'subtype'")
        self.call(CmdBuildSet(), "subtype piercing")
        self.assertEqual(self.item.db.subtype, "piercing")

    def test_set_type_armor_fields(self):
        self.call(CmdBuildSet(), "type armor")
        self.call(CmdBuildSet(), "base_ac 16")
        self.call(CmdBuildSet(), "subtype heavy")
        self.assertEqual(self.item.db.base_ac, 16)
        self.assertEqual(self.item.db.subtype, "heavy")
        self.call(CmdBuildSet(), "subtype slashing", "Invalid value for 'subtype'")

    def test_changing_type_clears_old_fields(self):
        self.call(CmdBuildSet(), "type weapon")
        self.call(CmdBuildSet(), "subtype slashing")
        self.call(CmdBuildSet(), "damage 2d6")
        # Switching type must drop the weapon's stale damage/subtype.
        self.call(CmdBuildSet(), "type armor")
        self.assertIsNone(self.item.db.subtype)
        self.assertIsNone(self.item.db.damage)
        out = self.call(CmdBuildFields(), "")
        self.assertIn("base_ac", out)
        self.assertNotIn("damage", out)

    def test_set_type_container_capacity(self):
        self.call(CmdBuildSet(), "type container")
        self.call(CmdBuildSet(), "capacity 30")
        self.assertEqual(self.item.db.capacity, 30.0)

    def test_set_type_none_reverts_to_generic(self):
        self.call(CmdBuildSet(), "type weapon")
        self.call(CmdBuildSet(), "type none")
        self.assertIsNone(self.item.db.type)
        out = self.call(CmdBuildFields(), "")
        self.assertNotIn("damage", out)

    def test_invalid_type_rejected(self):
        self.call(CmdBuildSet(), "type wand", "Invalid value for 'type'")
        self.assertIsNone(self.item.db.type)

    def test_edit_new_kind_keyword_is_not_creation(self):
        # 'weapon' is no longer a creation keyword; only room/item are.
        self.call(CmdBuild(), "new weapon Sword", "Usage: edit new")


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
