"""Validated, immutable starting-equipment registry (ITEM-07A).

Staff author packages in ``world.starting_package_data`` and item prototypes in
``world.prototypes``.  This module turns that hand-written content into one
frozen registry, the only equipment source chargen presents and ITEM-07B
grants.  Bad content never breaks server boot: every problem becomes a staff
diagnostic, and an incomplete registry fails closed so no character can
receive a partial or unvalidated package.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import combinations
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from evennia.utils.logger import log_info, log_trace, log_warn
from systems.encumbrance import (
    default_carried_item_limit,
    pounds_to_units,
    units_to_pounds,
)
from systems.equipment import WEAR_LOCATIONS

SRD_PREFIX = "SRD 5.2.1 "
ITEM_TYPECLASS = "typeclasses.objects.Item"
SOURCES: tuple[str, str] = ("class", "background")
MAX_ITEM_QUANTITY = 100
MAX_PACKAGE_COINS = 100_000
MAX_ITEM_ENTRIES = 40
MAX_CHOICES = 8
MAX_OPTIONS = 8
# A top-level choice plus one nested level, e.g. a tool kind inside option A.
MAX_CHOICE_DEPTH = 2
MAX_SELECTION_COMBINATIONS = 4096
MAX_TEXT_LENGTH = 500
# 4d6-drop-lowest can total 3, below point buy and the standard array.
LOWEST_ROLLED_SCORE = 3

_KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_PROTOTYPE_KEY_RE = re.compile(r"^[a-z0-9_.:-]{1,80}$")
_PROTFUNC_RE = re.compile(r"\$\w+\(")
_PACKAGE_FIELDS = frozenset(
    {"srd_reference", "adaptation", "items", "coins", "choices"}
)
_ITEM_FIELDS = frozenset({"prototype", "quantity", "equip"})
_CHOICE_FIELDS = frozenset({"key", "count", "options"})
_OPTION_FIELDS = frozenset({"key", "items", "coins", "choices"})
_REQUIRED_PROTOTYPE_FIELDS = ("weight", "value", "type", "no_drop", "account_bound")
# Authored identity that no builder field exposes but prototypes must carry.
_EXTRA_PROTOTYPE_ATTRIBUTES = frozenset({"srd_reference", "door_state"})


class StartingPackageError(ValueError):
    """Raised for invalid package content or an impossible grant request."""


@dataclass(frozen=True)
class PackageItem:
    """One ordered prototype grant and the instances to equip from it."""

    prototype_key: str
    quantity: int
    equip: tuple[str, ...]


@dataclass(frozen=True)
class PackageOption:
    """One labelled branch of a choice, e.g. SRD option ``a``."""

    key: str
    items: tuple[PackageItem, ...]
    coins: int
    choices: tuple["PackageChoice", ...]


@dataclass(frozen=True)
class PackageChoice:
    """Pick exactly ``count`` distinct options from an ordered set."""

    key: str
    count: int
    options: tuple[PackageOption, ...]


@dataclass(frozen=True)
class StartingPackage:
    """One class or background package as authored, after validation."""

    source: str
    owner: str
    srd_reference: str
    adaptation: str
    items: tuple[PackageItem, ...]
    coins: int
    choices: tuple[PackageChoice, ...]


@dataclass(frozen=True)
class ItemFacts:
    """The validated prototype properties a grant plan relies on."""

    prototype_key: str
    name: str
    item_type: str
    weight_units: int
    value: int
    wear_locations: tuple[str, ...]
    armor_category: str | None
    no_drop: bool
    account_bound: bool
    srd_reference: str


@dataclass(frozen=True)
class PlannedItem:
    """One entry of a grant plan with its package provenance."""

    prototype_key: str
    quantity: int
    equip: tuple[str, ...]
    source: str
    choice_path: str


@dataclass(frozen=True)
class GrantPlan:
    """Deterministic projection of one class/background/selection triple."""

    registry_version: int
    fingerprint: str
    class_key: str
    background_key: str
    selections: Mapping[str, tuple[str, ...]]
    items: tuple[PlannedItem, ...]
    coins: int
    weight_units: int
    item_count: int

    def fits(self, capacity_units: int, item_limit: int) -> bool:
        """Return whether a character with these limits can carry the plan."""
        return self.weight_units <= capacity_units and self.item_count <= item_limit


@dataclass(frozen=True)
class StartingPackageRegistry:
    """Versioned packages, the item facts they use, and staff diagnostics."""

    version: int
    fingerprint: str
    classes: Mapping[str, StartingPackage]
    backgrounds: Mapping[str, StartingPackage]
    items: Mapping[str, ItemFacts]
    diagnostics: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """Only a registry with no diagnostics may be presented or granted."""
        return not self.diagnostics

    def package(self, source: str, owner: str) -> StartingPackage | None:
        """Return the validated package for ``class``/``background`` ``owner``."""
        if source not in SOURCES:
            raise StartingPackageError(f"Unknown package source: {source}.")
        packages = self.classes if source == "class" else self.backgrounds
        return packages.get(owner)


# ---------------------------------------------------------------------------
# Release limits
# ---------------------------------------------------------------------------


def minimum_release_capacity_units() -> int:
    """Carry capacity of the weakest character chargen can produce.

    Background bonuses only raise scores, so the floor is the lowest score any
    generation method allows at the smallest carry multiplier of any species.
    """
    from world.chargen_data import (
        CARRY_CAPACITY_MULTIPLIER,
        POINT_BUY_MIN,
        SPECIES,
        STANDARD_ARRAY,
    )

    strength = min(LOWEST_ROLLED_SCORE, POINT_BUY_MIN, min(STANDARD_ARRAY))
    sizes = {
        size.strip()
        for data in SPECIES.values()
        for size in str(data["size"]).split(" or ")
    }
    multiplier = min(CARRY_CAPACITY_MULTIPLIER[size] for size in sizes)
    # Mirrors CharacterStats.carry_capacity, which truncates to whole pounds.
    return pounds_to_units(int(strength * multiplier))


# ---------------------------------------------------------------------------
# Package parsing
# ---------------------------------------------------------------------------


def _require_mapping(raw: Any, label: str, allowed: frozenset[str]) -> Mapping:
    if not isinstance(raw, Mapping):
        raise StartingPackageError(f"{label} must be a mapping.")
    unknown = sorted(str(name) for name in set(raw) - allowed)
    if unknown:
        raise StartingPackageError(
            f"{label} has unknown field(s): {', '.join(unknown)}."
        )
    return raw


def _require_sequence(raw: Any, label: str, maximum: int) -> Sequence:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise StartingPackageError(f"{label} must be a list.")
    if len(raw) > maximum:
        raise StartingPackageError(f"{label} has more than {maximum} entries.")
    return raw


def _bounded_int(raw: Any, label: str, minimum: int, maximum: int) -> int:
    if (
        isinstance(raw, bool)
        or not isinstance(raw, int)
        or not minimum <= raw <= maximum
    ):
        raise StartingPackageError(
            f"{label} must be a whole number from {minimum} to {maximum}."
        )
    return raw


def _stable_key(raw: Any, label: str) -> str:
    if not isinstance(raw, str) or not _KEY_RE.match(raw):
        raise StartingPackageError(
            f"{label} must be 1-40 lowercase letters, digits, or underscores."
        )
    return raw


def _parse_items(raw: Any, label: str) -> tuple[PackageItem, ...]:
    entries = _require_sequence(raw, f"{label} items", MAX_ITEM_ENTRIES)
    items: list[PackageItem] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        entry_label = f"{label} item {index}"
        entry = _require_mapping(entry, entry_label, _ITEM_FIELDS)
        key = entry.get("prototype")
        if not isinstance(key, str) or not _PROTOTYPE_KEY_RE.match(key):
            raise StartingPackageError(
                f"{entry_label} needs a lowercase prototype key."
            )
        if key in seen:
            # Two entries for one prototype would give it two equip plans.
            raise StartingPackageError(
                f"{label} lists prototype {key} twice; use quantity instead."
            )
        seen.add(key)
        quantity = _bounded_int(
            entry.get("quantity", 1), f"{entry_label} quantity", 1, MAX_ITEM_QUANTITY
        )
        equip_raw = _require_sequence(
            entry.get("equip", ()), f"{entry_label} equip", len(WEAR_LOCATIONS)
        )
        equip = tuple(
            str(location).strip().lower() if isinstance(location, str) else ""
            for location in equip_raw
        )
        if any(location not in WEAR_LOCATIONS for location in equip):
            raise StartingPackageError(f"{entry_label} names an unknown wear location.")
        if len(set(equip)) != len(equip):
            raise StartingPackageError(f"{entry_label} repeats a wear location.")
        if len(equip) > quantity:
            raise StartingPackageError(
                f"{entry_label} equips more copies than its quantity."
            )
        items.append(PackageItem(key, quantity, equip))
    return tuple(items)


def _parse_coins(raw: Any, label: str) -> int:
    return _bounded_int(raw, f"{label} coins", 0, MAX_PACKAGE_COINS)


def _parse_choices(raw: Any, label: str, depth: int) -> tuple[PackageChoice, ...]:
    entries = _require_sequence(raw, f"{label} choices", MAX_CHOICES)
    if entries and depth > MAX_CHOICE_DEPTH:
        raise StartingPackageError(f"{label} nests choices too deeply.")
    choices: list[PackageChoice] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        entry = _require_mapping(entry, f"{label} choice {index}", _CHOICE_FIELDS)
        key = _stable_key(entry.get("key"), f"{label} choice {index} key")
        if key in seen:
            raise StartingPackageError(f"{label} repeats choice {key}.")
        seen.add(key)
        choice_label = f"{label} choice {key}"
        option_entries = _require_sequence(
            entry.get("options"), f"{choice_label} options", MAX_OPTIONS
        )
        if len(option_entries) < 2:
            raise StartingPackageError(f"{choice_label} needs at least two options.")
        options: list[PackageOption] = []
        option_keys: set[str] = set()
        for option_index, option_raw in enumerate(option_entries, start=1):
            option_raw = _require_mapping(
                option_raw, f"{choice_label} option {option_index}", _OPTION_FIELDS
            )
            option_key = _stable_key(
                option_raw.get("key"), f"{choice_label} option {option_index} key"
            )
            if option_key in option_keys:
                raise StartingPackageError(
                    f"{choice_label} repeats option {option_key}."
                )
            option_keys.add(option_key)
            option_label = f"{choice_label} option {option_key}"
            option = PackageOption(
                option_key,
                _parse_items(option_raw.get("items", ()), option_label),
                _parse_coins(option_raw.get("coins", 0), option_label),
                _parse_choices(option_raw.get("choices", ()), option_label, depth + 1),
            )
            if not (option.items or option.coins or option.choices):
                raise StartingPackageError(f"{option_label} grants nothing.")
            options.append(option)
        count = _bounded_int(
            entry.get("count", 1), f"{choice_label} count", 1, len(options)
        )
        choices.append(PackageChoice(key, count, tuple(options)))
    return tuple(choices)


def _parse_text(raw: Any, label: str, *, required_prefix: str = "") -> str:
    if not isinstance(raw, str) or len(raw) > MAX_TEXT_LENGTH:
        raise StartingPackageError(
            f"{label} must be text of at most {MAX_TEXT_LENGTH} characters."
        )
    if required_prefix and not raw.startswith(required_prefix):
        raise StartingPackageError(f"{label} must start with '{required_prefix}'.")
    return raw


def parse_package(source: str, owner: str, raw: Any) -> StartingPackage:
    """Validate one authored package's structure without resolving prototypes."""
    raw = _require_mapping(raw, "package", _PACKAGE_FIELDS)
    package = StartingPackage(
        source,
        owner,
        _parse_text(
            raw.get("srd_reference"), "srd_reference", required_prefix=SRD_PREFIX
        ),
        _parse_text(raw.get("adaptation", ""), "adaptation"),
        _parse_items(raw.get("items", ()), "package"),
        _parse_coins(raw.get("coins", 0), "package"),
        _parse_choices(raw.get("choices", ()), "package", 1),
    )
    # Enumerating proves every choice count is satisfiable and bounded.
    package_selections(package)
    return package


