import unittest

from verify_function_deployment import verify_environment


class FunctionDeploymentTest(unittest.TestCase):
    def test_missing_deployment_protections_are_rejected(self):
        protected = {
            "can_admins_bypass": False,
            "deployment_branch_policy": {"custom_branch_policies": True},
            "protection_rules": [{"type": "required_reviewers", "reviewers": [
                {"reviewer": {"login": "michaelalinks"}},
            ]}],
        }
        verify_environment(protected)
        for field, value in [("can_admins_bypass", True), ("protection_rules", []),
                             ("deployment_branch_policy", {}), ("deployment_branch_policy", None)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_environment({**protected, field: value})

