"""MAGIC-02's shared, synchronous magical-action service.

The registry describes an action; this module decides whether an actor may use
it, selects its target, snapshots its potency, and resolves the small set of
handlers that are safe before INTERACT-06 supplies delayed actions.  Commands,
NPCs, and items call this service directly, never by constructing command text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from django.db import transaction
from systems.action_policy import ActionCategory
from systems.injury import (
    InjuryError,
    InjuryState,
    apply_damage,
    apply_healing,
    injury_record,
)
from systems.magic import (
    AccessMode,
    CastSnapshot,
    MagicDefinition,
    MagicKind,
    MagicRegistry,
    MagicRegistryError,
    RangeCategory,
    TargetingMode,
)
from systems.magic_resources import MagicResourceError, resource_current, spend_resource

MAGIC_ACTION_STATE_ATTRIBUTE = "magic_action_state"
MAGIC_ACTION_STATE_VERSION = 2
CONCENTRATION_ATTRIBUTE = "magic_concentration"
CONCENTRATION_VERSION = 1
_MAX_CONCENTRATION_EFFECTS = 16
_ENTITLEMENT_MODES = frozenset(
    {AccessMode.LEARNED, AccessMode.PREPARED, AccessMode.INNATE}
)
_SPELLBOOK_KEY = "spellbook"


class MagicActionError(ValueError):
    """Raised for a safe, player-presentable rejected magical action."""


@dataclass(frozen=True)
class MagicActionResult:
    """One accepted or rejected instant action, without hidden target details."""

    accepted: bool
    reason: str
    definition: MagicDefinition | None = None
    target: Any | None = None
    snapshot: CastSnapshot | None = None
    amount: int = 0


@dataclass(frozen=True)
class ConcentrationResult:
    """The auditable outcome of one concentration maintenance check."""

    attempted: bool
    maintained: bool
    dc: int | None = None
    check: Any | None = None
    reason: str = ""


def grant_action(actor: Any, action_key: str, mode: str) -> None:
    """Grant a validated learned, prepared, or innate action to an actor.

    ADV-03, NPC templates, and item adapters use this narrow data boundary.
    It never accepts executable content and cannot grant a disabled registry key.
    """
    if mode not in _ENTITLEMENT_MODES:
        raise MagicActionError("Magic action mode is invalid.")
    if mode == AccessMode.PREPARED:
        prepare_action(actor, action_key)
        return
    definition = _registry().definition_for(action_key, include_disabled=False)
    if mode not in definition.access_modes:
        raise MagicActionError("That action cannot be granted through this mode.")
    state = _action_state(actor)
    values = state[mode]
    if action_key not in values:
        values.append(action_key)
        values.sort()
        _write_action_state(actor, state)


def revoke_action(actor: Any, action_key: str, mode: str) -> None:
    """Remove one previously granted entitlement without touching resources."""
    if mode not in _ENTITLEMENT_MODES:
        raise MagicActionError("Magic action mode is invalid.")
    state = _action_state(actor)
    if mode == AccessMode.PREPARED:
        if action_key in state[mode]:
            state[mode].remove(action_key)
            _write_action_state(actor, state)
        return
    if action_key in state[mode]:
        state[mode].remove(action_key)
        _write_action_state(actor, state)


def grant_spellbook_entry(actor: Any, action_key: str) -> None:
    """Add one Wizard spellbook entry without also preparing that spell.

    Spellbook capacity and preparation capacity are separate SRD tables. This
    method is the durable ownership boundary used by future learning and loot
    adapters; it deliberately cannot create a prepared entitlement by itself.
    """
    definition = _registry().definition_for(action_key, include_disabled=False)
    access, level = _spell_access(actor)
    if (
        definition.kind != MagicKind.SPELL
        or AccessMode.PREPARED not in definition.access_modes
        or not _has_class_access(actor, definition)
        or access.spellbook_entries[level - 1] < 1
    ):
        raise MagicActionError("That spell cannot be added to your spellbook.")
    state = _action_state(actor)
    entries = state[_SPELLBOOK_KEY]
    if action_key in entries:
        return
    if len(entries) >= access.spellbook_entries[level - 1]:
        raise MagicActionError("Your spellbook cannot hold another spell.")
    entries.append(action_key)
    entries.sort()
    _write_action_state(actor, state)


def prepare_action(actor: Any, action_key: str) -> None:
    """Prepare one eligible spell through the class's durable access model."""
    definition = _registry().definition_for(action_key, include_disabled=False)
    access, level = _spell_access(actor)
    if (
        definition.kind != MagicKind.SPELL
        or AccessMode.PREPARED not in definition.access_modes
        or not _has_class_access(actor, definition)
    ):
        raise MagicActionError("That spell cannot be prepared.")
    state = _action_state(actor)
    if access.spellbook_entries[level - 1] and action_key not in state[_SPELLBOOK_KEY]:
        raise MagicActionError("That spell is not in your spellbook.")
    prepared = state[AccessMode.PREPARED]
    if action_key in prepared:
        return
    if len(prepared) >= access.spells_prepared[level - 1]:
        raise MagicActionError("You cannot prepare another spell right now.")
    prepared.append(action_key)
    prepared.sort()
    _write_action_state(actor, state)


