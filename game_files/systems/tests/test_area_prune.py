"""AREA-04C conservative stale-record retirement coverage."""

from __future__ import annotations

from unittest.mock import patch

from commands.building import CmdAreaPrune
from django.test import override_settings
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest
from systems.area_apply import plan_fingerprint
from systems.area_prune import AreaPruneError, prune_area
from systems.areas import assign_area, compile_area_load_plan


def _manifest() -> dict:
    """Return a retired area source manifest with no remaining room records."""
    return {
        "key": "alpha",
        "display_name": "Alpha",
        "schema_version": 1,
        "dependencies": [],
        "credits": [],
        "srd_references": [],
        "reset_policy": "never",
        "lifespan_pulses": 0,
        "rooms": {},
        "exits": {},
        "mobiles": [],
        "objects": {},
        "renames": {"rooms": {}, "exits": {}},
    }


class TestAreaPrune(EvenniaCommandTest):
    """Prune deletes only empty stale graph records with a reviewed revision."""

    def setUp(self):
        super().setUp()
        self.char1.permissions.add("Admin")

    def _plan(self):
        return compile_area_load_plan(manifests={"alpha": _manifest()})

    def _compiler(self):
        plan = self._plan()
        return lambda _selected: plan

    def _stale_room(self):
        room = create_object("typeclasses.rooms.Room", key="Retired room")
        assign_area(room, "alpha")
        return room

    def test_safe_prune_requires_exact_review_and_is_idempotent(self):
        room = self._stale_room()
        fingerprint = plan_fingerprint(self._plan())
        result = prune_area("alpha", fingerprint, compiler=self._compiler())
        self.assertEqual(result.rooms_deleted, 1)
        self.assertFalse(room.pk)
        repeated = prune_area("alpha", fingerprint, compiler=self._compiler())
        self.assertEqual((repeated.rooms_deleted, repeated.blocked), (0, ()))

    def test_contents_incoming_and_configured_role_block_without_deleting(self):
        occupied = self._stale_room()
        create_object("typeclasses.objects.Object", key="kept", location=occupied)
        incoming_target = self._stale_room()
        create_object(
            "typeclasses.exits.Exit",
            key="in",
            location=self.room2,
            destination=incoming_target,
        )
        role_room = self._stale_room()
        fingerprint = plan_fingerprint(self._plan())
        with override_settings(
            CHARACTER_START_ROOM=f"alpha:{role_room.tags.get(category='room_key', return_list=True)[0]}"
        ):
            result = prune_area("alpha", fingerprint, compiler=self._compiler())
        self.assertEqual(result.rooms_deleted, 0)
        self.assertIn("contents_or_occupant", " ".join(result.blocked))
        self.assertIn("incoming_exit", " ".join(result.blocked))
        self.assertIn("configured_role", " ".join(result.blocked))
        self.assertTrue(occupied.pk and incoming_target.pk and role_room.pk)

    def test_stale_review_and_admin_command_gate(self):
        self.char2.permissions.clear()
        self.assertFalse(CmdAreaPrune().access(self.char2, "cmd"))
        with self.assertRaisesRegex(AreaPruneError, "stale_review"):
            prune_area("alpha", "0" * 64, compiler=self._compiler())
        fingerprint = plan_fingerprint(self._plan())
        with patch(
            "commands.building.prune_area",
            return_value=prune_area("alpha", fingerprint, compiler=self._compiler()),
        ):
            self.call(CmdAreaPrune(), f"/prune alpha = {fingerprint}", "Area prune")