# ---------------------------------------------------------------------------
# Prototype validation
# ---------------------------------------------------------------------------


def module_item_prototypes(key: str) -> list[dict[str, Any]]:
    """Return every module or database prototype whose key is exactly ``key``."""
    from evennia.prototypes.prototypes import search_prototype

    return [
        proto for proto in search_prototype(key) if proto.get("prototype_key") == key
    ]


def _is_dynamic(value: Any) -> bool:
    """Callables and protfuncs would make weights and names non-deterministic."""
    if callable(value):
        return True
    if isinstance(value, str):
        return bool(_PROTFUNC_RE.search(value))
    if isinstance(value, Mapping):
        return any(_is_dynamic(item) for pair in value.items() for item in pair)
    if isinstance(value, (list, tuple, set)):
        return any(_is_dynamic(item) for item in value)
    return False


def _flatten(prototype: Mapping[str, Any]) -> dict[str, Any]:
    """Merge the parent chain into one flat mapping of effective values."""
    from evennia.prototypes.prototypes import _PROTOTYPE_RESERVED_KEYS
    from evennia.prototypes.spawner import flatten_prototype

    try:
        flattened = flatten_prototype(dict(prototype), validate=True)
    except (RuntimeError, RuntimeWarning, KeyError, ValueError) as err:
        reason = str(err).strip().splitlines()[0][:200] if str(err).strip() else ""
        raise StartingPackageError(
            f"cannot be spawned ({reason or 'invalid prototype'})."
        )
    flat = {
        name: value
        for name, value in flattened.items()
        if name not in _PROTOTYPE_RESERVED_KEYS or name in {"key", "typeclass"}
    }
    for attr in flattened.get("attrs", ()):
        if len(attr) > 2 and attr[2]:
            raise StartingPackageError(f"uses categorized attribute {attr[0]}.")
        flat[attr[0]] = attr[1]
    return flat


