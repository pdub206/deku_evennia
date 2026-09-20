"""Strict settings-selected routing for start, respawn, and recall rooms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from evennia.utils.search import search_tag
from systems.areas import AREA_TAG_CATEGORY, ROOM_KEY_CATEGORY

ROOM_ROLE_SETTINGS = (
    "CHARACTER_START_ROOM",
    "COMBAT_RESPAWN_SANCTUARY",
    "RECALL_DESTINATION",
)


class RoomRoleError(ValueError):
    """A configured room role is malformed, missing, stale, or ambiguous."""


@dataclass(frozen=True)
class RoomRoleResult:
    """One validated setting and its unique room."""

    setting: str
    reference: str
    room: Any


def resolve_room_role(setting_name: str) -> RoomRoleResult:
    """Resolve exactly one ``area:room_key`` without any fallback."""
    if setting_name not in ROOM_ROLE_SETTINGS:
        raise RoomRoleError("Unknown room-role setting.")
    configured = getattr(settings, setting_name, None)
    if not isinstance(configured, str) or configured.count(":") != 1:
        raise RoomRoleError(f"{setting_name} must be one area:room_key string.")
    area, room_key = (part.strip().lower() for part in configured.split(":"))
    if not area or not room_key:
        raise RoomRoleError(f"{setting_name} contains an empty area or room key.")
    matches = [
        room
        for room in search_tag(room_key, category=ROOM_KEY_CATEGORY)
        if room.tags.has(area, category=AREA_TAG_CATEGORY)
        and room.is_typeclass("typeclasses.rooms.Room", exact=False)
    ]
    if len(matches) != 1:
        raise RoomRoleError(
            f"{setting_name} '{configured}' resolved to {len(matches)} rooms."
        )
    return RoomRoleResult(setting_name, f"{area}:{room_key}", matches[0])


def validate_room_roles() -> tuple[RoomRoleResult, ...]:
    """Validate all three independent roles, allowing an intentional shared room."""
    return tuple(resolve_room_role(name) for name in ROOM_ROLE_SETTINGS)
