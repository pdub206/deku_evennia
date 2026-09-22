"""AREA-03A's durable, single-lane area reset controller registry.

The registry deliberately contains scheduling state only.  Door, mobile, and
object directives are added by AREA-03B through AREA-03D; keeping their work
behind ``run_area_directives`` ensures this package never creates a second
reset timer or mutates live area state merely while registering controllers.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from evennia.server.models import ServerConfig
from evennia.utils import logger
from systems.areas import (
    AREA_TAG_CATEGORY,
    EXIT_KEY_CATEGORY,
    ROOM_KEY_CATEGORY,
    AreaLoadPlan,
    AreaPlanError,
    area_of,
    compile_area_load_plan,
)
from systems.pulses import PulseEvent

AREA_RESET_CONFIG_KEY = "area_reset_controllers"
AREA_RESET_VERSION = 1
CONTROLLER_VERSION = 1
POLICIES = frozenset({"boot", "if_empty", "always", "never"})
MAX_FAILURE_LENGTH = 240


class AreaResetError(ValueError):
    """An area reset controller or explicit request is invalid."""


@dataclass(frozen=True)
class AreaResetResult:
    """A bounded outcome for one area considered on one reset-lane token."""

    area_key: str
    status: str
    reason: str = ""


@dataclass(frozen=True)
class AreaControllerRecovery:
    """Controller-only recovery performed without accepting changed source."""

    recovered: tuple[str, ...]
    pending: tuple[str, ...]
    retired: tuple[str, ...]


def process_area_reset_pulse(event: PulseEvent) -> tuple[AreaResetResult, ...]:
    """Register source areas then dispatch due controllers in stable key order.

    An invalid source plan is deliberately a global dependency failure: no
    controller is allowed to reset against an unvalidated world graph.
    """
    from systems.area_startup import (
        AreaStartupError,
        configured_enabled_area_manifests,
    )

    try:
        plan = compile_area_load_plan(configured_enabled_area_manifests())
    except (AreaPlanError, AreaStartupError) as err:
        logger.log_err(f"AREA-03A reset plan is invalid: {_summary(err)}")
        return ()
    recovery = recover_area_controllers(plan)
    if recovery.pending:
        logger.log_warn(
            "AREA-06B reset skipped changed source pending apply: "
            + ", ".join(recovery.pending)
        )
    allowed = tuple(
        area_key
        for area_key in plan.registry.manifests
        if area_key not in recovery.pending
    )
    return process_area_reset_plan(event, plan, reconcile=False, area_keys=allowed)


def process_area_reset_plan(
    event: PulseEvent,
    plan: AreaLoadPlan,
    *,
    directive_runner: Callable[[str, str, AreaLoadPlan], None] | None = None,
    reconcile: bool = True,
    area_keys: tuple[str, ...] | None = None,
) -> tuple[AreaResetResult, ...]:
    """Run one already-validated plan, chiefly for AREA-03 integrations/tests."""
    if not isinstance(event.sequence, int) or isinstance(event.sequence, bool):
        raise AreaResetError("Reset lane token must be an integer.")
    controllers = _reconcile_controllers(plan) if reconcile else controller_snapshot()
    runner = directive_runner or run_area_directives
    results: list[AreaResetResult] = []
    for area_key in sorted(plan.registry.manifests):
        if area_keys is not None and area_key not in area_keys:
            continue
        controller = controllers[area_key]
        try:
            result = _process_controller(
                area_key, controller, event.sequence, plan, runner
            )
        except Exception as err:
            _record_failure(area_key, event.sequence, err)
            logger.log_trace(f"AREA-03A reset failed for area '{area_key}'.")
            result = AreaResetResult(area_key, "failed", "reset_failure")
        results.append(result)
    return tuple(results)


def request_manual_reset(
    area_key: str,
    plan: AreaLoadPlan,
    *,
    directive_runner: Callable[[str, str, AreaLoadPlan], None] | None = None,
) -> AreaResetResult:
    """Run a ``never`` area only through an explicit Admin-facing request.

    The caller is responsible for the command's Admin lock.  This narrow
    service intentionally accepts a compiled plan, so a request cannot race
    raw manifest data or make a partially invalid world runnable.
    """
    if area_key not in plan.registry.manifests:
        raise AreaResetError("Unknown enabled area.")
    controllers = _reconcile_controllers(plan)
    controller = controllers[area_key]
    token = controller["last_attempted_token"] + 1
    runner = directive_runner or run_area_directives
    return _claim_and_run(area_key, token, plan, runner, manual=True)


def run_area_directives(area_key: str, reset_token: str, plan: AreaLoadPlan) -> None:
    """Run the reset work owned by completed AREA-03 packages.

    Each directive isolates its own malformed live record.  A bad door must
    never prevent later mobile/object packages, or a different area, from
    receiving its reset attempt.
    """
    reconcile_door_resets(area_key, reset_token, plan)
    reconcile_mobile_resets(area_key, reset_token, plan)
    reconcile_object_resets(area_key, reset_token, plan)


def reconcile_object_resets(
    area_key: str, reset_token: str, plan: AreaLoadPlan
) -> None:
    """Feed object-root deficits to AREA-03D's containment-safe service."""
    from evennia.utils import logger
    from evennia.utils.search import search_tag
    from systems.object_spawning import reconcile_object_placement

    for owner, placement_key, placement in plan.object_placements:
        if owner != area_key:
            continue
        try:
            room = _live_room(area_key, placement["room_key"], search_tag)
            result = reconcile_object_placement(
                _object_reset_identity(area_key, reset_token),
                area_key,
                placement_key,
                placement,
                {placement["room_key"]: room},
            )
            if result.status == "failed":
                logger.log_err(
                    f"AREA-03D object reset failed for {area_key}:{placement_key}: "
                    f"{result.reason}"
                )
        except Exception:
            logger.log_trace(
                f"AREA-03D object reset failed for {area_key}:{placement_key}."
            )


