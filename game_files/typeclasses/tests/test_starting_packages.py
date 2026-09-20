"""ITEM-07A starting-package registry, validation, projection, and output tests.

Every prototype and package here is a synthetic fixture built inside the test;
shipped game content is authored by hand and never lives in tests.

Run from the game/ directory:
    evennia test --settings settings.py typeclasses.tests.test_starting_packages
"""

from copy import deepcopy
from unittest.mock import MagicMock, patch

from commands.starting_packages import CmdStartPackages
from django.test import override_settings
from evennia.prototypes import prototypes as protlib
from evennia.utils.test_resources import EvenniaCommandTest, EvenniaTest
from systems.encumbrance import pounds_to_units
from systems.progression import SELECTABLE_CLASS_NAMES
from systems.starting_packages import (ITEM_TYPECLASS, StartingPackageError,
                                       build_starting_package_registry,
                                       describe_package,
                                       minimum_release_capacity_units,
                                       module_item_prototypes,
                                       package_selections, plan_starting_grant,
                                       starting_package_registry)
from world.chargen_data import BACKGROUNDS
from world.chargen_menu import (menunode_background_detail,
                                menunode_class_detail)
from world.help_entries import HELP_ENTRY_DICTS


def _prototype(key: str, name: str, **fields) -> dict:
    """Return a minimal valid module-style Item prototype fixture."""
    proto = {
        "prototype_key": key,
        "prototype_tags": ["module"],
        "typeclass": ITEM_TYPECLASS,
        "key": name,
        "weight": 1.0,
        "value": 1,
        "type": "other",
        "no_drop": False,
        "account_bound": False,
        "srd_reference": "SRD 5.2.1 Equipment",
    }
    proto.update(fields)
    return proto


FIXTURE_PROTOTYPES = {
    "fx_armor": _prototype(
        "fx_armor",
        "Fixture Armor",
        type="armor",
        weight=10.0,
        base_ac=11,
        subtype="light",
        wear_locations=["body"],
    ),
    "fx_dagger": _prototype(
        "fx_dagger",
        "Fixture Dagger",
        type="weapon",
        damage="1d4",
        subtype="piercing",
        weapon_category="simple",
        weapon_kind="dagger",
        attack_ability="dexterity",
        wear_locations=["wield", "hold"],
    ),
    "fx_clothes": _prototype(
        "fx_clothes",
        "Fixture Clothes",
        type="worn",
        weight=4.0,
        wear_locations=["body"],
    ),
    "fx_dice": _prototype("fx_dice", "Fixture Dice", weight=0.0),
    "fx_cards": _prototype("fx_cards", "Fixture Cards", weight=0.0),
    "fx_boulder": _prototype("fx_boulder", "Fixture Boulder", weight=46.0),
}


def _lookup(prototypes: dict | None = None):
    source = FIXTURE_PROTOTYPES if prototypes is None else prototypes

    def lookup(key: str) -> list[dict]:
        return [deepcopy(source[key])] if key in source else []

    return lookup


def _class_package() -> dict:
    return {
        "srd_reference": "SRD 5.2.1 Fixture Class",
        "choices": [
            {
                "key": "equipment",
                "count": 1,
                "options": [
                    {
                        "key": "a",
                        "items": [
                            {"prototype": "fx_armor"},
                            {
                                "prototype": "fx_dagger",
                                "quantity": 2,
                                "equip": ["wield"],
                            },
                        ],
                        "coins": 7,
                    },
                    {"key": "b", "coins": 110},
                ],
            }
        ],
    }


def _background_package() -> dict:
    return {
        "srd_reference": "SRD 5.2.1 Fixture Background",
        "choices": [
            {
                "key": "equipment",
                "options": [
                    {
                        "key": "a",
                        "items": [{"prototype": "fx_clothes", "equip": ["body"]}],
                        "coins": 8,
                        "choices": [
                            {
                                "key": "gaming_set",
                                "options": [
                                    {
                                        "key": "dice",
                                        "items": [{"prototype": "fx_dice"}],
                                    },
                                    {
                                        "key": "cards",
                                        "items": [{"prototype": "fx_cards"}],
                                    },
                                ],
                            }
                        ],
                    },
                    {"key": "b", "coins": 50},
                ],
            }
        ],
    }


