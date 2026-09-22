"""AREA-05D authored mobile-loadout validation tests."""

from unittest.mock import patch

from evennia.utils.test_resources import EvenniaTest
from systems.mobile_loadouts import MobileLoadoutError, validate_loadout


class TestMobileLoadouts(EvenniaTest):
    """Loadouts accept only bounded source-owned item records."""

    @patch("systems.mobile_loadouts.resolve_prototype")
    def test_valid_source_item_loadout(self, resolve):
        resolve.return_value = {
            "prototype_key": "dagger",
            "typeclass": "typeclasses.objects.Item",
            "type": "weapon",
            "account_bound": False,
        }
        self.assertEqual(
            validate_loadout(
                {"version": 1, "items": [{"prototype_key": "dagger", "quantity": 1, "wear": "wield"}]}
            ),
            ({"prototype_key": "dagger", "quantity": 1, "wear": "wield"},),
        )

    @patch("systems.mobile_loadouts.resolve_prototype")
    def test_rejects_bound_money_bad_shape_and_quantity(self, resolve):
        resolve.return_value = {
            "prototype_key": "bound", "type": "other", "account_bound": True
        }
        with self.assertRaisesRegex(MobileLoadoutError, "bound"):
            validate_loadout({"version": 1, "items": [{"prototype_key": "bound", "quantity": 1}]})
        with self.assertRaises(MobileLoadoutError):
            validate_loadout({"version": 2, "items": []})
        with self.assertRaises(MobileLoadoutError):
            validate_loadout({"version": 1, "items": [{"prototype_key": "dagger", "quantity": 0}]})
