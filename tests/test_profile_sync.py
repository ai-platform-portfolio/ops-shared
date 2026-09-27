import base64
import hashlib
import hmac
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "functions/profile_sync"))
from sync import START, END, GitHub, accept_event, catalogue, public_repositories, reconcile, request_onboarding, update_readme


class ProfileSyncTest(unittest.TestCase):
    def test_onboarding_dispatch_contains_no_webhook_content_or_apply_request(self):
        github = MagicMock()
        request_onboarding("ai-platform-portfolio", github)
        github.request.assert_called_once_with("repos/ai-platform-portfolio/ops-shared/dispatches", "POST", {
            "event_type": "repository-onboarding",
        })
        with self.assertRaises(ValueError):
            request_onboarding("downstream", github)
        response = MagicMock()
        response.__enter__.return_value.status = 204
        with patch("sync.urlopen", return_value=response):
            self.assertIsNone(GitHub("fixture").request("repos/fixture/dispatches", "POST", {}))

    def test_only_signed_events_for_the_configured_installation_are_accepted(self):
        body = json.dumps({"organization": {"login": "portfolio"}, "installation": {"id": 1}}).encode()
        secret = "fixture-only-not-a-real-secret"
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(accept_event(body, signature, "repository", secret, "portfolio", 1))
        for change in [(body + b" ", signature, "repository", secret, "portfolio", 1),
                       (body, signature, "repository", secret, "another-org", 1),
                       (body, signature, "repository", secret, "portfolio", 2),
                       (body, "", "repository", secret, "portfolio", 1),
                       (body, signature, "push", secret, "portfolio", 1)]:
            self.assertFalse(accept_event(*change))

    def test_private_repositories_are_excluded_and_public_descriptions_are_text(self):
        repos = [
            {"name": "private-project", "private": True, "visibility": "private", "description": "private text"},
            {"name": "public-project", "private": False, "visibility": "public", "description": "[click](https://example.com) | <script>\ntext"},
        ]
        rendered = catalogue(repos, "portfolio")
        self.assertNotIn("private", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("[click]", rendered)
        self.assertIn("&#124;", rendered)

    def test_reconcile_preserves_handwritten_text_and_duplicate_deliveries_are_noops(self):
        class Repository:
            text = "Intro\n" + START + "\nOld catalogue\n" + END + "\nFooter"
            writes = 0

            def request(self, path, method="GET", data=None):
                if method == "GET":
                    return {"sha": "expected-sha", "content": base64.b64encode(self.text.encode()).decode()}
                if data["sha"] != "expected-sha" or data["branch"] != "main":
                    raise AssertionError("Unsafe write")
                self.text = base64.b64decode(data["content"]).decode()
                self.writes += 1

        repository = Repository()
        self.assertTrue(reconcile("portfolio", repository, lambda path: []))
        self.assertFalse(reconcile("portfolio", repository, lambda path: []))
        self.assertEqual(repository.writes, 1)
        self.assertTrue(repository.text.startswith("Intro\n"))
        self.assertTrue(repository.text.endswith("\nFooter"))

    def test_invalid_markers_and_incomplete_api_reads_cannot_publish(self):
        for text in ["no markers", END + START, START + END + START]:
            with self.assertRaises(ValueError):
                update_readme(text, "replacement")
        with self.assertRaises(ValueError):
            public_repositories("portfolio", lambda path: {"error": "unavailable"})
        calls = []

        def fetch(path):
            calls.append(path)
            return [{}] * 100 if len(calls) == 1 else [{"name": "last"}]

        self.assertEqual(len(public_repositories("portfolio", fetch)), 101)
        self.assertIn("type=public", calls[0])
        self.assertIn("page=2", calls[1])


if __name__ == "__main__":
    unittest.main()
