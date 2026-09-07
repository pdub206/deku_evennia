"""MAGIC-01's immutable, data-only spell and ability registry.

This module deliberately defines *what* a magical action is, but never spends
resources, selects objects, rolls dice, or changes the world.  Those are
MAGIC-02 and MAGIC-04 responsibilities.  Keeping definitions serializable and
handlers named makes class progression, NPCs, prototypes, help, and future
cast snapshots agree without allowing content to execute Python.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from systems.equipment import DAMAGE_TYPES
from world.chargen_data import ABILITY_NAMES, ABILITY_SHORT

MAGIC_REGISTRY_VERSION = 1
MAX_ALIASES = 12
MAX_TAGS = 16
MAX_TARGETS = 32
MAX_CAST_TIME = 60
MAX_DICE_COUNT = 32
MAX_DIE_SIZE = 100
MAX_DICE_BONUS = 100
MAX_SCALING_STEPS = 20
MAX_HELP_SUMMARY = 320
MAX_SRD_REFERENCE = 160

_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9 .'-]{0,63}$")
_HELP_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9 .'-]{0,95}$")
_PRIMITIVE_TYPES = (str, int, float, bool, type(None))


class MagicRegistryError(ValueError):
    """Raised when a magic definition or serializable state is unsafe."""


class MagicKind:
    """Stable kinds shared by spells, class features, and item activations."""

    SPELL = "spell"
    ABILITY = "ability"


class AccessMode:
    """How an action may be supplied to an actor."""

    LEARNED = "learned"
    PREPARED = "prepared"
    INNATE = "innate"
    ITEM = "item"


class TargetingMode:
    """The only target-selection shapes a handler may request."""

    SELF = "self"
    CREATURE = "creature"
    ALLY = "ally"
    HOSTILE = "hostile"
    OBJECT = "object"
    ROOM = "room"
    AREA = "area"
    GROUP = "group"


_TARGETING_MODES = frozenset(
    {
        TargetingMode.SELF,
        TargetingMode.CREATURE,
        TargetingMode.ALLY,
        TargetingMode.HOSTILE,
        TargetingMode.OBJECT,
        TargetingMode.ROOM,
        TargetingMode.AREA,
        TargetingMode.GROUP,
    }
)
TARGET_FILTERS = frozenset(
    {
        "character",
        "living",
        "undead",
        "construct",
        "item",
        "worn",
        "unworn",
        "willing",
    }
)


class RangeCategory:
    """Semantic ranges backed by the current room model, not coordinates."""

    SELF = "self"
    TOUCH = "touch"
    ROOM = "room"
    SIGHT = "sight"


class StackingPolicy:
    """How recasting interacts with an existing effect of this source."""

    REJECT = "reject"
    REFRESH = "refresh"
    REPLACE = "replace"
    STACK = "stack"
    INDEPENDENT = "independent"


@dataclass(frozen=True)
class ClassAccess:
    """One class and minimum character level that may select an action."""

    class_key: str
    minimum_level: int


@dataclass(frozen=True)
class Targeting:
    """Bounded selection and eligibility rules, expressed without search code."""

    mode: str
    filters: tuple[str, ...] = ()
    include_caster: bool = False
    allow_dead_or_dying: bool = False
    allow_hidden: bool = False
    allow_npcs: bool = True
    maximum_targets: int = 1


@dataclass(frozen=True)
class ResourceCost:
    """One integer resource reservation interpreted later by MAGIC-02."""

    resource_key: str
    amount: int


@dataclass(frozen=True)
class DiceExpression:
    """A bounded dice expression that callers resolve through ``systems.dice``."""

    count: int
    sides: int
    bonus: int = 0

    def notation(self) -> str:
        """Return the canonical player-safe notation for this expression."""
        suffix = f"+{self.bonus}" if self.bonus > 0 else str(self.bonus or "")
        return f"{self.count}d{self.sides}{suffix}"


@dataclass(frozen=True)
class Scaling:
    """Finite, explicit potency changes keyed by effective cast level."""

    levels: tuple[int, ...] = ()
    dice_per_step: int = 0
    healing_per_step: int = 0


@dataclass(frozen=True)
class Damage:
    """Validated damage metadata; application remains a combat adapter concern."""

    dice: DiceExpression
    damage_type: str


@dataclass(frozen=True)
class Save:
    """A target saving throw using MAGIC-02's snapshotted canonical DC."""

    ability: str
    on_success: str = "negate"


