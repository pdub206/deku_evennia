"""ITEM-04B potion, scroll, wand, and staff activation.

One immutable registry names the released MAGIC-01 actions an item may release,
the command that releases them, who may do it, and what the activation costs.
Target resolution, attack rolls, saves, damage, healing, RULES-03 effects,
concentration, and their messages stay with MAGIC-01/MAGIC-04; this module only
decides that an activation is legal, reserves the portion, item, or ITEM-05B
charge, and spends it once the magic adapter reports a committed result.

Authored data is primitive. Every activation carries one durable identity so a
retry after reload cannot repeat an effect, a spend, a deletion, or output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from uuid import uuid4

from django.db import transaction
from evennia.utils import logger
from systems.action_policy import ActionCategory
from systems.item_resources import (
    ItemResourceError,
    commit_resource_use,
    item_mutation,
    resource_profile,
    resource_state,
)
from systems.magic import MagicRegistry, MagicRegistryError, TargetingMode
from systems.magic_actions import commit_item_action, knows_action, prepare_item_action

MAGIC_ITEM_ATTRIBUTE = "magic_item"
MAGIC_ITEM_STATE_ATTRIBUTE = "magic_item_state"
MAGIC_ITEM_VERSION = 1
MAGIC_ITEM_REGISTRY_VERSION = 1
MAX_MAGIC_ITEM_USES = 20

# Access rules are fixed choices, never an ad-hoc skill check or attunement.
ACCESS_ANYONE = "anyone"
ACCESS_KNOWN_CLASS_ACTION = "known-class-action"
ACCESS_RULES = frozenset({ACCESS_ANYONE, ACCESS_KNOWN_CLASS_ACTION})

CONSUMPTION_PORTION = "portion"
CONSUMPTION_ITEM = "item"
CONSUMPTION_CHARGE = "charge"

# The activation command and consumption mode are a property of the item kind,
# not of authored content, so a potion can never be recited and a wand can never
# delete itself.  ``MagicItemDefinition`` restates both and is validated here.
_TYPE_POLICY: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "potion": ("quaff", CONSUMPTION_PORTION),
        "scroll": ("recite", CONSUMPTION_ITEM),
        "wand": ("use", CONSUMPTION_CHARGE),
        "staff": ("use", CONSUMPTION_CHARGE),
    }
)
MAGIC_ITEM_TYPES: tuple[str, ...] = tuple(sorted(_TYPE_POLICY))
MAGIC_ITEM_COMMANDS: tuple[str, ...] = ("quaff", "recite", "use")

_ACTIVATION_MESSAGES: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "quaff": ("You quaff {name}.", "quaffs"),
        "recite": ("You recite {name}.", "recites"),
        "use": ("You invoke {name}.", "invokes"),
    }
)

_KEY_PATTERN = re.compile(r"[a-z][a-z0-9_]*(\.[a-z0-9_]+)+")
_IDENTITY_PATTERN = re.compile(r"[a-zA-Z0-9_.:/-]{1,128}")


class MagicItemError(ValueError):
    """A safe, player-presentable denial or a quarantined authored item."""


@dataclass(frozen=True)
class MagicItemDefinition:
    """One released magic-item activation, expressed only as reviewed data."""

    key: str
    display_name: str
    item_type: str
    action_key: str
    targeting_mode: str
    command: str
    access_rule: str
    consumption: str
    help_summary: str
    srd_reference: str
    enabled: bool = True


@dataclass(frozen=True)
class MagicItemRegistry:
    """Read-only, deterministic lookup shared by items, builders, and help."""

    version: int
    definitions: Mapping[str, MagicItemDefinition]

    def definition_for(
        self, key: Any, *, include_disabled: bool = True
    ) -> MagicItemDefinition:
        """Return one exact stable key, keeping disabled entries identifiable."""
        definition = self.definitions.get(key) if isinstance(key, str) else None
        if definition is None or (not include_disabled and not definition.enabled):
            raise MagicItemError("Unknown or unavailable magic item.")
        return definition

    def is_available(self, key: Any) -> bool:
        """Return whether a stable key may currently be authored on an item."""
        return (
            isinstance(key, str)
            and key in self.definitions
            and self.definitions[key].enabled
        )

    def for_item_type(self, item_type: Any) -> tuple[MagicItemDefinition, ...]:
        """Return the released definitions a builder may set on this item kind."""
        return tuple(
            definition
            for definition in self.definitions.values()
            if definition.enabled and definition.item_type == item_type
        )


@dataclass(frozen=True)
class ActivationResult:
    """The auditable outcome of exactly one activation attempt."""

    definition: MagicItemDefinition
    accepted: bool
    replayed: bool = False
    remaining: int = 0
    consumed: bool = False
    magic: Any = None


def build_magic_item_registry(
    definitions: Iterable[MagicItemDefinition],
    *,
    version: int = MAGIC_ITEM_REGISTRY_VERSION,
    magic: MagicRegistry | None = None,
) -> MagicItemRegistry:
    """Validate every released definition against MAGIC-01 before it is usable.

    A definition that names an unknown, disabled, slow, or post-alpha-targeted
    action is rejected outright rather than failing later in a player's hands.
    """
    registry = magic if magic is not None else _magic_registry()
    validated: dict[str, MagicItemDefinition] = {}
    for definition in definitions:
        if not isinstance(definition, MagicItemDefinition):
            raise MagicItemError("A magic item definition is malformed.")
        _validate_definition(definition, registry)
        if definition.key in validated:
            raise MagicItemError("A magic item definition key is duplicated.")
        validated[definition.key] = definition
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise MagicItemError("Magic item registry version is invalid.")
    return MagicItemRegistry(version, MappingProxyType(dict(validated)))


def _validate_definition(
    definition: MagicItemDefinition, registry: MagicRegistry
) -> None:
    """Reject anything that would let content escape the reviewed contract."""
    if not isinstance(definition.key, str) or not _KEY_PATTERN.fullmatch(
        definition.key
    ):
        raise MagicItemError("A magic item definition key is invalid.")
    if (
        not isinstance(definition.display_name, str)
        or not 1 <= len(definition.display_name) <= 64
    ):
        raise MagicItemError("A magic item definition name is invalid.")
    if definition.item_type not in _TYPE_POLICY:
        raise MagicItemError("A magic item definition has an unsupported type.")
    command, consumption = _TYPE_POLICY[definition.item_type]
    if (definition.command, definition.consumption) != (command, consumption):
        raise MagicItemError("A magic item definition has an invalid activation.")
    if definition.access_rule not in ACCESS_RULES:
        raise MagicItemError("A magic item definition has an invalid access rule.")
    if not isinstance(definition.enabled, bool):
        raise MagicItemError("A magic item definition has an invalid enabled flag.")
    try:
        action = registry.definition_for(definition.action_key, include_disabled=False)
    except MagicRegistryError as err:
        raise MagicItemError(
            "A magic item definition names an unreleased magic action."
        ) from err
    if action.targeting.mode != definition.targeting_mode:
        raise MagicItemError("A magic item definition has a stale targeting mode.")
    if action.cast_time > 1:
        raise MagicItemError("A magic item cannot release a slow casting action.")
    if (
        not isinstance(definition.help_summary, str)
        or not 1 <= len(definition.help_summary) <= 400
    ):
        raise MagicItemError("A magic item definition needs a player help summary.")
    if not isinstance(
        definition.srd_reference, str
    ) or not definition.srd_reference.startswith("SRD 5.2.1 "):
        raise MagicItemError("A magic item definition needs an SRD 5.2.1 reference.")


# ---------------------------------------------------------------------------
# Authored profile and durable activation state
# ---------------------------------------------------------------------------


def validate_magic_item_profile(
    raw: Any, item_type: Any = None, *, registry: MagicItemRegistry | None = None
) -> dict[str, Any]:
    """Validate a builder/prototype profile without accepting executable data."""
    active = registry if registry is not None else _registry()
    if not isinstance(raw, Mapping) or set(raw) != {"version", "definition", "uses"}:
        raise MagicItemError("Magic item profile has an invalid shape.")
    if raw["version"] != MAGIC_ITEM_VERSION or isinstance(raw["version"], bool):
        raise MagicItemError("Magic item profile version is invalid.")
    definition = active.definition_for(raw["definition"], include_disabled=False)
    if item_type is not None and definition.item_type != item_type:
        raise MagicItemError("That item type cannot use this magic item definition.")
    uses = _integer(raw["uses"], high=MAX_MAGIC_ITEM_USES)
    if definition.consumption == CONSUMPTION_PORTION and not 1 <= uses:
        raise MagicItemError("A potion needs at least one portion.")
    if definition.consumption == CONSUMPTION_ITEM and uses != 1:
        raise MagicItemError("A scroll is consumed by exactly one use.")
    if definition.consumption == CONSUMPTION_CHARGE and uses != 0:
        # Wands and staves keep their units in ITEM-05B's charge resource so
        # only one service can ever spend them.
        raise MagicItemError("A charged device stores its uses in its resource.")
    return {
        "version": MAGIC_ITEM_VERSION,
        "definition": definition.key,
        "uses": uses,
    }


def magic_item_profile(item: Any) -> dict[str, Any]:
    """Read a strict authored profile; a malformed item fails on its own."""
    return validate_magic_item_profile(
        item.attributes.get(MAGIC_ITEM_ATTRIBUTE), item.attributes.get("type")
    )


def magic_item_definition(item: Any) -> MagicItemDefinition:
    """Return the released definition this live item activates."""
    return _registry().definition_for(
        magic_item_profile(item)["definition"], include_disabled=False
    )


def magic_item_state(item: Any) -> dict[str, Any]:
    """Project durable portions and permanent receipts, clamped to the profile."""
    profile = magic_item_profile(item)
    raw = item.attributes.get(MAGIC_ITEM_STATE_ATTRIBUTE)
    if raw is None:
        return {
            "version": MAGIC_ITEM_VERSION,
            "uses": profile["uses"],
            "receipts": {},
        }
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"version", "uses", "receipts"}
        or raw["version"] != MAGIC_ITEM_VERSION
        or isinstance(raw["version"], bool)
        or not isinstance(raw["receipts"], Mapping)
    ):
        raise MagicItemError("Magic item state needs builder repair.")
    state = {
        "version": MAGIC_ITEM_VERSION,
        "uses": min(_integer(raw["uses"], high=MAX_MAGIC_ITEM_USES), profile["uses"]),
        "receipts": {},
    }
    for identity, receipt in raw["receipts"].items():
        _identity(identity)
        if (
            not isinstance(receipt, Mapping)
            or set(receipt) != {"operation", "uses"}
            or receipt["operation"] != "activate"
        ):
            raise MagicItemError("Magic item receipts need builder repair.")
        _integer(receipt["uses"], low=1, high=MAX_MAGIC_ITEM_USES)
        state["receipts"][identity] = dict(receipt)
    return state


def set_magic_item_profile(item: Any, raw: Any) -> None:
    """Author a live profile without manufacturing or replaying spent uses."""
    profile = validate_magic_item_profile(raw, item.attributes.get("type"))
    try:
        with item_mutation(item):
            state = (
                magic_item_state(item)
                if item.attributes.has(MAGIC_ITEM_ATTRIBUTE)
                else None
            )
            item.attributes.add(MAGIC_ITEM_ATTRIBUTE, profile)
            if state is not None:
                state["uses"] = min(state["uses"], profile["uses"])
                _store(item, state)
            else:
                _store(item, magic_item_state(item))
    except ItemResourceError as err:
        raise MagicItemError(str(err)) from err


def remaining_uses(item: Any) -> int:
    """Return the portions, item uses, or charges this item can still release."""
    definition = magic_item_definition(item)
    if definition.consumption == CONSUMPTION_CHARGE:
        return _charge_units(item)
    return magic_item_state(item)["uses"]


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------


def activate_magic_item(
    user: Any,
    item: Any,
    *,
    command: str,
    target_name: str | None = None,
    identity: str | None = None,
) -> ActivationResult:
    """Release one activation, spending only when the magic adapter commits.

    The portion, item, or charge is reserved first, the magic runs inside that
    reservation, and every denial, exception, or rejected result rolls the whole
    transaction back so nothing is spent, deleted, or announced.
    """
    if command not in _ACTIVATION_MESSAGES:
        raise MagicItemError("That is not a magic item activation.")
    identity = _identity(uuid4().hex if identity is None else identity)
    definition = magic_item_definition(item)
    if definition.command != command:
        raise MagicItemError(f"You cannot {command} that.")
    _require_usable(user, item)
    _require_access(user, definition)
    action, target, snapshot = prepare_item_action(
        user, definition.action_key, target_name
    )
    if definition.consumption == CONSUMPTION_CHARGE:
        return _spend_charge(user, item, definition, action, target, snapshot, identity)
    return _spend_use(user, item, definition, action, target, snapshot, identity)


def _spend_charge(
    user: Any,
    item: Any,
    definition: MagicItemDefinition,
    action: Any,
    target: Any,
    snapshot: Any,
    identity: str,
) -> ActivationResult:
    """Reserve one ITEM-05B charge and release it only on a committed cast."""
    resolved: dict[str, Any] = {}

    def adapter() -> bool:
        """Revalidate under lock, then hand the magic to MAGIC-02 to commit."""
        _require_usable(user, item)
        _require_access(user, definition)
        if resource_profile(item)["kind"] != "charges":
            raise MagicItemError("That device stores no charges.")
        outcome = commit_item_action(user, action, target, snapshot)
        if not outcome.accepted:
            return False
        resolved["magic"] = outcome
        _announce(user, item, definition)
        return True

    try:
        committed = commit_resource_use(
            item, 1, identity, adapter, participants=_participants(user, target)
        )
    except ItemResourceError as err:
        raise MagicItemError(_charge_denial(str(err))) from err
    if not committed:
        raise MagicItemError("The magic fails to take hold.")
    return ActivationResult(
        definition,
        True,
        replayed="magic" not in resolved,
        remaining=_charge_units(item),
        magic=resolved.get("magic"),
    )


def _spend_use(
    user: Any,
    item: Any,
    definition: MagicItemDefinition,
    action: Any,
    target: Any,
    snapshot: Any,
    identity: str,
) -> ActivationResult:
    """Reserve one portion or the scroll itself, deleting it with its last use."""
    try:
        with item_mutation(item, *_participants(user, target)):
            state = magic_item_state(item)
            receipt = state["receipts"].get(identity)
            if receipt is not None:
                # A retry of a committed activation repeats no effect or output.
                return ActivationResult(
                    definition, True, replayed=True, remaining=state["uses"]
                )
            _require_usable(user, item)
            _require_access(user, definition)
            if state["uses"] < 1:
                raise MagicItemError("There is nothing left to release.")
            outcome = commit_item_action(user, action, target, snapshot)
            if not outcome.accepted:
                raise MagicItemError("The magic fails to take hold.")
            state["uses"] -= 1
            state["receipts"][identity] = {"operation": "activate", "uses": 1}
            _announce(user, item, definition)
            consumed = not state["uses"]
            if consumed:
                # Deleting inside the reservation keeps the last spend and the
                # item's disappearance from ever diverging.
                item.delete()
            else:
                _store(item, state)
            return ActivationResult(
                definition,
                True,
                remaining=state["uses"],
                consumed=consumed,
                magic=outcome,
            )
    except ItemResourceError as err:
        raise MagicItemError(str(err)) from err


def _require_usable(user: Any, item: Any) -> None:
    """Require a directly carried, accessible item and a noncombat free hand."""
    decision = user.actions.check(ActionCategory.MANIPULATE)
    if not decision.allowed:
        raise MagicItemError(decision.message)
    from systems.combat import get_encounter_id

    if get_encounter_id(user) is not None:
        # Combat-time item use arrives with INTERACT-06's delayed actions.
        raise MagicItemError("You cannot use magic items while fighting.")
    if (
        item.location is not user
        or user.location is None
        or not item.access(user, "view", default=True)
        or not item.access(user, "interact", default=True)
    ):
        raise MagicItemError("You must directly carry an accessible magic item.")


def _require_access(user: Any, definition: MagicItemDefinition) -> None:
    """Apply the definition's fixed access rule, never an ad-hoc skill check."""
    if definition.access_rule == ACCESS_ANYONE:
        return
    if not knows_action(user, definition.action_key):
        raise MagicItemError("You do not know the magic bound into that item.")


