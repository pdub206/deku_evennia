"""ITEM-06 note writing, sanitization, authorship, rendering, and staff tests."""

from __future__ import annotations

from unittest.mock import patch

from commands.building import CmdBuild, CmdBuildSet, _apply_field, _set_item_type
from commands.default_cmdsets import CharacterCmdSet
from commands.notes import CmdNoteAdmin, CmdRead, CmdWrite
from django.test import override_settings
from evennia import create_object
from evennia.objects.models import ObjectDB
from evennia.prototypes.prototypes import delete_prototype, search_prototype
from evennia.prototypes.spawner import spawn
from evennia.utils.ansi import parse_ansi
from evennia.utils.test_resources import EvenniaCommandTest, EvenniaTest
from evennia.utils.text2html import parse_html
from systems.item_resources import item_mutation
from systems.notes import (
    PIPE_REPLACEMENT,
    RECORD_ATTRIBUTE,
    TITLE_ATTRIBUTE,
    NoteError,
    describe_authorship,
    note_record,
    render_note,
    sanitize_text,
    sanitize_title,
    staff_delete_note,
    write_note,
)
from typeclasses.objects import Item
from world.build_schema import TYPE_FIELDS, as_note_title


def make_item(location, key: str, item_type: str | None) -> Item:
    """Create a real Item of one type at ``location``."""
    item = create_object(Item, key=key, location=location)
    item.db.type = item_type
    return item


def _immediate_commit():
    """Deliver post-commit messages inside the test transaction."""
    return patch(
        "systems.notes.transaction.on_commit", side_effect=lambda callback: callback()
    )


class TestNoteSanitization(EvenniaTest):
    """Stored text is bounded, normalized plain text with no markup survivors."""

    def test_exact_and_over_length(self):
        self.assertEqual(
            sanitize_text("a" * 10, multiline=True, max_length=10), "a" * 10
        )
        with self.assertRaises(NoteError):
            sanitize_text("a" * 11, multiline=True, max_length=10)
        # Limits apply after normalization: trailing whitespace does not count.
        self.assertEqual(
            sanitize_text("a" * 10 + "   ", multiline=True, max_length=10), "a" * 10
        )
        with self.assertRaises(NoteError):
            sanitize_text("a" * 41, multiline=True, max_length=10)
        with self.assertRaises(NoteError):
            sanitize_text(None, multiline=True, max_length=10)

    @override_settings(NOTE_TITLE_MAX_LENGTH=5)
    def test_title_bounds_from_settings(self):
        self.assertEqual(sanitize_title("Title"), "Title")
        with self.assertRaises(NoteError):
            sanitize_title("Titles")
        with self.assertRaises(NoteError):
            sanitize_title("  |r|n ")

    def test_unicode_and_multiline_normalization(self):
        decomposed = "Cafe\u0301 \u65e5\u672c \U0001f469\u200d\U0001f4bb"
        self.assertEqual(
            sanitize_text(decomposed, multiline=True, max_length=50),
            "Caf\u00e9 \u65e5\u672c \U0001f469\u200d\U0001f4bb",
        )
        text = "\n\none  \r\ntwo\rthree\u2028\n\n\n\tfour|/five\n\n"
        self.assertEqual(
            sanitize_text(text, multiline=True, max_length=100),
            "one\ntwo\nthree\n\n four\nfive",
        )
        self.assertEqual(
            sanitize_text("a\n\n b ", multiline=False, max_length=100), "a b"
        )

    def test_markup_and_control_sanitization(self):
        hostile = (
            "|rred|n |lclook|ltclick|le |luhttp://x|ltsite|le ||lcq||ltL||le "
            "\x1b[31mesc\x07\x00 \u202eevil\u200b $pad(x) {{b}} <b>tag</b> #12"
        )
        clean = sanitize_text(hostile, multiline=True, max_length=500)
        self.assertNotIn("|", clean)
        for fragment in ("\x1b", "\x07", "\x00", "\u202e", "\u200b"):
            self.assertNotIn(fragment, clean)
        self.assertIn("red click", clean)
        self.assertIn("$pad(x) {{b}} <b>tag</b> #12", clean)
        self.assertIn(PIPE_REPLACEMENT, clean)


