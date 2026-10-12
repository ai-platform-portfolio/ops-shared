"""Verify deployment approval controls and GitHub OIDC before publishing."""

import json
import base64
import os
import subprocess

from federation_contract import verify as verify_federation


def api(path):
    return json.loads(subprocess.check_output(['gh', 'api', path], text=True))


def verify_environment(environment):
    reviewers = [rule for rule in environment["protection_rules"] if rule["type"] == "required_reviewers"]
    if (len(reviewers) != 1
            or [item["reviewer"].get("login") for item in reviewers[0]["reviewers"]] != ["michaelalinks"]
            or environment.get("can_admins_bypass") is not False
            or not (environment.get("deployment_branch_policy") or {}).get("custom_branch_policies")):
        raise ValueError("central-apply must require the owner, forbid bypass and restrict deployment branches")


if __name__ == "__main__":
    repository = os.environ["GITHUB_REPOSITORY"]
    if repository != "ai-platform-portfolio/ops-shared" or os.environ["GITHUB_REF"] != "refs/heads/main":
        raise ValueError("Function deployment must run from ops-shared main")
    # GitHub enforces main and environment approval before publication.
    # Read the public environment metadata, not the admin-only branch-policy API.
    verify_environment(api(f"repos/{repository}/environments/central-apply"))
    contract = api("repos/ai-platform-portfolio/platform-iac/contents/terraform/ci/github.auto.tfvars.json?ref=main")
    repositories = json.loads(base64.b64decode(contract["content"]))["github_repositories"]
    os.environ["TF_PHASE"] = "apply"
    verify_federation(lambda name: api(f"repos/{name}"), repositories)