def end_concentration(caster: Any) -> None:
    """End the caster's current concentration and its linked effect instances.

    The record is removed before effects so their post-removal listener cannot
    observe a half-finished concentration relationship.  Missing targets and
    already-removed instances are harmless: a reload or independent dispel
    must never leave concentration stuck on the caster.
    """
    state = _concentration_state(caster, required=False)
    caster.attributes.remove(CONCENTRATION_ATTRIBUTE)
    if state is None:
        return
    from evennia.objects.models import ObjectDB
    from systems.effects import RemovalReason

    for link in state["effects"]:
        owner = ObjectDB.objects.filter(id=link["owner_id"]).first()
        if owner is not None and hasattr(owner, "effects"):
            owner.effects.remove(
                link["instance_id"], reason=RemovalReason.SOURCE, quiet=True
            )


def maintain_concentration(
    caster: Any, damage: int, *, roller: Callable[[int], int] | None = None
) -> ConcentrationResult:
    """Resolve SRD concentration after one positive canonical damage event.

    The DC is 10 or half the damage, whichever is higher.  A failed saving
    throw ends the exact durable relationship through the same cleanup path as
    replacement and effect removal; it cannot leave linked effects orphaned.
    """
    if isinstance(damage, bool) or not isinstance(damage, int) or damage < 0:
        raise MagicActionError("Concentration damage is invalid.")
    state = _concentration_state(caster, required=False)
    if state is None or damage == 0:
        return ConcentrationResult(False, True, reason="not_concentrating")
    dc = max(10, damage // 2)
    try:
        from systems.checks import CheckError, resolve_saving_throw

        if roller is None:
            check = resolve_saving_throw(
                caster, "Constitution", dc, action_key="concentration"
            )
        else:
            check = resolve_saving_throw(
                caster,
                "Constitution",
                dc,
                action_key="concentration",
                roller=roller,
            )
    except CheckError:
        # A malformed character must not retain a condition whose required
        # maintenance cannot be resolved.
        end_concentration(caster)
        return ConcentrationResult(True, False, dc, reason="invalid_check")
    if check.success:
        return ConcentrationResult(True, True, dc, check, "maintained")
    end_concentration(caster)
    return ConcentrationResult(True, False, dc, check, "failed")


def available_actions(actor: Any, kind: str) -> tuple[MagicDefinition, ...]:
    """Return only actions this actor both has and may currently select."""
    if kind not in {MagicKind.SPELL, MagicKind.ABILITY}:
        raise MagicActionError("Magic action kind is invalid.")
    state = _action_state(actor)
    return tuple(
        definition
        for definition in _registry().definitions.values()
        if definition.enabled
        and definition.kind == kind
        and _has_access(actor, definition, state)
    )


def cast_action(
    caster: Any,
    action_name: str,
    *,
    target_name: str | None = None,
    registry: MagicRegistry | None = None,
) -> MagicActionResult:
    """Accept and resolve one instant action through the shared policy path.

    Definitions with a cast time above one action are deliberately rejected:
    resolving them synchronously would allow a future content entry to bypass
    INTERACT-06's cancellation and exact-once guarantees.
    """
    active_registry = registry or _registry()
    try:
        definition = active_registry.resolve(action_name)
    except MagicRegistryError as err:
        raise MagicActionError("You do not know that spell or ability.") from err
    state = _action_state(caster)
    if not _has_access(caster, definition, state):
        raise MagicActionError("You have not learned or prepared that action.")
    if definition.cast_time > 1:
        raise MagicActionError(
            "That action requires casting support that is not available."
        )
    category = _action_category(definition)
    decision = caster.actions.check(category)
    if not decision.allowed:
        raise MagicActionError(decision.message)
    target = _resolve_target(caster, definition, target_name)
    snapshot = _snapshot(caster, definition, target, active_registry)

    try:
        with transaction.atomic():
            _lock(caster)
            if target is not caster:
                _lock(target)
            # State that can change between parsing and execution is checked a
            # second time while the relevant character rows are locked.
            _revalidate(caster, definition, target)
            result = _execute(caster, definition, target, snapshot)
            if definition.cost is not None:
                spend_resource(
                    caster, definition.cost.resource_key, definition.cost.amount
                )
    except MagicResourceError as err:
        raise MagicActionError("You do not have enough magical resources.") from err
    except MagicActionError:
        raise
    except Exception as err:
        raise MagicActionError("Your magic fails to take hold.") from err
    if definition.kind == MagicKind.SPELL:
        try:
            from systems.magic_rest import interrupt_magic_rest

            interrupt_magic_rest(caster)
        except Exception:
            # A malformed rest record cannot undo an otherwise committed spell.
            pass
    return result


def _registry() -> MagicRegistry:
    """Read the current registry late so reloads and tests share one source."""
    from systems.magic import MAGIC_REGISTRY

    return MAGIC_REGISTRY


def _action_state(actor: Any) -> dict[str, list[str]]:
    """Read detached, primitive entitlement state and reject malformed records."""
    raw = actor.attributes.get(MAGIC_ACTION_STATE_ATTRIBUTE)
    if raw is None:
        return {mode: [] for mode in (*_ENTITLEMENT_MODES, _SPELLBOOK_KEY)}
    if not isinstance(raw, Mapping):
        raise MagicActionError("Your magic training record needs staff repair.")
    version = raw.get("version")
    expected = {"version", *_ENTITLEMENT_MODES}
    if version == MAGIC_ACTION_STATE_VERSION:
        expected.add(_SPELLBOOK_KEY)
    elif version != 1:
        raise MagicActionError("Your magic training record needs staff repair.")
    if set(raw) != expected:
        raise MagicActionError("Your magic training record needs staff repair.")
    state: dict[str, list[str]] = {}
    for mode in (*_ENTITLEMENT_MODES, _SPELLBOOK_KEY):
        values = raw.get(mode, [])
        if (
            isinstance(values, (str, bytes))
            or not isinstance(values, Sequence)
            or any(not isinstance(value, str) for value in values)
            or len(values) != len(set(values))
        ):
            raise MagicActionError("Your magic training record needs staff repair.")
        state[mode] = list(values)
    return state


def _write_action_state(actor: Any, state: Mapping[str, list[str]]) -> None:
    """Persist one detached entitlement record after complete validation."""
    payload = {"version": MAGIC_ACTION_STATE_VERSION}
    payload.update({mode: list(state[mode]) for mode in _ENTITLEMENT_MODES})
    payload[_SPELLBOOK_KEY] = list(state[_SPELLBOOK_KEY])
    actor.attributes.add(MAGIC_ACTION_STATE_ATTRIBUTE, payload)


def _has_access(
    actor: Any, definition: MagicDefinition, state: Mapping[str, list[str]]
) -> bool:
    """Combine class/level availability with the required durable entitlement."""
    if not _has_class_access(actor, definition):
        return False
    access, level = _spell_access(actor, required=False)
    if (
        definition.kind == MagicKind.SPELL
        and AccessMode.PREPARED in definition.access_modes
        and access is not None
        and access.spellbook_entries[level - 1]
        and definition.key not in state[_SPELLBOOK_KEY]
    ):
        return False
    return any(
        mode in definition.access_modes and definition.key in state[mode]
        for mode in _ENTITLEMENT_MODES
    )


def _has_class_access(actor: Any, definition: MagicDefinition) -> bool:
    """Check an action's source-controlled class and level gate."""
    class_key = actor.attributes.get("char_class")
    level = actor.attributes.get("level", 1)
    return (
        isinstance(class_key, str)
        and not isinstance(level, bool)
        and isinstance(level, int)
        and any(
            access.class_key == class_key and access.minimum_level <= level
            for access in definition.class_access
        )
    )


def _begin_concentration(
    caster: Any, source_key: str, effects: Sequence[Mapping[str, Any]]
) -> None:
    """Replace prior concentration only after new linked instances exist."""
    links = _validated_concentration_links(effects)
    end_concentration(caster)
    caster.attributes.add(
        CONCENTRATION_ATTRIBUTE,
        {
            "version": CONCENTRATION_VERSION,
            "source_key": source_key,
            "effects": links,
        },
    )


def _concentration_state(
    caster: Any, *, required: bool = True
) -> dict[str, Any] | None:
    """Return one detached concentration record, failing closed when requested."""
    raw = caster.attributes.get(CONCENTRATION_ATTRIBUTE)
    if raw is None:
        return None
    try:
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"version", "source_key", "effects"}
            or raw["version"] != CONCENTRATION_VERSION
            or not isinstance(raw["source_key"], str)
            or not raw["source_key"]
        ):
            raise ValueError
        return {
            "source_key": raw["source_key"],
            "effects": _validated_concentration_links(raw["effects"]),
        }
    except (KeyError, TypeError, ValueError):
        if required:
            raise MagicActionError("Your concentration record needs staff repair.")
        return None


