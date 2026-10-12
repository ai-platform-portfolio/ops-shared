import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "functions"))
from onboarding import onboard


class OnboardingTest(unittest.TestCase):
    def repository(self, **changes):
        return dict(id=42, owner={"login": "ai-platform-portfolio", "id": 334196300},
                    archived=False, private=False, visibility="public", **changes)

    def run_onboard(self, actual, credentials):
        github = Mock()
        github.request.return_value = actual
        azure = Mock()
        azure.side_effect = lambda path, method="GET", data=None: (
            {"value": credentials} if method == "GET" else data)
        with patch.dict(os.environ, {"GITHUB_APP_ID": "1", "GITHUB_INSTALLATION_ID": "2",
                                    "FEDERATION_IDENTITY_ID": "/identity"}), \
                patch("onboarding.installation_client", return_value=github) as token:
            onboard("fixture", 42, "fixture-key", azure)
        return github, azure, token

    def configured(self, **changes):
        return self.repository(delete_branch_on_merge=True, allow_squash_merge=True,
                               allow_merge_commit=False, allow_rebase_merge=False, **changes)

    def test_duplicate_delivery_reuses_existing_trust_and_scopes_token(self):
        credentials = [{"name": phase, "properties": {
            "issuer": "https://token.actions.githubusercontent.com",
            "audiences": ["api://AzureADTokenExchange"],
            "subject": f"repo:ai-platform-portfolio@334196300/fixture@42:environment:central-{phase}"}}
            for phase in ("plan", "apply")]
        github, azure, token = self.run_onboard(self.configured(), credentials)
        self.assertEqual(github.request.call_count, 1)
        self.assertEqual(azure.call_count, 1)
        self.assertEqual(token.call_args.kwargs, {"repository": "fixture", "permissions": {"administration": "write"}})

    def test_new_trust_has_immutable_ids_and_only_two_environments(self):
        _, azure, _ = self.run_onboard(self.configured(), [])
        puts = [call for call in azure.call_args_list if len(call.args) > 1]
        self.assertEqual(len(puts), 2)
        self.assertEqual([call.args[0] for call in puts],
                         ["/identity/federatedIdentityCredentials/github-42-plan",
                          "/identity/federatedIdentityCredentials/github-42-apply"])

    def test_wrong_repository_identity_and_capacity_fail_closed(self):
        actual = self.configured(); actual["id"] = 43
        with self.assertRaises(ValueError):
            self.run_onboard(actual, [])
        with self.assertRaises(ValueError):
            self.run_onboard(self.configured(), [{"name": str(i), "properties": {}} for i in range(19)])

    def test_private_repository_receives_no_azure_trust(self):
        actual = self.configured(); actual.update(private=True, visibility="private")
        _, azure, _ = self.run_onboard(actual, [])
        azure.assert_not_called()

    def test_conflicting_credential_is_not_overwritten(self):
        with self.assertRaises(ValueError):
            self.run_onboard(self.configured(), [{"name": "github-42-plan", "properties": {"subject": "other"}}])

    def test_settings_are_patched_and_verified_before_trust(self):
        from onboarding import SETTINGS_FILE
        import json
        actual = self.repository()
        verified = {**actual, **json.loads(SETTINGS_FILE.read_text())}
        github = Mock(); github.request.side_effect = [actual, verified, verified]
        azure = Mock(); azure.side_effect = lambda path, method="GET", data=None: {"value": []} if method == "GET" else data
        with patch.dict(os.environ, {"GITHUB_APP_ID": "1", "GITHUB_INSTALLATION_ID": "2", "FEDERATION_IDENTITY_ID": "/identity"}), patch("onboarding.installation_client", return_value=github):
            onboard("fixture", 42, "fixture-key", azure)
        self.assertEqual(github.request.call_args_list[1].args[1], "PATCH")
        self.assertEqual(github.request.call_args_list[2].args, ("repos/ai-platform-portfolio/fixture",))

    def test_incomplete_inventory_cannot_create_credentials(self):
        github = Mock(); github.request.return_value = self.configured()
        azure = Mock(return_value={"value": [], "nextLink": "https://example.invalid/next"})
        with patch.dict(os.environ, {"GITHUB_APP_ID": "1", "GITHUB_INSTALLATION_ID": "2", "FEDERATION_IDENTITY_ID": "/identity"}), patch("onboarding.installation_client", return_value=github):
            with self.assertRaises(ValueError):
                onboard("fixture", 42, "fixture-key", azure)
        self.assertEqual(azure.call_count, 1)

    def test_delete_matches_immutable_id_and_preserves_other_trust(self):
        from onboarding import remove_repository
        def credential(name, subject, issuer="https://token.actions.githubusercontent.com"):
            return {"name": name, "properties": {"subject": subject, "issuer": issuer,
                    "audiences": ["api://AzureADTokenExchange"]}}
        prefix = "repo:ai-platform-portfolio@334196300/"
        targeted = [credential("old-name-plan", prefix + "renamed@42:environment:central-plan"),
                    credential("old-name-apply", prefix + "fixture@42:environment:central-apply")]
        retained = [credential("recreated", prefix + "fixture@43:environment:central-plan"),
                    credential("other-env", prefix + "fixture@42:environment:other"),
                    credential("other-issuer", prefix + "fixture@42:environment:central-plan", "https://other.invalid"),
                    credential("other-org", "repo:other@334196300/fixture@42:environment:central-plan")]
        remaining = targeted + retained
        def request(path, method="GET", data=None):
            if method == "DELETE":
                remaining[:] = [item for item in remaining if item["name"] != path.rsplit("/", 1)[1]]
                return None
            return {"value": list(remaining)}
        azure = Mock(side_effect=request)
        with patch.dict(os.environ, {"FEDERATION_IDENTITY_ID": "/identity"}):
            remove_repository(42, azure)
            remove_repository(42, azure)
        self.assertEqual(remaining, retained)
        self.assertEqual(sum(call.args[1:] == ("DELETE",) for call in azure.call_args_list), 2)

    def test_delete_retries_until_verified_and_rejects_incomplete_inventory(self):
        from onboarding import remove_repository
        credential = {"name": "fixture", "properties": {
            "subject": "repo:ai-platform-portfolio@334196300/fixture@42:environment:central-plan",
            "issuer": "https://token.actions.githubusercontent.com", "audiences": ["api://AzureADTokenExchange"]}}
        with patch.dict(os.environ, {"FEDERATION_IDENTITY_ID": "/identity"}):
            with self.assertRaises(RuntimeError):
                remove_repository(42, Mock(return_value={"value": [credential]}))
            azure = Mock(return_value={"value": [], "nextLink": "next"})
            with self.assertRaises(ValueError):
                remove_repository(42, azure)
            self.assertEqual(azure.call_count, 1)
            for invalid in [True, 0, -1, "42"]:
                with self.assertRaises(ValueError):
                    remove_repository(invalid, Mock())