class TestNoteWriting(EvenniaTest):
    """The service enforces pen, ownership, locks, policy, and atomic records."""

    def setUp(self):
        super().setUp()
        self.note = make_item(self.char1, "letter", "note")
        self.pen = make_item(self.char1, "quill", "pen")

    def test_blank_written_and_rewritten_records(self):
        self.assertIsNone(note_record(self.note))
        self.assertIn("It is blank.", render_note(self.note, self.char1))
        with _immediate_commit():
            first = write_note(self.char1, self.note, "Hello")
        self.assertEqual(first["revision"], 1)
        self.assertEqual(first["character_id"], self.char1.id)
        self.assertEqual(first["account_id"], self.account.id)
        self.assertEqual(first["display_name"], self.char1.key)
        self.assertTrue(first["written_at"].endswith("+00:00"))
        with _immediate_commit():
            second = write_note(self.char1, self.note, "Goodbye")
        self.assertEqual((second["revision"], second["body"]), (2, "Goodbye"))
        self.assertEqual(note_record(self.note)["body"], "Goodbye")
        with self.assertRaises(NoteError):
            write_note(self.char1, self.note, "   |n  ")
        self.assertEqual(note_record(self.note)["body"], "Goodbye")

    def test_exact_and_over_body_limit(self):
        with override_settings(NOTE_BODY_MAX_LENGTH=5), _immediate_commit():
            write_note(self.char1, self.note, "12345")
            with self.assertRaises(NoteError):
                write_note(self.char1, self.note, "123456")
        self.assertEqual(note_record(self.note)["body"], "12345")

    def test_missing_and_nested_pen(self):
        self.pen.location = self.room1
        with self.assertRaisesRegex(NoteError, "pen"):
            write_note(self.char1, self.note, "text")
        bag = make_item(self.char1, "bag", "container")
        self.pen.location = bag
        with self.assertRaisesRegex(NoteError, "pen"):
            write_note(self.char1, self.note, "text")
        self.pen.locks.add("view:false()")
        self.pen.location = self.char1
        with self.assertRaisesRegex(NoteError, "pen"):
            write_note(self.char1, self.note, "text")
        self.assertIsNone(note_record(self.note))

    def test_ownership_lock_and_action_denials(self):
        self.note.location = self.room1
        with self.assertRaisesRegex(NoteError, "carrying"):
            write_note(self.char1, self.note, "text")
        self.note.location = self.char2
        with self.assertRaisesRegex(NoteError, "carrying"):
            write_note(self.char1, self.note, "text")
        self.note.location = self.char1
        self.note.locks.add("write:false()")
        with self.assertRaisesRegex(NoteError, "cannot write"):
            write_note(self.char1, self.note, "text")
        self.note.locks.add("write:all()")
        self.char1.db.position = "sleeping"
        with self.assertRaisesRegex(NoteError, "asleep"):
            write_note(self.char1, self.note, "text")
        self.char1.db.position = "standing"
        not_a_note = make_item(self.char1, "rock", "other")
        with self.assertRaises(NoteError):
            write_note(self.char1, not_a_note, "text")
        self.assertIsNone(note_record(self.note))

    def test_two_writers_last_committed_wins_and_concurrent_is_refused(self):
        make_item(self.char2, "pen", "pen")
        with _immediate_commit():
            write_note(self.char1, self.note, "First")
        self.note.location = self.char2
        with _immediate_commit():
            record = write_note(self.char2, self.note, "Second")
        self.assertEqual(record["character_id"], self.char2.id)
        self.assertEqual(record["revision"], 2)
        with item_mutation(self.note), self.assertRaisesRegex(NoteError, "using"):
            write_note(self.char2, self.note, "Third")
        self.assertEqual(note_record(self.note)["body"], "Second")

    def test_failed_write_rolls_back_to_previous_complete_record(self):
        with _immediate_commit():
            write_note(self.char1, self.note, "Original")
        messages = []

        def fail(note, record):
            note.attributes.add(RECORD_ATTRIBUTE, record)
            raise RuntimeError("write failed")

        with patch("systems.notes._store", side_effect=fail), patch(
            "systems.notes.transaction.on_commit", side_effect=messages.append
        ), self.assertRaises(RuntimeError):
            write_note(self.char1, self.note, "Replacement")
        self.assertEqual(note_record(self.note)["body"], "Original")
        self.assertEqual(note_record(self.note)["revision"], 1)
        self.assertEqual(messages, [])

    def test_authorship_persists_after_rename_and_reload(self):
        with _immediate_commit():
            write_note(self.char1, self.note, "Signed")
        original_name = self.char1.key
        self.char1.key = "Renamed"
        reloaded = ObjectDB.objects.get(pk=self.note.pk)
        record = note_record(reloaded)
        self.assertEqual(record["display_name"], original_name)
        self.assertEqual(record["character_id"], self.char1.id)
        self.assertIn(
            f"Last written by {original_name}.", render_note(reloaded, self.char2)
        )
        staff_view = describe_authorship(reloaded)
        self.assertIn("now Renamed", staff_view)
        self.assertIn(f"name when written: {original_name}", staff_view)

    def test_malformed_record_fails_closed(self):
        self.note.attributes.add(RECORD_ATTRIBUTE, {"body": "forged"})
        with self.assertRaises(NoteError):
            render_note(self.note, self.char1)
        with self.assertRaises(NoteError):
            write_note(self.char1, self.note, "text")
        self.assertIn("malformed", describe_authorship(self.note))

    def test_telnet_and_web_rendering_escape_stored_text(self):
        self.note.attributes.add(TITLE_ATTRIBUTE, "Title {x}")
        with _immediate_commit():
            write_note(
                self.char1,
                self.note,
                "|rred|n <script>alert(1)</script> {{b}} $pad(x) |lclook|ltL|le",
            )
        rendered = render_note(self.note, self.char2)
        telnet = parse_ansi(rendered, strip_ansi=True)
        self.assertIn("red <script>alert(1)</script> {{b}} $pad(x) L", telnet)
        self.assertIn("Title {x}", telnet)
        web = parse_html(rendered)
        self.assertNotIn("<script>", web)
        self.assertIn("&lt;script&gt;", web)
        self.assertNotIn("mxplink", web)
        self.assertNotIn("<a ", web)

    def test_staff_delete_needs_reason_and_logs(self):
        with self.assertRaises(NoteError):
            staff_delete_note(self.note, self.char1, "  ")
        with patch("systems.notes.logger.log_info") as log:
            staff_delete_note(self.note, self.char1, "abusive")
        self.assertIn("abusive", log.call_args.args[0])
        self.assertFalse(ObjectDB.objects.filter(pk=self.note.pk).exists())
        with self.assertRaises(NoteError):
            staff_delete_note(self.pen, self.char1, "not a note")


