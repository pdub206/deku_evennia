"""COMBAT-09's observational combat controls and safe presentation helpers.

The functions here deliberately read the same live statistics, injury state,
and encounter registry used by combat.  They neither resolve attacks nor keep
a second copy of combat state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from systems.combat import (get_encounter_id, get_target, is_fighting,
                            schedule_flee)
from systems.combat_math import (HIT_LOCATION_WEIGHTS,
                                 expected_damage_per_action, hit_probability)
from systems.combat_movement import choose_flee_exit
from systems.injury import InjuryError, InjuryState, injury_record

WIMPY_ATTRIBUTE = "combat_wimpy"
WIMPY_TRIGGERED_ATTRIBUTE = "combat_wimpy_triggered"
PROMPT_ATTRIBUTE = "combat_prompt"
VERBOSE_ATTRIBUTE = "combat_verbose"

DEFAULT_WIMPY = 0
DEFAULT_PROMPT = True
DEFAULT_VERBOSE = "normal"
VERBOSE_MODES = frozenset({"compact", "normal", "detailed"})
BANDS = ("trivial", "easy", "even", "dangerous", "deadly", "overwhelming")


@dataclass(frozen=True)
class CombatEstimate:
    """A deterministic private comparison of two current combatants."""

    band: str
    threat_ratio: float
    observer_level: int
    target_level: int
    observer_hp: int
    target_hp: int
    observer_armor_class: int
    target_armor_class: int
    observer_hit_rate: float
    target_hit_rate: float
    observer_expected_damage: float
    target_expected_damage: float
    observer_delay: float
    target_delay: float


def health_description(character: Any) -> str:
    """Return the one public qualitative health state for a character."""
    current, maximum = character.stats.hp_current, character.stats.hp_max
    if current <= 0:
        try:
            state = injury_record(character).state
        except InjuryError:
            return "dying"
        return {
            InjuryState.DYING: "dying",
            InjuryState.INCAPACITATED: "stable",
            InjuryState.DEAD: "dead",
        }.get(state, "dying")
    percentage = current * 100 / max(1, maximum)
    if percentage >= 100:
        return "unhurt"
    if percentage >= 76:
        return "healthy"
    if percentage >= 51:
        return "wounded"
    if percentage >= 26:
        return "badly wounded"
    return "near death"


def get_wimpy(character: Any) -> int:
    """Read a persisted wimpy percentage, repairing malformed legacy values."""
    value = character.attributes.get(WIMPY_ATTRIBUTE)
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 90:
        return value
    if value is not None:
        character.attributes.add(WIMPY_ATTRIBUTE, DEFAULT_WIMPY)
    return DEFAULT_WIMPY


def set_wimpy(character: Any, percentage: int) -> int:
    """Persist a validated survival threshold and reset its crossing latch."""
    if (
        isinstance(percentage, bool)
        or not isinstance(percentage, int)
        or not 0 <= percentage <= 90
    ):
        raise ValueError("Wimpy must be a whole percentage from 0 to 90.")
    character.attributes.add(WIMPY_ATTRIBUTE, percentage)
    character.attributes.add(WIMPY_TRIGGERED_ATTRIBUTE, False)
    refresh_combat_prompt(character)
    return percentage


def get_combat_prompt(character: Any) -> bool:
    """Read the persistent prompt preference, repairing invalid stored data."""
    value = character.attributes.get(PROMPT_ATTRIBUTE)
    if isinstance(value, bool):
        return value
    if value is not None:
        character.attributes.add(PROMPT_ATTRIBUTE, DEFAULT_PROMPT)
    return DEFAULT_PROMPT


def set_combat_prompt(character: Any, enabled: bool) -> bool:
    """Persist combat prompt preference and immediately redraw or clear it."""
    character.attributes.add(PROMPT_ATTRIBUTE, bool(enabled))
    refresh_combat_prompt(character)
    return bool(enabled)


def get_combat_verbose(character: Any) -> str:
    """Read one documented verbosity mode, repairing malformed state."""
    value = character.attributes.get(VERBOSE_ATTRIBUTE)
    if isinstance(value, str) and value.lower() in VERBOSE_MODES:
        return value.lower()
    if value is not None:
        character.attributes.add(VERBOSE_ATTRIBUTE, DEFAULT_VERBOSE)
    return DEFAULT_VERBOSE


def set_combat_verbose(character: Any, mode: str) -> str:
    """Persist a presentation-only combat verbosity preference."""
    normalized = str(mode).strip().lower()
    if normalized not in VERBOSE_MODES:
        raise ValueError("Combat verbosity must be compact, normal, or detailed.")
    character.attributes.add(VERBOSE_ATTRIBUTE, normalized)
    return normalized


def wimpy_state(character: Any) -> str:
    """Return a safe prompt label for the current automatic-flee policy."""
    threshold = get_wimpy(character)
    if not threshold:
        return "off"
    return (
        "triggered"
        if bool(character.attributes.get(WIMPY_TRIGGERED_ATTRIBUTE))
        else "armed"
    )


def reconcile_wimpy(character: Any, previous_hp: int, current_hp: int) -> None:
    """Arm on recovery and queue one flee when a conscious PC crosses downward."""
    threshold = get_wimpy(character)
    if not threshold:
        return
    boundary = character.stats.hp_max * threshold / 100
    if current_hp > boundary:
        if character.attributes.get(WIMPY_TRIGGERED_ATTRIBUTE):
            character.attributes.add(WIMPY_TRIGGERED_ATTRIBUTE, False)
        refresh_combat_prompt(character)
        return
    if (
        previous_hp <= boundary
        or current_hp > boundary
        or bool(character.attributes.get(WIMPY_TRIGGERED_ATTRIBUTE))
        or not _is_player_character(character)
        or not is_fighting(character)
    ):
        return
    try:
        conscious = injury_record(character).state is InjuryState.CONSCIOUS
    except InjuryError:
        conscious = False
    if not conscious:
        return
    # Latch before route selection: an exit-less room is still one failed
    # survival attempt for this crossing, not an action-consuming retry loop.
    character.attributes.add(WIMPY_TRIGGERED_ATTRIBUTE, True)
    decision = choose_flee_exit(character)
    if decision.allowed:
        schedule_flee(character, decision.exit.id)
    refresh_combat_prompt(character)


def combat_prompt_data(character: Any) -> dict[str, Any] | None:
    """Return protocol-safe combat prompt fields, or ``None`` out of combat."""
    if not is_fighting(character):
        return None
    target = get_target(character)
    intent = _pending_intent(character)
    return {
        "hp": {
            "current": character.stats.hp_current,
            "maximum": character.stats.hp_max,
        },
        "target_health": health_description(target) if target is not None else None,
        "intent": _intent_label(intent),
        "wimpy": wimpy_state(character),
    }


def refresh_combat_prompt(character: Any) -> None:
    """Update one combat prompt without disturbing a higher-priority editor prompt."""
    data = combat_prompt_data(character)
    prompt = (
        _render_prompt(data)
        if data is not None and get_combat_prompt(character)
        else None
    )
    character.ndb._combat_prompt = prompt
    if getattr(character.ndb, "_prompt", None):
        return
    # Unit-test and offline objects have no client to render a prompt. Keeping
    # delivery session-bound also avoids manufacturing an output event for them.
    if character.sessions.count() and not getattr(
        character.ndb, "_command_running", False
    ):
        character.msg(prompt=prompt or "")


def estimate_threat(observer: Any, target: Any) -> CombatEstimate:
    """Compare live combat expectations without rolling, messaging, or mutating."""
    observer_profile = observer.stats.attack_profile()
    target_profile = target.stats.attack_profile()
    observer_hit = hit_probability(
        observer_profile.attack_bonus, target.stats.armor_class
    )
    target_hit = hit_probability(
        target_profile.attack_bonus, observer.stats.armor_class
    )
    observer_damage = expected_damage_per_action(
        observer_profile,
        target.stats.armor_class,
        mitigate=target.stats.mitigate_damage,
        location_weights=HIT_LOCATION_WEIGHTS,
    )
    target_damage = expected_damage_per_action(
        target_profile,
        observer.stats.armor_class,
        mitigate=observer.stats.mitigate_damage,
        location_weights=HIT_LOCATION_WEIGHTS,
    )
    base_delay = float(getattr(settings, "GAME_COMBAT_BASE_DELAY", 1.0))
    observer_delay = max(0.001, observer.stats.combat_delay(base_delay))
    target_delay = max(0.001, target.stats.combat_delay(base_delay))
    # Time-to-defeat includes live HP, mitigation, cadence and effective level.
    observer_ttk = target.stats.hp_current / max(0.01, observer_damage / observer_delay)
    target_ttk = observer.stats.hp_current / max(0.01, target_damage / target_delay)
    ratio = (observer_ttk / max(0.01, target_ttk)) * (
        target.stats.level / observer.stats.level
    )
    return CombatEstimate(
        _band_for_ratio(ratio),
        ratio,
        observer.stats.level,
        target.stats.level,
        observer.stats.hp_current,
        target.stats.hp_current,
        observer.stats.armor_class,
        target.stats.armor_class,
        observer_hit,
        target_hit,
        observer_damage,
        target_damage,
        observer_delay,
        target_delay,
    )


def _band_for_ratio(ratio: float) -> str:
    thresholds = getattr(
        settings, "COMBAT_CONSIDER_THRESHOLDS", (0.25, 0.5, 1.25, 2.0, 4.0)
    )
    for band, threshold in zip(BANDS, thresholds):
        if ratio <= float(threshold):
            return band
    return BANDS[-1]


def _pending_intent(character: Any) -> dict[str, Any] | None:
    """Read only the current actor's serializable pending intent."""
    from evennia.server.models import ServerConfig
    from systems.combat import COMBAT_CONFIG_KEY

    encounter_id = get_encounter_id(character)
    state = ServerConfig.objects.conf(COMBAT_CONFIG_KEY)
    if encounter_id is None or not isinstance(state, dict):
        return None
    return (
        state.get("encounters", {})
        .get(str(encounter_id), {})
        .get("participants", {})
        .get(str(character.id), {})
        .get("pending_intent")
    )


def _intent_label(intent: dict[str, Any] | None) -> str | None:
    if not intent:
        return None
    return (
        "flee"
        if intent.get("kind") == "flee"
        else intent.get("action") or intent.get("kind")
    )


def _render_prompt(data: dict[str, Any]) -> str:
    pieces = [f"HP {data['hp']['current']}/{data['hp']['maximum']}"]
    if data["target_health"]:
        pieces.append(f"Target {data['target_health']}")
    if data["intent"]:
        pieces.append(f"Ready {data['intent']}")
    pieces.append(f"Wimpy {data['wimpy']}")
    return "[" + " | ".join(pieces) + "] "


def _is_player_character(character: Any) -> bool:
    """Match combat's durable PC/NPC convention without session assumptions."""
    value = character.attributes.get("is_player_character")
    return True if value is None else bool(value)
