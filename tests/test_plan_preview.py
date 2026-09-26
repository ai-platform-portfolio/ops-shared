import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from plan_preview import MARKER, comment_body, preview, publish, redact


class PlanPreviewTest(unittest.TestCase):
    def test_plan_values_are_redacted_but_changes_remain(self):
        plan = {
            "outputs": {"ids": {"type": ["map", "string"]}},
            "resource_changes": [
                {
                    "type": "azurerm_key_vault",
                    "change": {"after": {"name": "private-vault"}},
                },
                {
                    "type": "azurerm_storage_account",
                    "change": {"before": {"name": "private-storage"}},
                },
            ]
        }
        text = "private-vault private-storage secret-value 11111111-1111-1111-1111-111111111111\nPlan: 0 to add, 1 to change, 0 to destroy."
        result = redact(text, ["secret-value"], plan)
        for value in ("private-vault", "private-storage", "secret-value", "11111111"):
            self.assertNotIn(value, result)
        self.assertIn("1 to change", result)
        body = comment_body("abc123", "ok", result)
        self.assertIn("<details><summary>Show plan</summary>", body)
        self.assertIn("commit `abc123`", body)

    @patch.dict(
        "os.environ",
        TF_STATE_RESOURCE_GROUP="rg",
        TF_STATE_STORAGE_ACCOUNT="sa",
        TF_STATE_CONTAINER="state",
    )
    @patch("plan_preview.subprocess.run")
    @patch("plan_preview.verify")
    def test_authentication_failure_has_a_safe_failure_comment(self, verify, run):
        run.return_value = subprocess.CompletedProcess(
            [], 1, "", "AADSTS700213 private details"
        )
        status, text = preview(Path("/tmp/test.tfplan"))
        self.assertEqual(status, "failed")
        self.assertIn("AADSTS700213", text)
        self.assertNotIn("private details", text)
        run.assert_called_once()

    @patch("plan_preview.api")
    def test_rerun_updates_bot_comment_and_ignores_forged_marker(self, api):
        forged = {"id": 4, "user": {"login": "someone"}, "body": MARKER}
        owned = {"id": 5, "user": {"login": "github-actions[bot]"}, "body": MARKER}
        for comments, method, path in [
            ([forged], "POST", "issues/3/comments"),
            ([forged, owned], "PATCH", "issues/comments/5"),
        ]:
            api.reset_mock()
            api.side_effect = [{"head": {"sha": "current"}}, [comments], {}]
            publish(3, "current", "report")
            self.assertEqual(api.call_args.args, (path, method, {"body": "report"}))

    @patch("plan_preview.api", return_value={"head": {"sha": "new"}})
    def test_old_run_cannot_overwrite_new_revision(self, api):
        publish(3, "old", "report")
        api.assert_called_once_with("pulls/3")
