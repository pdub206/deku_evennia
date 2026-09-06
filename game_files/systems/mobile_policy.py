"""MOB-03's versioned, data-only policy for non-player characters.

The policy is deliberately separate from MOB-01's behavior registry and
MOB-02's tactical profile.  Templates and live NPCs persist the same compact
record; runtime users must ask this module rather than interpreting ad-hoc
attributes.  This keeps a malformed profile safe (no combat or autonomous
action) and leaves future sensory systems a single replacement seam.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from systems.encumbrance import can_receive
from systems.injury import InjuryError, InjuryState, injury_record

MOBILE_POLICY_ATTRIBUTE = "mobile_policy"
MOBILE_POLICY_VERSION = 1
DETECTION_CAPABILITIES = frozenset({"sight", "hearing", "smell"})


class MobilePolicyError(ValueError):
    """A mobile policy contains unsupported or unsafe persisted data."""


@dataclass(frozen=True)
class PolicyDecision:
    """A side-effect-free policy result with a stable reason code."""

    allowed: bool
    reason: str = ""


def default_mobile_policy() -> dict[str, Any]:
    """Return the safe profile assigned to all newly-authored NPC templates."""
    return {
        "version": MOBILE_POLICY_VERSION,
        "sentinel": False,
        "scavenger": False,
        "aggressive": False,
        "stay_in_area": False,
        "wimpy": 0,
        "detection": [],
        "protected": False,
        "noncombatant": False,
    }


def validate_mobile_policy(profile: Any) -> dict[str, Any]:
    """Validate and detach one exact primitive MOB-03 policy profile."""
    expected = set(default_mobile_policy())
    if not isinstance(profile, Mapping) or set(profile) != expected:
        raise MobilePolicyError("Mobile policy has an invalid shape.")
    if profile["version"] != MOBILE_POLICY_VERSION:
        raise MobilePolicyError("Mobile policy has an unsupported version.")
    flags = (
        "sentinel",
        "scavenger",
        "aggressive",
        "stay_in_area",
        "protected",
        "noncombatant",
    )
    if any(not isinstance(profile[name], bool) for name in flags):
        raise MobilePolicyError("Mobile policy flags must be true or false.")
    wimpy = profile["wimpy"]
    if isinstance(wimpy, bool) or not isinstance(wimpy, int) or not 0 <= wimpy <= 90:
        raise MobilePolicyError("NPC wimpy must be a whole percentage from 0 to 90.")
    detection = profile["detection"]
    if isinstance(detection, (str, bytes)) or not isinstance(detection, Sequence):
        raise MobilePolicyError("Mobile detection must be a list.")
    if any(
        not isinstance(value, str) or value not in DETECTION_CAPABILITIES
        for value in detection
    ):
        raise MobilePolicyError("Mobile detection has an unsupported capability.")
    return {
        **default_mobile_policy(),
        **{name: profile[name] for name in flags},
        "wimpy": wimpy,
        "detection": list(dict.fromkeys(detection)),
    }


def mobile_policy(npc: Any) -> dict[str, Any]:
    """Return an NPC's detached policy, defaulting only when it is absent."""
    raw = npc.attributes.get(MOBILE_POLICY_ATTRIBUTE)
    return default_mobile_policy() if raw is None else validate_mobile_policy(raw)


def set_mobile_policy(npc: Any, profile: Any) -> dict[str, Any]:
    """Persist one validated profile and return a detached copy."""
    normalized = validate_mobile_policy(profile)
    npc.attributes.add(MOBILE_POLICY_ATTRIBUTE, normalized)
    return deepcopy(normalized)


def set_mobile_policy_value(npc: Any, name: str, value: Any) -> dict[str, Any]:
    """Change exactly one builder-exposed policy value on a live NPC."""
    profile = mobile_policy(npc)
    if name not in profile or name == "version":
        raise MobilePolicyError("Mobile policy field is unknown.")
    profile[name] = value
    return set_mobile_policy(npc, profile)


def policy_value(source: Any, name: str) -> Any:
    """Read one validated builder-visible value from a prototype or NPC."""
    profile = (
        mobile_policy(source)
        if not isinstance(source, Mapping)
        else validate_mobile_policy(
            source.get(MOBILE_POLICY_ATTRIBUTE, default_mobile_policy())
        )
    )
    return deepcopy(profile[name])


def is_protected(character: Any) -> bool:
    """Return whether a target is protected, failing closed on bad NPC data."""
    if getattr(getattr(character, "db", None), "is_player_character", None) is False:
        try:
            profile = mobile_policy(character)
        except (MobilePolicyError, AttributeError, TypeError):
            return True
        return profile["protected"] or profile["noncombatant"]
    return bool(
        character.attributes.get("protected")
        or character.attributes.get("noncombatant")
    )


def may_enter_combat(character: Any) -> PolicyDecision:
    """Deny NPC combat initiation, joining, and retaliation when configured."""
    if (
        getattr(getattr(character, "db", None), "is_player_character", None)
        is not False
    ):
        return PolicyDecision(True)
    try:
        profile = mobile_policy(character)
    except (MobilePolicyError, AttributeError, TypeError):
        return PolicyDecision(False, "malformed_mobile_policy")
    if profile["noncombatant"]:
        return PolicyDecision(False, "noncombatant")
    return PolicyDecision(True)