def _validated_concentration_links(
    values: Sequence[Mapping[str, Any]],
) -> list[dict[str, int | str]]:
    """Validate primitive effect identities owned by one concentration record."""
    if (
        isinstance(values, (str, bytes))
        or not isinstance(values, Sequence)
        or not 1 <= len(values) <= _MAX_CONCENTRATION_EFFECTS
    ):
        raise ValueError
    links: list[dict[str, int | str]] = []
    seen: set[tuple[int, str]] = set()
    for value in values:
        if not isinstance(value, Mapping) or set(value) != {"owner_id", "instance_id"}:
            raise ValueError
        owner_id, instance_id = value["owner_id"], value["instance_id"]
        if (
            isinstance(owner_id, bool)
            or not isinstance(owner_id, int)
            or owner_id <= 0
            or not isinstance(instance_id, str)
            or not 1 <= len(instance_id) <= 64
            or (owner_id, instance_id) in seen
        ):
            raise ValueError
        seen.add((owner_id, instance_id))
        links.append({"owner_id": owner_id, "instance_id": instance_id})
    return links


def _on_concentration_effect_removed(effect: Any, _reason: Any) -> None:
    """Unlink an independently removed effect and end empty concentration."""
    caster = getattr(effect, "source", None)
    if caster is None or not hasattr(caster, "attributes"):
        return
    state = _concentration_state(caster, required=False)
    if state is None:
        return
    link = (getattr(effect.owner, "id", None), getattr(effect, "instance_id", None))
    remaining = [
        item
        for item in state["effects"]
        if (item["owner_id"], item["instance_id"]) != link
    ]
    if len(remaining) == len(state["effects"]):
        return
    if not remaining:
        caster.attributes.remove(CONCENTRATION_ATTRIBUTE)
        return
    caster.attributes.add(
        CONCENTRATION_ATTRIBUTE,
        {
            "version": CONCENTRATION_VERSION,
            "source_key": state["source_key"],
            "effects": remaining,
        },
    )


