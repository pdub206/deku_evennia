"""ADV-03's durable class-choice and trainer service.

Only ADV-02 choice sets are interpreted here.  Each choice names one
code-owned option adapter; this service never interprets a selected key as a
command or executable content.  The skill and magic adapters each retain
ownership of their persistent state and validation rules.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from systems.progression import (
    CLASS_PROGRESSION,
    MAX_CLASS_LEVEL,
    ChoiceSet,
    RegistryValidationError,
)

CHOICE_STATE_ATTRIBUTE = "progression_choices"
CHOICE_STATE_VERSION = 2
TRAINER_PROFILE_ATTRIBUTE = "trainer_profile"
TRAINER_PROFILE_VERSION = 1
_MAGIC_CHOICE_MODES = {
    "magic_learned": "learned",
    "magic_prepared": "prepared",
    "magic_innate": "innate",
}


class TrainingError(ValueError):
    """Raised when a requested choice or trainer cannot be used safely."""


@dataclass(frozen=True)
class PracticeView:
    """Safe, read-only presentation data for one character's training."""

    known_proficiencies: tuple[str, ...]
    automatic_features: tuple[str, ...]
    resources: tuple[tuple[str, int], ...]
    spell_access: tuple[tuple[str, int, int, int], ...]
    pending_choices: tuple[Mapping[str, Any], ...]
    replaceable_choices: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class TrainingResult:
    """Result of resolving exactly one entitlement option."""

    applied: bool
    reason: str
    choice_key: str
    option: str


def initialize_choice_entitlements(character: Any, class_key: str, level: int) -> None:
    """Add the ADV-02 choices earned at one level, idempotently.

    Called by ADV-01 while it already holds its transaction.  It writes only
    primitive entitlement data and never picks an option on the player's behalf.
    """
    definition = _class_definition(class_key)
    grants = definition.grants_at(level)
    state = _choice_state(character, create=True)
    existing = {item["id"] for item in state["pending"] + state["resolved"]}
    for choice_key in grants.choice_keys:
        entitlement = _entitlement(definition.key, level, choice_key)
        if entitlement["id"] not in existing:
            state["pending"].append(entitlement)
    _write_choice_state(character, state)


def record_chargen_choice(
    character: Any, class_key: str, options: Sequence[str]
) -> None:
    """Record the already-confirmed chargen class selection as provenance."""
    definition = _class_definition(class_key)
    choice_key = definition.skill_choice_key
    initialize_choice_entitlements(character, definition.key, 1)
    _resolve_without_trainer(character, choice_key, tuple(options), origin="chargen")


def practice_view(character: Any) -> PracticeView:
    """Return known and pending ADV-02 progression data without mutation."""
    definition = _class_definition(_character_class(character))
    level = _character_level(character)
    state = _choice_state(character, create=False)
    pending = () if state is None else tuple(deepcopy(state["pending"]))
    resolved = () if state is None else tuple(state["resolved"])
    replaceable = tuple(
        deepcopy(item)
        for item in resolved
        if CLASS_PROGRESSION.choices[item["choice_key"]].replacement_policy
        == "replace_one"
    )
    features = tuple(
        key
        for grants in definition.levels[:level]
        for key in grants.automatic_feature_keys
    )
    resource_keys = tuple(
        dict.fromkeys(
            key for grants in definition.levels[:level] for key in grants.resource_keys
        )
    )
    spell_keys = tuple(
        dict.fromkeys(
            key
            for grants in definition.levels[:level]
            for key in grants.spell_access_keys
        )
    )
    resources = tuple(
        (key, CLASS_PROGRESSION.resources[key].maxima[level - 1])
        for key in resource_keys
    )
    spells = tuple(
        (
            key,
            CLASS_PROGRESSION.spell_access[key].cantrips[level - 1],
            CLASS_PROGRESSION.spell_access[key].spells_known[level - 1],
            CLASS_PROGRESSION.spell_access[key].maximum_spell_level[level - 1],
        )
        for key in spell_keys
    )
    return PracticeView(
        tuple(sorted(set(character.attributes.get("skill_proficiencies") or []))),
        tuple(dict.fromkeys(features)),
        resources,
        spells,
        pending,
        replaceable,
    )


def default_trainer_profile() -> dict[str, Any]:
    """Return the explicit, no-cost profile builders may opt an NPC into."""
    return {
        "version": TRAINER_PROFILE_VERSION,
        "classes": [],
        "choices": [],
        "minimum_level": 1,
        "maximum_level": MAX_CLASS_LEVEL,
        "service_lock": "all()",
    }


