"""Persistent effect and condition infrastructure for characters.

Effect definitions live in a validated code registry. Active instances contain
only the stable definition key and serializable runtime state in an Evennia
Attribute. The future world-pulse service advances durations through
``EffectHandler.process_duration``; effects never create their own timers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from string import Formatter
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from systems.dice import RollResult, roll_check
from world.chargen_data import ABILITY_NAMES, ABILITY_SHORT, SKILLS

EFFECTS_ATTRIBUTE = "active_effects"
EFFECTS_SCHEMA_VERSION = 1

_KEY_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_.-")
_BASE_MODIFIERS = frozenset(
    {
        "armor_class",
        "attack_bonus",
        "damage_bonus",
        "hp_max",
        "passive_perception",
        "proficiency_bonus",
        "reaction",
        "saving_throw",
        "skill_bonus",
        "speed",
    }
)
_ABILITY_KEYS = frozenset(name.lower() for name in ABILITY_NAMES)
_SKILL_KEYS = frozenset(name.lower() for name in SKILLS)
_MESSAGE_EVENTS = frozenset({"apply", "expire", "refresh", "remove", "save", "stack"})
_MESSAGE_FIELDS = frozenset({"effect", "source", "stacks", "target"})


class EffectError(ValueError):
    """Base error for invalid effect definitions or operations."""


class EffectStorageError(EffectError):
    """An owner's persistent effect data has an unsupported shape."""


class StackingPolicy(str, Enum):
    """How another application of the same effect key is resolved."""

    REJECT = "reject"
    REFRESH = "refresh"
    REPLACE = "replace"
    STACK = "stack"
    INDEPENDENT = "independent"


class SaveTiming(str, Enum):
    """When an effect's saving throw is attempted."""

    ON_APPLY = "on_apply"
    ON_PULSE = "on_pulse"


class SaveSuccess(str, Enum):
    """What a successful effect saving throw accomplishes."""

    NEGATE = "negate"
    END = "end"


class ApplyOutcome(str, Enum):
    """Result of applying or reapplying an effect."""

    APPLIED = "applied"
    REJECTED = "rejected"
    REFRESHED = "refreshed"
    REPLACED = "replaced"
    STACKED = "stacked"
    SAVED = "saved"


class RemovalReason(str, Enum):
    """Why an active effect left its owner."""

    ADMIN = "admin"
    CURED = "cured"
    DISPELLED = "dispelled"
    EXPIRED = "expired"
    REPLACED = "replaced"
    SAVED = "saved"
    SOURCE = "source"


class RemovalOutcome(str, Enum):
    """Result of an attempt to remove one effect instance."""

    REMOVED = "removed"
    DENIED = "denied"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class SaveRule:
    """A snapshottable saving throw attached to an effect application."""

    ability: str
    dc: int
    timing: SaveTiming = SaveTiming.ON_APPLY
    success: SaveSuccess = SaveSuccess.NEGATE

    def __post_init__(self) -> None:
        if not isinstance(self.timing, SaveTiming) or not isinstance(
            self.success, SaveSuccess
        ):
            raise EffectError("Effect save timing and outcome must use their enums.")
        canonical = _canonical_ability(self.ability)
        object.__setattr__(self, "ability", canonical)
        if isinstance(self.dc, bool) or not isinstance(self.dc, int) or self.dc < 0:
            raise EffectError("An effect save DC must be a non-negative integer.")
        if (
            self.timing is SaveTiming.ON_APPLY
            and self.success is not SaveSuccess.NEGATE
        ):
            raise EffectError("An on-application save must negate the effect.")
        if self.timing is SaveTiming.ON_PULSE and self.success is not SaveSuccess.END:
            raise EffectError("A pulse save must end the effect.")

    def serialize(self) -> dict[str, Any]:
        """Return primitive data suitable for an Evennia Attribute."""
        return {
            "ability": self.ability,
            "dc": self.dc,
            "timing": self.timing.value,
            "success": self.success.value,
        }

    @classmethod
    def deserialize(cls, value: Mapping[str, Any]) -> SaveRule:
        """Reconstruct and validate a persisted saving-throw rule."""
        try:
            return cls(
                ability=str(value["ability"]),
                dc=value["dc"],
                timing=SaveTiming(value["timing"]),
                success=SaveSuccess(value["success"]),
            )
        except (KeyError, TypeError, ValueError) as err:
            raise EffectStorageError("An active effect has invalid save data.") from err