@dataclass(frozen=True)
class PlayerHelp:
    """Minimal presentation metadata kept beside the executable-safe definition."""

    key: str
    summary: str
    restrictions: str = ""


@dataclass(frozen=True)
class HandlerContract:
    """Code-owned schema for a named action adapter.

    ``key`` is all content stores.  The actual implementation is registered by
    the owning subsystem when casting is introduced; it is never persisted in
    a definition or prototype.
    """

    key: str
    required_fields: frozenset[str] = frozenset()
    permitted_targets: frozenset[str] = _TARGETING_MODES


STANDARD_HANDLERS: Mapping[str, HandlerContract] = MappingProxyType(
    {
        "spell_attack": HandlerContract(
            "spell_attack", frozenset({"damage"}), frozenset({TargetingMode.HOSTILE})
        ),
        "saving_throw": HandlerContract(
            "saving_throw",
            frozenset({"save"}),
            frozenset(
                {
                    TargetingMode.CREATURE,
                    TargetingMode.ALLY,
                    TargetingMode.HOSTILE,
                    TargetingMode.AREA,
                    TargetingMode.GROUP,
                }
            ),
        ),
        "healing": HandlerContract(
            "healing",
            frozenset({"healing"}),
            frozenset(
                {
                    TargetingMode.SELF,
                    TargetingMode.CREATURE,
                    TargetingMode.ALLY,
                    TargetingMode.AREA,
                    TargetingMode.GROUP,
                }
            ),
        ),
        "effect": HandlerContract("effect", frozenset({"effects"}), _TARGETING_MODES),
        "movement": HandlerContract(
            "movement",
            frozenset(),
            frozenset(
                {
                    TargetingMode.SELF,
                    TargetingMode.CREATURE,
                    TargetingMode.ALLY,
                    TargetingMode.OBJECT,
                    TargetingMode.ROOM,
                }
            ),
        ),
        "utility": HandlerContract("utility"),
    }
)


@dataclass(frozen=True)
class MagicDefinition:
    """The complete declarative contract for one spell or class ability."""

    key: str
    display_name: str
    aliases: tuple[str, ...]
    kind: str
    school: str
    tags: tuple[str, ...]
    class_access: tuple[ClassAccess, ...]
    access_modes: tuple[str, ...]
    action_category: str
    handler_key: str
    targeting: Targeting
    range: str
    spell_level: int = 0
    uses_spell_slot: bool = False
    cost: ResourceCost | None = None
    cast_time: int = 1
    concentration: bool = False
    maintenance: str = "none"
    damage: Damage | None = None
    healing: DiceExpression | None = None
    save: Save | None = None
    duration: int | None = None
    interruption: str = "standard"
    effect_keys: tuple[str, ...] = ()
    scaling: Scaling = field(default_factory=Scaling)
    stacking: str = StackingPolicy.REJECT
    messages: Mapping[str, str] = field(default_factory=dict)
    player_help: PlayerHelp | None = None
    srd_reference: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class MagicRegistry:
    """Read-only, deterministic registry for every magic consumer."""

    version: int
    definitions: Mapping[str, MagicDefinition]
    aliases: Mapping[str, str]
    handlers: Mapping[str, HandlerContract]
    fingerprint: str
    requires_srd_references: bool = False

    def definition_for(
        self, key: str, *, include_disabled: bool = True
    ) -> MagicDefinition:
        """Return an exact stable key, retaining disabled entries for cleanup."""
        definition = self.definitions.get(key) if isinstance(key, str) else None
        if definition is None or (not include_disabled and not definition.enabled):
            raise MagicRegistryError("Unknown or unavailable magic action.")
        return definition

    def resolve(self, name: str, *, include_disabled: bool = False) -> MagicDefinition:
        """Resolve a normalized display alias without fuzzy matching or leaks."""
        normalized = normalize_alias(name)
        key = self.aliases.get(normalized)
        if key is None:
            raise MagicRegistryError("Unknown or unavailable magic action.")
        return self.definition_for(key, include_disabled=include_disabled)

    def is_available(self, key: object) -> bool:
        """Return whether a stable key is currently selectable."""
        return (
            isinstance(key, str)
            and key in self.definitions
            and self.definitions[key].enabled
        )

    def available_for(self, class_key: str, level: int) -> tuple[MagicDefinition, ...]:
        """Return source-ordered actions a class can select at this level."""
        _validate_level(level)
        if not isinstance(class_key, str):
            return ()
        return tuple(
            definition
            for definition in self.definitions.values()
            if definition.enabled
            and any(
                access.class_key == class_key and access.minimum_level <= level
                for access in definition.class_access
            )
        )

    def player_help_entry(self, key: str) -> Mapping[str, Any]:
        """Generate player help from the same metadata used for validation."""
        definition = self.definition_for(key, include_disabled=False)
        if definition.player_help is None:
            raise MagicRegistryError("Magic action has no player help.")
        return MappingProxyType(
            {
                "key": definition.player_help.key,
                "aliases": list(definition.aliases),
                "category": "Magic",
                "text": _render_player_help(definition),
            }
        )