def validate_trainer_profile(profile: Any) -> dict[str, Any]:
    """Validate primitive MOB-06-compatible trainer configuration."""
    if not isinstance(profile, Mapping) or set(profile) != set(
        default_trainer_profile()
    ):
        raise TrainingError("Trainer profile has an invalid shape.")
    if profile["version"] != TRAINER_PROFILE_VERSION:
        raise TrainingError("Trainer profile has an unsupported version.")
    classes, choices = profile["classes"], profile["choices"]
    if (
        isinstance(classes, (str, bytes))
        or not isinstance(classes, Sequence)
        or any(not CLASS_PROGRESSION.is_available(key) for key in classes)
        or len(set(classes)) != len(classes)
    ):
        raise TrainingError("Trainer profile has invalid classes.")
    if (
        isinstance(choices, (str, bytes))
        or not isinstance(choices, Sequence)
        or any(
            not isinstance(key, str) or key not in CLASS_PROGRESSION.choices
            for key in choices
        )
        or len(set(choices)) != len(choices)
        or any(
            not any(key.startswith(f"{class_key.casefold()}.") for class_key in classes)
            for key in choices
        )
    ):
        raise TrainingError("Trainer profile has invalid choices.")
    low, high = profile["minimum_level"], profile["maximum_level"]
    if (
        isinstance(low, bool)
        or isinstance(high, bool)
        or not isinstance(low, int)
        or not isinstance(high, int)
        or not 1 <= low <= high <= MAX_CLASS_LEVEL
        or not isinstance(profile["service_lock"], str)
        or not profile["service_lock"].strip()
    ):
        raise TrainingError("Trainer profile has invalid service access.")
    return {
        "version": TRAINER_PROFILE_VERSION,
        "classes": list(classes),
        "choices": list(choices),
        "minimum_level": low,
        "maximum_level": high,
        "service_lock": profile["service_lock"].strip(),
    }


def set_trainer_profile(npc: Any, profile: Any) -> dict[str, Any]:
    """Persist a validated trainer profile and its ordinary service lock."""
    normalized = validate_trainer_profile(profile)
    if getattr(getattr(npc, "db", None), "is_player_character", None) is not False:
        raise TrainingError("Only NPCs may provide training.")
    npc.attributes.add(TRAINER_PROFILE_ATTRIBUTE, normalized)
    npc.locks.add(f"training:{normalized['service_lock']}")
    return deepcopy(normalized)


def resolve_training(
    character: Any, choice_key: str, option: str, trainer: Any
) -> TrainingResult:
    """Atomically resolve one pending entitlement through a qualified trainer."""
    if not isinstance(choice_key, str) or not isinstance(option, str):
        raise TrainingError("Training choice and option are required.")
    try:
        with transaction.atomic():
            _lock(character)
            _lock(trainer)
            _validate_trainer(character, trainer, choice_key)
            return _resolve_without_trainer(
                character, choice_key, (option,), origin="trainer"
            )
    except Exception:
        _discard_attribute_cache(character)
        raise


def find_trainer(actor: Any, name: str | None = None) -> Any:
    """Find one explicit or unambiguous colocated trainer without leaking profiles."""
    location = getattr(actor, "location", None)
    if location is None:
        raise TrainingError("There is no trainer here.")
    candidates = [
        obj
        for obj in location.contents
        if obj is not actor
        and obj.attributes.get(TRAINER_PROFILE_ATTRIBUTE) is not None
    ]
    if name:
        needle = name.strip().casefold()
        candidates = [obj for obj in candidates if needle in obj.key.casefold()]
    if not candidates:
        raise TrainingError("There is no matching trainer here.")
    if len(candidates) != 1:
        raise TrainingError("Please name the trainer you want to use.")
    return candidates[0]


def _resolve_without_trainer(
    character: Any, choice_key: str, options: tuple[str, ...], *, origin: str
) -> TrainingResult:
    state = _choice_state(character, create=False)
    if state is None:
        raise TrainingError("Your training record needs staff repair.")
    pending = next(
        (item for item in state["pending"] if item["choice_key"] == choice_key), None
    )
    if pending is None:
        raise TrainingError("That choice is not pending.")
    choice = CLASS_PROGRESSION.choices.get(choice_key)
    if choice is None or tuple(options) == ():
        raise TrainingError("That choice is unavailable.")
    selected = list(pending["selected"])
    for option in options:
        _validate_option(character, choice, option, selected)
        selected.append(option)
    if len(selected) > pending["count"]:
        raise TrainingError("That choice has no remaining selections.")
    pending["selected"] = selected
    if len(selected) == pending["count"]:
        _grant_options(character, choice, selected)
        state["pending"].remove(pending)
        pending["origin"] = origin
        state["resolved"].append(pending)
    _write_choice_state(character, state)
    return TrainingResult(True, "resolved", choice_key, options[-1])


