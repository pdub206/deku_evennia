"""Persistent scripts used by game-wide systems.

Scripts are powerful jacks-of-all-trades. They have no in-game
existence and can be used to represent persistent game systems in some
circumstances. Scripts can also have a time component that allows them
to "fire" regularly or a limited number of times.

There is generally no "tree" of Scripts inheriting from each other.
Rather, each script tends to inherit from the base Script class and
just overloads its hooks to have it perform its function.

"""

from typing import Any

from django.conf import settings
from evennia.scripts.scripts import DefaultScript
from evennia.utils import logger
from systems.pulses import (PulseEvent, PulseLane, advance_pulse_state,
                            configured_cadences, initial_pulse_state,
                            process_effect_pulse,
                            process_resource_recovery_pulse)


class Script(DefaultScript):
    """
    This is the base TypeClass for all Scripts. Scripts describe
    all entities/systems without a physical existence in the game world
    that require database storage (like an economic system or
    combat tracker). They
    can also have a timer/ticker component.

    A script type is customized by redefining some or all of its hook
    methods and variables.

    * available properties (check docs for full listing, this could be
      outdated).

     key (string) - name of object
     name (string)- same as key
     aliases (list of strings) - aliases to the object. Will be saved
              to database as AliasDB entries but returned as strings.
     dbref (int, read-only) - unique #id-number. Also "id" can be used.
     date_created (string) - time stamp of object creation
     permissions (list of strings) - list of permission strings

     desc (string)      - optional description of script, shown in listings
     obj (Object)       - optional object that this script is connected to
                          and acts on (set automatically by obj.scripts.add())
     interval (int)     - how often script should run, in seconds. <0 turns
                          off ticker
     start_delay (bool) - if the script should start repeating right away or
                          wait self.interval seconds
     repeats (int)      - how many times the script should repeat before
                          stopping. 0 means infinite repeats
     persistent (bool)  - if script should survive a server shutdown or not
     is_active (bool)   - if script is currently running

    * Handlers

     locks - lock-handler: use locks.add() to add new lock strings
     db - attribute-handler: store/retrieve database attributes on this
                        self.db.myattr=val, val=self.db.myattr
     ndb - non-persistent attribute handler: same as db but does not
                        create a database entry when storing data

    * Helper methods

     create(key, **kwargs)
     start() - start script (this usually happens automatically at creation
               and obj.script.add() etc)
     stop()  - stop script, and delete it
     pause() - put the script on hold, until unpause() is called. If script
               is persistent, the pause state will survive a shutdown.
     unpause() - restart a previously paused script. The script will continue
                 from the paused timer (but at_start() will be called).
     time_until_next_repeat() - if a timed script (interval>0), returns time
                 until next tick

    * Hook methods (should also include self as the first argument):

     at_script_creation() - called only once, when an object of this
                            class is first created.
     is_valid() - is called to check if the script is valid to be running
                  at the current time. If is_valid() returns False, the running
                  script is stopped and removed from the game. You can use this
                  to check state changes (i.e. an script tracking some combat
                  stats at regular intervals is only valid to run while there is
                  actual combat going on).
      at_start() - Called every time the script is started, which for persistent
                  scripts is at least once every server start. Note that this is
                  unaffected by self.delay_start, which only delays the first
                  call to at_repeat().
      at_repeat() - Called every self.interval seconds. It will be called
                  immediately upon launch unless self.delay_start is True, which
                  will delay the first call of this method by self.interval
                  seconds. If self.interval==0, this method will never
                  be called.
      at_pause()
      at_stop() - Called as the script object is stopped and is about to be
                  removed from the game, e.g. because is_valid() returned False.
      at_script_delete()
      at_server_reload() - Called when server reloads. Can be used to
                  save temporary variables you want should survive a reload.
      at_server_shutdown() - called at a full server shutdown.
      at_server_start()

    """

    pass


class GamePulseScript(Script):
    """Dispatch every recurring live-world system from one persistent timer."""

    def at_script_creation(self) -> None:
        """Configure the timer and initialize its versioned persistent state."""
        self.desc = "Central scheduler for recurring live-world systems."
        self.interval = getattr(settings, "GAME_PULSE_INTERVAL_SECONDS", 1)
        self.start_delay = True
        self.repeats = 0
        self.persistent = True
        self.db.pulse_state = initial_pulse_state()

    def at_repeat(self, **kwargs: Any) -> None:
        """Persist the next tokens, then isolate and dispatch every due lane."""
        state, events = advance_pulse_state(self.db.pulse_state, configured_cadences())
        # Persist before running consumers. A crash may skip work but cannot
        # replay a token and duplicate combat, rewards, decay, or reset actions.
        self.db.pulse_state = state
        for event in events:
            try:
                self._dispatch(event)
            except Exception:
                logger.log_trace(
                    f"Game pulse lane '{event.lane.value}' failed at token "
                    f"{event.sequence} during heartbeat {event.heartbeat}."
                )

    def _dispatch(self, event: PulseEvent) -> None:
        """Route a validated event to its stable lane extension hook."""
        handlers = {
            PulseLane.COMBAT: self.at_combat_pulse,
            PulseLane.RECOVERY: self.at_recovery_pulse,
            PulseLane.MOBILES: self.at_mobiles_pulse,
            PulseLane.EFFECTS: self.at_effects_pulse,
            PulseLane.CORPSES: self.at_corpses_pulse,
            PulseLane.WORLD_TIME: self.at_world_time_pulse,
            PulseLane.WEATHER: self.at_weather_pulse,
            PulseLane.RESETS: self.at_resets_pulse,
        }
        handlers[event.lane](event)

    def at_combat_pulse(self, event: PulseEvent) -> None:
        """Run combat work supplied by COMBAT-01."""

    def at_recovery_pulse(self, event: PulseEvent) -> None:
        """Run resource recovery supplied by RULES-04."""
        process_resource_recovery_pulse(event)

    def at_mobiles_pulse(self, event: PulseEvent) -> None:
        """Run mobile behavior supplied by MOB-01."""

    def at_effects_pulse(self, event: PulseEvent) -> None:
        """Advance persistent timed effects for PCs and NPCs."""
        process_effect_pulse(event)

    def at_corpses_pulse(self, event: PulseEvent) -> None:
        """Run corpse decay supplied by COMBAT-05."""

    def at_world_time_pulse(self, event: PulseEvent) -> None:
        """Advance the persisted clock supplied by ENV-01."""

    def at_weather_pulse(self, event: PulseEvent) -> None:
        """Run weather transitions supplied by ENV-02."""

    def at_resets_pulse(self, event: PulseEvent) -> None:
        """Run area resets supplied by AREA-03."""