class TestNoteBuilder(EvenniaTest):
    """The title is a validated builder field that round-trips through templates."""

    def test_live_title_and_type_change_clears_writing(self):
        note = make_item(self.char1, "letter", "note")
        make_item(self.char1, "pen", "pen")
        _apply_field(
            note, "title", TYPE_FIELDS["note"]["title"], as_note_title("|rA Letter|n")
        )
        self.assertEqual(note.attributes.get(TITLE_ATTRIBUTE), "A Letter")
        with self.assertRaises(ValueError):
            as_note_title("x" * 81)
        with _immediate_commit():
            write_note(self.char1, note, "text")
        _set_item_type(note, "other")
        self.assertFalse(note.attributes.has(RECORD_ATTRIBUTE))
        self.assertFalse(note.attributes.has(TITLE_ATTRIBUTE))

    def test_prototype_round_trip_spawns_blank_titled_notes(self):
        key = "item06_test_note"
        self.addCleanup(delete_prototype, key)
        prototype = {
            "prototype_key": key,
            "key": "template letter",
            "typeclass": "typeclasses.objects.Item",
            "type": "note",
            "location": self.char1,
        }
        _apply_field(
            prototype, "title", TYPE_FIELDS["note"]["title"], as_note_title("Orders")
        )
        first, second = spawn(key)[0], spawn(key)[0]
        make_item(self.char1, "pen", "pen")
        with _immediate_commit():
            write_note(self.char1, first, "Only on the first")
        self.assertEqual(first.attributes.get(TITLE_ATTRIBUTE), "Orders")
        self.assertIsNone(note_record(second))
        self.assertNotIn(RECORD_ATTRIBUTE, search_prototype(key)[0])