def _register_concentration_removal_listener() -> None:
    """Register this reload-safe effect adapter exactly once per process."""
    from systems.effects import register_removal_listener, removal_listener_registered

    listener_key = "magic.concentration"
    if not removal_listener_registered(listener_key):
        register_removal_listener(listener_key, _on_concentration_effect_removed)


def _spell_access(actor: Any, *, required: bool = True):
    """Return the actor's class spell table and validated effective level."""
    class_key = actor.attributes.get("char_class")
    level = actor.attributes.get("level", 1)
    if (
        not isinstance(class_key, str)
        or isinstance(level, bool)
        or not isinstance(level, int)
        or not 1 <= level <= 20
    ):
        if required:
            raise MagicActionError("Your magic training record needs staff repair.")
        return None, 0
    from systems.progression import CLASS_PROGRESSION

    access = CLASS_PROGRESSION.spell_access.get(f"{class_key.casefold()}.spell_access")
    if access is None and required:
        raise MagicActionError("Your class cannot prepare spells.")
    return access, level


def _action_category(definition: MagicDefinition) -> ActionCategory:
    """Translate the registry's reviewed category into WORLD-02's enum."""
    try:
        return ActionCategory(definition.action_category)
    except ValueError as err:
        raise MagicActionError("That action is not available right now.") from err


