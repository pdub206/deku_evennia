"""
Room

Rooms are simple containers that has no location of their own.

"""

from evennia.objects.objects import DefaultRoom

from .objects import ObjectParent


class Room(ObjectParent, DefaultRoom):
    """
    Rooms are like any Object, except their location is None
    (which is default). They also use basetype_setup() to
    add locks so they cannot be puppeted or picked up.
    (to change that, use at_object_creation instead)

    See mygame/typeclasses/objects.py for a list of
    properties and methods available on all Objects.
    """

    def get_display_characters(self, looker, **kwargs) -> str:
        """List characters in the room with their positional state."""
        characters = self.filter_visible(
            self.contents_get(content_type="character"), looker, **kwargs
        )
        lines = []
        for char in characters:
            if char is looker:
                continue
            position = char.action_position.value
            name = char.get_display_name(looker, **kwargs)
            lines.append(f"{name} is {position} here.")
        return "\n".join(lines)

    def filter_visible(self, object_list, looker, **kwargs):
        """Apply INTERACT-04's canonical local visibility to room contents."""
        from systems.visibility import discover_passively, target_visibility

        passive = set(discover_passively(looker, object_list))
        return [
            obj
            for obj in object_list
            if obj in passive or target_visibility(looker, obj, source=self).visible
        ]