def _object_reset_identity(area_key: str, reset_token: str) -> str:
    """Give object claims a bounded opaque reset identity."""
    digest = hashlib.sha256(
        f"object:{area_key}:{reset_token}".encode("utf-8")
    ).hexdigest()
    return f"area_object_reset_{digest[:40]}"


def reconcile_mobile_resets(
    area_key: str, reset_token: str, plan: AreaLoadPlan
) -> None:
    """Reconcile each validated placement through MOB-05 without touching survivors.

    MOB-05 owns all fresh population counting, ceiling enforcement, source
    identity, room admission, and NPC construction.  AREA-03C supplies only a
    reset-lane identity and the stable live room map needed for its placement
    service; a failure is intentionally contained to one placement.
    """
    from evennia.utils import logger
    from evennia.utils.search import search_tag
    from systems.mob_spawning import reconcile_mobile_placement

    rooms: dict[str, Any] = {}
    placements = [
        placement for owner, placement in plan.mobile_placements if owner == area_key
    ]
    for placement in placements:
        room_key = placement["room_key"]
        try:
            rooms[room_key] = _live_room(area_key, room_key, search_tag)
        except AreaResetError as err:
            logger.log_err(
                f"AREA-03C mobile reset skipped for {area_key}:{placement['placement_key']}: "
                f"{_summary(err)}"
            )
    for placement in placements:
        if placement["room_key"] not in rooms:
            continue
        try:
            result = reconcile_mobile_placement(
                _mobile_reset_identity(area_key, reset_token), placement, rooms
            )
            if result.status == "failed":
                logger.log_err(
                    f"AREA-03C mobile reset failed for {area_key}:"
                    f"{placement['placement_key']}: {result.reason}"
                )
        except Exception:
            logger.log_trace(
                f"AREA-03C mobile reset crashed for {area_key}:"
                f"{placement['placement_key']}."
            )