def _builder_text(validate: Callable[[str], Any], value: Any) -> str:
    """Render a stored value as the text a builder would type for it."""
    from world.build_schema import (
        as_equipment_capabilities,
        as_extra_descriptions,
        as_item_resource,
        as_magic_item,
    )

    if validate in (
        as_equipment_capabilities,
        as_extra_descriptions,
        as_item_resource,
        as_magic_item,
    ):
        return json.dumps(value)
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(part) for part in value)
    return str(value)


def _check_builder_fields(flat: Mapping[str, Any], item_type: str) -> None:
    """Hold prototypes to the same field rules as the item builder.

    The stored value must survive the builder validator unchanged, so a spawned
    copy is exactly what a builder could have authored in game.
    """
    from world.build_schema import schema_for_prototype

    schema = (
        schema_for_prototype({"typeclass": ITEM_TYPECLASS, "type": item_type}) or {}
    )
    allowed = set(_EXTRA_PROTOTYPE_ATTRIBUTES) | {"key", "typeclass", "type"}
    for name, field in schema.items():
        if field.kind != "attr":
            continue
        target = field.target or name
        allowed.add(target)
        if target not in flat:
            continue
        value = flat[target]
        try:
            normalized = field.validate(_builder_text(field.validate, value))
        except (ValueError, TypeError) as err:
            raise StartingPackageError(f"field {target}: {err}")
        comparable = list(value) if isinstance(value, tuple) else value
        if normalized != comparable or isinstance(value, bool) != isinstance(
            normalized, bool
        ):
            raise StartingPackageError(
                f"field {target} is not in builder form; use {normalized!r}."
            )
    unknown = sorted(name for name in flat if name not in allowed)
    if unknown:
        raise StartingPackageError(
            f"has field(s) the {item_type} builder does not define: {', '.join(unknown)}."
        )


