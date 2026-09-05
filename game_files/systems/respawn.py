"""COMBAT-06 terminal death, sanctuary respawn, and link-dead presence.

The module deliberately stores only durable primitive state on characters.
The recovery lane is the sole clock: server downtime therefore never advances
link-dead removal and reloads cannot replay a completed removal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from evennia.utils import logger
from evennia.utils.search import search_tag
from systems.combat import handle_departure, is_fighting
from systems.corpses import CorpseError, corpse_record
from systems.injury import (
    InjuryError,
    InjuryState,
    injury_record,
    repair_injury,
)
from systems.lifecycle import (
    CharacterAvailability,
    CharacterLifecycleEvent,
    LifecycleConsumer,
    LifecycleError,
    UnavailabilityCause,
    register_lifecycle_consumer,
    unregister_lifecycle_consumer,
)
from systems.pulses import PulseEvent, PulseLane

LINKDEAD_ATTRIBUTE = "combat_linkdead"
LINKDEAD_VERSION = 1
RESPAWN_LIFECYCLE_KEY = "combat06.respawn"
DEFAULT_LINKDEAD_MINUTES = 30


class RespawnError(ValueError):
    """A persistent respawn or link-dead transaction cannot proceed safely."""


@dataclass(frozen=True)
class LinkdeadRecord:
    """One disconnected PC's pulse-token based logout state."""

    start_pulse: int | None
    last_pulse: int
    removal_due: bool = False
    stowed: bool = False
    return_location_id: int | None = None
    recover_stable: bool = False


RespawnResourcePolicy = Callable[[Any], None]
_RESOURCE_POLICIES: dict[str, RespawnResourcePolicy] = {}


def register_respawn_resource_policy(key: str, policy: RespawnResourcePolicy) -> None:
    """Register a named, explicit resource reset policy for future systems."""
    if not isinstance(key, str) or not key or not callable(policy):
        raise RespawnError("A respawn resource policy needs a key and callable.")
    _RESOURCE_POLICIES[key] = policy


def final_death(owner: Any, corpse: Any | None) -> None:
    """Finish a death only after the idempotent corpse transaction is ready."""
    if corpse is None:
        return
    try:
        if corpse_record(corpse).status != "ready":
            return
    except CorpseError:
        logger.log_err(f"COMBAT-06 cannot finalize malformed corpse for #{owner.id}.")
        return
    if _is_pc(owner):
        _return_controller_to_ooc(owner)
        return
    # A failed delete remains recoverable and is retried when corpse creation is
    # retried through the same death identity; never extract before assets move.
    try:
        owner.delete()
    except Exception:
        logger.log_trace(f"COMBAT-06 could not extract dead NPC #{owner.id}.")


def prepare_entry(character: Any) -> None:
    """Atomically reconstruct a PC before its account puppets or shows a room."""
    try:
        record = injury_record(character)
    except InjuryError as err:
        raise RespawnError("Character injury state needs staff repair.") from err
    if record.state is InjuryState.DEAD:
        _respawn_dead(character)
    linkdead = _linkdead_record(character, required=False)
    if linkdead is not None and linkdead.stowed:
        _restore_stowed(character, linkdead)


def process_linkdead_pulse(event: PulseEvent) -> None:
    """Advance all link-dead PCs once for one durable recovery token."""
    if not isinstance(event, PulseEvent) or event.lane is not PulseLane.RECOVERY:
        raise RespawnError("Link-dead processing requires a recovery pulse.")
    from typeclasses.characters import Character

    for character in (
        Character.objects.filter_family(db_attributes__db_key=LINKDEAD_ATTRIBUTE)
        .distinct()
        .iterator()
    ):
        try:
            record = _linkdead_record(character, required=True)
            if record is None or record.stowed or record.last_pulse >= event.sequence:
                continue
            # A disconnect can occur between global pulses. Anchor it at the
            # next durable token rather than guessing from wall time or from a
            # pre-reload sequence number; downtime then cannot consume time.
            if record.start_pulse is None:
                _write_linkdead(
                    character,
                    LinkdeadRecord(
                        event.sequence,
                        event.sequence,
                        record.removal_due,
                        record.stowed,
                        record.return_location_id,
                        record.recover_stable,
                    ),
                )
                continue
            updated = LinkdeadRecord(
                record.start_pulse,
                event.sequence,
                record.removal_due,
                record.stowed,
                record.return_location_id,
                record.recover_stable,
            )
            if event.sequence - record.start_pulse < _linkdead_pulses():
                _write_linkdead(character, updated)
                continue
            _stow_if_eligible(character, updated)
        except Exception:
            logger.log_trace(
                f"COMBAT-06 link-dead processing failed for #{character.id} at "
                f"recovery token {event.sequence}."
            )