@dataclass(frozen=True)
class EffectMessage:
    """Target and room text for one lifecycle event."""

    target: str = ""
    room: str = ""


@dataclass(frozen=True)
class EffectDefinition:
    """Immutable rules shared by all active instances of one effect."""

    key: str
    name: str
    duration: int | None = None
    stacking: StackingPolicy = StackingPolicy.REJECT
    max_stacks: int = 1
    modifiers: Mapping[str, int] = field(default_factory=dict)
    modifiers_per_stack: bool = True
    conditions: frozenset[str] = field(default_factory=frozenset)
    save: SaveRule | None = None
    removal_categories: frozenset[str] = field(default_factory=frozenset)
    messages: Mapping[str, EffectMessage] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_key(self.key, "effect")
        if not isinstance(self.name, str) or not self.name.strip():
            raise EffectError("An effect name cannot be empty.")
        if not isinstance(self.stacking, StackingPolicy):
            raise EffectError("An effect stacking policy must use StackingPolicy.")
        _validate_duration(self.duration)
        if isinstance(self.max_stacks, bool) or not isinstance(self.max_stacks, int):
            raise EffectError("An effect's maximum stacks must be an integer.")
        if self.max_stacks < 1:
            raise EffectError("An effect must allow at least one stack.")
        if self.stacking is not StackingPolicy.STACK and self.max_stacks != 1:
            raise EffectError("Only a stacking effect may allow multiple stacks.")
        if self.save is not None and not isinstance(self.save, SaveRule):
            raise EffectError("An effect save must use SaveRule.")

        if isinstance(self.conditions, str) or isinstance(self.removal_categories, str):
            raise EffectError("Effect conditions and removal categories must be sets.")

        modifiers = _validated_modifiers(self.modifiers)
        conditions = frozenset(self.conditions)
        removal_categories = frozenset(self.removal_categories)
        for condition in conditions:
            _validate_key(condition, "condition")
        for category in removal_categories:
            _validate_key(category, "removal category")
        messages = dict(self.messages)
        _validate_messages(messages)

        object.__setattr__(self, "modifiers", MappingProxyType(modifiers))
        object.__setattr__(self, "conditions", conditions)
        object.__setattr__(self, "removal_categories", removal_categories)
        object.__setattr__(self, "messages", MappingProxyType(messages))


class EffectRegistry:
    """Validated mapping from stable effect keys to their definitions."""

    def __init__(self) -> None:
        self._definitions: dict[str, EffectDefinition] = {}

    def register(self, definition: EffectDefinition) -> EffectDefinition:
        """Register and return a definition, rejecting duplicate keys."""
        if not isinstance(definition, EffectDefinition):
            raise EffectError("Only EffectDefinition instances may be registered.")
        if definition.key in self._definitions:
            raise EffectError(f"Effect '{definition.key}' is already registered.")
        self._definitions[definition.key] = definition
        return definition

    def get(self, key: str) -> EffectDefinition | None:
        """Return a registered definition by exact stable key."""
        return self._definitions.get(key)

    def require(self, key: str) -> EffectDefinition:
        """Return a definition or raise a useful operation error."""
        definition = self.get(key)
        if definition is None:
            raise EffectError(f"Unknown effect definition: {key}")
        return definition


EFFECT_REGISTRY = EffectRegistry()


