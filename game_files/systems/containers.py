"""Shared policy helpers for ordinary item containers."""

from __future__ import annotations

from typing import Any

from systems.doors import DoorError, door_state
from systems.encumbrance import is_container


def container_is_open(container: Any) -> bool:
    """Return whether contents may move, failing closed on malformed state.

    Legacy containers without an INTERACT-01A record remain open so existing
    authored content does not become inaccessible when this system is enabled.
    """
    if not is_container(container):
        return False
    try:
        state = door_state(container)
    except DoorError:
        return False
    return state is None or state.open


def container_is_transparent(container: Any) -> bool:
    """Return the validated player-visible transparency flag."""
    return container.attributes.get("transparent", False) in (True, "on")


def can_access_contents(container: Any, actor: Any, *, insert: bool) -> bool:
    """Apply state and access locks for one ordinary container operation."""
    if not container_is_open(container):
        return False
    access_type = "put" if insert else "get"
    return container.access(actor, access_type, default=True) and container.access(
        actor, "view", default=True
    )