def _validate_option(
    character: Any, choice: ChoiceSet, option: str, selected: list[str]
) -> None:
    if option not in choice.legal_options:
        raise TrainingError("That option is not available for this choice.")
    if option in selected:
        raise TrainingError("You already know that option.")
    if choice.option_adapter == "skill" and option in (
        character.attributes.get("skill_proficiencies") or []
    ):
        raise TrainingError("You already know that option.")
    if choice.option_adapter == "feature" and option in (
        character.attributes.get("class_feature_choices") or []
    ):
        raise TrainingError("You already know that option.")
    if choice.option_adapter not in {"skill", "feature"}:
        _validate_unowned_magic_option(character, choice, option)
    if any(
        option in group and any(item in group for item in selected)
        for group in choice.mutual_exclusions
    ):
        raise TrainingError("That option conflicts with an earlier selection.")


def _grant_options(character: Any, choice: ChoiceSet, options: list[str]) -> None:
    if choice.option_adapter == "skill":
        known = list(character.attributes.get("skill_proficiencies") or [])
        character.db.skill_proficiencies = sorted(set(known + options))
        return
    if choice.option_adapter == "feature":
        known = list(character.attributes.get("class_feature_choices") or [])
        character.db.class_feature_choices = sorted(set(known + options))
        return
    try:
        from systems.magic_actions import (
            MagicActionError,
            grant_action,
            grant_spellbook_entry,
        )

        if choice.option_adapter == "magic_spellbook":
            for option in options:
                grant_spellbook_entry(character, option)
            return
        mode = _MAGIC_CHOICE_MODES.get(choice.option_adapter)
        if mode is None:
            raise TrainingError("That choice's owning system is unavailable.")
        for option in options:
            grant_action(character, option, mode)
    except MagicActionError as err:
        raise TrainingError(str(err)) from err


def _validate_unowned_magic_option(
    character: Any, choice: ChoiceSet, option: str
) -> None:
    """Reject an already-owned magic option before consuming a choice."""
    try:
        from systems.magic_actions import (
            MagicActionError,
            has_action_entitlement,
            has_spellbook_entry,
        )

        mode = (
            "spellbook"
            if choice.option_adapter == "magic_spellbook"
            else _MAGIC_CHOICE_MODES.get(choice.option_adapter)
        )
        if mode is None:
            raise TrainingError("That choice's owning system is unavailable.")
        if mode == "spellbook":
            owned = has_spellbook_entry(character, option)
        else:
            owned = has_action_entitlement(character, option, mode)
        if owned:
            raise TrainingError("You already know that option.")
    except MagicActionError as err:
        raise TrainingError("Your magic training record needs staff repair.") from err


def _validate_trainer(character: Any, trainer: Any, choice_key: str) -> None:
    if getattr(
        character, "location", None
    ) is None or character.location is not getattr(trainer, "location", None):
        raise TrainingError("That trainer is not here.")
    profile = validate_trainer_profile(
        trainer.attributes.get(TRAINER_PROFILE_ATTRIBUTE)
    )
    definition = _class_definition(_character_class(character))
    level = _character_level(character)
    if (
        definition.key not in profile["classes"]
        or choice_key not in profile["choices"]
        or not profile["minimum_level"] <= level <= profile["maximum_level"]
        or not trainer.access(character, "training", default=False)
    ):
        raise TrainingError("That trainer cannot help you with this choice.")


def _entitlement(class_key: str, level: int, choice_key: str) -> dict[str, Any]:
    choice = CLASS_PROGRESSION.choices[choice_key]
    return {
        "id": f"{class_key}:{level}:{choice_key}",
        "class_key": class_key,
        "level": level,
        "choice_key": choice_key,
        "count": choice.count,
        "selected": [],
        "registry_version": CLASS_PROGRESSION.version,
        "registry_fingerprint": CLASS_PROGRESSION.fingerprint,
    }