def resolve_item_facts(key: str, matches: Sequence[Mapping[str, Any]]) -> ItemFacts:
    """Validate that ``key`` names one spawnable, source-controlled Item prototype."""
    from world.build_schema import ITEM_TYPES, as_text

    module_matches = [p for p in matches if "module" in (p.get("prototype_tags") or ())]
    if not matches:
        raise StartingPackageError("does not exist in world/prototypes.py.")
    if not module_matches:
        raise StartingPackageError(
            "exists only in the database; starting items must be module prototypes."
        )
    if len(matches) > 1:
        raise StartingPackageError(
            "is shadowed by a database prototype with the same key; delete that copy."
        )
    flat = _flatten(module_matches[0])
    if _is_dynamic(flat):
        raise StartingPackageError("uses a callable or $protfunc value.")
    if flat.get("typeclass") != ITEM_TYPECLASS:
        raise StartingPackageError(f"must use typeclass {ITEM_TYPECLASS}.")
    missing = [name for name in _REQUIRED_PROTOTYPE_FIELDS if name not in flat]
    if missing:
        raise StartingPackageError(f"is missing {', '.join(missing)}.")
    item_type = flat["type"]
    if item_type not in ITEM_TYPES:
        raise StartingPackageError(f"has unknown type {item_type!r}.")
    if item_type == "money":
        raise StartingPackageError(
            "is money; grant coins with the package coins field."
        )
    try:
        name = as_text(flat.get("key") if isinstance(flat.get("key"), str) else "")
    except ValueError:
        raise StartingPackageError("needs a key (the item's name).")
    reference = flat.get("srd_reference")
    if not isinstance(reference, str) or not reference.startswith(SRD_PREFIX):
        raise StartingPackageError(f"needs srd_reference starting with '{SRD_PREFIX}'.")
    for flag in ("no_drop", "account_bound"):
        if not isinstance(flat[flag], bool):
            raise StartingPackageError(f"needs {flag} set to True or False.")
    _check_builder_fields(flat, item_type)
    if flat.get("door_state") is not None:
        from systems.doors import DoorError, configured_door_record

        try:
            configured_door_record(flat["door_state"], "door", "on")
        except DoorError as err:
            raise StartingPackageError(f"door_state: {err}")
    armor = flat.get("subtype") if item_type == "armor" else None
    return ItemFacts(
        key,
        name,
        item_type,
        pounds_to_units(flat["weight"]),
        flat["value"],
        tuple(flat.get("wear_locations") or ()),
        armor,
        flat["no_drop"],
        flat["account_bound"],
        reference,
    )