def _resolve_target(
    caster: Any, definition: MagicDefinition, target_name: str | None
) -> Any:
    """Resolve exactly one local target without broad or hidden-object search."""
    targeting = definition.targeting
    if targeting.mode == TargetingMode.SELF:
        if target_name:
            raise MagicActionError("That action can only target you.")
        return caster
    if not target_name:
        raise MagicActionError("You must name a target for that action.")
    if getattr(caster, "location", None) is None:
        raise MagicActionError("There is no valid target here.")
    target = caster.search(target_name, location=caster.location)
    if target is None:
        raise MagicActionError("There is no valid target here.")
    _validate_target(caster, definition, target)
    return target


def _validate_target(caster: Any, definition: MagicDefinition, target: Any) -> None:
    """Enforce MAGIC-01's single-target filters and ordinary access checks."""
    targeting = definition.targeting
    if target is caster and not targeting.include_caster:
        raise MagicActionError("You cannot target yourself with that action.")
    if definition.range not in {
        RangeCategory.TOUCH,
        RangeCategory.ROOM,
        RangeCategory.SIGHT,
    }:
        raise MagicActionError("That action has no usable range.")
    if target is not caster and target.location is not caster.location:
        raise MagicActionError("That target is out of range.")
    if not target.access(caster, "magic", default=True):
        raise MagicActionError("You cannot affect that target.")

    from typeclasses.characters import Character

    needs_character = targeting.mode in {
        TargetingMode.CREATURE,
        TargetingMode.ALLY,
        TargetingMode.HOSTILE,
    }
    if needs_character and not isinstance(target, Character):
        raise MagicActionError("That action needs a creature target.")
    if isinstance(target, Character):
        if (
            not targeting.allow_npcs
            and target.attributes.get("is_player_character") is False
        ):
            raise MagicActionError("That action cannot target that creature.")
        try:
            state = injury_record(target).state
        except InjuryError as err:
            raise MagicActionError("That target cannot be affected right now.") from err
        if not targeting.allow_dead_or_dying and state in {
            InjuryState.DYING,
            InjuryState.INCAPACITATED,
            InjuryState.DEAD,
        }:
            raise MagicActionError("That target cannot be affected right now.")
    if targeting.mode == TargetingMode.HOSTILE:
        from systems.attacks import can_attack

        if not can_attack(caster, target).allowed:
            raise MagicActionError("You cannot attack that target.")
    if targeting.mode == TargetingMode.ALLY and target is not caster:
        # There is no party/allegiance system yet.  Fail closed rather than
        # pretending room co-location is consent or alliance.
        raise MagicActionError("You cannot identify that creature as an ally.")


