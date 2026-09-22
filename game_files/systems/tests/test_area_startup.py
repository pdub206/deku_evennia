"""AREA-06A enabled-world cold-start reconciliation coverage."""

from __future__ import annotations

from unittest.mock import Mock

from django.test import override_settings
from evennia.server.models import ServerConfig
from evennia.utils.search import search_tag
from evennia.utils.test_resources import EvenniaTest
from systems.area_apply import AreaApplyResult
from systems.area_resets import AREA_RESET_CONFIG_KEY, controller_snapshot
from systems.area_startup import (
    AreaStartupError,
    configured_enabled_area_manifests,
    reconcile_enabled_world,
    recover_enabled_world_reload,
)
from systems.areas import AREA_TAG_CATEGORY, ROOM_KEY_CATEGORY, compile_area_load_plan
from systems.room_environment import default_room_environment
from systems.room_policy import default_room_policy


def _manifest(description: str = "Source-owned entry.") -> dict:
    """Return a complete minimal source area with one role-capable room."""
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
                "name": "Entry",
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


@override_settings(
    GAME_ENABLED_AREA_MANIFESTS=("alpha",),
    CHARACTER_START_ROOM="alpha:entry",
    COMBAT_RESPAWN_SANCTUARY="alpha:entry",
    RECALL_DESTINATION="alpha:entry",
)
class TestEnabledWorldStartup(EvenniaTest):
    """Cold start accepts only a complete, source-validated enabled world."""

    def setUp(self):
        super().setUp()
        ServerConfig.objects.filter(db_key=AREA_RESET_CONFIG_KEY).delete()

    @staticmethod
    def _compiler(_selected):
        return compile_area_load_plan(manifests={"alpha": _manifest()})

    @staticmethod
    def _changed_compiler(_selected):
        return compile_area_load_plan(manifests={"alpha": _manifest("Changed.")})

    def test_repeated_startup_applies_one_idempotent_world_and_controller(self):
        first = reconcile_enabled_world(compiler=self._compiler)
        second = reconcile_enabled_world(compiler=self._compiler)
        rooms = [
            room
            for room in search_tag("entry", category=ROOM_KEY_CATEGORY)
            if room.tags.has("alpha", category=AREA_TAG_CATEGORY)
        ]
        self.assertEqual(first.status, "ready")
        self.assertEqual(second.status, "ready")
        self.assertEqual(len(rooms), 1)
        self.assertEqual(tuple(controller_snapshot()), ("alpha",))

    @override_settings(RECALL_DESTINATION="alpha:missing")
    def test_missing_source_role_blocks_before_apply(self):
        applier = Mock()
        result = reconcile_enabled_world(compiler=self._compiler, applier=applier)
        self.assertEqual(result.status, "blocked")
        self.assertIn(
            "RECALL_DESTINATION targets no enabled source room", result.detail
        )
        applier.assert_not_called()

    @override_settings(GAME_ENABLED_AREA_MANIFESTS=("alpha", "alpha"))
    def test_duplicate_enabled_key_is_rejected(self):
        with self.assertRaisesRegex(AreaStartupError, "must be unique"):
            configured_enabled_area_manifests()

    @override_settings(GAME_ENABLED_AREA_MANIFESTS=())
    def test_disabled_world_does_not_attempt_apply(self):
        applier = Mock(spec=AreaApplyResult)
        result = reconcile_enabled_world(applier=applier)
        self.assertEqual(result.status, "disabled")
        applier.assert_not_called()

    def test_unchanged_reload_preserves_due_token_and_recovers_claim(self):
        reconcile_enabled_world(compiler=self._compiler)
        state = ServerConfig.objects.conf(AREA_RESET_CONFIG_KEY)
        state["controllers"]["alpha"].update(
            next_due_token=71, last_attempted_token=70, status="running"
        )
        ServerConfig.objects.conf(AREA_RESET_CONFIG_KEY, value=state)

        result = recover_enabled_world_reload(compiler=self._compiler)
        controller = controller_snapshot()["alpha"]

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.recovered, ("alpha",))
        self.assertEqual(controller["next_due_token"], 71)
        self.assertEqual(controller["last_attempted_token"], 70)
        self.assertEqual(controller["status"], "idle")

    def test_changed_reload_reports_pending_without_applying_source(self):
        reconcile_enabled_world(compiler=self._compiler)
        before = controller_snapshot()["alpha"]["manifest_fingerprint"]

        result = recover_enabled_world_reload(compiler=self._changed_compiler)
        room = next(
            room
            for room in search_tag("entry", category=ROOM_KEY_CATEGORY)
            if room.tags.has("alpha", category=AREA_TAG_CATEGORY)
        )

        self.assertEqual(result.status, "pending")
        self.assertEqual(result.pending, ("alpha",))
        self.assertEqual(room.db.desc, "Source-owned entry.")
        self.assertEqual(controller_snapshot()["alpha"]["manifest_fingerprint"], before)

    @override_settings(GAME_ENABLED_AREA_MANIFESTS=())
    def test_disabled_reload_retires_controller_without_touching_content(self):
        with override_settings(GAME_ENABLED_AREA_MANIFESTS=("alpha",)):
            reconcile_enabled_world(compiler=self._compiler)

        result = recover_enabled_world_reload(
            compiler=lambda _selected: compile_area_load_plan(manifests={})
        )

        self.assertEqual(result.status, "disabled")
        self.assertEqual(result.retired, ("alpha",))
        self.assertEqual(controller_snapshot(), {})