# ---------------------------------------------------------------------------
# Selection and projection
# ---------------------------------------------------------------------------


def _choice_selections(
    choices: Sequence[PackageChoice], prefix: str
) -> list[dict[str, tuple[str, ...]]]:
    results: list[dict[str, tuple[str, ...]]] = [{}]
    for choice in choices:
        path = f"{prefix}.{choice.key}"
        branch: list[dict[str, tuple[str, ...]]] = []
        for picked in combinations(choice.options, choice.count):
            nested: list[dict[str, tuple[str, ...]]] = [{}]
            for option in picked:
                sub = _choice_selections(option.choices, f"{path}.{option.key}")
                nested = [{**left, **right} for left in nested for right in sub]
            keys = tuple(option.key for option in picked)
            branch.extend({path: keys, **rest} for rest in nested)
        results = [{**left, **right} for left in results for right in branch]
        if len(results) > MAX_SELECTION_COMBINATIONS:
            raise StartingPackageError("has too many choice combinations.")
    return results


def package_selections(
    package: StartingPackage,
) -> tuple[dict[str, tuple[str, ...]], ...]:
    """Return every legal selection mapping for one package, in stable order."""
    return tuple(_choice_selections(package.choices, package.source))


def _project(
    package: StartingPackage,
    selections: Mapping[str, Any],
    normalized: dict[str, tuple[str, ...]],
) -> tuple[list[PlannedItem], int]:
    source = f"{package.source}:{package.owner}"
    items = [
        PlannedItem(item.prototype_key, item.quantity, item.equip, source, "")
        for item in package.items
    ]
    coins = package.coins

    def resolve(choices: Sequence[PackageChoice], prefix: str) -> None:
        nonlocal coins
        for choice in choices:
            path = f"{prefix}.{choice.key}"
            picked = selections.get(path)
            by_key = {option.key: option for option in choice.options}
            if (
                not isinstance(picked, (list, tuple))
                or len(picked) != choice.count
                or len(set(picked)) != len(picked)
                or any(not isinstance(key, str) or key not in by_key for key in picked)
            ):
                raise StartingPackageError(
                    f"Choose {choice.count} of {', '.join(by_key)} for {path}."
                )
            # Declaration order, not selection order, keeps plans deterministic.
            chosen = [option for option in choice.options if option.key in picked]
            normalized[path] = tuple(option.key for option in chosen)
            for option in chosen:
                option_path = f"{path}.{option.key}"
                items.extend(
                    PlannedItem(
                        item.prototype_key,
                        item.quantity,
                        item.equip,
                        source,
                        option_path,
                    )
                    for item in option.items
                )
                coins += option.coins
                resolve(option.choices, option_path)

    resolve(package.choices, package.source)
    return items, coins