def _all_packages() -> tuple[dict, dict]:
    classes = {name: _class_package() for name in SELECTABLE_CLASS_NAMES}
    backgrounds = {name: _background_package() for name in BACKGROUNDS}
    return classes, backgrounds


def _registry(classes=None, backgrounds=None, prototypes=None, **kwargs):
    default_classes, default_backgrounds = _all_packages()
    return build_starting_package_registry(
        default_classes if classes is None else classes,
        default_backgrounds if backgrounds is None else backgrounds,
        prototype_lookup=_lookup(prototypes),
        **kwargs,
    )


def _one(owner: str, package: dict, source: str = "class") -> tuple[dict, dict]:
    """Packages for a registry limited to one class and one background."""
    if source == "class":
        return {owner: package}, {"Acolyte": _background_package()}
    return {"Fighter": _class_package()}, {owner: package}


def _narrow(classes: dict, backgrounds: dict, **kwargs):
    return _registry(
        classes,
        backgrounds,
        selectable_classes=tuple(classes),
        selectable_backgrounds=tuple(backgrounds),
        **kwargs,
    )


class TestStartingPackageCompleteness(EvenniaTest):
    """The registry covers exactly the release's selectable classes/backgrounds."""

    def test_empty_authored_data_is_incomplete_and_fails_closed(self):
        registry = build_starting_package_registry({}, {}, prototype_lookup=_lookup())
        self.assertFalse(registry.complete)
        for name in SELECTABLE_CLASS_NAMES:
            self.assertIn(
                f"class {name} has no starting package.", registry.diagnostics
            )
        for name in BACKGROUNDS:
            self.assertIn(
                f"background {name} has no starting package.", registry.diagnostics
            )
        with self.assertRaises(StartingPackageError):
            plan_starting_grant("Fighter", "Acolyte", {}, registry=registry)
        self.assertEqual(
            describe_package("class", "Fighter", registry=registry),
            "Not yet available.",
        )

    def test_shipped_data_module_is_complete(self):
        """Released chargen content resolves through module item prototypes."""
        registry = starting_package_registry()
        self.assertTrue(registry.complete, "\n".join(registry.diagnostics))
        self.assertEqual(registry.diagnostics, ())

    def test_unavailable_class_cannot_contribute_a_package(self):
        classes, backgrounds = _all_packages()
        classes["Bard"] = _class_package()
        registry = _registry(classes, backgrounds)
        self.assertIn(
            "class Bard is not selectable in this release; remove its package.",
            registry.diagnostics,
        )
        self.assertNotIn("Bard", registry.classes)

    def test_every_released_class_background_and_branch_projects(self):
        registry = _registry()
        self.assertEqual(registry.diagnostics, ())
        for class_key in SELECTABLE_CLASS_NAMES:
            for background_key in BACKGROUNDS:
                class_choices = package_selections(registry.classes[class_key])
                background_choices = package_selections(
                    registry.backgrounds[background_key]
                )
                self.assertEqual(len(class_choices), 2)
                self.assertEqual(len(background_choices), 3)
                for class_selection in class_choices:
                    for background_selection in background_choices:
                        selection = {**class_selection, **background_selection}
                        plan = plan_starting_grant(
                            class_key, background_key, selection, registry=registry
                        )
                        self.assertEqual(plan.class_key, class_key)
                        self.assertEqual(plan.background_key, background_key)
                        self.assertTrue(
                            plan.fits(minimum_release_capacity_units(), 100)
                        )


