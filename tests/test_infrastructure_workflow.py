import os
from pathlib import Path
import subprocess
import textwrap
import unittest


class InfrastructureWorkflowTest(unittest.TestCase):
    def test_operation_guard_rejects_untrusted_events_before_checkout(self):
        workflow = Path(".github/workflows/terraform.yml").read_text()
        guard = textwrap.dedent(workflow.split("        run: |\n", 1)[1].split("      - uses:", 1)[0])
        cases = [
            ("preview", "pull_request", "org/repo", "refs/pull/1/merge", True),
            ("preview", "pull_request", "other/repo", "refs/pull/1/merge", False),
            ("preview", "push", "org/repo", "refs/heads/main", False),
            ("deploy", "push", "", "refs/heads/main", True),
            ("deploy", "push", "", "refs/heads/feature", False),
            ("plan-only", "workflow_dispatch", "", "refs/heads/main", True),
            ("invalid", "push", "", "refs/heads/main", False),
        ]
        for operation, event, head, ref, allowed in cases:
            with self.subTest(operation=operation, event=event, head=head, ref=ref):
                result = subprocess.run(["bash", "-e", "-c", guard], env={
                    **os.environ, "OPERATION": operation, "EVENT": event,
                    "HEAD_REPOSITORY": head, "GITHUB_REPOSITORY": "org/repo",
                    "GITHUB_REF": ref,
                }, capture_output=True)
                self.assertEqual(result.returncode == 0, allowed)