def _trained_armor(class_key: str) -> set[str]:
    from systems.progression import CLASSES

    return {
        str(value).strip().lower().removesuffix("s")
        for value in CLASSES.get(class_key, {}).get("armor_training", [])
    }


def plan_problems(
    class_key: str, items: Sequence[PlannedItem], facts: Mapping[str, ItemFacts]
) -> list[str]:
    """Return equip-planning conflicts in one combined class/background plan."""
    problems: list[str] = []
    occupied: dict[str, str] = {}
    trained = _trained_armor(class_key)
    for item in items:
        fact = facts[item.prototype_key]
        for location in item.equip:
            if location not in fact.wear_locations:
                problems.append(f"{item.prototype_key} cannot be worn at {location}.")
            if location in occupied:
                problems.append(
                    f"{item.prototype_key} and {occupied[location]} both equip {location}."
                )
            occupied[location] = item.prototype_key
        if item.equip and fact.armor_category and fact.armor_category not in trained:
            problems.append(
                f"{item.prototype_key} is {fact.armor_category} armor that "
                f"{class_key} is not trained to wear."
            )
    return problems


def build_plan(
    registry: StartingPackageRegistry,
    class_key: str,
    background_key: str,
    selections: Mapping[str, Any],
) -> GrantPlan:
    """Project a plan from validated packages without requiring completeness."""
    class_package = registry.classes.get(class_key)
    background_package = registry.backgrounds.get(background_key)
    if class_package is None:
        raise StartingPackageError(f"{class_key} has no valid starting package.")
    if background_package is None:
        raise StartingPackageError(f"{background_key} has no valid starting package.")
    if not isinstance(selections, Mapping):
        raise StartingPackageError("Starting package selections must be a mapping.")
    normalized: dict[str, tuple[str, ...]] = {}
    class_items, class_coins = _project(class_package, selections, normalized)
    background_items, background_coins = _project(
        background_package, selections, normalized
    )
    unknown = sorted(str(path) for path in set(selections) - set(normalized))
    if unknown:
        raise StartingPackageError(
            f"Unknown starting package choice: {', '.join(unknown)}."
        )
    items = (*class_items, *background_items)
    if any(item.prototype_key not in registry.items for item in items):
        raise StartingPackageError("A starting package item is not validated.")
    problems = plan_problems(class_key, items, registry.items)
    if problems:
        raise StartingPackageError(problems[0])
    return GrantPlan(
        registry.version,
        registry.fingerprint,
        class_key,
        background_key,
        MappingProxyType(dict(sorted(normalized.items()))),
        items,
        class_coins + background_coins,
        sum(registry.items[i.prototype_key].weight_units * i.quantity for i in items),
        sum(item.quantity for item in items),
    )


def plan_starting_grant(
    class_key: str,
    background_key: str,
    selections: Mapping[str, Any],
    *,
    registry: StartingPackageRegistry | None = None,
) -> GrantPlan:
    """Return the immutable grant plan ITEM-07B commits for a new character.

    Fails closed while any package or prototype is invalid, so a character can
    never receive equipment from a partially validated registry.
    """
    registry = registry or starting_package_registry()
    if not registry.complete:
        raise StartingPackageError(
            "Starting equipment is not available until staff finish its packages."
        )
    return build_plan(registry, class_key, background_key, selections)


# ---------------------------------------------------------------------------
# Registry construction
# ---------------------------------------------------------------------------


def _package_keys(package: StartingPackage) -> list[str]:
    keys = [item.prototype_key for item in package.items]

    def visit(choices: Sequence[PackageChoice]) -> None:
        for choice in choices:
            for option in choice.options:
                keys.extend(item.prototype_key for item in option.items)
                visit(option.choices)

    visit(package.choices)
    return keys