class TestNoteCommands(EvenniaCommandTest):
    """Grammar, visibility, locks, and staff tools through the command runner."""

    def test_registration(self):
        for key in ("read", "write", "noteadmin"):
            self.assertIsNotNone(CharacterCmdSet().get(key))

    def test_write_then_read_from_inventory_and_room(self):
        note = make_item(self.char1, "letter", "note")
        make_item(self.char1, "quill", "pen")
        self.call(CmdRead(), "letter", "letter\nIt is blank.")
        with _immediate_commit():
            self.call(
                CmdWrite(), "letter = Meet at dawn.|/Bring rope.", "You write on letter"
            )
        self.call(
            CmdRead(),
            "letter",
            f"letter\nMeet at dawn.\nBring rope.\n(Last written by {self.char1.key}.)",
        )
        note.location = self.room1
        self.call(CmdRead(), "letter", "letter\nMeet at dawn.", caller=self.char2)

    def test_command_denials(self):
        note = make_item(self.char1, "letter", "note")
        self.call(CmdWrite(), "letter", "Usage: write <note> = <text>")
        self.call(CmdWrite(), "letter =   ", "Usage: write <note> = <text>")
        self.call(CmdWrite(), "letter = hi", "You need to be carrying a pen to write.")
        self.call(
            CmdWrite(), "ghost = hi", "You are not carrying one note by that name."
        )
        self.call(CmdRead(), "", "You do not see one thing by that name to read.")
        make_item(self.char1, "rock", "other")
        self.call(CmdRead(), "rock", "There is nothing written on that to read.")
        note.locks.add("read:false()")
        self.call(CmdRead(), "letter", "You cannot read that.")
        note.locks.add("read:all();view:false()")
        self.call(CmdRead(), "letter", "You do not see one thing by that name to read.")
        self.char1.db.position = "sleeping"
        self.call(CmdRead(), "letter", "You are asleep and cannot do that.")

    def test_reading_in_darkness_is_refused(self):
        make_item(self.char1, "letter", "note")
        with patch(
            "commands.notes.room_visibility",
            return_value=type("Decision", (), {"visible": False})(),
        ):
            self.call(CmdRead(), "letter", "It is too dark to read.")

    def test_staff_inspection_and_delete(self):
        note = make_item(self.char1, "letter", "note")
        make_item(self.char1, "quill", "pen")
        with _immediate_commit():
            write_note(self.char1, note, "abuse")
        self.call(CmdNoteAdmin(), "letter", f"letter (#{note.id}) titled 'letter'")
        self.call(CmdNoteAdmin(), "/delete letter", "Usage: noteadmin")
        self.call(
            CmdNoteAdmin(), "/delete letter = spam", f"Deleted letter (#{note.id})."
        )
        self.assertFalse(ObjectDB.objects.filter(pk=note.pk).exists())

    def test_builder_command_sets_title(self):
        key = "item06_editor_test"
        self.addCleanup(delete_prototype, key)
        self.call(CmdBuild(), f"new item {key}")
        self.call(CmdBuildSet(), "type note")
        self.call(CmdBuildSet(), "title Wanted Poster")
        self.assertEqual(self.char1.ndb._build_target[TITLE_ATTRIBUTE], "Wanted Poster")
        self.call(CmdBuildSet(), "title " + "x" * 81, "Invalid value for 'title'")
        self.assertEqual(self.char1.ndb._build_target[TITLE_ATTRIBUTE], "Wanted Poster")
