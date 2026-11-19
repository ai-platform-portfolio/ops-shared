"""Execute preview freshness, identity, cleanup and literal input boundaries."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/terraform-preview.yml").read_text())


def step(name):
    return next(item for item in WORKFLOW["jobs"]["preview"]["steps"] if item.get("name") == name)


class PreviewTests(unittest.TestCase):
    def intent(self, state="open", head="current", event="pull_request", repository_id="123", number=7, missing=False, fork=False):
        script = step("Resolve current PR intent")["with"]["script"]
        harness = """
          const outputs = {};
          const core = {setOutput: (key, value) => {outputs[key] = value;}, notice: () => {}};
          const context = {repo: {owner: 'example', repo: 'site'}, issue: {number: NUMBER}, eventName: EVENT,
            payload: {pull_request: {head: {sha: 'current'}, base: {sha: 'trusted-base'}}}};
          const github = {rest: {
            pulls: {get: async () => ({data: {number: NUMBER, state: STATE, head: {sha: HEAD, repo: {full_name: REPOSITORY}}}})},
            repos: {getContent: async ({ref}) => {if (ref !== 'trusted-base') throw Error('Untrusted ref'); if (MISSING) throw {status: 404};}}
          }};
        """
        for key, value in {"NUMBER": number, "EVENT": event, "STATE": state, "HEAD": head,
                           "REPOSITORY": "attacker/fork" if fork else "example/site", "MISSING": missing}.items():
            harness = harness.replace(key, json.dumps(value))
        harness += f"(async () => {{ {script}; process.stdout.write(JSON.stringify(outputs)); }})().catch(e => {{console.error(e); process.exit(1);}});"
        result = subprocess.run(["node", "-e", harness], env={**os.environ, "GITHUB_REPOSITORY": "example/site", "GITHUB_REPOSITORY_ID": repository_id, "TF_ROOT": "infra/preview", "IMAGE": "example.azurecr.io/site@sha256:" + "a" * 64}, capture_output=True, text=True)
        if fork:
            self.assertNotEqual(result.returncode, 0)
            return {}
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_freshness_close_reopen_and_trusted_bootstrap(self):
        self.assertEqual(self.intent()["operation"], "apply")
        self.assertEqual(self.intent(head="newer")["operation"], "skip")
        # Old builds reconcile closed PRs as destroy, including after cleanup ran.
        self.assertEqual(self.intent(state="closed", head="newer")["operation"], "destroy")
        self.assertEqual(self.intent(state="closed", event="pull_request_target")["operation"], "destroy")
        self.assertEqual(self.intent(event="pull_request_target")["operation"], "skip")
        self.assertEqual(self.intent(missing=True)["operation"], "skip")
        self.intent(fork=True)

    def test_identities_are_stable_and_separate_across_repositories_and_prs(self):
        first = self.intent()
        self.assertEqual(first, self.intent())
        others = [self.intent(number=8), self.intent(repository_id="456")]
        self.assertEqual(len({first["state"], *(item["state"] for item in others)}), 3)
        self.assertRegex(first["name"], r"^pv-[a-f0-9]{12}-pr-7$")
        self.assertEqual(first["ref"], "trusted-base")
        self.assertFalse(WORKFLOW["concurrency"]["cancel-in-progress"])
        self.assertEqual(WORKFLOW["concurrency"]["queue"], "max")

    def test_second_freshness_check_converts_closed_pr_to_cleanup(self):
        script = step("Recheck PR before mutation")["with"]["script"]
        harness = """
          const context = {repo: {}, issue: {number: 7}, eventName: 'pull_request', payload: {pull_request: {head: {sha: 'old'}}}};
          const core = {setOutput: (key, value) => process.stdout.write(value)};
          const github = {rest: {pulls: {get: async () => ({data: {state: 'closed', head: {sha: 'new'}}})}}};
        """ + f"(async () => {{ {script} }})();"
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "destroy")

    def test_saved_destroy_and_literal_variables_with_failure_propagation(self):
        script = step("Apply or destroy preview")["run"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "tofu"
            executable.write_text('#!/bin/bash\nprintf "%s\\n" "$@" >> "$RUNNER_TEMP/args"\nif [[ "$2" == plan ]]; then exit "${PLAN_EXIT:-0}"; fi\nexit "${APPLY_EXIT:-0}"\n')
            executable.chmod(0o755)
            env = {**os.environ, "PATH": f"{root}:{os.environ['PATH']}", "RUNNER_TEMP": directory,
                   "TF_ROOT": "root with spaces", "PREVIEW_NAME": "pv-123456789abc-pr-7", "IMAGE": "",
                   "OPERATION": "destroy", "VARIABLES": json.dumps({"literal": "$(touch compromised)"})}
            result = subprocess.run(["bash", "-c", script], env=env, cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = (root / "args").read_text()
            self.assertIn("-destroy", args)
            self.assertIn(f"{directory}/preview.tfplan", args)
            self.assertFalse((root / "compromised").exists())
            values = json.loads((root / "preview.tfvars.json").read_text())
            self.assertEqual(values["preview_name"], env["PREVIEW_NAME"])
            self.assertTrue(values["image"].startswith("example.invalid/unused@sha256:"))

            for failure in ("PLAN_EXIT", "APPLY_EXIT"):
                (root / "args").unlink()
                result = subprocess.run(["bash", "-c", script], env={**env, failure: "1"}, cwd=root, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                if failure == "PLAN_EXIT":
                    self.assertNotIn("\napply\n", (root / "args").read_text())

            result = subprocess.run(["bash", "-c", script], env={**env, "VARIABLES": '{"preview_name":"production"}'}, capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_closed_preview_is_not_advertised_and_cleanup_retires_deployments(self):
        script = step("Report preview outcome")["with"]["script"]
        harness = """
          const statuses = [], comments = [];
          const context = {repo: {}, issue: {number: 7}, payload: {pull_request: {head: {sha: 'current'}}}};
          const core = {summary: {addRaw: () => core.summary, write: async () => {}}};
          const github = {
            paginate: async (method) => method === github.rest.repos.listDeployments ? [{id: 1}, {id: 2}] : [],
            rest: {
              pulls: {get: async () => ({data: {state: 'closed', number: 7, head: {sha: 'current'}}})},
              repos: {createDeploymentStatus: async data => statuses.push(data), listDeployments: () => {}},
              issues: {listComments: () => {}, createComment: async data => comments.push(data)}
            }
          };
        """ + f"async function report() {{ {script} }}\n(async () => {{ await report(); process.stdout.write(JSON.stringify({{statuses, comments}})); }})();"
        env = {**os.environ, "RESULT": "success", "PREVIEW_NAME": "pv-example", "PREVIEW_URL": "https://preview.example", "DEPLOYMENT_ID": "3"}
        for operation in ("apply", "destroy"):
            result = subprocess.run(["node", "-e", harness], env={**env, "OPERATION": operation}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertTrue(all(item["state"] == "inactive" for item in data["statuses"]))
            if operation == "apply":
                self.assertEqual(data["comments"], [])
            else:
                self.assertEqual(len(data["statuses"]), 3)
                self.assertIn("Preview removed", data["comments"][0]["body"])


if __name__ == "__main__":
    unittest.main()
