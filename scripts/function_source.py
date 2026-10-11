"""Resolve a reviewed, immutable Function package without shell interpolation."""

import json
import base64
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

from federation_contract import verify as verify_federation


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def approval_controls(environment, branches, owner):
    reviewers = [rule for rule in environment['protection_rules'] if rule['type'] == 'required_reviewers']
    policies = branches['branch_policies']
    if (environment.get('can_admins_bypass') is not False
            or len(reviewers) != 1
            or [item['reviewer'].get('login') for item in reviewers[0]['reviewers']] != [owner]
            or environment.get('deployment_branch_policy', {}).get('custom_branch_policies') is not True
            or len(policies) != 1
            or policies[0]['name'] != 'main'
            or policies[0]['type'] != 'branch'):
        raise ValueError('Apply environment must require the named owner, forbid bypass and allow main only')



def source(config, application):
    app = config["function_apps"][application]
    if app["source"].get("format") != "zip":
        raise ValueError("Flex Consumption requires a ZIP package; custom images are unsupported")
    result = {"name": app["name"], **app["source"]}
    if not re.fullmatch(r"[a-z0-9-]+", result["name"]):
        raise ValueError("Invalid Function name")
    path = PurePosixPath(result["path"])
    if path.is_absolute() or ".." in path.parts or not re.fullmatch(r"[A-Za-z0-9_./-]+", str(path)):
        raise ValueError("Invalid Function source path")
    return result


if __name__ == "__main__":
    repository = os.environ["GITHUB_REPOSITORY"]
    if repository != "ai-platform-portfolio/ops-shared" or os.environ["GITHUB_REF"] != "refs/heads/main":
        raise ValueError("Function deployment must run from ops-shared main")
    prefix = f"repos/{repository}/environments/central-apply"
    approval_controls(api(prefix), api(prefix + "/deployment-branch-policies"), "michaelalinks")
    contract = api("repos/ai-platform-portfolio/terraform-modules/contents/ci/github.auto.tfvars.json?ref=main")
    repositories = json.loads(base64.b64decode(contract["content"]))["github_repositories"]
    os.environ["TF_PHASE"] = "apply"
    verify_federation(lambda name: api(f"repos/{name}"), repositories)
    config = json.loads(Path("functions/apps.json").read_text())
    resolved = source(config, os.environ["APPLICATION"])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write("".join(f"{key}={value}\n" for key, value in resolved.items()))
