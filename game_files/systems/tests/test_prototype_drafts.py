"""AREA-05B draft persistence, conflicts, and atomic deterministic export."""

from pathlib import Path
from tempfile import TemporaryDirectory

from evennia.server.models import ServerConfig
from evennia.utils.test_resources import EvenniaTest
from systems.prototype_drafts import (
    PrototypeDraftError,
    begin_draft,
    draft_diff,
    export_drafts,
    save_draft,
)


class TestPrototypeDrafts(EvenniaTest):
    """Source edits remain database drafts until an explicit export."""

    def setUp(self):
        super().setUp()
        ServerConfig.objects.conf("area05b_prototype_drafts", delete=True)

    def tearDown(self):
        ServerConfig.objects.conf("area05b_prototype_drafts", delete=True)
        super().tearDown()

    def test_edit_diff_and_export_are_deterministic(self):
        draft = begin_draft("dagger", self.account)
        draft["value"] = 3
        save_draft(draft)
        self.assertEqual(draft_diff("dagger"), ["value"])

        with TemporaryDirectory() as directory:
            path = Path(directory) / "prototypes.py"
            self.assertEqual(export_drafts(path), ("dagger",))
            first = path.read_text(encoding="utf-8")
            self.assertEqual(export_drafts(path), ("dagger",))
            self.assertEqual(path.read_text(encoding="utf-8"), first)
        self.assertNotIn("_area05b_draft", first)

    def test_draft_identity_cannot_be_changed(self):
        draft = begin_draft("dagger", self.account)
        draft["prototype_key"] = "counterfeit"
        with self.assertRaisesRegex(PrototypeDraftError, "identity"):
            save_draft(draft)
