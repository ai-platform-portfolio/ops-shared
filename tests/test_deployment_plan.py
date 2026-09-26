import copy
import unittest

from deployment_plan import fingerprint, verify


class DeploymentPlanTest(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "timestamp": "2026-09-26T18:00:00Z",
            "resource_changes": [{"address": 'module.network.azurerm_subnet.this["functions"]',
                                  "change": {"actions": ["create"], "after": {"address_prefixes": ["10.50.3.0/26"]}}}],
            "variables": {"example": {"value": "private-value"}},
            "prior_state": {"values": {"root_module": {"resources": []}}},
        }

    def test_new_timestamp_and_key_order_do_not_require_another_approval(self):
        reviewed = fingerprint(self.plan)
        reordered = dict(reversed(list(self.plan.items())))
        reordered["timestamp"] = "2026-09-26T18:05:00Z"
        verify(reordered, reviewed)

    def test_changed_action_value_or_state_requires_fresh_approval(self):
        reviewed = fingerprint(self.plan)
        for field in ("action", "subnet", "sensitive", "state"):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.plan)
                if field == "action":
                    changed["resource_changes"][0]["change"]["actions"] = ["delete", "create"]
                elif field == "subnet":
                    changed["resource_changes"][0]["change"]["after"]["address_prefixes"] = ["10.50.4.0/26"]
                elif field == "sensitive":
                    changed["variables"]["example"]["value"] = "changed-private-value"
                else:
                    changed["prior_state"]["values"]["root_module"]["resources"] = [{"address": "unexpected"}]
                with self.assertRaisesRegex(ValueError, "fresh approval"):
                    verify(changed, reviewed)


if __name__ == "__main__":
    unittest.main()
