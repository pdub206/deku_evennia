"""AREA-06A cold-start reconciliation for the enabled source-owned world."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from django.conf import settings
from evennia.utils import logger
from systems import room_roles
from systems.area_apply import AreaApplyError, AreaApplyResult, apply_areas
from systems.area_resets import recover_area_controllers, reconcile_area_controllers
from systems.areas import AreaLoadPlan, AreaPlanError, compile_area_load_plan
from world.build_schema import as_slug

ENABLED_AREA_SETTING = "GAME_ENABLED_AREA_MANIFESTS"


class AreaStartupError(ValueError):
    """The configured enabled world cannot safely accept entry or resets."""


@dataclass(frozen=True)
class AreaStartupResult:
    """One bounded, staff-safe cold-start reconciliation outcome."""

    status: str
    areas: tuple[str, ...] = ()
    detail: str = ""
    applied: AreaApplyResult | None = None


@dataclass(frozen=True)
class AreaReloadResult:
    """Read-only source validation and controller recovery at hot reload."""

    status: str
    pending: tuple[str, ...] = ()
    recovered: tuple[str, ...] = ()
    retired: tuple[str, ...] = ()
    detail: str = ""


def configured_enabled_area_manifests() -> tuple[str, ...]:
    """Return the exact ordered deployment manifest selection from settings."""
    raw = getattr(settings, ENABLED_AREA_SETTING, ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise AreaStartupError(f"{ENABLED_AREA_SETTING} must be an ordered list.")
    selected: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            raise AreaStartupError(f"{ENABLED_AREA_SETTING} keys must be text.")
        try:
            key = as_slug(value)
        except ValueError as err:
            raise AreaStartupError(
                f"{ENABLED_AREA_SETTING} contains an invalid area key."
            ) from err
        if key != value:
            raise AreaStartupError(
                f"{ENABLED_AREA_SETTING} keys must be normalized slugs."
            )
        if key in selected:
            raise AreaStartupError(f"{ENABLED_AREA_SETTING} keys must be unique.")
        selected.append(key)
    return tuple(selected)


def reconcile_enabled_world(
    *,
    compiler: Callable[[Sequence[str] | None], AreaLoadPlan] = compile_area_load_plan,
    applier: Callable[..., AreaApplyResult] = apply_areas,
) -> AreaStartupResult:
    """Validate, apply, and arm exactly the cold-start enabled source world.

    Planning and source role checks finish before the applier can mutate live
    data. A failure is contained to staff logs and returns no partially
    accepted result; ordinary role resolution and the reset lane remain strict.
    """
    try:
        selected = configured_enabled_area_manifests()
        if not selected:
            return AreaStartupResult("disabled")
        plan = compiler(selected)
        _validate_required_source_roles(plan)
        applied = applier(selected, compiler=compiler)
        room_roles.validate_room_roles()
        reconcile_area_controllers(plan)
    except (
        AreaStartupError,
        AreaPlanError,
        AreaApplyError,
        room_roles.RoomRoleError,
    ) as err:
        detail = _summary(err)
        logger.log_err(f"AREA-06A enabled-world startup blocked: {detail}")
        return AreaStartupResult("blocked", detail=detail)
    except Exception:
        logger.log_trace("AREA-06A enabled-world startup failed unexpectedly.")
        return AreaStartupResult("blocked", detail="unexpected_startup_failure")
    return AreaStartupResult("ready", tuple(plan.registry.manifests), applied=applied)


def recover_enabled_world_reload(
    *,
    compiler: Callable[[Sequence[str] | None], AreaLoadPlan] = compile_area_load_plan,
) -> AreaReloadResult:
    """Validate synced source without applying it or altering live content.

    Changed manifests remain pending. Only missing, interrupted, or disabled
    controller records are recovered; no directive, apply, prune, or Builder
    draft operation is performed from this reload hook.
    """
    try:
        selected = configured_enabled_area_manifests()
        if not selected:
            recovery = recover_area_controllers(compiler(selected))
            return AreaReloadResult("disabled", retired=recovery.retired)
        plan = compiler(selected)
        _validate_required_source_roles(plan)
        recovery = recover_area_controllers(plan)
    except (AreaStartupError, AreaPlanError) as err:
        detail = _summary(err)
        logger.log_err(f"AREA-06B enabled-world reload blocked: {detail}")
        return AreaReloadResult("blocked", detail=detail)
    except Exception:
        logger.log_trace("AREA-06B enabled-world reload failed unexpectedly.")
        return AreaReloadResult("blocked", detail="unexpected_reload_failure")
    if recovery.pending:
        logger.log_warn(
            "AREA-06B source pending authorized apply: " + ", ".join(recovery.pending)
        )
        status = "pending"
    else:
        status = "ready"
    return AreaReloadResult(
        status,
        pending=recovery.pending,
        recovered=recovery.recovered,
        retired=recovery.retired,
    )


def _validate_required_source_roles(plan: AreaLoadPlan) -> None:
    """Require all entry roles to target exact rooms in the enabled plan."""
    for setting_name in room_roles.ROOM_ROLE_SETTINGS:
        configured = getattr(settings, setting_name, None)
        if not isinstance(configured, str) or configured.count(":") != 1:
            raise AreaStartupError(f"{setting_name} must be one enabled area:room key.")
        area, room = (part.strip() for part in configured.split(":"))
        try:
            reference = f"{as_slug(area)}:{as_slug(room)}"
        except ValueError as err:
            raise AreaStartupError(
                f"{setting_name} must be one enabled area:room key."
            ) from err
        if reference not in plan.registry.rooms:
            raise AreaStartupError(f"{setting_name} targets no enabled source room.")


def _summary(error: Exception) -> str:
    """Keep a diagnostic useful to staff and bounded in persistent logs."""
    return str(error).replace("\n", " ")[:240] or type(error).__name__
