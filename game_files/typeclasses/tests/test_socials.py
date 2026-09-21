"""Pure registry and safe-rendering coverage for COMM-02 socials."""

from evennia.utils.test_resources import EvenniaTest
from systems.socials import (
    SOCIALS,
    SocialDefinition,
    SocialTemplates,
    safe_display_name,
    validate_social,
)


class TestSocialRegistry(EvenniaTest):
    """The release list is immutable, fixed, and accepts no executable templates."""

    def test_registry_contains_only_the_released_ordered_verbs(self):
        """Player-visible listing is stable across reload reconstruction."""
        self.assertEqual(
            tuple(SOCIALS),
            (
                "bow",
                "grin",
                "laugh",
                "nod",
                "salute",
                "shake",
                "shrug",
                "sigh",
                "smile",
                "thank",
                "wave",
                "wink",
            ),
        )
        with self.assertRaises(TypeError):
            SOCIALS["dance"] = SOCIALS["bow"]

    def test_validation_rejects_executable_or_untrusted_template_shapes(self):
        """Only fixed actor and target display slots may be released."""
        definition = SocialDefinition(
            "test",
            (),
            SocialTemplates("You test {evil}.", "x", "x", "x", "x", "x", "x"),
        )
        with self.assertRaises(ValueError):
            validate_social(definition)

    def test_display_names_cannot_inject_client_markup_or_controls(self):
        """Template output never treats a player-controlled name as protocol text."""
        self.char1.get_display_name = lambda _viewer: "|rBad\nName|n"
        self.assertEqual(safe_display_name(self.char1, self.char2), "BadName")