def _snapshot(
    caster: Any, definition: MagicDefinition, target: Any, registry: MagicRegistry
) -> CastSnapshot:
    """Freeze potency and the exact resource cost before effect resolution."""
    class_key = caster.attributes.get("char_class")
    ability = None
    if isinstance(class_key, str):
        from systems.progression import CLASS_PROGRESSION

        definition_class = CLASS_PROGRESSION.definitions.get(class_key)
        ability = definition_class.spellcasting_ability if definition_class else None
    modifier = caster.stats.ability_modifier(ability) if ability else 0
    proficiency = caster.stats.proficiency_bonus
    reservation = (
        {definition.cost.resource_key: definition.cost.amount}
        if definition.cost is not None
        else {}
    )
    if (
        definition.cost is not None
        and resource_current(caster, definition.cost.resource_key)
        < definition.cost.amount
    ):
        raise MagicActionError("You do not have enough magical resources.")
    return CastSnapshot(
        definition.key,
        registry.version,
        caster.id,
        (target.id,),
        caster.stats.level,
        8 + proficiency + modifier if definition.save is not None else None,
        proficiency + modifier if definition.handler_key == "spell_attack" else None,
        MappingProxyType(reservation),
    )


def _revalidate(caster: Any, definition: MagicDefinition, target: Any) -> None:
    """Repeat mutable actor, target, and resource checks just before commit."""
    decision = caster.actions.check(_action_category(definition))
    if not decision.allowed:
        raise MagicActionError(decision.message)
    if target is caster:
        return
    _validate_target(caster, definition, target)
    if (
        definition.cost is not None
        and resource_current(caster, definition.cost.resource_key)
        < definition.cost.amount
    ):
        raise MagicActionError("You do not have enough magical resources.")


def _execute(
    caster: Any, definition: MagicDefinition, target: Any, snapshot: CastSnapshot
) -> MagicActionResult:
    """Dispatch only code-owned handlers; definitions never provide callables."""
    if definition.handler_key == "utility":
        return MagicActionResult(True, "cast", definition, target, snapshot)
    if definition.handler_key == "healing":
        amount = _roll_dice(definition.healing)
        result = apply_healing(target, amount, emit_messages=False)
        if not result.accepted:
            raise MagicActionError("That target cannot be healed.")
        _message(
            caster,
            target,
            definition,
            f"You restore {result.resulting_hp - result.previous_hp} health to",
        )
        return MagicActionResult(True, "healed", definition, target, snapshot, amount)
    if definition.handler_key == "spell_attack":
        return _spell_attack(caster, definition, target, snapshot)
    if definition.handler_key == "saving_throw":
        if definition.damage is not None:
            return _saving_throw_damage(caster, definition, target, snapshot)
        return _apply_effects(caster, definition, target, snapshot)
    if definition.handler_key == "effect":
        return _apply_effects(caster, definition, target, snapshot)
    # Movement and area consequences require their owning MAGIC-04/INTERACT
    # adapters. Rejecting them is safer than a partial cast that spends a
    # resource without a declared consequence.
    raise MagicActionError("That action's effect is not available yet.")


def _apply_effects(
    caster: Any, definition: MagicDefinition, target: Any, snapshot: CastSnapshot
) -> MagicActionResult:
    """Apply declared persistent effects through RULES-03's owned API.

    The definition supplies only effect keys and a duration.  RULES-03 retains
    ownership of stacking, storage, removal, and effect-specific modifiers;
    the action key becomes the durable source for dispelling and diagnostics.
    """
    from systems.effects import ApplyOutcome, EffectError, SaveRule

    handler = getattr(target, "effects", None)
    if handler is None:
        raise MagicActionError("That target cannot carry magical effects.")
    save = None
    if definition.save is not None:
        if definition.save.on_success != "negate" or snapshot.save_dc is None:
            raise MagicActionError("That action's saving throw is not available.")
        save = SaveRule(definition.save.ability, snapshot.save_dc)

    applied = 0
    saved = 0
    concentration_links: list[dict[str, int | str]] = []
    for effect_key in definition.effect_keys:
        try:
            result = handler.add(
                effect_key,
                source=caster,
                source_key=definition.key,
                duration=definition.duration,
                save=save,
            )
        except EffectError as err:
            raise MagicActionError(
                "That action's effect is not available yet."
            ) from err
        if result.outcome is ApplyOutcome.REJECTED:
            raise MagicActionError("That magical effect is already active.")
        if result.outcome is ApplyOutcome.SAVED:
            saved += 1
        elif result.effect is not None:
            applied += 1

            if definition.concentration:
                concentration_links.append(
                    {
                        "owner_id": target.id,
                        "instance_id": result.effect.instance_id,
                    }
                )

    if concentration_links:
        _begin_concentration(caster, definition.key, concentration_links)
    if applied:
        _message(caster, target, definition, "You surround")
    return MagicActionResult(
        True,
        "effect_applied" if applied else "saved",
        definition,
        target,
        snapshot,
        applied + saved,
    )


