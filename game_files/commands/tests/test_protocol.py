"""Regression tests for client protocol rendering."""

from evennia.server.portal.mxp import mxp_parse
from evennia.utils.test_resources import EvenniaTest


class TestMxpRendering(EvenniaTest):
    """MXP must preserve ordinary text that resembles markup."""

    def test_literal_angle_brackets_are_not_html_escaped(self) -> None:
        """Command placeholders should remain readable in MXP-aware clients."""
        text = "edit #<dbref> & ic <name>"

        self.assertEqual(mxp_parse(text), text)