def _participants(user: Any, target: Any) -> tuple[Any, ...]:
    """Lock and refresh every character whose state the activation may change."""
    return (user,) if target is user or target is None else (user, target)


def _charge_units(item: Any) -> int:
    """Read remaining ITEM-05B charges, treating a broken resource as empty."""
    try:
        return resource_state(item)["current"]
    except ItemResourceError:
        return 0


def _charge_denial(message: str) -> str:
    """Translate ITEM-05B's generic unit denial into device wording."""
    return (
        "That device has no charges left."
        if message == "That item has insufficient units."
        else message
    )


def _announce(user: Any, item: Any, definition: MagicItemDefinition) -> None:
    """Queue exactly one delivery attempt per committed activation."""
    room = user.location
    text, public_verb = _ACTIVATION_MESSAGES[definition.command]
    name = item.get_display_name(user)
    public_name = item.get_display_name(room) if room is not None else name
    actor_name = user.get_display_name(room) if room is not None else user.key

    def deliver() -> None:
        """Isolate a failed delivery so a committed activation still stands."""
        for callback in (
            lambda: user.msg(text.format(name=name)),
            lambda: room.msg_contents(
                f"{actor_name} {public_verb} {public_name}.", exclude=user
            ),
        ):
            try:
                callback()
            except Exception:
                logger.log_err("Magic item message delivery failed after commit.")

    transaction.on_commit(deliver)


