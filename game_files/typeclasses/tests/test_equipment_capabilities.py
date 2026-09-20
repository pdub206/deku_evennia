"""ITEM-08B capability validation, sourcing, and damage ordering tests."""

from evennia import create_object
from evennia.utils.test_resources import EvenniaTest
from systems.equipment_capabilities import (
    EquipmentCapabilityError,
    has_equipment_capability,
    validate_equipment_capabilities,
)


class TestEquipmentCapabilities(EvenniaTest):
    """Released categorical equipment capabilities remain bounded and local."""

    def item(self, name, item_type, capabilities, *, wear_locations=()):
        """Create one directly carried item with an authored capability profile."""
        return create_object(
            "typeclasses.objects.Item",
            key=name,
            location=self.char1,
            attributes=(
                ("type", item_type),
                ("wear_locations", list(wear_locations)),
                ("equipment_capabilities", capabilities),
            ),
        )

    def test_capabilities_validate_and_do_not_stack(self):
        """A reviewed capability has one source-independent boolean result."""
        profile = validate_equipment_capabilities(["terrain:boat"], "boat", [])
        self.assertEqual(profile, ["terrain:boat"])
        self.item("boat one", "boat", profile)
        self.item("boat two", "boat", profile)
        self.assertTrue(has_equipment_capability(self.char1, "terrain:boat"))
        with self.assertRaises(EquipmentCapabilityError):
            validate_equipment_capabilities(["terrain:boat"], "other", [])
        with self.assertRaises(EquipmentCapabilityError):
            validate_equipment_capabilities(
                ["terrain:boat", "terrain:boat"], "boat", []
            )

    def test_carried_tool_does_not_confer_proficiency(self):
        """Tools meet equipment requirements only; training remains separate."""
        tool = self.item("tools", "other", ["tool:thieves_tools"])
        self.assertTrue(has_equipment_capability(self.char1, "tool:thieves_tools"))
        self.assertNotIn("thieves_tools", self.char1.db.tool_proficiencies or [])
        tool.move_to(self.room1, quiet=True)
        self.assertFalse(has_equipment_capability(self.char1, "tool:thieves_tools"))

    def test_resistance_applies_once_after_locational_mitigation(self):
        """Resistance halves only the armor-mitigated remainder, never twice."""
        armor = self.item(
            "fireward cloak", "worn", ["resistance:fire"], wear_locations=["back"]
        )
        self.char1.equipment.equip(armor, "back")
        helmet = create_object(
            "typeclasses.objects.Item",
            key="helmet",
            location=self.char1,
            attributes=(
                ("type", "armor"),
                ("subtype", "light"),
                ("wear_locations", ["head"]),
                ("mitigation_flat", 3),
                ("mitigation_types", ["fire"]),
            ),
        )
        self.char1.equipment.equip(helmet, "head")
        self.assertEqual(self.char1.stats.mitigate_damage(10, "head", "fire").final, 3)
        self.assertEqual(self.char1.stats.mitigate_damage(1, "head", "fire").final, 0)
        self.char1.equipment.unequip(armor)
        self.assertEqual(self.char1.stats.mitigate_damage(10, "head", "fire").final, 7)
