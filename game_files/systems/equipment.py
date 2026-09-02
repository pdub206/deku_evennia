"""Shared equipment-slot definitions for wearable items."""

WEAR_LOCATIONS: tuple[str, ...] = (
    "right finger",
    "left finger",
    "neck",
    "back",
    "body",
    "head",
    "legs",
    "feet",
    "hands",
    "arms",
    "shield",
    "about",
    "waist",
    "right wrist",
    "left wrist",
    "wield",
    "hold",
    "right shoulder",
    "left shoulder",
    "right ankle",
    "left ankle",
    # TODO: Implement affixing items to a worn belt.
    "on belt",
)

WEAR_SIDES: tuple[str, str] = ("left", "right")


def wear_phrase(location: str) -> str:
    """Return a natural phrase describing where an item is equipped."""
    if location == "wield":
        return "as your weapon"
    if location == "hold":
        return "in your offhand"
    if location == "shield":
        return "as your shield"
    if location == "about":
        return "about your body"
    if location == "on belt":
        return "on your belt"
    return f"on your {location}"