@dataclass(frozen=True)
class CastSnapshot:
    """Primitive-only committed action data for MAGIC-02 delayed resolution."""

    source_key: str
    registry_version: int
    caster_id: int
    target_ids: tuple[int, ...]
    cast_level: int
    save_dc: int | None
    attack_bonus: int | None
    resource_reservation: Mapping[str, int]

    def serialize(self) -> dict[str, Any]:
        """Return only stable primitives suitable for an Evennia Attribute."""
        return {
            "source_key": self.source_key,
            "registry_version": self.registry_version,
            "caster_id": self.caster_id,
            "target_ids": list(self.target_ids),
            "cast_level": self.cast_level,
            "save_dc": self.save_dc,
            "attack_bonus": self.attack_bonus,
            "resource_reservation": dict(self.resource_reservation),
        }


def build_magic_registry(
    definitions: Iterable[MagicDefinition],
    *,
    version: int = MAGIC_REGISTRY_VERSION,
    handlers: Mapping[str, HandlerContract] = STANDARD_HANDLERS,
    class_keys: Iterable[str] | None = None,
    resource_keys: Iterable[str] | None = None,
    damage_types: Iterable[str] = DAMAGE_TYPES,
    effect_keys: Iterable[str] = (),
    help_keys: Iterable[str] = (),
    require_srd_references: bool = False,
) -> MagicRegistry:
    """Validate and freeze a whole magic graph before it becomes selectable."""
    _validate_positive_int(version, "Registry version", maximum=1000000)
    handler_map = _validated_handlers(handlers)
    classes = frozenset(class_keys if class_keys is not None else _default_class_keys())
    resources = frozenset(
        resource_keys if resource_keys is not None else _default_resource_keys()
    )
    damages = frozenset(damage_types)
    effects = frozenset(effect_keys)
    help_lookup = frozenset(normalize_help_key(value) for value in help_keys)
    indexed: dict[str, MagicDefinition] = {}
    aliases: dict[str, str] = {}
    for definition in definitions:
        if not isinstance(definition, MagicDefinition):
            raise MagicRegistryError(
                "Magic registry entries must be MagicDefinition values."
            )
        _validate_definition(
            definition,
            handler_map,
            classes,
            resources,
            damages,
            effects,
            help_lookup,
            require_srd_references,
        )
        if definition.key in indexed:
            raise MagicRegistryError(f"Duplicate magic key '{definition.key}'.")
        indexed[definition.key] = _freeze_definition(definition)
        for alias in (
            definition.key.replace(".", " ").replace("_", " "),
            definition.display_name,
            *definition.aliases,
        ):
            normalized = normalize_alias(alias)
            other = aliases.setdefault(normalized, definition.key)
            if other != definition.key:
                raise MagicRegistryError(f"Ambiguous magic alias '{alias}'.")
    payload = {
        "version": version,
        "definitions": [
            asdict(replace(value, messages=dict(value.messages)))
            for value in indexed.values()
        ],
    }
    fingerprint = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return MagicRegistry(
        version,
        MappingProxyType(indexed),
        MappingProxyType(aliases),
        MappingProxyType(handler_map),
        fingerprint,
        require_srd_references,
    )


def validate_persistent_magic_state(value: Any) -> None:
    """Reject executable or unbounded content before it reaches an Attribute."""
    _validate_primitive_tree(value, depth=0, items=[0])


