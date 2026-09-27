import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import Mock, patch

import azure.functions as func
import function_app


class HttpSecurityTest(unittest.TestCase):
    def test_real_http_handler_rejects_forgery_without_enqueueing(self):
        body = json.dumps({"organization": {"login": "portfolio"}, "installation": {"id": 1}}).encode()
        secret = "fixture-not-a-real-credential"
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        handler = function_app.webhook.build().get_user_function()
        cases = [(body, "", "repository", 403),
                 (body, "sha256=" + "0" * 64, "repository", 403),
                 (body + b" ", signature, "repository", 403),
                 (body, signature, "push", 403),
                 (body, signature, "repository", 202)]
        with patch.dict(os.environ, {"GITHUB_ORGANIZATION": "portfolio", "GITHUB_INSTALLATION_ID": "1"}), \
                patch.object(function_app, "secret", return_value=secret):
            for payload, header, event, status in cases:
                with self.subTest(status=status, event=event, signature=bool(header)):
                    queue = Mock()
                    request = func.HttpRequest(method="POST", url="https://fixture.invalid/api/github", body=payload,
                                               headers={"X-Hub-Signature-256": header, "X-GitHub-Event": event})
                    self.assertEqual(handler(request, queue).status_code, status)
                    if status == 202:
                        queue.set.assert_called_once_with("reconcile")
                    else:
                        queue.set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
