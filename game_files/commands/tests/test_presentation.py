"""INTERACT-05 display preference command and rendering coverage."""

from unittest.mock import patch

from commands.presentation import (
    CmdAutoExits,
    CmdBrief,
    CmdCompact,
    CmdPreferences,
    CmdPrompt,
)
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest
from systems.presentation import (
    DEFAULT_PROFILE,
    ORDINARY_PROMPT,
    PROFILE_ATTRIBUTE,
    active_prompt,
    get_presentation_profile,
)
from typeclasses.exits import Exit


class TestPresentationPreferences(EvenniaCommandTest):
    """Preferences remain persistent presentation-only character state."""

    def setUp(self) -> None:
        super().setUp()
        self.room1.db.desc = "A room description that must remain visible."
        self.exit = create_object(
            Exit, key="north", location=self.room1, destination=self.room2
        )

    def test_defaults_and_read_only_summary(self) -> None:
        """The first read stores documented defaults and summary changes nothing."""
        output = self.call(CmdPreferences(), "")

        self.assertIn("Brief: off", output)
        self.assertIn("Compact: off", output)
        self.assertIn("Auto-exits: on", output)
        self.assertIn("Prompt: on", output)
        self.assertEqual(self.char1.attributes.get(PROFILE_ATTRIBUTE), DEFAULT_PROFILE)
        stored = dict(self.char1.attributes.get(PROFILE_ATTRIBUTE))
        self.call(CmdPreferences(), "unexpected", "Usage: preferences.")
        self.assertEqual(self.char1.attributes.get(PROFILE_ATTRIBUTE), stored)

    def test_every_toggle_queries_sets_and_persists_together(self) -> None:
        """Each command supports query plus explicit on/off without field loss."""
        cases = (
            (CmdBrief, "Brief room mode"),
            (CmdCompact, "Compact spacing"),
            (CmdAutoExits, "Automatic exits"),
            (CmdPrompt, "Ordinary prompt"),
        )
        for command, label in cases:
            self.assertIn(label, self.call(command(), ""))
            self.assertIn("off", self.call(command(), "off"))
            self.assertIn("on", self.call(command(), "on"))
            self.assertIn("Usage:", self.call(command(), "maybe"))

        profile = get_presentation_profile(self.char1)
        self.assertEqual(profile.room_mode, "brief")
        self.assertEqual(profile.spacing, "compact")
        self.assertTrue(profile.auto_exits)
        self.assertTrue(profile.prompt)

    def test_malformed_and_old_profiles_repair_to_exact_schema(self) -> None:
        """Bad legacy fields cannot break rendering or survive normalization."""
        self.char1.attributes.add(
            PROFILE_ATTRIBUTE,
            {
                "version": 0,
                "room_mode": "tiny",
                "spacing": None,
                "auto_exits": "yes",
                "prompt": 1,
                "legacy": "discard me",
            },
        )

        profile = get_presentation_profile(self.char1)

        self.assertEqual(profile.room_mode, "full")
        self.assertEqual(self.char1.attributes.get(PROFILE_ATTRIBUTE), DEFAULT_PROFILE)
        self.assertIn("room description", self.room1.return_appearance(self.char1))

    def test_brief_affects_arrival_never_explicit_look(self) -> None:
        """Arrival context suppresses only the description, not room semantics."""
        self.call(CmdBrief(), "on")

        arrival = self.room1.return_appearance(self.char1, arrival=True)
        explicit = self.room1.return_appearance(self.char1)

        self.assertNotIn("room description", arrival)
        self.assertIn(self.room1.key, arrival)
        self.assertIn("north", arrival)
        self.assertIn("room description", explicit)

    def test_auto_exits_is_independent_of_brief_and_visibility(self) -> None:
        """Auto-exits removes only its summary and does not alter exit state."""
        self.call(CmdBrief(), "on")
        self.call(CmdAutoExits(), "off")

        output = self.room1.return_appearance(self.char1, arrival=True)

        self.assertNotIn("north", output)
        self.assertIs(self.exit.location, self.room1)
        self.assertIn(self.exit, self.room1.exits)

    def test_compact_removes_blank_lines_but_preserves_all_components(self) -> None:
        """Compact formatting cannot filter descriptions, exits, or occupants."""
        normal = self.room1.return_appearance(self.char1)
        self.call(CmdCompact(), "on")
        compact = self.room1.return_appearance(self.char1)

        self.assertLessEqual(compact.count("\n\n"), normal.count("\n\n"))
        for semantic_text in (self.room1.key, "room description", "north"):
            self.assertIn(semantic_text, compact)

    def test_prompt_priority_and_protocol_delivery(self) -> None:
        """Editor and combat prompts override one cross-client ordinary prompt."""
        self.assertEqual(active_prompt(self.char1), ORDINARY_PROMPT)
        self.char1.ndb._combat_prompt = "combat> "
        self.assertEqual(active_prompt(self.char1), "combat> ")
        self.char1.ndb._prompt = "editing> "
        self.assertEqual(active_prompt(self.char1), "editing> ")
        self.char1.ndb._prompt = None
        self.char1.ndb._combat_prompt = None

        with (
            patch.object(self.char1.sessions, "count", return_value=1),
            patch.object(self.char1, "msg") as msg,
        ):
            command = CmdPreferences()
            command.caller = self.char1
            command.args = ""
            command.at_post_cmd()
            msg.assert_any_call(prompt=ORDINARY_PROMPT)

        self.call(CmdPrompt(), "off")
        self.assertIsNone(active_prompt(self.char1))
