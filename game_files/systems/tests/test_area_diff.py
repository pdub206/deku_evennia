"""AREA-04A source/live reconciliation coverage."""

from __future__ import annotations

from unittest.mock import patch

from commands.building import CmdAreaDiff
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest
from systems.area_diff import build_area_diff
from systems.areas import EXIT_KEY_CATEGORY, assign_area, compile_area_load_plan
from systems.room_environment import default_room_environment
from systems.room_policy import default_room_policy


def manifest(*, rooms=None, exits=None, renames=None):
    """Build minimal valid source data for a named AREA-04A test area."""
    room = {
        "name": "Source room",
        "description": "Source description.",
        "extra_descriptions": [],
        "sector": "inside",
        "policy": default_room_policy(),
        "environment": default_room_environment(),
        "weather_profile": None,
    }
    return {
        "key": "alpha",
        "display_name": "Alpha",
        "schema_version": 1,
        "dependencies": [],
        "credits": [],
        "srd_references": [],
        "reset_policy": "default",
        "lifespan_pulses": 0,
        "rooms": rooms if rooms is not None else {"entry": room},
        "exits": exits if exits is not None else {},
        "mobiles": [],
        "objects": {},
        "renames": renames if renames is not None else {"rooms": {}, "exits": {}},
    }


class TestAreaDiff(EvenniaCommandTest):
    """The diff reads only stable authored identity and fields."""

    def setUp(self):
        super().setUp()
        self.char1.permissions.add("Builder")
        assign_area(self.room1, "alpha")
        for key in self.room1.tags.get(category="room_key", return_list=True):
            self.room1.tags.remove(key, category="room_key")
        self.room1.tags.add("entry", category="room_key")

    def _plan(self, **kwargs):
        return compile_area_load_plan(manifests={"alpha": manifest(**kwargs)})

    def test_addition_update_stale_and_explicit_rename(self):
        self.room1.key = "Old live room"
        self.room1.db.desc = "Old text."
        diff = build_area_diff(self._plan())
        self.assertEqual(
            [(entry.kind, entry.subject) for entry in diff.entries],
            [("authored_update", "room:alpha:entry")],
        )

        renamed = manifest(
            rooms={"arrival": manifest()["rooms"]["entry"]},
            renames={"rooms": {"entry": "arrival"}, "exits": {}},
        )
        diff = build_area_diff(compile_area_load_plan(manifests={"alpha": renamed}))
        self.assertEqual(diff.entries[0].kind, "rename")
        self.assertIn("consumed rename entry", diff.entries[0].detail)

        unannounced = manifest(rooms={"arrival": manifest()["rooms"]["entry"]})
        diff = build_area_diff(compile_area_load_plan(manifests={"alpha": unannounced}))
        self.assertEqual(
            {entry.kind for entry in diff.entries}, {"addition", "stale_candidate"}
        )

    def test_exit_reference_change_and_conflict_are_reported(self):
        second = create_object("typeclasses.rooms.Room", key="Second")
        assign_area(second, "alpha")
        for key in second.tags.get(category="room_key", return_list=True):
            second.tags.remove(key, category="room_key")
        second.tags.add("second", category="room_key")
        exit_obj = create_object(
            "typeclasses.exits.Exit",
            key="north",
            location=self.room1,
            destination=second,
        )
        exit_obj.tags.add("north_exit", category=EXIT_KEY_CATEGORY)
        rooms = {
            "entry": manifest()["rooms"]["entry"],
            "second": {**manifest()["rooms"]["entry"], "name": "Second"},
        }
        exits = {
            "north_exit": {
                "source_room": "second",
                "name": "south",
                "description": "",
                "aliases": [],
                "destination": {"kind": "local", "room_key": "entry"},
                "door": None,
            }
        }
        diff = build_area_diff(self._plan(rooms=rooms, exits=exits))
        self.assertIn("reference_change", [entry.kind for entry in diff.entries])

        duplicate = create_object("typeclasses.rooms.Room", key="Duplicate")
        assign_area(duplicate, "alpha")
        for key in duplicate.tags.get(category="room_key", return_list=True):
            duplicate.tags.remove(key, category="room_key")
        duplicate.tags.add("entry", category="room_key")
        self.assertIn(
            "blocked_conflict",
            [entry.kind for entry in build_area_diff(self._plan()).entries],
        )

    def test_command_is_builder_locked_and_read_only(self):
        self.char2.permissions.clear()
        self.assertFalse(CmdAreaDiff().access(self.char2, "cmd"))
        before = (self.room1.key, self.room1.db.desc, list(self.room1.tags.all()))
        first = build_area_diff(self._plan())
        second = build_area_diff(self._plan())
        self.assertEqual(first, second)
        self.assertNotIn(
            str(self.room1.dbref), "\n".join(entry.detail for entry in first.entries)
        )
        with patch(
            "commands.building.compile_area_load_plan", return_value=self._plan()
        ):
            self.call(CmdAreaDiff(), "/diff alpha", "Area diff")
        self.assertEqual(
            before, (self.room1.key, self.room1.db.desc, list(self.room1.tags.all()))
        )