def deserialize_cast_snapshot(value: Mapping[str, Any]) -> CastSnapshot:
    """Reconstruct strictly validated primitive delayed-cast state."""
    if not isinstance(value, Mapping) or set(value) != {
        "source_key",
        "registry_version",
        "caster_id",
        "target_ids",
        "cast_level",
        "save_dc",
        "attack_bonus",
        "resource_reservation",
    }:
        raise MagicRegistryError("A cast snapshot has an invalid shape.")
    source_key = value["source_key"]
    _validate_key(source_key, "magic key")
    for name, maximum in (
        ("registry_version", 1000000),
        ("caster_id", 2**63 - 1),
        ("cast_level", 9),
    ):
        _validate_positive_int(value[name], name.replace("_", " "), maximum=maximum)
    targets = value["target_ids"]
    if (
        isinstance(targets, (str, bytes))
        or not isinstance(targets, Sequence)
        or len(targets) > MAX_TARGETS
    ):
        raise MagicRegistryError("A cast snapshot has invalid targets.")
    if any(
        isinstance(item, bool) or not isinstance(item, int) or item <= 0
        for item in targets
    ):
        raise MagicRegistryError("A cast snapshot has invalid targets.")
    for name in ("save_dc", "attack_bonus"):
        number = value[name]
        if number is not None and (
            isinstance(number, bool)
            or not isinstance(number, int)
            or not -100 <= number <= 100
        ):
            raise MagicRegistryError("A cast snapshot has invalid potency.")
    reservation = value["resource_reservation"]
    if not isinstance(reservation, Mapping) or len(reservation) > 8:
        raise MagicRegistryError("A cast snapshot has invalid resource data.")
    for resource, amount in reservation.items():
        _validate_key(resource, "resource key")
        _validate_nonnegative_int(amount, "resource reservation", maximum=100000)
    return CastSnapshot(
        source_key,
        value["registry_version"],
        value["caster_id"],
        tuple(targets),
        value["cast_level"],
        value["save_dc"],
        value["attack_bonus"],
        MappingProxyType(dict(reservation)),
    )


def normalize_alias(value: Any) -> str:
    """Normalize a display key while preserving exact, unambiguous matching."""
    if not isinstance(value, str):
        raise MagicRegistryError("A magic alias must be text.")
    normalized = " ".join(value.casefold().split())
    if not _ALIAS_RE.fullmatch(normalized):
        raise MagicRegistryError("A magic alias contains unsupported characters.")
    return normalized


def normalize_help_key(value: Any) -> str:
    """Return the file-help lookup form used by definition validation."""
    if not isinstance(value, str):
        raise MagicRegistryError("A help key must be text.")
    normalized = " ".join(value.casefold().split())
    if not _HELP_KEY_RE.fullmatch(normalized):
        raise MagicRegistryError("A help key contains unsupported characters.")
    return normalized


