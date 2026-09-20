"""ENV-02 weather persistence and consumer integration coverage."""

from unittest.mock import patch

from django.test import override_settings
from evennia import create_object
from evennia.utils.test_resources import EvenniaCommandTest, EvenniaTest
from systems.areas import assign_area
from systems.pulses import PulseEvent, PulseLane
from systems.room_environment import set_room_environment_value
from systems.travel import travel_decision
from systems.visibility import passive_perception
from systems.weather import (
    WEATHER_ATTRIBUTE,
    WeatherState,
    blocks_directional_view,
    current_state,
    perception_modifier,
    process_weather_pulse,
    set_weather_profile,
    travel_multiplier,
)
from typeclasses.exits import Exit
from typeclasses.scripts import GamePulseScript


class TestWeather(EvenniaTest):
    """Area state is durable, isolated, and only affects outdoor consumers."""

    script_typeclass = GamePulseScript

    def setUp(self) -> None:
        super().setUp()
        self.script.key = "game_pulse"
        assign_area(self.room1, "test_weather")
        assign_area(self.room2, "test_weather")
        set_weather_profile(self.room1, "temperate")
        self.exit = create_object(
            Exit, key="north", location=self.room1, destination=self.room2
        )

    def pulse(self, token: int, chooser) -> None:
        process_weather_pulse(
            self.script,
            PulseEvent(token * 300, PulseLane.WEATHER, token),
            chooser=chooser,
        )

    def test_all_states_are_injected_and_replays_do_not_transition(self) -> None:
        for token, state in enumerate(
            (
                WeatherState.CLOUDY,
                WeatherState.RAIN,
                WeatherState.STORM,
                WeatherState.RAIN,
                WeatherState.CLOUDY,
                WeatherState.FOG,
            ),
            1,
        ):
            self.pulse(token, lambda _options, state=state: state)
            self.assertEqual(current_state(self.room1), state)
        self.pulse(6, lambda _options: WeatherState.CLEAR)
        self.assertEqual(current_state(self.room1), WeatherState.FOG)
        record = self.script.attributes.get(WEATHER_ATTRIBUTE)["test_weather"]
        self.assertEqual(record["last_token"], 6)

    def test_penalties_protection_indoor_and_directional_weather(self) -> None:
        self.script.attributes.add(
            WEATHER_ATTRIBUTE,
            {
                "test_weather": {
                    "version": 1,
                    "state": "storm",
                    "next_token": 2,
                    "last_token": 1,
                    "transition_id": "test",
                    "profile": "temperate",
                }
            },
        )
        self.assertEqual(perception_modifier(self.room1, self.char1), -5)
        self.assertEqual(travel_multiplier(self.room1, self.char1), 1.5)
        self.assertTrue(blocks_directional_view(self.room1))
        self.char1.db.passive_perception_override = 10
        self.assertEqual(passive_perception(self.char1, self.room1), 5)
        self.assertEqual(travel_decision(self.char1, self.exit).delay, 2)
        with patch("systems.weather.has_equipment_capability", return_value=True):
            self.assertEqual(perception_modifier(self.room1, self.char1), 0)
            self.assertEqual(travel_multiplier(self.room1, self.char1), 1.0)
        set_room_environment_value(self.room1, "indoors", True)
        self.assertIsNone(current_state(self.room1))
        self.assertEqual(travel_multiplier(self.room1, self.char1), 1.0)

    @override_settings(GAME_WEATHER_TRANSITION_TOKENS=2)
    def test_area_isolation_and_next_eligible_token(self) -> None:
        other = create_object("typeclasses.rooms.Room", key="other")
        assign_area(other, "other_weather")
        set_weather_profile(other, "temperate")
        self.pulse(1, lambda _options: WeatherState.CLOUDY)
        self.assertEqual(current_state(self.room1), WeatherState.CLOUDY)
        self.assertEqual(current_state(other), WeatherState.CLOUDY)
        self.pulse(2, lambda _options: WeatherState.RAIN)
        self.assertEqual(current_state(self.room1), WeatherState.CLOUDY)
        self.pulse(3, lambda _options: WeatherState.RAIN)
        self.assertEqual(current_state(other), WeatherState.RAIN)


class TestWeatherCommand(EvenniaCommandTest):
    """The command never advances state and has an indoor-safe answer."""

    def test_weather_output(self) -> None:
        from commands.weather import CmdWeather

        self.call(CmdWeather(), "", "You cannot judge the weather from here.")