def _store(item: Any, state: Mapping[str, Any]) -> None:
    """Persist detached state instead of mutating a cached Saver container."""
    item.attributes.add(MAGIC_ITEM_STATE_ATTRIBUTE, dict(state))


def _integer(value: Any, low: int = 0, high: int = MAX_MAGIC_ITEM_USES) -> int:
    """Reject booleans and fractional, negative, or oversized authored counts."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not low <= value <= high
    ):
        raise MagicItemError("Magic item values are out of bounds.")
    return value


def _identity(value: Any) -> str:
    """Accept only a bounded opaque activation identity, never content."""
    if not isinstance(value, str) or not _IDENTITY_PATTERN.fullmatch(value):
        raise MagicItemError("Invalid magic item activation identity.")
    return value


def _magic_registry() -> MagicRegistry:
    """Read MAGIC-01 late so reloads and tests share exactly one source."""
    from systems.magic import MAGIC_REGISTRY

    return MAGIC_REGISTRY


def _registry() -> MagicItemRegistry:
    """Read this module's registry late for the same reason."""
    return MAGIC_ITEM_REGISTRY


def magic_item_help_entries() -> tuple[Mapping[str, Any], ...]:
    """Generate the released catalogue from the same metadata that validates it."""
    return tuple(
        MappingProxyType(
            {
                "key": definition.key,
                "display_name": definition.display_name,
                "item_type": definition.item_type,
                "command": definition.command,
                "access_rule": definition.access_rule,
                "consumption": definition.consumption,
                "summary": definition.help_summary,
                "srd_reference": definition.srd_reference,
            }
        )
        for definition in _registry().definitions.values()
        if definition.enabled
    )


