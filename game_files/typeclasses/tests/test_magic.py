"""MAGIC-01 registry coverage."""

from types import MappingProxyType

from evennia.utils.test_resources import EvenniaTest
from systems.magic import (
    MAGIC_REGISTRY,
    AccessMode,
    CastSnapshot,
    ClassAccess,
    Damage,
    DiceExpression,
    MagicDefinition,
    MagicKind,
    MagicRegistryError,
    PlayerHelp,
    RangeCategory,
    ResourceCost,
    Save,
    Targeting,
    TargetingMode,
    build_magic_registry,
    deserialize_cast_snapshot,
    validate_persistent_magic_state,
)


def arcane_bolt(**changes):
    """Return one complete valid definition for focused registry tests."""
    values = {
        "key": "wizard.arcane_bolt",
        "display_name": "Arcane Bolt",
        "aliases": ("bolt",),
        "kind": MagicKind.SPELL,
        "school": "evocation",
        "tags": ("arcane",),
        "class_access": (ClassAccess("Wizard", 1),),
        "access_modes": (AccessMode.LEARNED,),
        "action_category": "combat",
        "handler_key": "spell_attack",
        "targeting": Targeting(TargetingMode.HOSTILE),
        "range": RangeCategory.ROOM,
        "cost": ResourceCost("arcane_energy", 1),
        "damage": Damage(DiceExpression(1, 8), "force"),
        "player_help": PlayerHelp("arcane bolt", "A focused mote of force."),
    }
    values.update(changes)
    return MagicDefinition(**values)


def build(*definitions):
    """Build with a deliberately small, explicit external reference graph."""
    return build_magic_registry(
        definitions,
        class_keys=("Wizard", "Cleric"),
        resource_keys=("arcane_energy",),
        damage_types=("force", "radiant"),
        effect_keys=("blessed",),
        help_keys=("arcane bolt", "radiant ward"),
    )