class TestStartingPackageProjection(EvenniaTest):
    """Plans are exact, ordered, provenance-tagged, and deterministic."""

    def setUp(self):
        super().setUp()
        self.registry = _registry()

    def test_option_a_with_nested_choice(self):
        plan = plan_starting_grant(
            "Fighter",
            "Acolyte",
            {
                "background.equipment.a.gaming_set": ["cards"],
                "background.equipment": ("a",),
                "class.equipment": ("a",),
            },
            registry=self.registry,
        )
        self.assertEqual(
            [(i.prototype_key, i.quantity, i.equip) for i in plan.items],
            [
                ("fx_armor", 1, ()),
                ("fx_dagger", 2, ("wield",)),
                ("fx_clothes", 1, ("body",)),
                ("fx_cards", 1, ()),
            ],
        )
        self.assertEqual(plan.items[0].source, "class:Fighter")
        self.assertEqual(plan.items[0].choice_path, "class.equipment.a")
        self.assertEqual(plan.items[3].source, "background:Acolyte")
        self.assertEqual(
            plan.items[3].choice_path, "background.equipment.a.gaming_set.cards"
        )
        self.assertEqual(plan.coins, 15)
        self.assertEqual(plan.item_count, 5)
        self.assertEqual(plan.weight_units, pounds_to_units(16))
        self.assertEqual(plan.registry_version, 1)
        self.assertEqual(plan.fingerprint, self.registry.fingerprint)
        self.assertEqual(
            list(plan.selections),
            [
                "background.equipment",
                "background.equipment.a.gaming_set",
                "class.equipment",
            ],
        )

    def test_coin_only_options_grant_no_items(self):
        plan = plan_starting_grant(
            "Wizard",
            "Sage",
            {"class.equipment": ("b",), "background.equipment": ("b",)},
            registry=self.registry,
        )
        self.assertEqual(plan.items, ())
        self.assertEqual(plan.coins, 160)
        self.assertEqual(plan.weight_units, 0)

    def test_projection_is_deterministic(self):
        selection = {
            "class.equipment": ("a",),
            "background.equipment": ("a",),
            "background.equipment.a.gaming_set": ("dice",),
        }
        reordered = dict(reversed(list(selection.items())))
        first = plan_starting_grant("Rogue", "Guard", selection, registry=self.registry)
        second = plan_starting_grant(
            "Rogue", "Guard", reordered, registry=self.registry
        )
        self.assertEqual(first, second)
        self.assertEqual(self.registry.fingerprint, _registry().fingerprint)

    def test_invalid_selections_are_refused(self):
        base = {"class.equipment": ("b",), "background.equipment": ("b",)}
        cases = [
            {"class.equipment": ("b",)},
            {**base, "class.equipment": ("c",)},
            {**base, "class.equipment": ("a", "b")},
            {**base, "class.equipment": "b"},
            {**base, "background.equipment.a.gaming_set": ("dice",)},
            {**base, "background.equipment": ("a",)},
        ]
        for selection in cases:
            with self.subTest(selection=selection), self.assertRaises(
                StartingPackageError
            ):
                plan_starting_grant(
                    "Cleric", "Acolyte", selection, registry=self.registry
                )
        with self.assertRaises(StartingPackageError):
            plan_starting_grant("Bard", "Acolyte", base, registry=self.registry)

    def test_multi_pick_choice_orders_by_declaration(self):
        package = _class_package()
        package["choices"][0]["options"].append({"key": "c", "coins": 1})
        package["choices"][0]["count"] = 2
        registry = _narrow(*_one("Fighter", package))
        self.assertEqual(len(package_selections(registry.classes["Fighter"])), 3)
        plan = plan_starting_grant(
            "Fighter",
            "Acolyte",
            {"class.equipment": ("c", "b"), "background.equipment": ("b",)},
            registry=registry,
        )
        self.assertEqual(plan.selections["class.equipment"], ("b", "c"))
        self.assertEqual(plan.coins, 161)


