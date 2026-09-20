"""Atomic, provenance-preserving ITEM-07B starting-equipment grants."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from django.db import transaction
from evennia.objects.models import ObjectDB
from systems.currency import credit
from systems.encumbrance import character_load, spawn_with_capacity
from systems.equipment import EquipmentError
from systems.starting_packages import GrantPlan, module_item_prototypes

GRANT_ATTRIBUTE = "starting_package_grant"
ITEM_PROVENANCE_ATTRIBUTE = "starting_package_provenance"


class StartingGrantError(RuntimeError):
    """Raised when an ITEM-07B grant cannot be completed safely."""


@dataclass(frozen=True)
class StartingGrantResult:
    """The durable outcome of attempting one character's starting grant."""

    identity: str
    completed: bool
    repeated: bool = False


def grant_state(character: Any) -> dict[str, Any] | None:
    """Return a defensive copy of the saved grant receipt, if it is valid."""
    raw = character.attributes.get(GRANT_ATTRIBUTE)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise StartingGrantError("Starting equipment state needs staff repair.")
    return dict(raw)


def commit_starting_grant(character: Any, plan: GrantPlan) -> StartingGrantResult:
    """Spawn one immutable plan exactly once, or atomically leave no grant.

    The state receipt is created and completed in the same database transaction
    as every item, wallet, and equipment mutation.  Therefore a process crash
    or an exception leaves the character retryable without partial equipment.
    """
    identity = f"starting-package:{character.id}"
    with transaction.atomic():
        # Serialize reconnects and two final-menu submissions for this PC.
        ObjectDB.objects.select_for_update().get(pk=character.id)
        state = grant_state(character)
        if state is not None:
            if state.get("identity") != identity:
                raise StartingGrantError("Starting equipment state needs staff repair.")
            if state.get("status") == "completed":
                return StartingGrantResult(identity, True, repeated=True)
            raise StartingGrantError(
                "Starting equipment transaction needs staff repair."
            )

        _ensure_capacity(character, plan)
        character.attributes.add(GRANT_ATTRIBUTE, _reserved_state(identity, plan))
        spawned: list[Any] = []
        for entry_index, entry in enumerate(plan.items):
            prototype = _prototype(entry.prototype_key)
            for copy_index in range(entry.quantity):
                created = spawn_with_capacity(prototype, character)
                if len(created) != 1:
                    raise StartingGrantError(
                        "You cannot carry that starting equipment."
                    )
                item = created[0]
                item.attributes.add(
                    ITEM_PROVENANCE_ATTRIBUTE,
                    {
                        "grant_identity": identity,
                        "registry_version": plan.registry_version,
                        "fingerprint": plan.fingerprint,
                        "prototype_key": entry.prototype_key,
                        "source": entry.source,
                        "choice_path": entry.choice_path,
                        "entry_index": entry_index,
                        "copy_index": copy_index,
                    },
                )
                spawned.append(item)

        offset = 0
        for entry in plan.items:
            for location_index, location in enumerate(entry.equip):
                try:
                    character.equipment.equip(
                        spawned[offset + location_index], location
                    )
                except EquipmentError as err:
                    raise StartingGrantError(
                        "Starting equipment cannot be equipped."
                    ) from err
            offset += entry.quantity

        if plan.coins:
            result = credit(
                character,
                plan.coins,
                f"{identity}:coins",
                actor=character,
                source="starting-package",
                reason="character creation",
            )
            if not result.success:
                raise StartingGrantError("You cannot carry any more starting coins.")

        completed = _reserved_state(identity, plan)
        completed["status"] = "completed"
        completed["item_ids"] = [item.id for item in spawned]
        character.attributes.add(GRANT_ATTRIBUTE, completed)
    return StartingGrantResult(identity, True)


def _ensure_capacity(character: Any, plan: GrantPlan) -> None:
    """Reject a plan before spawning when it exceeds the live character limits."""
    load = character_load(character)
    if not plan.fits(
        load.weight_limit_units - load.weight_units, load.count_limit - load.count
    ):
        raise StartingGrantError("You cannot carry that starting equipment.")


def _prototype(key: str) -> dict[str, Any]:
    """Resolve the one source-controlled prototype validated by ITEM-07A."""
    matches = module_item_prototypes(key)
    if len(matches) != 1:
        raise StartingGrantError("Starting equipment source needs staff repair.")
    return dict(matches[0])


def _reserved_state(identity: str, plan: GrantPlan) -> dict[str, Any]:
    """Serialize the complete immutable plan using only Attribute-safe primitives."""
    return {
        "identity": identity,
        "status": "reserved",
        "registry_version": plan.registry_version,
        "fingerprint": plan.fingerprint,
        "class_key": plan.class_key,
        "background_key": plan.background_key,
        "selections": {key: list(value) for key, value in plan.selections.items()},
        "items": [asdict(item) for item in plan.items],
        "coins": plan.coins,
    }