class TestMagicRegistry(EvenniaTest):
    """Definitions are immutable, deterministic, and fail closed."""

    def test_alias_lookup_availability_and_generated_help(self):
        registry = build(arcane_bolt())

        self.assertEqual(registry.resolve(" ARCANE   bolt ").key, "wizard.arcane_bolt")
        self.assertEqual(registry.resolve("bolt").display_name, "Arcane Bolt")
        self.assertEqual(
            [item.key for item in registry.available_for("Wizard", 1)],
            ["wizard.arcane_bolt"],
        )
        help_entry = registry.player_help_entry("wizard.arcane_bolt")
        self.assertIn("Target: hostile.", help_entry["text"])
        self.assertIn("Cost: 1 arcane_energy.", help_entry["text"])
        with self.assertRaises(TypeError):
            registry.definitions["new"] = arcane_bolt(key="new")
        with self.assertRaises(TypeError):
            registry.definitions["wizard.arcane_bolt"].messages["start"] = "No."

    def test_cross_references_handler_schema_and_aliases_are_validated(self):
        with self.assertRaises(MagicRegistryError):
            build(
                arcane_bolt(aliases=("same",)),
                arcane_bolt(
                    key="cleric.bolt",
                    display_name="Other",
                    aliases=("same",),
                    class_access=(ClassAccess("Cleric", 1),),
                ),
            )
        with self.assertRaises(MagicRegistryError):
            build(arcane_bolt(cost=ResourceCost("unknown", 1)))
        with self.assertRaises(MagicRegistryError):
            build(arcane_bolt(handler_key="not_registered"))
        with self.assertRaises(MagicRegistryError):
            build(arcane_bolt(handler_key="healing"))
        with self.assertRaises(MagicRegistryError):
            build(arcane_bolt(player_help=PlayerHelp("missing", "No entry.")))
        with self.assertRaises(MagicRegistryError):
            build(arcane_bolt(spell_level=10))
        with self.assertRaises(MagicRegistryError):
            build(arcane_bolt(kind=MagicKind.ABILITY, spell_level=1))
        with self.assertRaises(MagicRegistryError):
            build(
                arcane_bolt(
                    concentration=True,
                    maintenance="concentration",
                    duration=3,
                )
            )
        with self.assertRaises(MagicRegistryError):
            build(
                arcane_bolt(
                    key="cleric.no_consequence",
                    display_name="No Consequence",
                    aliases=("none",),
                    class_access=(ClassAccess("Cleric", 1),),
                    handler_key="saving_throw",
                    targeting=Targeting(TargetingMode.CREATURE),
                    damage=None,
                    save=Save("Wisdom"),
                    player_help=PlayerHelp("radiant ward", "An invalid save."),
                )
            )
        with self.assertRaises(MagicRegistryError):
            build(
                arcane_bolt(
                    key="cleric.partial_save",
                    display_name="Partial Save",
                    aliases=("partial",),
                    class_access=(ClassAccess("Cleric", 1),),
                    handler_key="saving_throw",
                    targeting=Targeting(TargetingMode.CREATURE),
                    damage=None,
                    save=Save("Wisdom", on_success="half"),
                    effect_keys=("blessed",),
                    player_help=PlayerHelp("radiant ward", "A partial test."),
                )
            )

    def test_released_registry_requires_an_srd_reference(self):
        """Production content cannot register without its SRD 5.2.1 citation."""
        with self.assertRaises(MagicRegistryError):
            build_magic_registry(
                (arcane_bolt(),),
                class_keys=("Wizard",),
                resource_keys=("arcane_energy",),
                damage_types=("force",),
                help_keys=("arcane bolt",),
                require_srd_references=True,
            )
        registry = build_magic_registry(
            (arcane_bolt(srd_reference="SRD 5.2.1 p.107: Spell Descriptions"),),
            class_keys=("Wizard",),
            resource_keys=("arcane_energy",),
            damage_types=("force",),
            help_keys=("arcane bolt",),
            require_srd_references=True,
        )
        self.assertTrue(registry.requires_srd_references)

    def test_released_cantrips_are_complete_and_deterministic(self):
        """The first alpha catalog exposes only executable SRD cantrips."""
        self.assertEqual(
            tuple(MAGIC_REGISTRY.definitions),
            (
                "wizard.acid_splash",
                "wizard.fire_bolt",
                "wizard.poison_spray",
                "cleric.sacred_flame",
                "cleric.spare_the_dying",
                "cleric.thaumaturgy",
                "cleric.cure_wounds",
                "cleric.healing_word",
                "cleric.shield_of_faith",
                "wizard.magic_missile",
                "wizard.thunderwave",
                "wizard.detect_magic",
                "wizard.burning_hands",
                "wizard.longstrider",
                "wizard.grease",
            ),
        )
        self.assertEqual(
            tuple(
                definition.key
                for definition in MAGIC_REGISTRY.available_for("Wizard", 1)
                if definition.spell_level == 0
            ),
            (
                "wizard.acid_splash",
                "wizard.fire_bolt",
                "wizard.poison_spray",
            ),
        )
        self.assertEqual(
            MAGIC_REGISTRY.resolve("sacred flame").key,
            "cleric.sacred_flame",
        )
        self.assertEqual(
            sum(
                definition.spell_level == 0
                for definition in MAGIC_REGISTRY.available_for("Cleric", 1)
            ),
            3,
        )
        self.assertEqual(
            sum(
                definition.spell_level == 1
                for definition in MAGIC_REGISTRY.available_for("Wizard", 1)
            ),
            6,
        )
        cantrips = tuple(
            definition
            for definition in MAGIC_REGISTRY.definitions.values()
            if definition.spell_level == 0
        )
        for definition in cantrips:
            self.assertEqual(definition.spell_level, 0)
            self.assertEqual(definition.access_modes, (AccessMode.LEARNED,))
            self.assertIn(definition.action_category, {"combat", "manipulate"})
        for definition in MAGIC_REGISTRY.definitions.values():
            self.assertTrue(definition.srd_reference.startswith("SRD 5.2.1 "))
            self.assertIn(
                definition.player_help.summary,
                MAGIC_REGISTRY.player_help_entry(definition.key)["text"],
            )

    def test_save_effect_and_disabled_entries_are_safe(self):
        definition = arcane_bolt(
            key="cleric.radiant_ward",
            display_name="Radiant Ward",
            aliases=("ward",),
            class_access=(ClassAccess("Cleric", 1),),
            handler_key="saving_throw",
            targeting=Targeting(TargetingMode.CREATURE),
            damage=None,
            save=Save("Wisdom"),
            effect_keys=("blessed",),
            player_help=PlayerHelp("radiant ward", "A ward of light."),
            enabled=False,
        )
        registry = build(definition)

        self.assertFalse(registry.is_available("cleric.radiant_ward"))
        self.assertEqual(
            registry.definition_for("cleric.radiant_ward").display_name, "Radiant Ward"
        )
        with self.assertRaises(MagicRegistryError):
            registry.resolve("ward")
        with self.assertRaises(MagicRegistryError):
            build(
                definition,
                arcane_bolt(
                    key="cleric.other",
                    display_name="Other",
                    aliases=("other",),
                    class_access=(ClassAccess("Cleric", 1),),
                    effect_keys=("missing",),
                ),
            )

    def test_cast_snapshots_and_runtime_choices_contain_primitives_only(self):
        snapshot = CastSnapshot(
            "wizard.arcane_bolt",
            1,
            11,
            (12,),
            1,
            13,
            5,
            3,
            MappingProxyType({"arcane_energy": 1}),
        )
        restored = deserialize_cast_snapshot(snapshot.serialize())
        self.assertEqual(restored.target_ids, (12,))
        self.assertEqual(restored.spellcasting_modifier, 3)
        legacy = snapshot.serialize()
        legacy["registry_version"] = 1
        legacy.pop("spellcasting_modifier")
        self.assertEqual(deserialize_cast_snapshot(legacy).spellcasting_modifier, 0)
        validate_persistent_magic_state({"known": ["wizard.arcane_bolt"], "choice": 1})
        with self.assertRaises(MagicRegistryError):
            validate_persistent_magic_state({"callback": lambda: None})
        with self.assertRaises(MagicRegistryError):
            deserialize_cast_snapshot({"source_key": "wizard.arcane_bolt"})