def _spell_attack(
    caster: Any, definition: MagicDefinition, target: Any, snapshot: CastSnapshot
) -> MagicActionResult:
    """Resolve a snapshotted spell attack through canonical injury handling."""
    from systems.dice import roll

    roll_value = roll(20)
    if roll_value + (snapshot.attack_bonus or 0) < target.stats.armor_class:
        _message(caster, target, definition, "You miss")
        return MagicActionResult(True, "miss", definition, target, snapshot)
    amount = _roll_dice(definition.damage.dice)
    injury = apply_damage(
        target, amount, emit_messages=False, source=caster, source_kind="magic"
    )
    if not injury.accepted:
        raise MagicActionError("That target cannot be affected right now.")
    from systems.combat import start_fight

    start_fight(caster, target)
    _message(
        caster, target, definition, f"You hit {target.get_display_name(caster)} with"
    )
    return MagicActionResult(True, "hit", definition, target, snapshot, amount)


def _saving_throw_damage(
    caster: Any, definition: MagicDefinition, target: Any, snapshot: CastSnapshot
) -> MagicActionResult:
    """Resolve one hostile save-versus-damage action through canonical injury.

    This intentionally supports only one damage consequence.  A spell that
    combines damage with a condition needs a combined-resolution adapter so it
    cannot accidentally roll separate saves or apply a condition after a save
    that should have prevented it.
    """
    if definition.save is None or definition.damage is None or snapshot.save_dc is None:
        raise MagicActionError("That action's saving throw is not available.")
    from systems.dice import roll_check

    save = roll_check(
        target.stats.saving_throw_bonus(definition.save.ability), snapshot.save_dc
    )
    rolled_damage = _roll_dice(definition.damage.dice)
    if save.success and definition.save.on_success == "negate":
        amount = 0
    elif save.success and definition.save.on_success == "half":
        amount = rolled_damage // 2
    else:
        amount = rolled_damage
    injury = apply_damage(
        target, amount, emit_messages=False, source=caster, source_kind="magic"
    )
    if not injury.accepted:
        raise MagicActionError("That target cannot be affected right now.")
    if amount:
        from systems.combat import start_fight

        start_fight(caster, target)
        _message(
            caster,
            target,
            definition,
            f"You strike {target.get_display_name(caster)} with",
        )
    return MagicActionResult(
        True,
        "saved" if save.success else "hit",
        definition,
        target,
        snapshot,
        amount,
    )


def _roll_dice(dice: Any) -> int:
    """Roll validated MAGIC-01 dice exclusively through the shared dice API."""
    from systems.dice import roll

    return sum(roll(dice.sides) for _ in range(dice.count)) + dice.bonus


def _message(caster: Any, target: Any, definition: MagicDefinition, text: str) -> None:
    """Emit the caster-facing success message without exposing private values."""
    caster.msg(f"{text} |w{definition.display_name}|n.")


def _lock(obj: Any) -> None:
    """Serialize simultaneous resource spends for saved character-like objects."""
    identifier = getattr(obj, "pk", None)
    if (
        not isinstance(identifier, int)
        or isinstance(identifier, bool)
        or identifier <= 0
    ):
        raise MagicActionError("Magic requires a saved character.")
    obj.__class__.objects.select_for_update().get(pk=identifier)


_register_concentration_removal_listener()