@dataclass(frozen=True)
class ActiveEffect:
    """Read-only view of one persisted effect instance."""

    owner: Any = field(repr=False, compare=False)
    instance_id: str
    key: str
    stacks: int
    remaining_pulses: int | None
    source: Any = field(repr=False, compare=False)
    source_dbref: str | None
    source_key: str | None
    source_name: str
    instance_modifiers: Mapping[str, int]
    instance_removal_categories: frozenset[str]
    save: SaveRule | None
    definition: EffectDefinition | None = field(repr=False, compare=False)

    @property
    def name(self) -> str:
        """Return a player-facing name, including for orphaned records."""
        return self.definition.name if self.definition else self.key

    @property
    def conditions(self) -> frozenset[str]:
        """Return the machine-readable condition flags this effect grants."""
        return self.definition.conditions if self.definition else frozenset()

    @property
    def removal_categories(self) -> frozenset[str]:
        """Return categories allowed to remove this effect normally."""
        return self.instance_removal_categories

    @property
    def modifiers(self) -> Mapping[str, int]:
        """Return effective numeric modifiers, accounting for stack count."""
        if self.definition is None:
            return MappingProxyType({})
        combined = dict(self.definition.modifiers)
        for name, value in self.instance_modifiers.items():
            combined[name] = combined.get(name, 0) + value
        if self.definition.modifiers_per_stack:
            combined = {name: value * self.stacks for name, value in combined.items()}
        return MappingProxyType(combined)


@dataclass(frozen=True)
class ApplyResult:
    """Structured result of an effect application."""

    outcome: ApplyOutcome
    effect: ActiveEffect | None
    save_roll: RollResult | None = None


@dataclass(frozen=True)
class RemovalResult:
    """Structured result of an effect removal attempt."""

    outcome: RemovalOutcome
    effect: ActiveEffect | None
    reason: RemovalReason


@dataclass(frozen=True)
class SaveAttempt:
    """Result of attempting one active effect's configured save."""

    effect: ActiveEffect
    roll: RollResult
    removal: RemovalResult | None

    @property
    def removed(self) -> bool:
        """Return whether the successful save ended the effect."""
        return self.removal is not None