def _mobile_reset_identity(area_key: str, reset_token: str) -> str:
    """Return MOB-05's bounded stable reset key without leaking controller data."""
    digest = hashlib.sha256(f"{area_key}:{reset_token}".encode("utf-8")).hexdigest()
    return f"area_reset_{digest[:48]}"


def reconcile_door_resets(area_key: str, reset_token: str, plan: AreaLoadPlan) -> None:
    """Restore this area's authored door defaults through INTERACT-01.

    Source records identify an exit by its immutable AREA-01B key, never by
    name or dbref.  Pair identities intentionally omit the owning area so a
    cross-area logical door is restored only once on a lane token.
    """
    from evennia.utils import logger
    from evennia.utils.search import search_tag
    from systems.doors import (
        DoorError,
        door_area_data,
        door_state,
        restore_initial_state,
    )

    for owner, exit_key, record in plan.exits_and_doors:
        if owner != area_key or record["door"] is None:
            continue
        try:
            exit_obj = _live_exit(owner, exit_key, record, search_tag)
            state = door_state(exit_obj)
            if state is None:
                raise AreaResetError("managed exit is no longer a door")
            if door_area_data(exit_obj) != record["door"]:
                raise AreaResetError("live door does not match the claimed manifest")
            if _exit_has_active_travel(exit_obj):
                raise _DoorResetDeferred("active travel")
            changed = (state.open, state.locked) != (
                state.initial_state == "open",
                state.initial_state == "locked",
            )
            identity = _door_reset_identity(reset_token, state.pair_key)
            result = restore_initial_state(exit_obj, identity)
            if result.status == "restored" and changed:
                _announce_door_reset(exit_obj, result.peer_id)
        except _DoorResetDeferred:
            # A deferred door stays eligible on the next area reset.  It is
            # not a controller failure: committed travel must win this race.
            continue
        except (AreaResetError, DoorError) as err:
            logger.log_err(
                f"AREA-03B door reset skipped for {owner}:{exit_key}: {_summary(err)}"
            )
        except Exception:
            logger.log_trace(f"AREA-03B door reset failed for {owner}:{exit_key}.")


class _DoorResetDeferred(Exception):
    """An active traversal makes a door reset safely retryable later."""


def _live_exit(
    area_key: str,
    exit_key: str,
    record: Mapping[str, Any],
    search_tag: Callable[..., Any],
) -> Any:
    """Resolve exactly one managed live exit from source-owned stable identity."""
    source = _live_room(area_key, record["source_room"], search_tag)
    destination_data = record["destination"]
    destination_area = (
        area_key
        if destination_data["kind"] == "local"
        else destination_data["area_key"]
    )
    destination = _live_room(destination_area, destination_data["room_key"], search_tag)
    candidates = [
        item
        for item in search_tag(exit_key, category=EXIT_KEY_CATEGORY)
        if getattr(item, "location", None) is source
        and getattr(item, "destination", None) is destination
    ]
    if len(candidates) != 1:
        raise AreaResetError("managed live exit is missing or ambiguous")
    return candidates[0]


def _live_room(area_key: str, room_key: str, search_tag: Callable[..., Any]) -> Any:
    """Resolve a managed room without dbref/name/fuzzy fallback."""
    candidates = [
        item
        for item in search_tag(room_key, category=ROOM_KEY_CATEGORY)
        if item.tags.has(area_key, category=AREA_TAG_CATEGORY)
    ]
    if len(candidates) != 1:
        raise AreaResetError("managed live room is missing or ambiguous")
    return candidates[0]


def _door_reset_identity(reset_token: str, pair_key: str | None) -> str:
    """Create one pair-wide idempotency key from the controller lane token."""
    sequence = reset_token.rsplit(":", 1)[-1]
    return f"area-door-reset:{sequence}:{pair_key or reset_token}"