def _parse_source(
    source: str,
    raw_packages: Any,
    selectable: Sequence[str],
    diagnostics: list[str],
) -> dict[str, StartingPackage]:
    if not isinstance(raw_packages, Mapping):
        diagnostics.append(f"{source.capitalize()} packages must be a mapping.")
        return {}
    for owner in raw_packages:
        if owner not in selectable:
            diagnostics.append(
                f"{source} {owner} is not selectable in this release; remove its package."
            )
    parsed: dict[str, StartingPackage] = {}
    for owner in selectable:
        if owner not in raw_packages:
            diagnostics.append(f"{source} {owner} has no starting package.")
            continue
        try:
            parsed[owner] = parse_package(source, owner, raw_packages[owner])
        except StartingPackageError as err:
            diagnostics.append(f"{source} {owner}: {err}")
    return parsed


def _check_combinations(
    registry: StartingPackageRegistry,
    minimum_capacity_units: int,
    item_limit: int,
    maximum_coins: int,
    diagnostics: list[str],
) -> None:
    """Prove every class/background pair has conflict-free, carryable choices."""
    for class_key, class_package in registry.classes.items():
        for background_key, background_package in registry.backgrounds.items():
            pair = f"{class_key} + {background_key}"
            keys = _package_keys(class_package) + _package_keys(background_package)
            if any(key not in registry.items for key in keys):
                continue
            fits = False
            reported = False
            for class_selection in package_selections(class_package):
                for background_selection in package_selections(background_package):
                    selection = {**class_selection, **background_selection}
                    try:
                        plan = build_plan(
                            registry, class_key, background_key, selection
                        )
                    except StartingPackageError as err:
                        if not reported:
                            diagnostics.append(
                                f"{pair} with {_selection_text(selection)}: {err}"
                            )
                            reported = True
                        continue
                    if plan.coins > maximum_coins:
                        if not reported:
                            diagnostics.append(
                                f"{pair} grants more coins than a wallet holds."
                            )
                            reported = True
                        continue
                    fits = fits or plan.fits(minimum_capacity_units, item_limit)
            if not fits and not reported:
                diagnostics.append(
                    f"{pair}: no choice combination fits the weakest eligible character "
                    f"({units_to_pounds(minimum_capacity_units):g} lb, {item_limit} items)."
                )


def _selection_text(selection: Mapping[str, Sequence[str]]) -> str:
    return (
        ", ".join(f"{path}={'+'.join(keys)}" for path, keys in selection.items())
        or "no choices"
    )


def build_starting_package_registry(
    class_packages: Any,
    background_packages: Any,
    *,
    version: Any = 1,
    selectable_classes: Sequence[str] | None = None,
    selectable_backgrounds: Sequence[str] | None = None,
    prototype_lookup: Callable[
        [str], Sequence[Mapping[str, Any]]
    ] = module_item_prototypes,
    minimum_capacity_units: int | None = None,
    item_limit: int | None = None,
) -> StartingPackageRegistry:
    """Validate authored packages and prototypes into one immutable registry."""
    from systems.currency import maximum_balance
    from systems.progression import SELECTABLE_CLASS_NAMES
    from world.chargen_data import BACKGROUNDS

    diagnostics: list[str] = []
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        diagnostics.append("STARTING_PACKAGE_VERSION must be a positive whole number.")
        version = 0
    classes = _parse_source(
        "class",
        class_packages,
        tuple(
            SELECTABLE_CLASS_NAMES if selectable_classes is None else selectable_classes
        ),
        diagnostics,
    )
    backgrounds = _parse_source(
        "background",
        background_packages,
        tuple(
            BACKGROUNDS if selectable_backgrounds is None else selectable_backgrounds
        ),
        diagnostics,
    )
    items: dict[str, ItemFacts] = {}
    referenced = dict.fromkeys(
        key
        for package in (*classes.values(), *backgrounds.values())
        for key in _package_keys(package)
    )
    for key in referenced:
        try:
            items[key] = resolve_item_facts(key, prototype_lookup(key))
        except StartingPackageError as err:
            diagnostics.append(f"prototype {key} {err}")
    payload = {
        "version": version,
        "classes": {key: asdict(value) for key, value in sorted(classes.items())},
        "backgrounds": {
            key: asdict(value) for key, value in sorted(backgrounds.items())
        },
        "items": {key: asdict(value) for key, value in sorted(items.items())},
    }
    fingerprint = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    registry = StartingPackageRegistry(
        version,
        fingerprint,
        MappingProxyType(classes),
        MappingProxyType(backgrounds),
        MappingProxyType(items),
        (),
    )
    _check_combinations(
        registry,
        (
            minimum_release_capacity_units()
            if minimum_capacity_units is None
            else minimum_capacity_units
        ),
        default_carried_item_limit() if item_limit is None else item_limit,
        maximum_balance(),
        diagnostics,
    )
    return StartingPackageRegistry(
        version,
        fingerprint,
        registry.classes,
        registry.backgrounds,
        registry.items,
        tuple(diagnostics),
    )


