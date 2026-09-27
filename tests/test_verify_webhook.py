from unittest import TestCase
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from verify_webhook import verify


class VerifyWebhookTest(TestCase):
    def test_unsigned_success_fails_deployment(self):
        with patch("verify_webhook.urlopen", return_value=MagicMock()):
            with self.assertRaisesRegex(RuntimeError, "accepted an unsigned"):
                verify("https://fixture.invalid/api/github", attempts=1)

    def test_transient_startup_retries_but_requires_explicit_rejection(self):
        with patch("verify_webhook.time.sleep"), patch("verify_webhook.urlopen", side_effect=[
            HTTPError("", 503, "", {}, None), HTTPError("", 403, "", {}, None),
        ]) as request:
            verify("https://fixture.invalid/api/github", attempts=2)
            self.assertEqual(request.call_count, 2)

    def test_missing_endpoint_cannot_pass(self):
        with patch("verify_webhook.urlopen", side_effect=HTTPError("", 404, "", {}, None)):
            with self.assertRaisesRegex(RuntimeError, "unverified"):
                verify("https://fixture.invalid/api/github", attempts=1)