def _validate_definition(
    definition: MagicDefinition,
    handlers: Mapping[str, HandlerContract],
    classes: frozenset[str],
    resources: frozenset[str],
    damages: frozenset[str],
    effects: frozenset[str],
    help_keys: frozenset[str],
    require_srd_references: bool,
) -> None:
    _validate_key(definition.key, "magic key")
    if (
        not isinstance(definition.display_name, str)
        or not definition.display_name.strip()
        or len(definition.display_name) > 64
    ):
        raise MagicRegistryError("A magic action needs a bounded display name.")
    if definition.kind not in {MagicKind.SPELL, MagicKind.ABILITY}:
        raise MagicRegistryError("A magic action has an invalid kind.")
    _validate_nonnegative_int(definition.spell_level, "spell level", maximum=9)
    if definition.kind == MagicKind.ABILITY and definition.spell_level:
        raise MagicRegistryError("Only spells may have a spell level.")
    if not isinstance(definition.uses_spell_slot, bool):
        raise MagicRegistryError("A magic action has an invalid slot-cost rule.")
    if definition.uses_spell_slot and (
        definition.kind != MagicKind.SPELL
        or definition.spell_level == 0
        or definition.cost is not None
    ):
        raise MagicRegistryError(
            "A slot-cost spell must be leveled and cannot also have a fixed cost."
        )
    _validate_key(definition.school, "school")
    _validate_key(definition.action_category, "action category")
    _validate_key(definition.handler_key, "handler")
    if definition.handler_key not in handlers:
        raise MagicRegistryError(
            f"Magic action '{definition.key}' has an unknown handler."
        )
    _validate_strings(definition.aliases, "aliases", MAX_ALIASES, normalize_alias)
    _validate_strings(
        definition.tags, "tags", MAX_TAGS, lambda item: _key_result(item, "tag")
    )
    if not definition.class_access or len(definition.class_access) > 12:
        raise MagicRegistryError("A magic action needs one or more class access rules.")
    seen_classes: set[str] = set()
    for access in definition.class_access:
        if not isinstance(access, ClassAccess) or access.class_key not in classes:
            raise MagicRegistryError("A magic action references an unknown class.")
        _validate_level(access.minimum_level)
        if access.class_key in seen_classes:
            raise MagicRegistryError("A magic action has duplicate class access.")
        seen_classes.add(access.class_key)
    if not definition.access_modes or set(definition.access_modes) - {
        AccessMode.LEARNED,
        AccessMode.PREPARED,
        AccessMode.INNATE,
        AccessMode.ITEM,
    }:
        raise MagicRegistryError("A magic action has invalid access modes.")
    if len(set(definition.access_modes)) != len(definition.access_modes):
        raise MagicRegistryError("A magic action has duplicate access modes.")
    _validate_targeting(definition.targeting)
    if definition.range not in {
        RangeCategory.SELF,
        RangeCategory.TOUCH,
        RangeCategory.ROOM,
        RangeCategory.SIGHT,
    }:
        raise MagicRegistryError("A magic action has an invalid range.")
    if (
        definition.targeting.mode == TargetingMode.SELF
        and definition.range != RangeCategory.SELF
    ):
        raise MagicRegistryError("Self-targeted actions must use self range.")
    if (
        definition.targeting.mode != TargetingMode.SELF
        and definition.range == RangeCategory.SELF
    ):
        raise MagicRegistryError("Only self-targeted actions may use self range.")
    if definition.cost is not None:
        if (
            not isinstance(definition.cost, ResourceCost)
            or definition.cost.resource_key not in resources
        ):
            raise MagicRegistryError("A magic action references an unknown resource.")
        _validate_nonnegative_int(
            definition.cost.amount, "resource cost", maximum=100000
        )
    _validate_nonnegative_int(definition.cast_time, "cast time", maximum=MAX_CAST_TIME)
    if not isinstance(definition.concentration, bool) or definition.maintenance not in {
        "none",
        "concentration",
        "sustained",
    }:
        raise MagicRegistryError("A magic action has an invalid maintenance rule.")
    if definition.concentration != (definition.maintenance == "concentration"):
        raise MagicRegistryError("Concentration must match the maintenance rule.")
    if definition.duration is not None:
        _validate_positive_int(definition.duration, "duration", maximum=100000)
    if definition.interruption not in {"standard", "none", "on_damage", "on_move"}:
        raise MagicRegistryError("A magic action has an invalid interruption rule.")
    _validate_damage(definition.damage, damages)
    if definition.healing is not None:
        _validate_dice(definition.healing)
    _validate_save(definition.save)
    _validate_scaling(definition.scaling)
    if definition.scaling.dice_per_step and definition.damage is None:
        raise MagicRegistryError("Damage scaling needs a damage consequence.")
    if definition.scaling.healing_per_step and definition.healing is None:
        raise MagicRegistryError("Healing scaling needs a healing consequence.")
    if definition.stacking not in {
        StackingPolicy.REJECT,
        StackingPolicy.REFRESH,
        StackingPolicy.REPLACE,
        StackingPolicy.STACK,
        StackingPolicy.INDEPENDENT,
    }:
        raise MagicRegistryError("A magic action has an invalid stacking rule.")
    if (
        not isinstance(definition.effect_keys, tuple)
        or len(definition.effect_keys) > 16
        or len(set(definition.effect_keys)) != len(definition.effect_keys)
        or any(key not in effects for key in definition.effect_keys)
    ):
        raise MagicRegistryError(
            "A magic action references an unknown or duplicate effect."
        )
    if definition.concentration and (
        definition.duration is None or not definition.effect_keys
    ):
        raise MagicRegistryError(
            "A concentration action needs a timed registered effect."
        )
    if definition.handler_key == "saving_throw" and (
        bool(definition.damage) == bool(definition.effect_keys)
    ):
        raise MagicRegistryError(
            "A saving-throw action needs exactly one supported consequence."
        )
    if (
        definition.handler_key == "saving_throw"
        and definition.damage is not None
        and definition.targeting.mode != TargetingMode.HOSTILE
    ):
        raise MagicRegistryError(
            "A damaging saving-throw action must use hostile targeting."
        )
    if (
        definition.handler_key in {"effect", "saving_throw"}
        and definition.effect_keys
        and definition.save is not None
        and definition.save.on_success != "negate"
    ):
        raise MagicRegistryError(
            "A saving-throw effect currently supports only negated application."
        )
    _validate_messages(definition.messages)
    _validate_help(definition.player_help, help_keys)
    if require_srd_references and (
        not isinstance(definition.srd_reference, str)
        or not definition.srd_reference.startswith("SRD 5.2.1 ")
        or len(definition.srd_reference) > MAX_SRD_REFERENCE
    ):
        raise MagicRegistryError(
            "A released magic action needs an SRD 5.2.1 reference."
        )
    supplied = {
        "damage" if definition.damage else "",
        "healing" if definition.healing else "",
        "save" if definition.save else "",
        "effects" if definition.effect_keys else "",
    }
    required = handlers[definition.handler_key].required_fields
    if not required <= supplied:
        raise MagicRegistryError("A magic action does not meet its handler contract.")
    if (
        definition.targeting.mode
        not in handlers[definition.handler_key].permitted_targets
    ):
        raise MagicRegistryError(
            "A magic action target mode is not supported by its handler."
        )
    if not isinstance(definition.enabled, bool):
        raise MagicRegistryError("A magic action availability flag must be boolean.")


