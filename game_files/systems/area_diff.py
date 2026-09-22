"""Read-only AREA-04A reconciliation between compiled source and live areas.

This module deliberately has no mutation imports.  It reports stable managed
identities only; player contents, occupants, transient door state, and dbrefs
are never read into its output.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from evennia.utils.search import search_tag
from systems.areas import (
    AREA_TAG_CATEGORY,
    EXIT_KEY_CATEGORY,
    ROOM_KEY_CATEGORY,
    AreaLoadPlan,
    AreaManifestError,
    external_destination_data,
)
from systems.doors import door_area_data
from systems.room_environment import room_environment_data
from systems.room_policy import room_policy_data
from systems.travel import sector_key

DIFF_KINDS = (
    "addition",
    "authored_update",
    "reference_change",
    "rename",
    "blocked_conflict",
    "stale_candidate",
)
MAX_DIFF_ENTRIES = 50


@dataclass(frozen=True)
class AreaDiffEntry:
    """One safe, stable reconciliation finding."""

    kind: str
    subject: str
    detail: str


@dataclass(frozen=True)
class AreaDiff:
    """Deterministically ordered findings for one compiled plan."""

    entries: tuple[AreaDiffEntry, ...]

    def page(
        self, number: int, size: int = MAX_DIFF_ENTRIES
    ) -> tuple[AreaDiffEntry, ...]:
        """Return one bounded one-based page without changing live state."""
        if number < 1 or size < 1:
            return ()
        start = (number - 1) * size
        return self.entries[start : start + size]


def _room_record(room: Any) -> dict[str, Any]:
    """Read the authored portion of a live managed room."""
    profiles = room.tags.get(category="weather_profile", return_list=True)
    if len(profiles) > 1:
        raise AreaManifestError("Live room has ambiguous weather profiles.")
    return {
        "name": room.key,
        "description": room.attributes.get("desc") or "",
        "extra_descriptions": room.attributes.get("extra_descs") or [],
        "sector": sector_key(room),
        "policy": room_policy_data(room),
        "environment": room_environment_data(room),
        "weather_profile": profiles[0] if profiles else None,
    }


def _exit_record(
    exit_obj: Any, room_by_id: dict[int, tuple[str, str]]
) -> dict[str, Any]:
    """Read authored exit fields while excluding current door state and dbrefs."""
    destination = exit_obj.destination
    if destination is not None and destination.id in room_by_id:
        area_key, room_key = room_by_id[destination.id]
        reference = (
            {"kind": "local", "room_key": room_key}
            if area_key == room_by_id[exit_obj.location.id][0]
            else {"kind": "external", "area_key": area_key, "room_key": room_key}
        )
    else:
        external = external_destination_data(exit_obj)
        if external is None:
            raise AreaManifestError("Live exit has no managed destination reference.")
        reference = {"kind": "external", **external}
    return {
        "source_room": room_by_id[exit_obj.location.id][1],
        "name": exit_obj.key,
        "description": exit_obj.attributes.get("desc") or "",
        "aliases": sorted(exit_obj.aliases.all()),
        "destination": reference,
        "door": door_area_data(exit_obj),
    }


def _index_live(
    plan: AreaLoadPlan,
) -> tuple[dict[str, list[Any]], dict[str, list[Any]], list[AreaDiffEntry]]:
    """Index live tagged records and identify malformed/duplicate identities."""
    areas = set(plan.registry.manifests)
    rooms: dict[str, list[Any]] = defaultdict(list)
    exits: dict[str, list[Any]] = defaultdict(list)
    conflicts: list[AreaDiffEntry] = []
    managed_rooms = []
    for area_key in sorted(areas):
        for room in search_tag(area_key, category=AREA_TAG_CATEGORY):
            keys = room.tags.get(category=ROOM_KEY_CATEGORY, return_list=True)
            if len(keys) != 1:
                conflicts.append(
                    AreaDiffEntry(
                        "blocked_conflict",
                        f"room:{area_key}",
                        "ambiguous live room identity",
                    )
                )
                continue
            identity = f"{area_key}:{keys[0]}"
            rooms[identity].append(room)
            managed_rooms.append(room)
    room_by_id = {
        room.id: (area, key)
        for identity, found in rooms.items()
        for room in found
        for area, key in [identity.split(":", 1)]
    }
    for room in managed_rooms:
        for exit_obj in room.exits:
            keys = exit_obj.tags.get(category=EXIT_KEY_CATEGORY, return_list=True)
            if not keys:
                continue
            if len(keys) != 1:
                conflicts.append(
                    AreaDiffEntry(
                        "blocked_conflict",
                        "exit:unknown",
                        "ambiguous live exit identity",
                    )
                )
                continue
            area_key, source_key = room_by_id.get(room.id, ("unknown", "unknown"))
            exits[f"{area_key}:{keys[0]}"].append(exit_obj)
    for identity, found in rooms.items():
        if len(found) > 1:
            conflicts.append(
                AreaDiffEntry(
                    "blocked_conflict",
                    f"room:{identity}",
                    "duplicate live room identity",
                )
            )
    for identity, found in exits.items():
        if len(found) > 1:
            conflicts.append(
                AreaDiffEntry(
                    "blocked_conflict",
                    f"exit:{identity}",
                    "duplicate live exit identity",
                )
            )
    return rooms, exits, conflicts


def _compare_records(
    subject: str,
    source: dict[str, Any],
    live: dict[str, Any],
    reference_fields: set[str],
) -> list[AreaDiffEntry]:
    """Classify a same-identity record without exposing unsafe live details."""
    changed = sorted(key for key in source if source[key] != live.get(key))
    if not changed:
        return []
    references = sorted(set(changed) & reference_fields)
    authored = sorted(set(changed) - reference_fields)
    entries = []
    if references:
        entries.append(
            AreaDiffEntry("reference_change", subject, ", ".join(references))
        )
    if authored:
        entries.append(AreaDiffEntry("authored_update", subject, ", ".join(authored)))
    return entries


def build_area_diff(plan: AreaLoadPlan) -> AreaDiff:
    """Compare a compiled plan to live managed identity without performing writes."""
    live_rooms, live_exits, entries = _index_live(plan)
    room_by_id = {
        room.id: tuple(identity.split(":", 1))
        for identity, matches in live_rooms.items()
        for room in matches
    }
    expected_rooms = dict(plan.rooms)
    expected_exits = {
        f"{area}:{key}": record for area, key, record in plan.exits_and_doors
    }
    renames: dict[str, str] = {}
    for area, manifest in plan.registry.manifests.items():
        for old, new in manifest["renames"]["rooms"].items():
            renames[f"room:{area}:{old}"] = f"room:{area}:{new}"
        for old, new in manifest["renames"]["exits"].items():
            renames[f"exit:{area}:{old}"] = f"exit:{area}:{new}"

    def reconcile(
        kind: str,
        expected: dict[str, Any],
        live: dict[str, list[Any]],
        reader,
        references: set[str],
    ) -> None:
        for identity in sorted(expected):
            subject = f"{kind}:{identity}"
            matches = live.get(identity, [])
            old_subjects = [old for old, new in renames.items() if new == subject]
            old_matches = [
                match
                for old in old_subjects
                for match in live.get(old.split(":", 1)[1], [])
            ]
            if matches and old_matches:
                entries.append(
                    AreaDiffEntry(
                        "blocked_conflict",
                        subject,
                        "rename target and source both exist",
                    )
                )
            elif matches:
                if len(matches) == 1:
                    try:
                        entries.extend(
                            _compare_records(
                                subject,
                                dict(expected[identity]),
                                reader(matches[0]),
                                references,
                            )
                        )
                    except (AreaManifestError, ValueError):
                        entries.append(
                            AreaDiffEntry(
                                "blocked_conflict",
                                subject,
                                "invalid live authored record",
                            )
                        )
            elif old_matches:
                if len(old_matches) == 1:
                    entries.append(
                        AreaDiffEntry(
                            "rename",
                            subject,
                            f"from {old_subjects[0]} (consumed rename entry)",
                        )
                    )
                else:
                    entries.append(
                        AreaDiffEntry(
                            "blocked_conflict", subject, "rename source is ambiguous"
                        )
                    )
            else:
                entries.append(
                    AreaDiffEntry("addition", subject, "missing live managed record")
                )
        for identity in sorted(live):
            subject = f"{kind}:{identity}"
            if identity not in expected and subject not in renames:
                entries.append(
                    AreaDiffEntry("stale_candidate", subject, "not present in source")
                )

    reconcile("room", expected_rooms, live_rooms, _room_record, set())
    reconcile(
        "exit",
        expected_exits,
        live_exits,
        lambda obj: _exit_record(obj, room_by_id),
        {"source_room", "destination"},
    )
    return AreaDiff(
        tuple(
            sorted(
                entries,
                key=lambda item: (
                    DIFF_KINDS.index(item.kind),
                    item.subject,
                    item.detail,
                ),
            )
        )
    )


def render_area_diff(diff: AreaDiff, page: int = 1) -> str:
    """Render a bounded Builder-safe diff page with stable ordering."""
    rows = diff.page(page)
    pages = max(1, (len(diff.entries) + MAX_DIFF_ENTRIES - 1) // MAX_DIFF_ENTRIES)
    if not rows:
        return f"Area diff: no findings (page {page}/{pages})."
    lines = [f"Area diff ({len(diff.entries)} finding(s), page {page}/{pages}):"]
    lines.extend(f"  {entry.kind}: {entry.subject} — {entry.detail}" for entry in rows)
    if page < pages:
        lines.append("Use area/diff <area|all> <page> for more.")
    return "\n".join(lines)
