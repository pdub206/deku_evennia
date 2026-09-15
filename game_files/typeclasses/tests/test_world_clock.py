"""ENV-01 calendar, persistence, dispatch, and presentation contracts."""

from unittest.mock import patch

from commands.default_cmdsets import CharacterCmdSet
from commands.world_time import CmdTime
from django.test import override_settings
from evennia.utils.test_resources import EvenniaCommandTest, EvenniaTest
from systems import world_clock
from systems.pulses import PulseEvent, PulseLane
from systems.room_environment import LightLevel
from systems.visibility import daylight_level
from systems.world_clock import (
    CLOCK_ATTRIBUTE,
    ClockBoundary,
    WorldClockError,
    advance_world_clock,
    clock_configuration,
    clock_presentation,
    clock_state,
    initial_clock_state,
    is_daylight,
    reconcile_clock,
    register_clock_consumer,
)
from typeclasses.scripts import GamePulseScript


class TestWorldClock(EvenniaTest):
    """Tokens, never wall time, advance a validated primitive calendar."""

    script_typeclass = GamePulseScript

    def setUp(self) -> None:
        super().setUp()
        self.saved_consumers = world_clock._CONSUMERS.copy()
        world_clock._CONSUMERS.clear()
        self.addCleanup(world_clock._CONSUMERS.update, self.saved_consumers)
        self.addCleanup(world_clock._CONSUMERS.clear)

    def seed(self, minute: int) -> None:
        self.script.attributes.add(
            CLOCK_ATTRIBUTE, {**initial_clock_state(), "minute": minute}
        )

    def pulse(self, token: int = 1):
        return advance_world_clock(
            self.script, PulseEvent(token * 60, PulseLane.WORLD_TIME, token)
        )

    def test_default_step_and_read_only_epoch(self):
        self.assertEqual(clock_configuration()["step"], 6)
        self.assertEqual(clock_state(self.script)["minute"], 720)
        self.assertFalse(self.script.attributes.has(CLOCK_ATTRIBUTE))
        self.pulse()
        self.assertEqual(clock_state(self.script)["minute"], 726)

    def test_duplicate_out_of_order_reload_and_missing_tokens(self):
        self.pulse(5)
        self.assertEqual(self.pulse(5), ())
        self.assertEqual(self.pulse(2), ())
        self.script.at_server_start()
        self.assertEqual(clock_state(self.script)["minute"], 726)
        self.pulse(20)
        self.assertEqual(clock_state(self.script)["minute"], 732)

    def test_every_hour_and_special_boundary(self):
        for hour in range(24):
            minute = 1440 + hour * 60
            self.seed(minute - 1)
            kinds = [event.kind for event in self.pulse()]
            self.assertEqual(
                kinds,
                ["hour"] + ({0: ["day"], 6: ["dawn"], 18: ["dusk"]}.get(hour, [])),
            )

    def test_calendar_rollovers_and_daylight(self):
        for minute, expected in [
            (0, "Year 1, month 1, day 1, 00:00 (night)."),
            (30 * 1440, "Year 1, month 2, day 1, 00:00 (night)."),
            (360 * 1440, "Year 2, month 1, day 1, 00:00 (night)."),
            (360 * 1440 - 1, "Year 1, month 12, day 30, 23:59 (night)."),
        ]:
            self.assertEqual(clock_presentation(minute), expected)
        self.assertEqual(
            [is_daylight(m) for m in (359, 360, 1079, 1080)], [False, True, True, False]
        )

    def test_dispatch_order_failure_isolation_and_persist_before_delivery(self):
        seen = []

        def failing(event: ClockBoundary) -> None:
            seen.append(("a", event.identity, clock_state(self.script)["minute"]))
            raise RuntimeError("test")

        def successful(event: ClockBoundary) -> None:
            seen.append(("b", event.identity, clock_state(self.script)["minute"]))

        register_clock_consumer("b", frozenset({"dawn"}), successful)
        register_clock_consumer("a", frozenset({"dawn"}), failing)
        register_clock_consumer("b", frozenset({"dawn"}), successful)
        self.seed(359)
        with patch("systems.world_clock.logger.log_trace") as trace:
            self.pulse()
        self.assertEqual(seen, [("a", "360/0:dawn", 365), ("b", "360/0:dawn", 365)])
        trace.assert_called_once()
        self.pulse()
        self.assertEqual(len(seen), 2)

    def test_quarantine_and_explicit_scale_reconciliation(self):
        self.pulse()
        before = clock_state(self.script)
        with override_settings(
            GAME_CLOCK_REAL_SECONDS_PER_HOUR=300, GAME_CLOCK_SCALE_VERSION=2
        ):
            with self.assertRaises(WorldClockError):
                self.pulse(2)
            reconcile_clock(self.script)
            self.pulse(2)
            self.assertEqual(clock_state(self.script)["minute"], before["minute"] + 12)
        self.script.db.world_clock = {"version": 99}
        with self.assertRaises(WorldClockError):
            self.pulse(3)
        self.assertEqual(self.script.db.world_clock, {"version": 99})

    @override_settings(GAME_CLOCK_REAL_SECONDS_PER_HOUR=601)
    def test_fractional_scale_denied(self):
        with self.assertRaises(WorldClockError):
            clock_configuration()

    def test_wrong_lane_and_boolean_token_denied(self):
        for event in (
            PulseEvent(1, PulseLane.WEATHER, 1),
            PulseEvent(1, PulseLane.WORLD_TIME, True),
        ):
            with self.assertRaises(WorldClockError):
                advance_world_clock(self.script, event)

    def test_daylight_adapter_indoor_and_outdoor(self):
        from systems.room_environment import default_room_environment

        self.room1.db.room_environment = {
            **default_room_environment(),
            "indoors": False,
        }
        with patch("systems.world_clock.clock_owner", return_value=self.script):
            self.seed(360)
            self.assertEqual(daylight_level(self.room1), LightLevel.BRIGHT)
            self.seed(1080)
            self.assertEqual(daylight_level(self.room1), LightLevel.DARK)
            self.room1.db.room_environment = {
                **default_room_environment(),
                "indoors": True,
            }
            self.assertIsNone(daylight_level(self.room1))

    @override_settings(GAME_CLOCK_REAL_SECONDS_PER_HOUR=60)
    def test_multiple_boundaries_recharge_and_reset_hooks(self):
        events = []
        register_clock_consumer("recharge", frozenset({"dawn"}), events.append)
        register_clock_consumer("area_reset", frozenset({"day"}), events.append)
        self.seed(1439)
        boundaries = self.pulse()
        self.assertEqual([event.kind for event in boundaries], ["hour", "day"])
        self.seed(359)
        self.pulse(2)
        self.assertEqual([event.kind for event in events], ["day", "dawn"])
        self.assertEqual(len({event.identity for event in events}), 2)

    def test_mobile_schedule_payload_and_owner_isolation(self):
        from evennia import create_object
        from systems.mobile_specials import MOBILE_SPECIALS_ATTRIBUTE
        from typeclasses.characters import Character

        first = create_object(Character, key="clock npc", location=self.room1)
        second = create_object(Character, key="clock npc two", location=self.room1)
        for npc in (first, second):
            npc.db.is_player_character = False
            npc.attributes.add(
                MOBILE_SPECIALS_ATTRIBUTE, {"version": 1, "specials": []}
            )
        with patch(
            "systems.mobile_specials.dispatch_specials",
            side_effect=[RuntimeError("bad owner"), None],
        ) as dispatch, patch("systems.world_clock.logger.log_trace"):
            world_clock._mobile_schedule(ClockBoundary(1800, "hour"))
        self.assertEqual(dispatch.call_count, 2)
        event = dispatch.call_args.args[1]
        self.assertEqual(event.service, "schedule")
        self.assertEqual(
            dict(event.data), {"day": 1, "hour": 6, "identity": "1800/1:hour"}
        )

    def test_reentrant_delivery_cannot_replay_token(self):
        delivered = []

        def consumer(event: ClockBoundary) -> None:
            delivered.append(event.identity)
            self.assertEqual(self.pulse(), ())

        register_clock_consumer("reentrant", frozenset({"dawn"}), consumer)
        self.seed(359)
        self.pulse()
        self.assertEqual(delivered, ["360/0:dawn"])

    def test_shop_hour_adapter(self):
        from systems.shops import current_shop_hour

        self.seed(1234)
        with patch("systems.world_clock.clock_owner", return_value=self.script):
            self.assertEqual(current_shop_hour(), 20)


class TestTimeCommand(EvenniaCommandTest):
    """The command is registered, state independent, and hides broken state."""

    def test_time_and_usage(self):
        with patch("commands.world_time.clock_state", return_value={"minute": 720}):
            self.call(CmdTime(), "", "Year 1, month 1, day 1, 12:00 (daylight).")
            self.call(CmdTime(), "change", "Usage: time.")
        self.assertTrue(CharacterCmdSet().get("time"))

    def test_unavailable(self):
        with patch(
            "commands.world_time.clock_state", side_effect=WorldClockError("internal")
        ):
            self.call(
                CmdTime(), "", "The world clock is unavailable; please notify staff."
            )
