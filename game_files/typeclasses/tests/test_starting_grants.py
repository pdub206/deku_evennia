"""ITEM-07B atomic starting-equipment grant regression coverage."""

from __future__ import annotations

from evennia.utils.test_resources import EvenniaTest
from systems.currency import balance
from systems.starting_grants import (ITEM_PROVENANCE_ATTRIBUTE,
                                     StartingGrantError, commit_starting_grant,
                                     grant_state)
from systems.starting_packages import (package_selections, plan_starting_grant,
                                       starting_package_registry)


class TestStartingGrant(EvenniaTest):
    """Exercise a released plan through real spawning, wallet, and equipment APIs."""

    def setUp(self) -> None:
        super().setUp()
        self.char1.db.strength = 20
        self.char1.db.dexterity = 14
        self.char1.db.constitution = 12
        self.char1.db.is_player_character = True
        self.registry = starting_package_registry()

    def _plan(self):
        class_package = self.registry.classes["Fighter"]
        background_package = self.registry.backgrounds["Acolyte"]
        selections = {
            **package_selections(class_package)[0],
            **package_selections(background_package)[0],
        }
        return plan_starting_grant("Fighter", "Acolyte", selections, registry=self.registry)

    def test_commit_is_exactly_once_and_records_item_provenance(self) -> None:
        """A repeat submission cannot add a second item, coin, or worn state."""
        plan = self._plan()
        self.assertEqual(self.char1.contents, [])
        self.assertEqual(balance(self.char1), 0)

        first = commit_starting_grant(self.char1, plan)
        contents = list(self.char1.contents)
        self.assertTrue(first.completed)
        self.assertFalse(first.repeated)
        self.assertEqual(len(contents), plan.item_count)
        self.assertEqual(balance(self.char1), plan.coins)
        self.assertEqual(grant_state(self.char1)["fingerprint"], plan.fingerprint)
        for item in contents:
            provenance = item.attributes.get(ITEM_PROVENANCE_ATTRIBUTE)
            self.assertEqual(provenance["grant_identity"], first.identity)
            self.assertEqual(provenance["fingerprint"], plan.fingerprint)

        repeated = commit_starting_grant(self.char1, plan)
        self.assertTrue(repeated.repeated)
        self.assertEqual(len(self.char1.contents), plan.item_count)
        self.assertEqual(balance(self.char1), plan.coins)

    def test_capacity_failure_leaves_no_receipt_or_partial_items(self) -> None:
        """Preflight failure is recoverable and cannot reserve a partial grant."""
        self.char1.db.strength = 1
        with self.assertRaisesRegex(StartingGrantError, "cannot carry"):
            commit_starting_grant(self.char1, self._plan())
        self.assertIsNone(grant_state(self.char1))
        self.assertEqual(self.char1.contents, [])
        self.assertEqual(balance(self.char1), 0)
