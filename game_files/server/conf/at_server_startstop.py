"""
Server startstop hooks

This module contains functions called by Evennia at various
points during its startup, reload and shutdown sequence. It
allows for customizing the server operation as desired.

This module must contain at least these global functions:

at_server_init()
at_server_start()
at_server_stop()
at_server_reload_start()
at_server_reload_stop()
at_server_cold_start()
at_server_cold_stop()

"""

from systems.lifecycle import (ServerTransitionMode, prepare_server_transition,
                               recover_server_transition)


def at_server_init():
    """
    This is called first as the server is starting up, regardless of how.
    """
    pass


def at_server_start():
    """
    This is called every time the server starts up, regardless of
    how it was shut down.
    """
    pass


def at_server_stop():
    """
    This is called just before the server is shut down, regardless
    of it is for a reload, reset or shutdown.
    """
    pass


def at_server_reload_start() -> None:
    """Recover reload-safe systems without replaying completed work."""
    recover_server_transition(ServerTransitionMode.HOT_RELOAD)


def at_server_reload_stop() -> None:
    """Give reload-safe systems one idempotent preparation event."""
    prepare_server_transition(ServerTransitionMode.HOT_RELOAD)


def at_server_cold_start() -> None:
    """Recover persistent world state without catching up downtime."""
    recover_server_transition(ServerTransitionMode.COLD_RESTART)


def at_server_cold_stop() -> None:
    """Prepare persistent state for a cold shutdown or reset."""
    prepare_server_transition(ServerTransitionMode.COLD_RESTART)
