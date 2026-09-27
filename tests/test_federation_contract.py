import unittest
from unittest.mock import patch

from federation_contract import validate_claims, validate_repository


class FederationContractTest(unittest.TestCase):
    def setUp(self):
        self.repo = dict(
            owner="example",
            owner_id="123",
            name="infra",
            repository_id="456",
            plan_environment="plan",
            apply_environment="apply",
        )
        self.actual = {"id": 456, "owner": {"id": 123}, "full_name": "example/infra"}
        self.claims = {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "api://AzureADTokenExchange",
            "sub": "repo:example@123/infra@456:environment:plan",
        }

    def test_missing_empty_invalid_and_wrong_ids_are_rejected(self):
        validate_repository(self.repo, self.actual)
        for value in (None, "", "wrong", "999"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_repository(dict(self.repo, repository_id=value), self.actual)

    def test_old_subject_wrong_audience_and_issuer_are_rejected(self):
        validate_claims(self.repo, self.claims)
        for key, value in (
            ("sub", "repo:example@123/infra@456:pull_request"),
            ("sub", "repo:example@123/infra@456:environment:apply"),
            ("sub", "repo:example/infra:pull_request"),
            ("aud", "other"),
            ("iss", "https://other.invalid"),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_claims(self.repo, dict(self.claims, **{key: value}))

    def test_missing_or_shared_plan_environment_is_rejected(self):
        for value in (None, "", "apply"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_repository(
                    dict(self.repo, plan_environment=value), self.actual
                )

    def test_apply_only_repository_and_token(self):
        repo = dict(self.repo)
        del repo["plan_environment"]
        validate_repository(repo, self.actual, planning=False)
        with self.assertRaises(ValueError):
            validate_repository(repo, self.actual)
        with patch.dict("os.environ", {"TF_PHASE": "apply"}):
            validate_claims(repo, dict(self.claims, sub="repo:example@123/infra@456:environment:apply"))
            with self.assertRaises(ValueError):
                validate_claims(repo, self.claims)