class EffectHandler:
    """Manage the persistent effects attached to one character."""

    def __init__(
        self,
        owner: Any,
        registry: EffectRegistry = EFFECT_REGISTRY,
        attribute_key: str = EFFECTS_ATTRIBUTE,
    ) -> None:
        self.owner = owner
        self.registry = registry
        self.attribute_key = attribute_key

    def all(self) -> tuple[ActiveEffect, ...]:
        """Return all active instances in stable insertion order."""
        return tuple(
            self._instance(instance_id, record)
            for instance_id, record in self._records().items()
        )

    def get(self, instance_id: str) -> ActiveEffect | None:
        """Return one active instance by its unique ID."""
        record = self._records().get(instance_id)
        return self._instance(instance_id, record) if record is not None else None

    def has(self, key: str) -> bool:
        """Return whether any active instance uses this definition key."""
        return any(effect.key == key for effect in self.all())

    def has_condition(self, condition: str) -> bool:
        """Return whether any active effect grants a condition flag."""
        return any(condition in effect.conditions for effect in self.all())

    def modifier_sources(self) -> tuple[Mapping[str, int], ...]:
        """Return active modifier mappings for the canonical stat API."""
        return tuple(effect.modifiers for effect in self.all() if effect.modifiers)

    def add(
        self,
        key: str,
        *,
        source: Any = None,
        source_key: str | None = None,
        duration: int | None = None,
        stacks: int = 1,
        modifiers: Mapping[str, int] | None = None,
        save: SaveRule | None = None,
        quiet: bool = False,
    ) -> ApplyResult:
        """Apply an effect according to its validated reapplication policy."""
        definition = self.registry.require(key)
        actual_duration = definition.duration if duration is None else duration
        _validate_duration(actual_duration)
        if source_key is not None:
            _validate_key(source_key, "effect source")
        if isinstance(stacks, bool) or not isinstance(stacks, int) or stacks < 1:
            raise EffectError("Applied stacks must be a positive integer.")
        if definition.stacking is not StackingPolicy.STACK and stacks != 1:
            raise EffectError("Only a stacking effect accepts multiple stacks.")
        instance_modifiers = _validated_modifiers(modifiers or {})
        actual_save = save or definition.save

        records = self._records()
        existing_ids = [
            instance_id
            for instance_id, record in records.items()
            if record.get("key") == key
        ]
        policy = definition.stacking
        if existing_ids and policy is StackingPolicy.REJECT:
            return ApplyResult(ApplyOutcome.REJECTED, self.get(existing_ids[0]))

        new_record = self._new_record(
            definition,
            source=source,
            source_key=source_key,
            duration=actual_duration,
            stacks=min(stacks, definition.max_stacks),
            modifiers=instance_modifiers,
            save=actual_save,
        )

        save_roll = None
        if actual_save and actual_save.timing is SaveTiming.ON_APPLY:
            effect = self._instance(uuid4().hex, new_record)
            save_roll = self._roll_save(actual_save)
            if save_roll.success:
                if not quiet:
                    self._emit(definition, "save", effect)
                return ApplyResult(ApplyOutcome.SAVED, None, save_roll)

        if existing_ids and policy is StackingPolicy.REFRESH:
            instance_id = existing_ids[0]
            new_record["stacks"] = records[instance_id]["stacks"]
            records[instance_id] = new_record
            self._write_records(records)
            effect = self.get(instance_id)
            self._reconcile_stats()
            if effect and not quiet:
                self._emit(definition, "refresh", effect)
            return ApplyResult(ApplyOutcome.REFRESHED, effect, save_roll)

        if existing_ids and policy is StackingPolicy.STACK:
            instance_id = existing_ids[0]
            current = int(records[instance_id]["stacks"])
            new_record["stacks"] = min(current + stacks, definition.max_stacks)
            records[instance_id] = new_record
            self._write_records(records)
            effect = self.get(instance_id)
            self._reconcile_stats()
            if effect and not quiet:
                self._emit(definition, "stack", effect)
            return ApplyResult(ApplyOutcome.STACKED, effect, save_roll)

        outcome = ApplyOutcome.APPLIED
        if existing_ids and policy is StackingPolicy.REPLACE:
            for instance_id in existing_ids:
                records.pop(instance_id, None)
            outcome = ApplyOutcome.REPLACED

        instance_id = uuid4().hex
        records[instance_id] = new_record
        self._write_records(records)
        effect = self.get(instance_id)
        self._reconcile_stats()
        if effect and not quiet:
            self._emit(definition, "apply", effect)
        return ApplyResult(outcome, effect, save_roll)

    def remove(
        self,
        instance_id: str,
        *,
        reason: RemovalReason = RemovalReason.ADMIN,
        category: str | None = None,
        quiet: bool = False,
    ) -> RemovalResult:
        """Remove one instance if its removal policy permits the reason."""
        if not isinstance(reason, RemovalReason):
            raise EffectError("An effect removal reason must use RemovalReason.")
        if category is not None:
            _validate_key(category, "removal category")
        records = self._records()
        record = records.get(instance_id)
        if record is None:
            return RemovalResult(RemovalOutcome.NOT_FOUND, None, reason)
        effect = self._instance(instance_id, record)
        if reason in {RemovalReason.CURED, RemovalReason.DISPELLED} and (
            category is None or category not in effect.removal_categories
        ):
            return RemovalResult(RemovalOutcome.DENIED, effect, reason)

        del records[instance_id]
        self._write_records(records)
        self._reconcile_stats()
        if effect.definition and not quiet:
            event = (
                "expire"
                if reason is RemovalReason.EXPIRED
                else "save" if reason is RemovalReason.SAVED else "remove"
            )
            self._emit(effect.definition, event, effect)
        return RemovalResult(RemovalOutcome.REMOVED, effect, reason)

    def remove_matching(
        self,
        category: str,
        *,
        reason: RemovalReason = RemovalReason.CURED,
        source: Any = None,
        quiet: bool = False,
    ) -> tuple[RemovalResult, ...]:
        """Remove every eligible effect matching a category and optional source."""
        _validate_key(category, "removal category")
        if not isinstance(reason, RemovalReason):
            raise EffectError("An effect removal reason must use RemovalReason.")
        matches = [
            effect
            for effect in self.all()
            if category in effect.removal_categories
            and (source is None or self._source_matches(effect, source))
        ]
        return tuple(
            self.remove(
                effect.instance_id,
                reason=reason,
                category=category,
                quiet=quiet,
            )
            for effect in matches
        )

    def remove_by_source(
        self, source: Any, *, quiet: bool = False
    ) -> tuple[RemovalResult, ...]:
        """End all effects from a source, bypassing ordinary cure restrictions."""
        if source is None:
            return ()
        matches = [
            effect for effect in self.all() if self._source_matches(effect, source)
        ]
        return tuple(
            self.remove(
                effect.instance_id,
                reason=RemovalReason.SOURCE,
                quiet=quiet,
            )
            for effect in matches
        )

    def attempt_save(
        self, instance_id: str, *, quiet: bool = False
    ) -> SaveAttempt | None:
        """Attempt an active effect's save and end it on a configured success."""
        effect = self.get(instance_id)
        if (
            effect is None
            or effect.save is None
            or effect.save.timing is not SaveTiming.ON_PULSE
        ):
            return None
        roll = self._roll_save(effect.save)
        removal = None
        if roll.success and effect.save.success is SaveSuccess.END:
            removal = self.remove(
                instance_id,
                reason=RemovalReason.SAVED,
                quiet=quiet,
            )
        return SaveAttempt(effect=effect, roll=roll, removal=removal)

    def process_duration(
        self, pulses: int = 1, *, quiet: bool = False
    ) -> tuple[RemovalResult, ...]:
        """Advance effect saves and durations by a number of world pulses."""
        if isinstance(pulses, bool) or not isinstance(pulses, int) or pulses < 0:
            raise EffectError(
                "Effect duration advancement must be a non-negative integer."
            )
        removed: list[RemovalResult] = []
        for _ in range(pulses):
            for snapshot in self.all():
                effect = self.get(snapshot.instance_id)
                if effect is None:
                    continue
                if effect.save and effect.save.timing is SaveTiming.ON_PULSE:
                    attempt = self.attempt_save(effect.instance_id, quiet=quiet)
                    if attempt and attempt.removal is not None:
                        removed.append(attempt.removal)
                        continue

                effect = self.get(snapshot.instance_id)
                if effect is None or effect.remaining_pulses is None:
                    continue
                remaining = effect.remaining_pulses - 1
                if remaining <= 0:
                    removed.append(
                        self.remove(
                            effect.instance_id,
                            reason=RemovalReason.EXPIRED,
                            quiet=quiet,
                        )
                    )
                    continue
                records = self._records()
                records[effect.instance_id]["remaining_pulses"] = remaining
                self._write_records(records)
        return tuple(removed)

    def _records(self) -> dict[str, dict[str, Any]]:
        """Read and validate a detached copy of persistent instance records."""
        storage = self.owner.attributes.get(self.attribute_key)
        if storage is None:
            return {}
        if not isinstance(storage, Mapping):
            raise EffectStorageError("Active effect storage must be a mapping.")
        if storage.get("version") != EFFECTS_SCHEMA_VERSION:
            raise EffectStorageError(
                "Active effect storage has an unsupported version."
            )
        instances = storage.get("instances")
        if not isinstance(instances, Mapping):
            raise EffectStorageError("Active effect instances must be a mapping.")
        records: dict[str, dict[str, Any]] = {}
        for instance_id, record in instances.items():
            if not isinstance(instance_id, str) or not isinstance(record, Mapping):
                raise EffectStorageError("An active effect record is malformed.")
            records[instance_id] = dict(record)
        return records

    def _write_records(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        """Persist a fresh versioned effect payload or remove an empty one."""
        if not records:
            self.owner.attributes.remove(self.attribute_key)
            return
        payload = {
            "version": EFFECTS_SCHEMA_VERSION,
            "instances": {
                instance_id: dict(record) for instance_id, record in records.items()
            },
        }
        self.owner.attributes.add(self.attribute_key, payload)

    def _new_record(
        self,
        definition: EffectDefinition,
        *,
        source: Any,
        source_key: str | None,
        duration: int | None,
        stacks: int,
        modifiers: Mapping[str, int],
        save: SaveRule | None,
    ) -> dict[str, Any]:
        """Build one validated, serializable active-effect record."""
        return {
            "key": definition.key,
            "stacks": stacks,
            "remaining_pulses": duration,
            "source": source,
            "source_dbref": getattr(source, "dbref", None),
            "source_key": source_key,
            "source_name": str(getattr(source, "key", "") or source_key or "Unknown"),
            "modifiers": dict(modifiers),
            "removal_categories": sorted(definition.removal_categories),
            "save": save.serialize() if save else None,
        }

    def _instance(self, instance_id: str, record: Mapping[str, Any]) -> ActiveEffect:
        """Validate a persistent record and expose it as a read-only view."""
        try:
            key = str(record["key"])
            stacks = record["stacks"]
            remaining = record["remaining_pulses"]
            source_name = str(record["source_name"])
            modifiers = _validated_modifiers(record.get("modifiers", {}))
            removal_categories = frozenset(record.get("removal_categories", ()))
        except (KeyError, TypeError, ValueError) as err:
            raise EffectStorageError("An active effect record is invalid.") from err
        _validate_key(key, "effect")
        if isinstance(stacks, bool) or not isinstance(stacks, int) or stacks < 1:
            raise EffectStorageError("An active effect has an invalid stack count.")
        try:
            _validate_duration(remaining)
        except EffectError as err:
            raise EffectStorageError(
                "An active effect has an invalid duration."
            ) from err
        raw_save = record.get("save")
        if raw_save is not None and not isinstance(raw_save, Mapping):
            raise EffectStorageError("An active effect has invalid save data.")
        save = SaveRule.deserialize(raw_save) if raw_save is not None else None
        for category in removal_categories:
            try:
                _validate_key(category, "removal category")
            except EffectError as err:
                raise EffectStorageError(
                    "An active effect has invalid removal metadata."
                ) from err
        return ActiveEffect(
            owner=self.owner,
            instance_id=instance_id,
            key=key,
            stacks=stacks,
            remaining_pulses=remaining,
            source=record.get("source"),
            source_dbref=record.get("source_dbref"),
            source_key=record.get("source_key"),
            source_name=source_name,
            instance_modifiers=MappingProxyType(modifiers),
            instance_removal_categories=removal_categories,
            save=save,
            definition=self.registry.get(key),
        )

    def _roll_save(self, rule: SaveRule) -> RollResult:
        """Resolve an effect save through the game's canonical dice API."""
        bonus = self.owner.stats.saving_throw_bonus(rule.ability)
        return roll_check(bonus, rule.dc)

    def _reconcile_stats(self) -> None:
        """Immediately clamp mutable resources after derived-stat changes."""
        stats = getattr(self.owner, "stats", None)
        if stats is not None:
            stats.set_hp(stats.hp_current)

    def _emit(
        self, definition: EffectDefinition, event: str, effect: ActiveEffect
    ) -> None:
        """Emit one definition-owned lifecycle message to target and room."""
        message = definition.messages.get(event)
        if message is None:
            return
        values = {
            "effect": effect.name,
            "source": effect.source_name,
            "stacks": effect.stacks,
            "target": str(getattr(self.owner, "key", "Someone")),
        }
        if message.target:
            target_message = message.target.format_map(values)
            if event in {"expire", "remove", "save"}:
                # Import lazily so the foundational systems remain acyclic.
                from systems.lifecycle import queue_or_deliver_character_notice

                queue_or_deliver_character_notice(
                    self.owner,
                    target_message,
                    notice_id=f"effect:{effect.instance_id}:{event}",
                )
            else:
                self.owner.msg(target_message)
        location = getattr(self.owner, "location", None)
        if message.room and location is not None:
            location.msg_contents(
                message.room.format_map(values),
                exclude=[self.owner],
                from_obj=self.owner,
            )

    @staticmethod
    def _source_matches(effect: ActiveEffect, source: Any) -> bool:
        """Match a live source or its stable dbref snapshot."""
        if effect.source == source:
            return True
        source_dbref = getattr(
            source, "dbref", source if isinstance(source, str) else None
        )
        return source_dbref is not None and effect.source_dbref == source_dbref


def _canonical_ability(value: str) -> str:
    """Return a canonical ability name for a full name or abbreviation."""
    lowered = str(value).strip().lower()
    for name in ABILITY_NAMES:
        if lowered in {name.lower(), ABILITY_SHORT[name].lower()}:
            return name
    raise EffectError(f"Unknown saving-throw ability: {value}")


def _validate_key(value: str, label: str) -> None:
    """Validate a stable lowercase registry/category key."""
    if (
        not isinstance(value, str)
        or not value
        or value[0] not in "abcdefghijklmnopqrstuvwxyz"
        or any(character not in _KEY_CHARACTERS for character in value)
    ):
        raise EffectError(f"Invalid {label} key: {value!r}")


def _validate_duration(value: int | None) -> None:
    """Validate a permanent or positive world-pulse duration."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EffectError("An effect duration must be permanent or positive pulses.")


def _validated_modifiers(modifiers: Mapping[str, Any]) -> dict[str, int]:
    """Return validated modifiers accepted by the canonical stat API."""
    if not isinstance(modifiers, Mapping):
        raise EffectError("Effect modifiers must be a mapping.")
    validated: dict[str, int] = {}
    for name, value in modifiers.items():
        if not isinstance(name, str) or not _is_modifier_name(name):
            raise EffectError(f"Unknown effect modifier: {name!r}")
        if isinstance(value, bool) or not isinstance(value, int):
            raise EffectError(f"Effect modifier '{name}' must be an integer.")
        validated[name] = value
    return validated


def _is_modifier_name(name: str) -> bool:
    """Return whether a modifier name is consumed by ``CharacterStats``."""
    if name in _BASE_MODIFIERS:
        return True
    prefix, separator, detail = name.partition(":")
    if not separator:
        return False
    if prefix == "ability":
        return detail in _ABILITY_KEYS
    if prefix == "saving_throw":
        return detail in _ABILITY_KEYS
    if prefix == "skill":
        return detail in _SKILL_KEYS
    if prefix == "recovery":
        # Resources own the semantics; RULES-04 only requires a stable resource
        # key so effects can provide numeric recovery bonuses or penalties.
        try:
            _validate_key(detail, "recovery resource")
        except EffectError:
            return False
        return True
    return False


def _validate_messages(messages: Mapping[str, EffectMessage]) -> None:
    """Reject unknown events, message types, and unsafe template fields."""
    formatter = Formatter()
    for event, message in messages.items():
        if event not in _MESSAGE_EVENTS:
            raise EffectError(f"Unknown effect message event: {event}")
        if not isinstance(message, EffectMessage):
            raise EffectError("Effect messages must use EffectMessage values.")
        for template in (message.target, message.room):
            try:
                fields = {
                    field_name
                    for _, field_name, format_spec, conversion in formatter.parse(
                        template
                    )
                    if field_name is not None and not format_spec and conversion is None
                }
            except ValueError as err:
                raise EffectError(
                    "An effect message contains invalid formatting."
                ) from err
            if any(
                field_name is not None and (format_spec or conversion is not None)
                for _, field_name, format_spec, conversion in formatter.parse(template)
            ):
                raise EffectError(
                    "Effect messages cannot use conversions or format specs."
                )
            unknown = fields - _MESSAGE_FIELDS
            if unknown:
                raise EffectError(f"Unknown effect message field: {sorted(unknown)[0]}")
