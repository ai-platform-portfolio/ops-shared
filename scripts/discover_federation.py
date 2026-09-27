"""Resolve the org's opt-in repository inventory into Terraform inputs."""

import json
import os
from pathlib import Path
import subprocess

from federation_contract import validate_repository
from settings import relative_path


def discover(policy, configured, fetch):
    if not isinstance(policy.get("enabled", False), bool):
        raise ValueError("Onboarding enabled must be a boolean")
    if policy.get("enabled", False) is not True:
        return configured
    organization = policy["organization"]
    if organization != "ai-platform-portfolio":
        raise ValueError("Automatic enrollment is scoped to ai-platform-portfolio")
    result = dict(configured)
    for page in range(1, 101):
        batch = fetch(f"orgs/{organization}/repos?type=public&per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("Repository discovery failed")
        for actual in batch:
            if actual["owner"]["login"] != organization:
                raise ValueError("Repository belongs to another organization")
            name = actual["name"]
            entry = result.get(name, {
                "owner": organization, "owner_id": str(actual["owner"]["id"]),
                "name": name, "repository_id": str(actual["id"]),
                "apply_environment": policy["apply_environment"],
            })
            validate_repository(entry, actual, planning=False)
            result[name] = entry
        if len(batch) < 100:
            break
    else:
        raise ValueError("Repository pagination limit exceeded")
    if sum(2 if repo.get("plan_environment") else 1 for repo in result.values()) > 20:
        raise ValueError("Federation capacity exceeded; review identity design before adding trust")
    return result


def main():
    if not os.environ.get("TF_ONBOARDING_FILE"):
        return
    path = Path(relative_path("TF_FEDERATION_FILE", "ci/github.auto.tfvars.json"))
    configured = json.loads(path.read_text())["github_repositories"]
    policy_path = Path(relative_path("TF_ONBOARDING_FILE", "ci/onboarding.json"))
    policy = json.loads(policy_path.read_text())
    resolved = discover(policy, configured, lambda endpoint: json.loads(
        subprocess.check_output(["gh", "api", endpoint], text=True)))
    # Runner-local expansion; the reviewed source remains the opt-in policy.
    path.write_text(json.dumps({"github_repositories": resolved}, indent=2) + "\n")
    print(f"Federation inventory resolved for {len(resolved)} repositories")


if __name__ == "__main__":
    main()
