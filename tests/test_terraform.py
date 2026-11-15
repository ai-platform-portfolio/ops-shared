"""Exercise variable handoff, plan failure handling and publication redaction."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/terraform.yml").read_text())


class TerraformTests(unittest.TestCase):
    def step(self, name):
        return next(step for step in WORKFLOW["jobs"]["plan"]["steps"] if step.get("name") == name)

    def test_plan_inputs_are_literal_and_failure_is_not_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            terraform = root / "terraform"
            terraform.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$RUNNER_TEMP/args"\necho "private plan output"\nexit "${TEST_EXIT:-0}"\n')
            terraform.chmod(0o755)
            variables = {"image": "registry/image@sha256:abc", "literal": "$(touch should-not-exist)"}
            env = {
                **os.environ, "PATH": f"{root}:{os.environ['PATH']}",
                "RUNNER_TEMP": directory, "TF_ROOT": "root with spaces",
                "VARS_FILE": "values with spaces.tfvars", "VARIABLES": json.dumps(variables),
            }
            result = subprocess.run(["bash", "-c", self.step("Plan")["run"]], env=env, cwd=root, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((root / "inputs.tfvars.json").read_text()), variables)
            self.assertFalse((root / "should-not-exist").exists())
            self.assertIn("-var-file=values with spaces.tfvars", (root / "args").read_text().splitlines())
            self.assertEqual(result.stdout, b"")

            failed = subprocess.run(["bash", "-c", self.step("Plan")["run"]], env={**env, "TEST_EXIT": "1"}, capture_output=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(failed.stdout, b"")

    def test_public_summary_redacts_identifiers_and_backend_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_id = "11111111-2222-3333-4444-555555555555"
            (root / "plan.txt").write_text(f"subscription={private_id} backend=private-backend-name ```")
            script = self.step("Publish redacted plan summary")["with"]["script"]
            harness = (
                "const core = {summary: {addRaw: body => {process.stdout.write(body); return core.summary;}, write: async () => {}}};\n"
                "const context = {eventName: 'workflow_dispatch'};\n"
                f"(async () => {{\n{script}\n}})().catch(e => {{console.error(e); process.exit(1);}});"
            )
            result = subprocess.run(["node", "-e", harness], env={**os.environ, "RUNNER_TEMP": directory, "PLAN_RESULT": "success", "TF_STATE_STORAGE_ACCOUNT": "private-backend-name"}, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(private_id, result.stdout)
            self.assertNotIn("private-backend-name", result.stdout)
            self.assertIn("[redacted-id]", result.stdout)


if __name__ == "__main__":
    unittest.main()