def _respawn_dead(character: Any) -> None:
    """Perform the sole sanctuary respawn transaction before puppeting."""
    sanctuary = _resolve_sanctuary()
    if sanctuary is None:
        raise RespawnError("The configured sanctuary is unavailable; contact staff.")
    # Relocation precedes healing so a failure cannot create a revived character
    # in an unknown location.  The named move bypasses ordinary voluntary policy.
    if character.location is not sanctuary and not character.move_to(
        sanctuary, quiet=True, move_type="respawn", respawn=True
    ):
        raise RespawnError("The sanctuary could not receive this character.")
    handle_departure(character)
    character.stats.set_hp(character.stats.hp_max)
    repair_injury(character, state=InjuryState.CONSCIOUS)
    character.db.position = "resting"
    character.effects.clear_for_death()
    for policy in tuple(_RESOURCE_POLICIES.values()):
        policy(character)


def _restore_stowed(character: Any, record: LinkdeadRecord) -> None:
    """Return a safely logged-out character to its original room once."""
    from evennia.objects.models import ObjectDB

    location = ObjectDB.objects.filter(id=record.return_location_id).first()
    if location is None or not character.move_to(
        location, quiet=True, move_type="linkdead_return", linkdead_return=True
    ):
        raise RespawnError("The saved logout location is unavailable; contact staff.")
    if record.recover_stable:
        character.stats.set_hp(1)
        repair_injury(character, state=InjuryState.CONSCIOUS)
        character.db.position = "resting"
    character.attributes.remove(LINKDEAD_ATTRIBUTE)


def _stow_if_eligible(character: Any, record: LinkdeadRecord) -> None:
    """Defer expiry during combat/dying and stow only a safe stable survivor."""
    injury = injury_record(character)
    if injury.state is InjuryState.DEAD:
        return
    if is_fighting(character) or injury.state is InjuryState.DYING:
        _write_linkdead(
            character,
            LinkdeadRecord(record.start_pulse, record.last_pulse, True),
        )
        return
    location_id = getattr(character.location, "id", None)
    if not isinstance(location_id, int) or location_id < 1:
        raise RespawnError("A link-dead character has no valid room to preserve.")
    if not character.move_to(None, to_none=True, move_type="linkdead_stow"):
        raise RespawnError("A link-dead character could not be stowed.")
    _write_linkdead(
        character,
        LinkdeadRecord(
            record.start_pulse,
            record.last_pulse,
            True,
            True,
            location_id,
            injury.state is InjuryState.INCAPACITATED,
        ),
    )


def _on_character_lifecycle(event: CharacterLifecycleEvent) -> None:
    """Create/cancel presence state only around final network disconnection."""
    if not _is_pc(event.character):
        return
    if event.availability is CharacterAvailability.AVAILABLE:
        record = _linkdead_record(event.character, required=False)
        if record is not None and not record.stowed:
            event.character.attributes.remove(LINKDEAD_ATTRIBUTE)
        return
    if event.cause is UnavailabilityCause.DISCONNECT:
        _write_linkdead(event.character, LinkdeadRecord(None, 0))


def _return_controller_to_ooc(character: Any) -> None:
    """Unpuppet the sole live controller after all death assets are committed."""
    for session in tuple(character.sessions.all()):
        account = getattr(session, "account", None)
        if account is None:
            continue
        try:
            account.unpuppet_object(session)
        except Exception:
            logger.log_trace(f"COMBAT-06 could not OOC dead PC #{character.id}.")


