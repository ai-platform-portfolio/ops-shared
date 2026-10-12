import copy
import json
from pathlib import Path
import unittest

from function_source import source, verify_environment


class FunctionSourceTest(unittest.TestCase):
    def test_unreviewed_sources_and_output_injection_are_rejected(self):
        config = {"function_apps": {"profile": {
            "name": "fixture-function",
            "source": {"path": "functions/profile_sync", "format": "zip"},
        }}}
        self.assertEqual(source(config, "profile")["path"], "functions/profile_sync")
        for field, value in [("path", "../outside"), ("path", "/absolute"), ("path", "valid\nname=injected"),
                             ("format", "docker"), ("format", "")]:
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(config)
                changed["function_apps"]["profile"]["source"][field] = value
                with self.assertRaises(ValueError):
                    source(changed, "profile")

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

    def test_reviewed_applications_match_the_deployment_workflow(self):
        config = json.loads((Path(__file__).resolve().parents[1] / "functions/apps.json").read_text())
        for application in config["function_apps"]:
            with self.subTest(application=application):
                source(config, application)


if __name__ == "__main__":
    unittest.main()