# Only activations whose magic already has a complete execution path belong in
# this catalogue.  Targeting modes here mirror MAGIC-01 and are revalidated.
_RELEASED_MAGIC_ITEMS = (
    MagicItemDefinition(
        key="potion.healing",
        display_name="Potion of Healing",
        item_type="potion",
        action_key="cleric.cure_wounds",
        targeting_mode=TargetingMode.CREATURE,
        command="quaff",
        access_rule=ACCESS_ANYONE,
        consumption=CONSUMPTION_PORTION,
        help_summary=(
            "Quaff a portion to restore health to yourself, or to a creature you "
            "name. Anyone may drink it."
        ),
        srd_reference="SRD 5.2.1 Magic Items: Potion of Healing",
    ),
    MagicItemDefinition(
        key="potion.blurring",
        display_name="Potion of Blurring",
        item_type="potion",
        action_key="wizard.blur",
        targeting_mode=TargetingMode.SELF,
        command="quaff",
        access_rule=ACCESS_ANYONE,
        consumption=CONSUMPTION_PORTION,
        help_summary=(
            "Quaff a portion to blur your own outline while you concentrate. "
            "Alpha adaptation: the SRD has no Potion of Blurring; this bottles "
            "the released Blur spell, so concentration still applies."
        ),
        srd_reference="SRD 5.2.1 Spell Descriptions: Blur",
    ),
    MagicItemDefinition(
        key="scroll.magic_missile",
        display_name="Spell Scroll of Magic Missile",
        item_type="scroll",
        action_key="wizard.magic_missile",
        targeting_mode=TargetingMode.HOSTILE,
        command="recite",
        access_rule=ACCESS_KNOWN_CLASS_ACTION,
        consumption=CONSUMPTION_ITEM,
        help_summary=(
            "Recite the scroll at a foe to release Magic Missile. Only someone "
            "who already knows the spell can read it, and the scroll is used up."
        ),
        srd_reference="SRD 5.2.1 Magic Items: Spell Scroll",
    ),
    MagicItemDefinition(
        key="wand.magic_missiles",
        display_name="Wand of Magic Missiles",
        item_type="wand",
        action_key="wizard.magic_missile",
        targeting_mode=TargetingMode.HOSTILE,
        command="use",
        access_rule=ACCESS_ANYONE,
        consumption=CONSUMPTION_CHARGE,
        help_summary=(
            "Use the wand on a foe to spend one charge on Magic Missile. Anyone "
            "may point it, and it stops working at zero charges."
        ),
        srd_reference="SRD 5.2.1 Magic Items: Wand of Magic Missiles",
    ),
    MagicItemDefinition(
        key="staff.healing",
        display_name="Staff of Healing",
        item_type="staff",
        action_key="cleric.healing_word",
        targeting_mode=TargetingMode.CREATURE,
        command="use",
        access_rule=ACCESS_KNOWN_CLASS_ACTION,
        consumption=CONSUMPTION_CHARGE,
        help_summary=(
            "Use the staff on yourself or a creature you name to spend one "
            "charge on Healing Word. Only someone who knows the spell can "
            "invoke it."
        ),
        srd_reference="SRD 5.2.1 Magic Items: Staff of Healing",
    ),
)

MAGIC_ITEM_REGISTRY = build_magic_item_registry(_RELEASED_MAGIC_ITEMS)