def can_detect(observer: Any, candidate: Any) -> PolicyDecision:
    """Make MOB-03's passive, session-independent awareness decision.

    A target may opt into the narrow pre-stealth seam with primitive
    ``detection_difficulty`` and ``detection_requires`` Attributes.  Until
    ADV-04/MAGIC-02 add richer senses, absent requirements mean ordinarily
    detectable.  Malformed target requirements are deliberately invisible.
    """
    if (
        observer is candidate
        or getattr(observer, "location", None) is None
        or observer.location != getattr(candidate, "location", None)
    ):
        return PolicyDecision(False, "not_colocated")
    try:
        profile = mobile_policy(observer)
        record = injury_record(candidate)
    except (MobilePolicyError, InjuryError, AttributeError, TypeError):
        return PolicyDecision(False, "invalid_detection_data")
    if record.state is not InjuryState.CONSCIOUS:
        return PolicyDecision(False, "target_ineligible")
    difficulty = candidate.attributes.get("detection_difficulty", 0)
    requirements = candidate.attributes.get("detection_requires", [])
    if (
        isinstance(difficulty, bool)
        or not isinstance(difficulty, int)
        or difficulty < 0
        or isinstance(requirements, (str, bytes))
        or not isinstance(requirements, Sequence)
        or any(
            not isinstance(item, str) or item not in DETECTION_CAPABILITIES
            for item in requirements
        )
    ):
        return PolicyDecision(False, "invalid_detection_data")
    senses = set(profile["detection"])
    if not set(requirements).issubset(senses):
        return PolicyDecision(False, "detection_denied")
    try:
        passive = observer.stats.passive_perception
    except (AttributeError, TypeError):
        return PolicyDecision(False, "invalid_detection_data")
    if (
        isinstance(passive, bool)
        or not isinstance(passive, int)
        or passive < difficulty
    ):
        return PolicyDecision(False, "detection_denied")
    return PolicyDecision(True)


def select_aggression_target(npc: Any) -> Any | None:
    """Select one detectable, attackable room character in stable dbref order."""
    try:
        profile = mobile_policy(npc)
    except (MobilePolicyError, AttributeError, TypeError):
        return None
    if (
        not profile["aggressive"]
        or not may_enter_combat(npc).allowed
        or npc.location is None
    ):
        return None
    from systems.attacks import can_attack
    from systems.combat import is_fighting

    candidates = []
    for candidate in npc.location.contents_get(content_type="character"):
        if (
            not is_fighting(candidate)
            and can_detect(npc, candidate).allowed
            and can_attack(npc, candidate).allowed
        ):
            candidates.append(candidate)
    return min(candidates, key=lambda candidate: candidate.id, default=None)


def select_scavenge_item(npc: Any) -> Any | None:
    """Choose one loose, accessible item using stable dbref ordering."""
    try:
        profile = mobile_policy(npc)
    except (MobilePolicyError, AttributeError, TypeError):
        return None
    if not profile["scavenger"] or npc.location is None:
        return None
    from typeclasses.objects import Corpse, Item

    candidates = [
        item
        for item in npc.location.contents
        if isinstance(item, Item)
        and not isinstance(item, Corpse)
        and item.location is npc.location
        and item.access(npc, "get", default=False)
        and not bool(item.attributes.get("protected"))
    ]
    return min(candidates, key=lambda item: item.id, default=None)


def permits_ordinary_wandering(npc: Any) -> PolicyDecision:
    """Expose MOB-03's narrow sentinel handoff for MOB-04 navigation."""
    try:
        profile = mobile_policy(npc)
    except (MobilePolicyError, AttributeError, TypeError):
        return PolicyDecision(False, "malformed_mobile_policy")
    return PolicyDecision(
        not profile["sentinel"], "sentinel" if profile["sentinel"] else ""
    )


def navigation_area_constraint(npc: Any) -> PolicyDecision:
    """Expose whether later MOB-04 routing must stay inside the authored area."""
    try:
        profile = mobile_policy(npc)
    except (MobilePolicyError, AttributeError, TypeError):
        return PolicyDecision(False, "malformed_mobile_policy")
    return PolicyDecision(
        profile["stay_in_area"], "stay_in_area" if profile["stay_in_area"] else ""
    )


def perform_autonomous_decision(npc: Any) -> PolicyDecision:
    """Perform at most one aggression or scavenging decision for a MOB-01 token."""
    try:
        profile = mobile_policy(npc)
    except (MobilePolicyError, AttributeError, TypeError):
        return PolicyDecision(False, "malformed_mobile_policy")
    if profile["noncombatant"]:
        return PolicyDecision(False, "noncombatant")
    target = select_aggression_target(npc)
    if target is not None:
        from systems.combat import start_fight

        if start_fight(npc, target).accepted:
            return PolicyDecision(True, "aggression")
        return PolicyDecision(False, "aggression_denied")
    if profile["scavenger"]:
        result = scavenge(npc)
        return PolicyDecision(
            result.allowed, "scavenge" if result.allowed else result.reason
        )
    return PolicyDecision(False, "no_policy_action")


def scavenge(npc: Any) -> PolicyDecision:
    """Pick up at most one selected loose item through normal capacity hooks."""
    item = select_scavenge_item(npc)
    if item is None:
        return PolicyDecision(False, "no_scavenge_item")
    try:
        if not item.at_pre_get(npc) or not can_receive(npc, [item]).allowed:
            return PolicyDecision(False, "pickup_denied")
        if not item.move_to(npc, quiet=True, move_type="get", capacity_actor=npc):
            return PolicyDecision(False, "pickup_denied")
        item.at_get(npc)
    except Exception:
        return PolicyDecision(False, "pickup_denied")
    return PolicyDecision(True)