def _resolve_sanctuary() -> Any | None:
    """Resolve exactly one stable ``area:room_key`` setting, never a home."""
    configured = getattr(settings, "COMBAT_RESPAWN_SANCTUARY", None)
    if not isinstance(configured, str) or configured.count(":") != 1:
        logger.log_err("COMBAT_RESPAWN_SANCTUARY must be one area:room_key string.")
        return None
    area, room_key = (part.strip().lower() for part in configured.split(":"))
    if not area or not room_key:
        logger.log_err("COMBAT_RESPAWN_SANCTUARY has an empty area or room key.")
        return None
    from systems.areas import AREA_TAG_CATEGORY, ROOM_KEY_CATEGORY

    matches = [
        room
        for room in search_tag(room_key, category=ROOM_KEY_CATEGORY)
        if room.tags.has(area, category=AREA_TAG_CATEGORY)
    ]
    if len(matches) != 1:
        logger.log_err(
            f"COMBAT_RESPAWN_SANCTUARY '{configured}' resolved to {len(matches)} rooms."
        )
        return None
    return matches[0]


def _linkdead_pulses() -> int:
    minutes = getattr(settings, "COMBAT_LINKDEAD_MINUTES", DEFAULT_LINKDEAD_MINUTES)
    cadence = getattr(settings, "GAME_PULSE_CADENCES", {}).get("recovery", 60)
    interval = getattr(settings, "GAME_PULSE_INTERVAL_SECONDS", 1)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in (minutes, cadence, interval)
    ):
        raise RespawnError("Link-dead timing settings must be positive integers.")
    return max(1, (minutes * 60 + cadence * interval - 1) // (cadence * interval))


def _linkdead_record(character: Any, *, required: bool) -> LinkdeadRecord | None:
    raw = character.attributes.get(LINKDEAD_ATTRIBUTE)
    if raw is None and not required:
        return None
    required_keys = {
        "version",
        "start_pulse",
        "last_pulse",
        "removal_due",
        "stowed",
        "return_location_id",
        "recover_stable",
    }
    if (
        not isinstance(raw, Mapping)
        or set(raw) != required_keys
        or raw.get("version") != LINKDEAD_VERSION
    ):
        raise RespawnError("Link-dead state is malformed.")
    start_pulse, last_pulse = raw["start_pulse"], raw["last_pulse"]
    if start_pulse is not None and (
        isinstance(start_pulse, bool)
        or not isinstance(start_pulse, int)
        or start_pulse < 0
    ):
        raise RespawnError("Link-dead start pulse is invalid.")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (last_pulse,)
    ):
        raise RespawnError("Link-dead pulse counters are invalid.")
    if (start_pulse is not None and last_pulse < start_pulse) or not all(
        isinstance(raw[key], bool)
        for key in ("removal_due", "stowed", "recover_stable")
    ):
        raise RespawnError("Link-dead state is inconsistent.")
    location_id = raw["return_location_id"]
    if location_id is not None and (
        isinstance(location_id, bool)
        or not isinstance(location_id, int)
        or location_id < 1
    ):
        raise RespawnError("Link-dead saved location is invalid.")
    return LinkdeadRecord(
        start_pulse,
        last_pulse,
        raw["removal_due"],
        raw["stowed"],
        location_id,
        raw["recover_stable"],
    )


def _write_linkdead(character: Any, record: LinkdeadRecord) -> None:
    character.attributes.add(
        LINKDEAD_ATTRIBUTE,
        {
            "version": LINKDEAD_VERSION,
            "start_pulse": record.start_pulse,
            "last_pulse": record.last_pulse,
            "removal_due": record.removal_due,
            "stowed": record.stowed,
            "return_location_id": record.return_location_id,
            "recover_stable": record.recover_stable,
        },
    )


def _is_pc(character: Any) -> bool:
    value = character.attributes.get("is_player_character")
    return True if value is None else bool(value)


def _register_lifecycle_consumer() -> None:
    consumer = LifecycleConsumer(
        RESPAWN_LIFECYCLE_KEY, on_character=_on_character_lifecycle
    )
    try:
        register_lifecycle_consumer(consumer)
    except LifecycleError:
        unregister_lifecycle_consumer(RESPAWN_LIFECYCLE_KEY)
        register_lifecycle_consumer(consumer)


_register_lifecycle_consumer()
