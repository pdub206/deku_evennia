"""AREA-05A source-catalog ownership and deterministic resolver tests."""

from unittest.mock import patch

from evennia.prototypes.prototypes import delete_prototype, save_prototype
from evennia.utils.test_resources import EvenniaTest
from systems.prototype_catalogs import (
    ITEM_TYPECLASS,
    PrototypeCatalogError,
    build_catalog,
    catalog_for_area_planning,
    resolve_prototype,
)


class TestPrototypeCatalogs(EvenniaTest):
    """Release prototypes come from tracked data, never database precedence."""

    def test_catalog_is_stable_and_contains_starting_equipment(self):
        first = build_catalog()
        second = build_catalog()

        self.assertEqual(first.version, 1)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(tuple(first.records), tuple(sorted(first.records)))
        self.assertEqual(resolve_prototype("dagger", kind="item")["key"], "dagger")
        self.assertIn("dagger", catalog_for_area_planning()["items"])

    def test_database_shadow_cannot_replace_a_release_record(self):
        with patch(
            "systems.prototype_catalogs.search_prototype",
            return_value=[{"prototype_key": "dagger"}, {"prototype_key": "dagger"}],
        ):
            with self.assertRaisesRegex(PrototypeCatalogError, "shadowed"):
                resolve_prototype("dagger", kind="item")

    def test_unknown_or_wrong_kind_never_falls_back_to_database(self):
        save_prototype(
            {
                "prototype_key": "area05a_legacy",
                "key": "legacy item",
                "typeclass": ITEM_TYPECLASS,
            }
        )
        self.addCleanup(delete_prototype, "area05a_legacy")

        with self.assertRaisesRegex(PrototypeCatalogError, "Unknown release"):
            resolve_prototype("area05a_legacy", kind="item")
        with self.assertRaisesRegex(PrototypeCatalogError, "wrong kind"):
            resolve_prototype("dagger", kind="mobile")
