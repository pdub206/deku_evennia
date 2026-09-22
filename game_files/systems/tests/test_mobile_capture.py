"""AREA-05C capture boundaries and restricted NPC force tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from evennia.utils.test_resources import EvenniaTest
from systems.mob_spawning import MobileSpawnIdentity
from systems.mobile_capture import MobileCaptureError, capture_npc, force_npc


class TestMobileCapture(EvenniaTest):
    """Only schema data crosses from a source-linked live NPC to a draft."""

    def _npc(self):
        npc = MagicMock()
        npc.id = 99
        npc.key = "guard"
        npc.db = SimpleNamespace(is_player_character=False)
        npc.attributes.has.side_effect = lambda name: name in {"xp", "hp_current"}
        npc.attributes.get.side_effect = {"xp": 123, "hp_current": 17}.get
        return npc

    @patch("systems.mobile_capture.mobile_policy", create=True)
    @patch("systems.mobile_capture.resolve_prototype")
    @patch("systems.mobile_capture.mobile_spawn_identity")
    def test_capture_keeps_schema_fields_and_explicit_hp_xp(
        self, identity, prototype, _policy
    ):
        identity.return_value = MobileSpawnIdentity("guard_template")
        prototype.return_value = {
            "prototype_key": "guard_template",
            "typeclass": "typeclasses.characters.Character",
            "key": "Guard",
            "xp": 0,
            "hp_current": 1,
        }
        npc = self._npc()
        with patch("systems.mobile_policy.mobile_policy", return_value={}):
            key, captured = capture_npc(npc)
        self.assertEqual(key, "guard_template")
        self.assertEqual(captured["xp"], 123)
        self.assertEqual(captured["hp_current"], 17)
        self.assertNotIn("location", captured)

    @patch("systems.mobile_capture.resolve_prototype")
    @patch("systems.mobile_capture.mobile_spawn_identity")
    def test_force_accepts_only_one_exact_loadout_verb(self, identity, prototype):
        identity.return_value = MobileSpawnIdentity("guard_template")
        prototype.return_value = {
            "prototype_key": "guard_template",
            "typeclass": "typeclasses.characters.Character",
        }
        npc = self._npc()
        force_npc(npc, "wear sword", self.account)
        npc.execute_cmd.assert_called_once_with("wear sword")
        for command in ("north", "say hi", "get coin;drop coin", "wear sword\nlook"):
            with self.assertRaises(MobileCaptureError):
                force_npc(npc, command, self.account)