class TestStartingPackageStructure(EvenniaTest):
    """Malformed, missing, duplicate, and incompatible package data."""

    def assert_problem(self, package: dict, expected: str, source: str = "class"):
        owner = "Fighter" if source == "class" else "Acolyte"
        registry = _narrow(*_one(owner, package, source))
        self.assertFalse(registry.complete)
        self.assertTrue(
            any(expected in problem for problem in registry.diagnostics),
            registry.diagnostics,
        )

    def test_structural_errors(self):
        def mutated(change) -> dict:
            package = _class_package()
            change(package)
            return package

        option_a = lambda p: p["choices"][0]["options"][0]  # noqa: E731
        cases = [
            (mutated(lambda p: p.update(color="red")), "unknown field(s): color"),
            (mutated(lambda p: p.pop("srd_reference")), "srd_reference must be text"),
            (mutated(lambda p: p.update(srd_reference="PHB")), "must start with"),
            (mutated(lambda p: p.update(coins=-1)), "coins must be a whole number"),
            (mutated(lambda p: p.update(coins=True)), "coins must be a whole number"),
            (
                mutated(
                    lambda p: option_a(p)["items"].append({"prototype": "fx_armor"})
                ),
                "lists prototype fx_armor twice",
            ),
            (
                mutated(lambda p: option_a(p)["items"][0].update(quantity=0)),
                "quantity must be a whole number",
            ),
            (
                mutated(
                    lambda p: option_a(p)["items"][1].update(
                        equip=["wield", "hold", "body"]
                    )
                ),
                "equips more copies than its quantity",
            ),
            (
                mutated(lambda p: option_a(p)["items"][1].update(equip=["tail"])),
                "unknown wear location",
            ),
            (
                mutated(
                    lambda p: option_a(p)["items"][1].update(equip=["wield", "wield"])
                ),
                "repeats a wear location",
            ),
            (
                mutated(lambda p: option_a(p)["items"][0].update(prototype="Bad Key")),
                "lowercase prototype key",
            ),
            (
                mutated(lambda p: p["choices"][0]["options"].pop()),
                "needs at least two options",
            ),
            (
                mutated(lambda p: p["choices"][0].update(count=3)),
                "count must be a whole number from 1 to 2",
            ),
            (
                mutated(lambda p: p["choices"][0]["options"][1].update(coins=0)),
                "grants nothing",
            ),
            (
                mutated(lambda p: p["choices"][0]["options"][1].update(key="a")),
                "repeats option a",
            ),
            (
                mutated(lambda p: p["choices"].append(deepcopy(p["choices"][0]))),
                "repeats choice equipment",
            ),
            (mutated(lambda p: p.update(items="fx_dice")), "items must be a list"),
        ]
        for package, expected in cases:
            with self.subTest(expected=expected):
                self.assert_problem(package, expected)

    def test_choices_nest_only_one_level(self):
        package = _background_package()
        inner = package["choices"][0]["options"][0]["choices"][0]["options"][0]
        inner["choices"] = deepcopy(package["choices"][0]["options"][0]["choices"])
        self.assert_problem(package, "nests choices too deeply", source="background")

    def test_version_must_be_positive(self):
        registry = _registry(version=0)
        self.assertIn(
            "STARTING_PACKAGE_VERSION must be a positive whole number.",
            registry.diagnostics,
        )