def _freeze_definition(definition: MagicDefinition) -> MagicDefinition:
    """Copy mappings so a caller cannot mutate a registry after validation."""
    return replace(definition, messages=MappingProxyType(dict(definition.messages)))


def _validated_handlers(
    handlers: Mapping[str, HandlerContract],
) -> dict[str, HandlerContract]:
    if not isinstance(handlers, Mapping):
        raise MagicRegistryError("Handler contracts must be a mapping.")
    normalized: dict[str, HandlerContract] = {}
    for key, contract in handlers.items():
        if not isinstance(contract, HandlerContract) or key != contract.key:
            raise MagicRegistryError("A handler contract is invalid.")
        _validate_key(key, "handler")
        if (
            not isinstance(contract.required_fields, frozenset)
            or not isinstance(contract.permitted_targets, frozenset)
            or not contract.required_fields <= {"damage", "healing", "save", "effects"}
            or not contract.permitted_targets <= _TARGETING_MODES
        ):
            raise MagicRegistryError("A handler contract has an unsafe schema.")
        normalized[key] = contract
    return normalized


def _validate_targeting(targeting: Targeting) -> None:
    if not isinstance(targeting, Targeting) or targeting.mode not in _TARGETING_MODES:
        raise MagicRegistryError("A magic action has an invalid targeting mode.")
    _validate_strings(
        targeting.filters,
        "target filters",
        12,
        _target_filter_result,
    )
    if len(set(targeting.filters)) != len(targeting.filters):
        raise MagicRegistryError("A magic action has duplicate target filters.")
    if not all(
        isinstance(value, bool)
        for value in (
            targeting.include_caster,
            targeting.allow_dead_or_dying,
            targeting.allow_hidden,
            targeting.allow_npcs,
        )
    ):
        raise MagicRegistryError("Target eligibility flags must be boolean.")
    _validate_positive_int(
        targeting.maximum_targets, "maximum targets", maximum=MAX_TARGETS
    )
    if targeting.mode == TargetingMode.SELF and (
        not targeting.include_caster or targeting.maximum_targets != 1
    ):
        raise MagicRegistryError("Self targeting must select exactly the caster.")
    if (
        targeting.mode
        in {
            TargetingMode.CREATURE,
            TargetingMode.ALLY,
            TargetingMode.HOSTILE,
            TargetingMode.OBJECT,
            TargetingMode.ROOM,
        }
        and targeting.maximum_targets != 1
    ):
        raise MagicRegistryError(
            "Single-target actions must select exactly one target."
        )