def _choice_state(character: Any, *, create: bool) -> dict[str, Any] | None:
    raw = character.attributes.get(CHOICE_STATE_ATTRIBUTE)
    if raw is None:
        return (
            {"version": CHOICE_STATE_VERSION, "pending": [], "resolved": []}
            if create
            else None
        )
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"version", "pending", "resolved"}
        or raw["version"] != CHOICE_STATE_VERSION
    ):
        raise TrainingError("Your training record needs staff repair.")
    pending, resolved = raw["pending"], raw["resolved"]
    if (
        isinstance(pending, (str, bytes))
        or isinstance(resolved, (str, bytes))
        or not isinstance(pending, Sequence)
        or not isinstance(resolved, Sequence)
    ):
        raise TrainingError("Your training record needs staff repair.")
    state = {
        "version": CHOICE_STATE_VERSION,
        "pending": list(deepcopy(pending)),
        "resolved": list(deepcopy(resolved)),
    }
    class_key = character.attributes.get("char_class")
    character_level = character.attributes.get("level", 1)
    for item in state["pending"]:
        if not _valid_entitlement(
            item, class_key=class_key, character_level=character_level, resolved=False
        ):
            raise TrainingError("Your training record needs staff repair.")
    for item in state["resolved"]:
        if not _valid_entitlement(
            item, class_key=class_key, character_level=character_level, resolved=True
        ):
            raise TrainingError("Your training record needs staff repair.")
    return state


def _valid_entitlement(
    item: Any, *, class_key: object, character_level: object, resolved: bool
) -> bool:
    """Validate provenance against the exact released choice grant."""
    if not (
        isinstance(item, Mapping)
        and set(item)
        == {
            "id",
            "class_key",
            "level",
            "choice_key",
            "count",
            "selected",
            "registry_version",
            "registry_fingerprint",
            *(("origin",) if resolved else ()),
        }
        and isinstance(item["id"], str)
        and item["class_key"] == class_key
        and isinstance(character_level, int)
        and not isinstance(character_level, bool)
        and isinstance(item["level"], int)
        and not isinstance(item["level"], bool)
        and 1 <= item["level"] <= character_level <= MAX_CLASS_LEVEL
        and item["choice_key"] in CLASS_PROGRESSION.choices
        and item["id"] == f"{item['class_key']}:{item['level']}:{item['choice_key']}"
        and item["choice_key"]
        in CLASS_PROGRESSION.class_for(item["class_key"])
        .grants_at(item["level"])
        .choice_keys
        and item["count"] == CLASS_PROGRESSION.choices[item["choice_key"]].count
        and not isinstance(item["selected"], (str, bytes))
        and isinstance(item["selected"], Sequence)
        and len(item["selected"]) == len(set(item["selected"]))
        and all(
            value in CLASS_PROGRESSION.choices[item["choice_key"]].legal_options
            for value in item["selected"]
        )
        and len(item["selected"]) <= item["count"]
        and item["registry_version"] == CLASS_PROGRESSION.version
        and item["registry_fingerprint"] == CLASS_PROGRESSION.fingerprint
    ):
        return False
    if resolved:
        return len(item["selected"]) == item["count"] and item["origin"] in {
            "chargen",
            "trainer",
        }
    return len(item["selected"]) < item["count"]


def _write_choice_state(character: Any, state: dict[str, Any]) -> None:
    character.attributes.add(CHOICE_STATE_ATTRIBUTE, state)


def _character_class(character: Any) -> str:
    value = character.attributes.get("char_class")
    if not isinstance(value, str):
        raise TrainingError("Your class progression needs staff repair.")
    return value


def _class_definition(class_key: str):
    try:
        return CLASS_PROGRESSION.class_for(class_key)
    except RegistryValidationError as err:
        raise TrainingError("Your class progression needs staff repair.") from err


def _character_level(character: Any) -> int:
    level = character.attributes.get("level", 1)
    if (
        isinstance(level, bool)
        or not isinstance(level, int)
        or not 1 <= level <= MAX_CLASS_LEVEL
    ):
        raise TrainingError("Your class progression needs staff repair.")
    return level


def _lock(obj: Any) -> None:
    object_id = getattr(obj, "pk", None)
    if not isinstance(object_id, int) or isinstance(object_id, bool) or object_id <= 0:
        raise TrainingError("Training requires saved characters.")
    obj.__class__.objects.select_for_update().get(pk=object_id)


def _discard_attribute_cache(character: Any) -> None:
    """Forget Attribute values written inside a rolled-back transaction."""
    for attribute in character.attributes.all():
        attribute.flush_from_cache(force=True)
    character.attributes.reset_cache()
