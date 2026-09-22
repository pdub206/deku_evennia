"""Command-level regression coverage for GROUP-01A following."""

from unittest.mock import patch

from commands.default_cmdsets import CharacterCmdSet
from commands.following import CmdFollow, CmdFollowAdmin, CmdUnfollow
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest
from systems.player_following import FollowOutcome, decline, request


class TestFollowingCommands(EvenniaCommandTest):
    """Exercise consent messages and the Builder recovery surface."""

    def setUp(self):
        super().setUp()
        self.staff = self.char1
        self.leader = create_object(
            "typeclasses.characters.Character", key="Leader", location=self.room1
        )
        self.follower = create_object(
            "typeclasses.characters.Character", key="Follower", location=self.room1
        )
        self.leader.db.is_player_character = True
        self.follower.db.is_player_character = True
        self.leader.db.senses = ["darkvision"]
        self.follower.db.senses = ["darkvision"]

    def test_request_accept_and_unfollow_messages(self):
        """Following remains consensual and either endpoint can end it."""
        self.assertTrue(request(self.follower, self.leader).accepted)
        self.assertTrue(decline(self.leader, self.follower).accepted)
        follow_request = CmdFollow()
        follow_request.switches = []
        with patch(
            "commands.following.request",
            return_value=FollowOutcome("accepted", "requested"),
        ):
            self.call(
                follow_request,
                "Leader",
                {
                    self.follower: "You ask to follow Leader.",
                    self.leader: "Follower asks to follow you. Type follow/accept Follower or follow/decline Follower.",
                },
                caller=self.follower,
            )
        with patch(
            "commands.following.accept",
            return_value=FollowOutcome("accepted", "linked"),
        ):
            follow_accept = CmdFollow()
            follow_accept.switches = ["accept"]
            self.call(
                follow_accept,
                "Follower",
                {
                    self.leader: "You allow Follower to follow you.",
                    self.follower: "Leader allows you to follow them.",
                },
                caller=self.leader,
            )
        with patch(
            "commands.following.unlink",
            return_value=FollowOutcome("accepted", "unlinked"),
        ):
            self.call(
                CmdUnfollow(),
                "Follower",
                {
                    self.leader: "You stop Follower from following you.",
                    self.follower: "Leader stops you from following them.",
                },
                caller=self.leader,
            )

    def test_admin_command_is_registered_locked_and_repairs(self):
        """Only Builders receive the explicit diagnostic/repair command."""
        cmdset = CharacterCmdSet()
        cmdset.at_cmdset_creation()
        self.assertTrue(any(command.key == "@follow" for command in cmdset.commands))
        self.assertTrue(CmdFollowAdmin().access(self.staff, "cmd"))
        self.follower.permissions.clear()
        self.assertFalse(CmdFollowAdmin().access(self.follower, "cmd"))

        self.follower.db.group01_follow = "broken"
        output = self.call(CmdFollowAdmin(), f"#{self.follower.id}", caller=self.staff)
        self.assertIn("follow state is invalid", output)
        output = self.call(
            CmdFollowAdmin(), f"/repair #{self.follower.id}", caller=self.staff
        )
        self.assertIn("Repaired Follower's follow state", output)