def _exit_has_active_travel(exit_obj: Any) -> bool:
    """Leave a committed queued traversal untouched until a later reset."""
    from systems.action_queue import inspect_action
    from systems.travel import TRAVEL_ACTION_KEY
    from typeclasses.characters import Character

    for actor in Character.objects.filter_family().iterator():
        action = inspect_action(actor)
        if (
            action is not None
            and action.get("definition") == TRAVEL_ACTION_KEY
            and action.get("arguments", {}).get("exit_id") == exit_obj.id
        ):
            return True
    return False


def _announce_door_reset(exit_obj: Any, peer_id: int | None) -> None:
    """Emit one non-revealing local message per affected room."""
    rooms = {getattr(exit_obj, "location", None)}
    if peer_id is not None:
        from evennia.objects.models import ObjectDB

        peer = ObjectDB.objects.filter(id=peer_id).first()
        rooms.add(getattr(peer, "location", None))
    for room in rooms:
        if room is not None:
            room.msg_contents("Something shifts nearby.")


def controller_snapshot() -> dict[str, dict[str, Any]]:
    """Return a primitive copy for bounded staff diagnostics and tests."""
    raw = ServerConfig.objects.conf(AREA_RESET_CONFIG_KEY)
    state = _validated_state(raw)
    return {key: dict(value) for key, value in state["controllers"].items()}


def _process_controller(
    area_key: str,
    controller: Mapping[str, Any],
    sequence: int,
    plan: AreaLoadPlan,
    runner: Callable[[str, str, AreaLoadPlan], None],
) -> AreaResetResult:
    policy = controller["policy"]
    if sequence <= controller["last_completed_token"]:
        return AreaResetResult(area_key, "duplicate", "completed")
    if policy == "never":
        return AreaResetResult(area_key, "idle", "manual_only")
    if policy == "boot" and controller["last_completed_token"] >= 0:
        return AreaResetResult(area_key, "idle", "boot_complete")
    if sequence < controller["next_due_token"]:
        _warn_if_imminent(area_key, controller, sequence)
        return AreaResetResult(area_key, "idle", "not_due")
    if policy == "if_empty" and _area_has_pc(area_key):
        # Do not move the due point: the next lane token may run it once empty.
        return AreaResetResult(area_key, "skipped", "occupied")
    return _claim_and_run(area_key, sequence, plan, runner)


def _claim_and_run(
    area_key: str,
    sequence: int,
    plan: AreaLoadPlan,
    runner: Callable[[str, str, AreaLoadPlan], None],
    *,
    manual: bool = False,
) -> AreaResetResult:
    token = f"area-reset:{area_key}:{sequence}"
    with _locked_state() as state:
        controller = state["controllers"][area_key]
        if not manual and sequence <= controller["last_completed_token"]:
            return AreaResetResult(area_key, "duplicate", "completed")
        controller["last_attempted_token"] = max(
            controller["last_attempted_token"], sequence
        )
        controller["status"] = "running"
        _write_state(state)
    try:
        runner(area_key, token, plan)
    except Exception as err:
        _record_failure(area_key, sequence, err)
        return AreaResetResult(area_key, "failed", "directive_failure")
    with _locked_state() as state:
        controller = state["controllers"][area_key]
        controller["last_completed_token"] = max(
            controller["last_completed_token"], sequence
        )
        controller["status"] = "idle"
        controller["failure_summary"] = ""
        if controller["policy"] == "boot":
            controller["next_due_token"] = sequence + 1
        else:
            controller["next_due_token"] = sequence + max(
                1, controller["lifespan_pulses"]
            )
        _write_state(state)
    return AreaResetResult(area_key, "completed")


def _reconcile_controllers(plan: AreaLoadPlan) -> dict[str, dict[str, Any]]:
    """Create/revise one controller per enabled manifest without running it."""
    with _locked_state() as state:
        live = state["controllers"]
        for area_key, manifest in plan.registry.manifests.items():
            fingerprint = _fingerprint(manifest)
            policy = _policy(manifest["reset_policy"])
            lifespan = manifest["lifespan_pulses"]
            old = live.get(area_key)
            if old is None or old["manifest_fingerprint"] != fingerprint:
                live[area_key] = _new_controller(fingerprint, policy, lifespan)
            else:
                old["policy"] = policy
                old["lifespan_pulses"] = lifespan
        for area_key in tuple(live):
            if area_key not in plan.registry.manifests:
                del live[area_key]
        _write_state(state)
        return {key: dict(value) for key, value in live.items()}


