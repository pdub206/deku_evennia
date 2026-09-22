"""AREA-04B transactional apply and live-state preservation coverage."""

from __future__ import annotations

from unittest.mock import patch

from commands.building import CmdAreaApply
from evennia import create_object
from evennia.server.models import ServerConfig
from evennia.utils.test_resources import EvenniaCommandTest
from systems.area_apply import (
    AREA_RESET_CONFIG_KEY,
    AreaApplyError,
    _APPLY_LOCK,
    apply_areas,
)
from systems.area_resets import controller_snapshot
from systems.areas import AREA_TAG_CATEGORY, ROOM_KEY_CATEGORY, compile_area_load_plan
from systems.room_environment import default_room_environment
from systems.room_policy import default_room_policy


def _manifest(description: str = "Source description.") -> dict:
    """Return one complete minimal manifest for a source-only apply test."""
    return {
        "key": "alpha",
        "display_name": "Alpha",
        "schema_version": 1,
        "dependencies": [],
        "credits": [],
        "srd_references": [],
        "reset_policy": "never",
        "lifespan_pulses": 0,
        "rooms": {
            "entry": {
                "name": "Source room",
                "description": description,
                "extra_descriptions": [],
                "sector": "inside",
                "policy": default_room_policy(),
                "environment": default_room_environment(),
                "weather_profile": None,
            }
        },
        "exits": {},
        "mobiles": [],
        "objects": {},
        "renames": {"rooms": {}, "exits": {}},
    }


class TestAreaApply(EvenniaCommandTest):
    """Apply preserves live residents while atomically updating source fields."""

    def setUp(self):
        super().setUp()
        self.char1.permissions.add("Builder")
        ServerConfig.objects.filter(db_key=AREA_RESET_CONFIG_KEY).delete()

    def _compiler(self, data: dict):
        plan = compile_area_load_plan(manifests={"alpha": data})
        return lambda _selected: plan

    def _room(self):
        rooms = [
            room
            for room in self.room1.__class__.objects.all()
            if room.tags.has("alpha", category=AREA_TAG_CATEGORY)
            and room.tags.has("entry", category=ROOM_KEY_CATEGORY)
        ]
        self.assertEqual(len(rooms), 1)
        return rooms[0]

    def test_create_update_and_preserve_occupants_and_contents(self):
        first = apply_areas(["alpha"], compiler=self._compiler(_manifest()))
        room = self._room()
        self.char2.move_to(room, quiet=True)
        item = create_object(
            "typeclasses.objects.Object", key="ordinary", location=room
        )
        first_controller = controller_snapshot()["alpha"]["manifest_fingerprint"]
        second = apply_areas(["alpha"], compiler=self._compiler(_manifest("Changed.")))
        self.assertEqual(first.rooms_created, 1)
        self.assertEqual(second.rooms_updated, 1)
        self.assertEqual(room.db.desc, "Changed.")
        self.assertIs(self.char2.location, room)
        self.assertIs(item.location, room)
        self.assertNotEqual(
            controller_snapshot()["alpha"]["manifest_fingerprint"], first_controller
        )

    def test_failed_stage_and_changed_source_leave_no_partial_graph_then_retry(self):
        with self.assertRaises(RuntimeError):
            apply_areas(
                ["alpha"],
                compiler=self._compiler(_manifest()),
                stage_hook=lambda stage: (
                    (_ for _ in ()).throw(RuntimeError()) if stage == "rooms" else None
                ),
            )
        self.assertEqual(
            [
                room
                for room in self.room1.__class__.objects.all()
                if room.tags.has("alpha", category=AREA_TAG_CATEGORY)
            ],
            [],
        )
        plan_a = compile_area_load_plan(manifests={"alpha": _manifest()})
        plan_b = compile_area_load_plan(manifests={"alpha": _manifest("new")})
        with self.assertRaisesRegex(AreaApplyError, "source_changed"):
            plans = iter((plan_a, plan_b))
            apply_areas(["alpha"], compiler=lambda _selected: next(plans))
        self.assertEqual(
            apply_areas(["alpha"], compiler=self._compiler(_manifest())).rooms_created,
            1,
        )

    def test_busy_and_command_access(self):
        self.char2.permissions.clear()
        self.assertFalse(CmdAreaApply().access(self.char2, "cmd"))
        self.assertTrue(_APPLY_LOCK.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(AreaApplyError, "busy"):
                apply_areas(["alpha"], compiler=self._compiler(_manifest()))
        finally:
            _APPLY_LOCK.release()
        with patch(
            "commands.building.apply_areas",
            return_value=apply_areas(["alpha"], compiler=self._compiler(_manifest())),
        ):
            self.call(CmdAreaApply(), "/apply alpha", "Area apply complete")