def _validate_damage(damage: Damage | None, damage_types: frozenset[str]) -> None:
    if damage is None:
        return
    if not isinstance(damage, Damage) or damage.damage_type not in damage_types:
        raise MagicRegistryError("A magic action references an unknown damage type.")
    _validate_dice(damage.dice)


def _validate_dice(dice: DiceExpression) -> None:
    if not isinstance(dice, DiceExpression):
        raise MagicRegistryError("Magic dice must use DiceExpression.")
    _validate_positive_int(dice.count, "dice count", maximum=MAX_DICE_COUNT)
    _validate_positive_int(dice.sides, "die size", maximum=MAX_DIE_SIZE)
    if (
        isinstance(dice.bonus, bool)
        or not isinstance(dice.bonus, int)
        or abs(dice.bonus) > MAX_DICE_BONUS
    ):
        raise MagicRegistryError("A magic dice bonus is outside the supported range.")


def _validate_save(save: Save | None) -> None:
    if save is None:
        return
    if not isinstance(save, Save) or save.on_success not in {"negate", "half", "none"}:
        raise MagicRegistryError("A magic saving throw is invalid.")
    canonical = _canonical_ability(save.ability)
    if canonical != save.ability:
        raise MagicRegistryError("Saving throws must use canonical ability names.")


def _validate_scaling(scaling: Scaling) -> None:
    if not isinstance(scaling, Scaling) or len(scaling.levels) > MAX_SCALING_STEPS:
        raise MagicRegistryError("A magic scaling rule is invalid.")
    if (
        any(
            isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 9
            for level in scaling.levels
        )
        or tuple(sorted(set(scaling.levels))) != scaling.levels
    ):
        raise MagicRegistryError("Magic scaling levels must be sorted and bounded.")
    for value in (scaling.dice_per_step, scaling.healing_per_step):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= MAX_DICE_COUNT
        ):
            raise MagicRegistryError("A magic scaling amount is invalid.")
    if scaling.levels and not (scaling.dice_per_step or scaling.healing_per_step):
        raise MagicRegistryError("A scaling rule must change damage or healing.")
    if not scaling.levels and (scaling.dice_per_step or scaling.healing_per_step):
        raise MagicRegistryError("A scaling amount needs explicit levels.")
    if scaling.dice_per_step and scaling.healing_per_step:
        raise MagicRegistryError(
            "A scaling rule may change damage or healing, not both."
        )


def _validate_help(help_data: PlayerHelp | None, available: frozenset[str]) -> None:
    if not isinstance(help_data, PlayerHelp):
        raise MagicRegistryError("Every magic action needs player help metadata.")
    key = normalize_help_key(help_data.key)
    if key not in available:
        raise MagicRegistryError("A magic action references missing player help.")
    if (
        not isinstance(help_data.summary, str)
        or not help_data.summary.strip()
        or len(help_data.summary) > MAX_HELP_SUMMARY
    ):
        raise MagicRegistryError("Magic player help needs a bounded summary.")
    if (
        not isinstance(help_data.restrictions, str)
        or len(help_data.restrictions) > MAX_HELP_SUMMARY
    ):
        raise MagicRegistryError("Magic player-help restrictions are invalid.")


def _validate_messages(messages: Mapping[str, str]) -> None:
    if not isinstance(messages, Mapping) or set(messages) - {
        "start",
        "success",
        "failure",
        "interrupted",
    }:
        raise MagicRegistryError("A magic action has invalid message metadata.")
    for value in messages.values():
        if (
            not isinstance(value, str)
            or len(value) > MAX_HELP_SUMMARY
            or "{" in value
            or "}" in value
        ):
            raise MagicRegistryError("Magic messages must be bounded literal text.")


def _validate_strings(values: Any, label: str, maximum: int, validator: Any) -> None:
    if (
        isinstance(values, (str, bytes))
        or not isinstance(values, tuple)
        or len(values) > maximum
    ):
        raise MagicRegistryError(f"Magic {label} must be a bounded tuple.")
    normalized = tuple(validator(value) for value in values)
    if len(set(normalized)) != len(normalized):
        raise MagicRegistryError(f"Magic {label} cannot contain duplicates.")