class TestStartingPackagePrototypes(EvenniaTest):
    """Referenced prototypes are source-controlled, spawnable, schema-valid Items."""

    def assert_prototype_problem(self, prototypes: dict, expected: str):
        registry = _narrow(*_one("Fighter", _class_package()), prototypes=prototypes)
        self.assertFalse(registry.complete)
        self.assertTrue(
            any(expected in problem for problem in registry.diagnostics),
            registry.diagnostics,
        )

    def with_armor(self, **changes) -> dict:
        prototypes = deepcopy(FIXTURE_PROTOTYPES)
        prototypes["fx_armor"].update(changes)
        return prototypes

    def test_prototype_validation_failures(self):
        def without(field):
            prototypes = deepcopy(FIXTURE_PROTOTYPES)
            del prototypes["fx_armor"][field]
            return prototypes

        missing = deepcopy(FIXTURE_PROTOTYPES)
        del missing["fx_armor"]
        cases = [
            (missing, "prototype fx_armor does not exist"),
            (self.with_armor(prototype_tags=[]), "exists only in the database"),
            (
                self.with_armor(typeclass="typeclasses.objects.Object"),
                "must use typeclass",
            ),
            (without("weight"), "is missing weight"),
            (without("srd_reference"), "needs srd_reference"),
            (self.with_armor(srd_reference="PHB p. 1"), "needs srd_reference"),
            (self.with_armor(type="money"), "is money"),
            (self.with_armor(type="gizmo"), "has unknown type"),
            (self.with_armor(no_drop="no"), "needs no_drop set to True or False"),
            (self.with_armor(weight=10.005), "not in builder form"),
            (self.with_armor(weight=-1), "field weight"),
            (self.with_armor(value=1.5), "field value"),
            (self.with_armor(wear_locations=["Body"]), "not in builder form"),
            (self.with_armor(subtype="plastic"), "field subtype"),
            (self.with_armor(wieght=3), "does not define: wieght"),
            (self.with_armor(value=lambda: 3), "callable or $protfunc"),
            (self.with_armor(key="$random_name()"), "callable or $protfunc"),
            (self.with_armor(key=""), "needs a key"),
        ]
        for prototypes, expected in cases:
            with self.subTest(expected=expected):
                self.assert_prototype_problem(prototypes, expected)

    def test_database_shadow_is_refused(self):
        def lookup(key):
            matches = _lookup()(key)
            if key == "fx_armor":
                shadow = deepcopy(matches[0])
                shadow["prototype_tags"] = []
                matches.append(shadow)
            return matches

        classes, backgrounds = _one("Fighter", _class_package())
        registry = build_starting_package_registry(
            classes,
            backgrounds,
            selectable_classes=("Fighter",),
            selectable_backgrounds=("Acolyte",),
            prototype_lookup=lookup,
        )
        self.assertIn(
            "prototype fx_armor is shadowed by a database prototype with the same key; "
            "delete that copy.",
            registry.diagnostics,
        )

    def test_real_module_prototype_lookup_and_parent_chain(self):
        parent = _prototype("fx_live_parent", "Fixture Parent", weight=2.0)
        child = {"prototype_key": "fx_live_child", "prototype_parent": "fx_live_parent"}
        protlib.load_module_prototypes(parent, child)
        try:
            matches = module_item_prototypes("fx_live_child")
            self.assertEqual(len(matches), 1)
            package = {
                "srd_reference": "SRD 5.2.1 Fixture",
                "items": [{"prototype": "fx_live_child"}],
            }
            registry = build_starting_package_registry(
                {"Fighter": package},
                {"Acolyte": {"srd_reference": "SRD 5.2.1 Fixture", "coins": 1}},
                selectable_classes=("Fighter",),
                selectable_backgrounds=("Acolyte",),
            )
            self.assertEqual(registry.diagnostics, ())
            facts = registry.items["fx_live_child"]
            self.assertEqual(facts.name, "Fixture Parent")
            self.assertEqual(facts.weight_units, pounds_to_units(2))
        finally:
            for key in ("fx_live_parent", "fx_live_child"):
                protlib._MODULE_PROTOTYPES.pop(key, None)
                protlib._MODULE_PROTOTYPE_MODULES.pop(key, None)

    def test_fingerprint_tracks_prototype_facts(self):
        heavier = self.with_armor(weight=11.0)
        self.assertNotEqual(
            _registry().fingerprint, _registry(prototypes=heavier).fingerprint
        )


class TestStartingPackageEquipAndCapacity(EvenniaTest):
    """Equip planning and carry capacity across the class/background product."""

    def test_minimum_release_capacity_is_strength_three(self):
        self.assertEqual(minimum_release_capacity_units(), pounds_to_units(45))

    def test_illegal_equip_location_for_item(self):
        package = _class_package()
        package["choices"][0]["options"][0]["items"][0]["equip"] = ["head"]
        registry = _narrow(*_one("Fighter", package))
        self.assertTrue(
            any("fx_armor cannot be worn at head" in p for p in registry.diagnostics)
        )

    def test_class_and_background_slot_conflict(self):
        package = _class_package()
        package["choices"][0]["options"][0]["items"][0]["equip"] = ["body"]
        registry = _narrow(*_one("Fighter", package))
        self.assertTrue(
            any("both equip body" in problem for problem in registry.diagnostics),
            registry.diagnostics,
        )

    def test_untrained_armor_is_not_auto_equipped(self):
        package = _class_package()
        package["choices"][0]["options"][0]["items"][0]["equip"] = ["body"]
        background = {"srd_reference": "SRD 5.2.1 Fixture", "coins": 1}
        wizard = _narrow({"Wizard": package}, {"Acolyte": background})
        fighter = _narrow({"Fighter": package}, {"Acolyte": background})
        self.assertTrue(
            any("Wizard is not trained" in p for p in wizard.diagnostics),
            wizard.diagnostics,
        )
        self.assertEqual(fighter.diagnostics, ())

    def test_pair_needs_a_combination_the_weakest_character_can_carry(self):
        heavy = {
            "srd_reference": "SRD 5.2.1 Fixture",
            "items": [{"prototype": "fx_boulder"}],
        }
        registry = _narrow(*_one("Fighter", heavy))
        self.assertIn(
            "Fighter + Acolyte: no choice combination fits the weakest eligible "
            "character (45 lb, 100 items).",
            registry.diagnostics,
        )
        optional = _class_package()
        optional["choices"][0]["options"][0]["items"].append(
            {"prototype": "fx_boulder"}
        )
        self.assertEqual(_narrow(*_one("Fighter", optional)).diagnostics, ())

    def test_item_count_limit_counts_quantities(self):
        registry = _registry(item_limit=2)
        self.assertEqual(registry.diagnostics, ())
        crowded = {
            "srd_reference": "SRD 5.2.1 Fixture",
            "items": [{"prototype": "fx_dice", "quantity": 3}],
        }
        registry = _narrow(*_one("Fighter", crowded), item_limit=2)
        self.assertTrue(any("2 items" in p for p in registry.diagnostics))

    @override_settings(GAME_MAX_CURRENCY=100)
    def test_coins_cannot_exceed_wallet_maximum(self):
        registry = _narrow(*_one("Fighter", _class_package()))
        self.assertIn(
            "Fighter + Acolyte grants more coins than a wallet holds.",
            registry.diagnostics,
        )