_REGISTRY: StartingPackageRegistry | None = None


def starting_package_registry() -> StartingPackageRegistry:
    """Return the process-wide registry, built on first use after boot/reload."""
    global _REGISTRY
    if _REGISTRY is None:
        try:
            from world import starting_package_data as data

            _REGISTRY = build_starting_package_registry(
                data.CLASS_PACKAGES,
                data.BACKGROUND_PACKAGES,
                version=data.STARTING_PACKAGE_VERSION,
            )
        except Exception:
            # Chargen and boot must survive broken content; staff get the trace.
            log_trace("Starting package validation failed unexpectedly.")
            _REGISTRY = StartingPackageRegistry(
                0,
                "",
                MappingProxyType({}),
                MappingProxyType({}),
                MappingProxyType({}),
                ("Validation failed unexpectedly; see the server log.",),
            )
    return _REGISTRY


def reset_starting_package_registry() -> None:
    """Forget the cached registry so the next read revalidates content."""
    global _REGISTRY
    _REGISTRY = None


def log_starting_package_status() -> StartingPackageRegistry:
    """Report registry health in the server log at startup."""
    registry = starting_package_registry()
    if registry.complete:
        log_info(
            f"Starting packages v{registry.version} validated "
            f"({registry.fingerprint[:12]})."
        )
    else:
        log_warn(
            f"Starting packages are incomplete with {len(registry.diagnostics)} "
            "problem(s); chargen offers no starting equipment. See startpackages."
        )
    return registry


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def _grant_text(
    items: Sequence[PackageItem], coins: int, facts: Mapping[str, ItemFacts]
) -> list[str]:
    from systems.currency import format_coins

    parts = []
    for item in items:
        fact = facts.get(item.prototype_key)
        name = fact.name if fact else item.prototype_key
        parts.append(name if item.quantity == 1 else f"{name} (x{item.quantity})")
    if coins:
        parts.append(format_coins(coins))
    return parts


def _choice_text(choice: PackageChoice, facts: Mapping[str, ItemFacts]) -> str:
    labels = [option.key.upper() for option in choice.options]
    listed = (
        ", ".join(labels[:-1]) + f", or {labels[-1]}"
        if len(labels) > 2
        else " or ".join(labels)
    )
    lead = "Choose" if choice.count == 1 else f"Choose {choice.count} of"
    branches = []
    for option in choice.options:
        parts = _grant_text(option.items, option.coins, facts)
        parts.extend(_choice_text(nested, facts) for nested in option.choices)
        branches.append(f"({option.key.upper()}) {', '.join(parts)}")
    return f"{lead} {listed}: " + "; or ".join(branches)


def describe_package(
    source: str,
    owner: str,
    *,
    registry: StartingPackageRegistry | None = None,
    require_complete: bool = True,
) -> str:
    """Generate player-facing package text from the registry, never from prose.

    Chargen requires a complete registry so it never advertises equipment that
    cannot be granted; staff previews pass ``require_complete=False``.
    """
    registry = registry or starting_package_registry()
    package = registry.package(source, owner)
    if package is None or (require_complete and not registry.complete):
        return "Not yet available."
    parts = _grant_text(package.items, package.coins, registry.items)
    parts.extend(_choice_text(choice, registry.items) for choice in package.choices)
    return "; ".join(parts) or "None."