def _validate_primitive_tree(value: Any, *, depth: int, items: list[int]) -> None:
    if depth > 6:
        raise MagicRegistryError("Magic persistent data is nested too deeply.")
    items[0] += 1
    if items[0] > 256:
        raise MagicRegistryError("Magic persistent data has too many values.")
    if isinstance(value, _PRIMITIVE_TYPES):
        if isinstance(value, float) and (
            value != value or value in (float("inf"), float("-inf"))
        ):
            raise MagicRegistryError(
                "Magic persistent data cannot contain non-finite numbers."
            )
        return
    if isinstance(value, Mapping):
        if len(value) > 64 or any(
            not isinstance(key, str) or len(key) > 64 for key in value
        ):
            raise MagicRegistryError("Magic persistent mappings are invalid.")
        for item in value.values():
            _validate_primitive_tree(item, depth=depth + 1, items=items)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > 64:
            raise MagicRegistryError("Magic persistent sequences are too large.")
        for item in value:
            _validate_primitive_tree(item, depth=depth + 1, items=items)
        return
    raise MagicRegistryError("Magic persistent data must contain primitives only.")


def _default_class_keys() -> tuple[str, ...]:
    from systems.progression import CLASS_PROGRESSION

    return tuple(
        definition.key for definition in CLASS_PROGRESSION.definitions.values()
    )


def _default_resource_keys() -> tuple[str, ...]:
    from systems.progression import CLASS_PROGRESSION

    return ("hp", *(resource.key for resource in CLASS_PROGRESSION.resources.values()))


def _canonical_ability(value: Any) -> str:
    normalized = str(value).strip().casefold()
    for ability in ABILITY_NAMES:
        if normalized in {ability.casefold(), ABILITY_SHORT[ability].casefold()}:
            return ability
    raise MagicRegistryError("A magic saving throw references an unknown ability.")


def _validate_key(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _KEY_RE.fullmatch(value):
        raise MagicRegistryError(f"Invalid {label} key.")


def _key_result(value: Any, label: str) -> str:
    _validate_key(value, label)
    return value


def _target_filter_result(value: Any) -> str:
    """Validate one semantic filter that MAGIC-02 knows how to enforce."""
    _validate_key(value, "target filter")
    if value not in TARGET_FILTERS:
        raise MagicRegistryError("A magic action has an unknown target filter.")
    return value


def _validate_level(value: Any) -> None:
    _validate_positive_int(value, "class level", maximum=20)


def _validate_positive_int(value: Any, label: str, *, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        raise MagicRegistryError(f"{label} must be between 1 and {maximum}.")


def _validate_nonnegative_int(value: Any, label: str, *, maximum: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= maximum
    ):
        raise MagicRegistryError(f"{label} must be between 0 and {maximum}.")


def _render_player_help(definition: MagicDefinition) -> str:
    """Render only declared, player-safe metadata; hidden policies stay hidden."""
    parts = [definition.player_help.summary]  # validated by ``player_help_entry``
    if definition.kind == MagicKind.SPELL:
        level = (
            "Cantrip"
            if definition.spell_level == 0
            else f"Level {definition.spell_level} spell"
        )
        parts.append(f"{level}.")
    parts.append(f"Target: {definition.targeting.mode}. Range: {definition.range}.")
    if definition.uses_spell_slot:
        parts.append(
            f"Cost: one spell slot of level {definition.spell_level} or higher."
        )
    elif definition.cost is not None:
        parts.append(f"Cost: {definition.cost.amount} {definition.cost.resource_key}.")
    parts.append(
        f"Cast time: {definition.cast_time} action{'s' if definition.cast_time != 1 else ''}."
    )
    if definition.save is not None:
        parts.append(
            f"Save: {definition.save.ability} ({definition.save.on_success} on success)."
        )
    elif definition.damage is not None and definition.handler_key == "spell_attack":
        parts.append("Attack: spell attack.")
    if definition.duration is not None:
        parts.append(
            f"Duration: {definition.duration} pulse{'s' if definition.duration != 1 else ''}."
        )
    if definition.scaling.levels:
        parts.append("Scaling: improves at declared cast levels.")
    if definition.player_help.restrictions:
        parts.append(definition.player_help.restrictions)
    return "\n\n".join(parts)


# SRD content is registered only after its class feature, resource, effect, and
# casting adapters exist.  An empty registry fails closed rather than exposing
# invented or mechanically incomplete actions.
MAGIC_REGISTRY = build_magic_registry((), require_srd_references=True)
