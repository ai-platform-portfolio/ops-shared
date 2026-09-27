import unittest

from plan_preflight import problems


class PlanningPreflightTest(unittest.TestCase):
    def setUp(self):
        self.repo = dict(
            owner="example",
            owner_id="123",
            name="infra",
            repository_id="456",
            plan_environment="plan",
        )
        self.environment = dict(deployment_branch_policy=None, protection_rules=[])
        self.credential = dict(
            subject="repo:example@123/infra@456:environment:plan",
            issuer="https://token.actions.githubusercontent.com",
            audiences=["api://AzureADTokenExchange"],
        )

    def test_ungated_environment_and_matching_trust_pass(self):
        self.assertEqual(problems(self.repo, self.environment, [self.credential]), [])

    def test_main_only_environment_and_old_pr_trust_fail(self):
        environment = dict(
            self.environment, deployment_branch_policy={"custom_branch_policies": True}
        )
        credential = dict(
            self.credential, subject="repo:example@123/infra@456:pull_request"
        )
        self.assertEqual(len(problems(self.repo, environment, [credential])), 2)

    def test_approval_timer_and_invalid_trust_fail(self):
        for rule in (
            "required_reviewers",
            "wait_timer",
            "custom_deployment_protection_rule",
        ):
            with self.subTest(rule=rule):
                environment = dict(self.environment, protection_rules=[{"type": rule}])
                self.assertTrue(problems(self.repo, environment, [self.credential]))
        for key, value in (
            ("issuer", "https://wrong.invalid"),
            ("audiences", ["wrong"]),
        ):
            with self.subTest(key=key):
                self.assertTrue(
                    problems(
                        self.repo,
                        self.environment,
                        [dict(self.credential, **{key: value})],
                    )
                )