def handoff_area_controllers(
    plan: AreaLoadPlan, area_keys: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    """Commit new controller revisions only after AREA-04B's graph succeeds.

    Unlike startup reconciliation, a targeted apply must not retire unrelated
    controllers simply because its compiled closure is smaller than the world.
    The caller already holds the same registry row inside its transaction.
    """
    with _locked_state() as state:
        live = state["controllers"]
        for area_key in area_keys:
            manifest = plan.registry.manifests[area_key]
            fingerprint = _fingerprint(manifest)
            policy = _policy(manifest["reset_policy"])
            lifespan = manifest["lifespan_pulses"]
            old = live.get(area_key)
            if old is None or old["manifest_fingerprint"] != fingerprint:
                live[area_key] = _new_controller(fingerprint, policy, lifespan)
            else:
                old["policy"] = policy
                old["lifespan_pulses"] = lifespan
        _write_state(state)
        return {key: dict(value) for key, value in live.items()}


def reconcile_area_controllers(plan: AreaLoadPlan) -> dict[str, dict[str, Any]]:
    """Make persisted controllers exactly match one enabled source plan.

    Startup uses this after a successful graph apply so disabled manifests do
    not retain reset authority. It deliberately does not run any directive.
    """
    return _reconcile_controllers(plan)


def recover_area_controllers(plan: AreaLoadPlan) -> AreaControllerRecovery:
    """Repair controller claims without accepting an un-applied fingerprint.

    An unchanged controller retains every scheduling token. A running claim is
    made eligible for its normal retry, while a changed manifest remains on its
    last applied controller until an authorized AREA-04B apply hands it off.
    """
    recovered: list[str] = []
    pending: list[str] = []
    retired: list[str] = []
    with _locked_state() as state:
        live = state["controllers"]
        for area_key, manifest in plan.registry.manifests.items():
            fingerprint = _fingerprint(manifest)
            controller = live.get(area_key)
            if controller is None:
                live[area_key] = _new_controller(
                    fingerprint,
                    _policy(manifest["reset_policy"]),
                    manifest["lifespan_pulses"],
                )
                recovered.append(area_key)
            elif controller["manifest_fingerprint"] != fingerprint:
                pending.append(area_key)
            elif controller["status"] == "running":
                controller["status"] = "idle"
                recovered.append(area_key)
        for area_key in tuple(live):
            if area_key not in plan.registry.manifests:
                del live[area_key]
                retired.append(area_key)
        _write_state(state)
    return AreaControllerRecovery(
        tuple(sorted(recovered)), tuple(sorted(pending)), tuple(sorted(retired))
    )


def retire_area_controller(area_key: str) -> bool:
    """Remove a controller only after AREA-04C fully retires its area graph."""
    with _locked_state() as state:
        if area_key not in state["controllers"]:
            return False
        del state["controllers"][area_key]
        _write_state(state)
        return True


def _new_controller(fingerprint: str, policy: str, lifespan: int) -> dict[str, Any]:
    return {
        "version": CONTROLLER_VERSION,
        "manifest_fingerprint": fingerprint,
        "policy": policy,
        "lifespan_pulses": lifespan,
        "next_due_token": 0,
        "last_attempted_token": -1,
        "last_completed_token": -1,
        "status": "idle",
        "failure_summary": "",
        "warning_token": -1,
    }


def _warn_if_imminent(
    area_key: str, controller: Mapping[str, Any], sequence: int
) -> None:
    if (
        controller["policy"] != "always"
        or sequence != controller["next_due_token"] - _warning_pulses()
    ):
        return
    with _locked_state() as state:
        live = state["controllers"].get(area_key)
        if live is None or live["warning_token"] == sequence:
            return
        live["warning_token"] = sequence
        _write_state(state)
    for pc in _area_pcs(area_key):
        pc.msg("The area shifts uneasily around you.")


def _area_pcs(area_key: str) -> list[Any]:
    from typeclasses.characters import Character

    result = []
    for character in Character.objects.filter_family().iterator():
        value = character.attributes.get("is_player_character")
        location = getattr(character, "location", None)
        if value is False or location is None or area_of(location) != area_key:
            continue
        raw = character.attributes.get("combat_linkdead")
        if isinstance(raw, Mapping) and raw.get("stowed") is True:
            continue
        result.append(character)
    return result


def _area_has_pc(area_key: str) -> bool:
    return bool(_area_pcs(area_key))


def _policy(raw: Any) -> str:
    if raw == "default":
        raw = getattr(settings, "GAME_AREA_RESET_DEFAULT_POLICY", "never")
    if raw not in POLICIES:
        raise AreaResetError("Area reset policy is unsupported.")
    return raw


def _warning_pulses() -> int:
    value = getattr(settings, "GAME_AREA_RESET_WARNING_PULSES", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AreaResetError("Area reset warning boundary must be positive.")
    return value


def _fingerprint(manifest: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _primitive_copy(manifest), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _primitive_copy(value: Any) -> Any:
    """Copy immutable registry proxies without reducing mappings to key lists."""
    if isinstance(value, Mapping):
        return {str(key): _primitive_copy(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_primitive_copy(item) for item in value]
    return value


def _initial_state() -> dict[str, Any]:
    return {"version": AREA_RESET_VERSION, "controllers": {}}


def _validated_state(raw: Any) -> dict[str, Any]:
    if raw is None:
        return _initial_state()
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"version", "controllers"}
        or raw["version"] != AREA_RESET_VERSION
        or not isinstance(raw["controllers"], Mapping)
    ):
        raise AreaResetError("Area reset controller state is malformed.")
    state = {"version": AREA_RESET_VERSION, "controllers": {}}
    for area_key, controller in raw["controllers"].items():
        if not isinstance(area_key, str) or not isinstance(controller, Mapping):
            raise AreaResetError("Area reset controller state is malformed.")
        required = {
            "version",
            "manifest_fingerprint",
            "policy",
            "lifespan_pulses",
            "next_due_token",
            "last_attempted_token",
            "last_completed_token",
            "status",
            "failure_summary",
            "warning_token",
        }
        if set(controller) != required or controller["version"] != CONTROLLER_VERSION:
            raise AreaResetError("Area reset controller record is malformed.")
        state["controllers"][area_key] = dict(controller)
    return state


class _locked_state:
    """Lock the one persistent registry while a controller record is changed."""

    def __enter__(self) -> dict[str, Any]:
        from django.db import transaction

        self.atomic = transaction.atomic()
        self.atomic.__enter__()
        ServerConfig.objects.select_for_update().get_or_create(
            db_key=AREA_RESET_CONFIG_KEY, defaults={"db_value": _initial_state()}
        )
        self.state = _validated_state(ServerConfig.objects.conf(AREA_RESET_CONFIG_KEY))
        return self.state

    def __exit__(self, *args: Any) -> None:
        self.atomic.__exit__(*args)


def _write_state(state: Mapping[str, Any]) -> None:
    ServerConfig.objects.conf(AREA_RESET_CONFIG_KEY, value=dict(state))


def _record_failure(area_key: str, sequence: int, error: Exception) -> None:
    with _locked_state() as state:
        controller = state["controllers"].get(area_key)
        if controller is None:
            return
        controller["last_attempted_token"] = max(
            controller["last_attempted_token"], sequence
        )
        controller["status"] = "failed"
        controller["failure_summary"] = _summary(error)
        _write_state(state)


def _summary(error: Exception) -> str:
    return str(error).replace("\n", " ")[:MAX_FAILURE_LENGTH] or type(error).__name__
