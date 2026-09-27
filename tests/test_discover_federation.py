import unittest

from discover_federation import discover


class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.policy = {"enabled": True, "organization": "ai-platform-portfolio", "apply_environment": "central-apply"}
        self.repo = {"name": "new-repo", "id": 123, "owner": {"login": "ai-platform-portfolio", "id": 456},
                     "full_name": "ai-platform-portfolio/new-repo"}

    def test_disabled_by_default_and_scoped_to_the_org(self):
        self.assertEqual(discover({}, {}, lambda _: self.fail("Must not fetch")), {})
        with self.assertRaises(ValueError):
            discover(dict(self.policy, organization="downstream"), {}, lambda _: [])

    def test_new_repository_gets_only_apply_trust_without_workflow_detection(self):
        entry = discover(self.policy, {}, lambda _: [self.repo])["new-repo"]
        self.assertNotIn("plan_environment", entry)
        self.assertEqual(entry["repository_id"], "123")
        self.assertEqual(entry["apply_environment"], "central-apply")
        existing = {"new-repo": dict(entry, plan_environment="plan")}
        self.assertEqual(discover(self.policy, existing, lambda _: [self.repo]), existing)

    def test_failed_metadata_and_capacity_cannot_silently_drop_trust(self):
        with self.assertRaises(ValueError):
            discover(self.policy, {}, lambda _: {"error": "unavailable"})
        wrong = dict(self.repo, id=999)
        configured = discover(self.policy, {}, lambda _: [self.repo])
        with self.assertRaises(ValueError):
            discover(self.policy, configured, lambda _: [wrong])
        with self.assertRaises(ValueError):
            discover(self.policy, {str(i): {"apply_environment": "apply"} for i in range(21)}, lambda _: [])