class TestStartingPackagePresentation(EvenniaTest):
    """Chargen and help output are generated from the registry and truthful."""

    def test_generated_package_text(self):
        registry = _registry()
        self.assertEqual(
            describe_package("class", "Fighter", registry=registry),
            "Choose A or B: (A) Fixture Armor, Fixture Dagger (x2), 7 coins; "
            "or (B) 110 coins",
        )
        self.assertEqual(
            describe_package("background", "Sage", registry=registry),
            "Choose A or B: (A) Fixture Clothes, 8 coins, Choose DICE or CARDS: "
            "(DICE) Fixture Dice; or (CARDS) Fixture Cards; or (B) 50 coins",
        )

    def test_incomplete_registry_only_previews_for_staff(self):
        classes, _ = _all_packages()
        registry = _registry(classes, {})
        self.assertEqual(
            describe_package("class", "Fighter", registry=registry),
            "Not yet available.",
        )
        self.assertIn(
            "Fixture Armor",
            describe_package(
                "class", "Fighter", registry=registry, require_complete=False
            ),
        )

    def test_chargen_detail_pages_use_the_registry(self):
        caller = MagicMock()
        with patch("systems.starting_packages._REGISTRY", _registry()):
            class_text, _ = menunode_class_detail(caller, selected_class="Cleric")
            background_text, _ = menunode_background_detail(caller, selected_bg="Guide")
        self.assertIn("Fixture Dagger (x2)", class_text)
        self.assertIn("Fixture Clothes", background_text)
        with patch("systems.starting_packages._REGISTRY", _registry({}, {})):
            class_text, _ = menunode_class_detail(caller, selected_class="Cleric")
        self.assertIn("Not yet available.", class_text)

    def test_backgrounds_have_no_second_equipment_source(self):
        self.assertTrue(all("equipment" not in data for data in BACKGROUNDS.values()))

    def test_developer_help_is_published(self):
        entry = next(
            e for e in HELP_ENTRY_DICTS if e["key"] == "building starting packages"
        )
        self.assertEqual(entry["locks"], "read:perm(Builder)")
        self.assertIn("## Validation failures", entry["text"])


class TestStartPackagesCommand(EvenniaCommandTest):
    """Builder diagnostics show status, problems, and previews."""

    def test_incomplete_status_lists_problems(self):
        with patch("systems.starting_packages._REGISTRY", _registry({}, {})):
            self.call(
                CmdStartPackages(),
                "",
                "Starting packages v1 incomplete",
            )
            output = self.call(CmdStartPackages(), "")
        self.assertIn("class Fighter has no starting package.", output)

    def test_preview_and_unknown_name(self):
        with patch("systems.starting_packages._REGISTRY", _registry()):
            self.call(CmdStartPackages(), "fighter", "Fighter (class)")
            self.call(CmdStartPackages(), "nobody", "No valid class or background")

    def test_check_revalidates_and_bad_switch_shows_usage(self):
        with patch("systems.starting_packages._REGISTRY", _registry()):
            self.call(CmdStartPackages(), "/bogus", "Usage: startpackages")
        with patch(
            "commands.starting_packages.reset_starting_package_registry"
        ) as reset, patch("systems.starting_packages._REGISTRY", _registry()):
            self.call(CmdStartPackages(), "/check", "Starting packages v1 complete")
        reset.assert_called_once()
