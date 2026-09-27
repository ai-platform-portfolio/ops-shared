"""Compare declared federation inputs with GitHub metadata and the planning token."""

import base64
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen
from settings import relative_path


def validate_repository(repo, actual, *, planning=True):
    keys = [
        "owner",
        "owner_id",
        "name",
        "repository_id",
        "apply_environment",
    ]
    if planning or repo.get("plan_environment") is not None:
        keys.append("plan_environment")
    for key in keys:
        if not isinstance(repo.get(key), str) or not repo[key].strip():
            raise ValueError(f"Federation field is missing or empty: {key}")
    if repo.get("plan_environment") == repo["apply_environment"]:
        raise ValueError("Planning and apply environments must be distinct")
    if not all(
        re.fullmatch(r"[1-9][0-9]*", repo[key]) for key in ("owner_id", "repository_id")
    ):
        raise ValueError("Federation IDs must be positive integers")
    if (
        str(actual["id"]) != repo["repository_id"]
        or str(actual["owner"]["id"]) != repo["owner_id"]
        or actual["full_name"] != f"{repo['owner']}/{repo['name']}"
    ):
        raise ValueError("Configured repository identity does not match GitHub")


def validate_claims(repo, claims):
    phase = os.environ.get("TF_PHASE", "plan")
    if phase not in {"plan", "apply"}:
        raise ValueError("Invalid federation phase")
    expected = f"repo:{repo['owner']}@{repo['owner_id']}/{repo['name']}@{repo['repository_id']}:environment:{repo[phase + '_environment']}"
    if (
        claims.get("iss") != "https://token.actions.githubusercontent.com"
        or claims.get("aud") != "api://AzureADTokenExchange"
        or claims.get("sub") != expected
    ):
        raise ValueError(
            "Actual GitHub planning token does not match the proposed federation contract"
        )


def verify(api, repositories=None):
    if repositories is None:
        repositories = json.loads(Path(relative_path("TF_FEDERATION_FILE", "ci/github.auto.tfvars.json")).read_text())["github_repositories"]
    current = None
    for repo in repositories.values():
        name = f"{repo.get('owner', '')}/{repo.get('name', '')}"
        validate_repository(repo, api(name), planning=name == os.environ["GITHUB_REPOSITORY"] and os.environ.get("TF_PHASE", "plan") == "plan")
        if name == os.environ["GITHUB_REPOSITORY"]:
            current = repo
    if current is None:
        raise ValueError("The current repository has no federation entry")
    request = Request(
        os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
        + "&audience=api%3A%2F%2FAzureADTokenExchange",
        headers={
            "Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]
        },
    )
    with urlopen(request, timeout=15) as response:
        token = json.load(response)["value"]
    payload = token.split(".")[1]
    validate_claims(
        current,
        json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))),
    )


def verify_planning_contract():
    verify(
        lambda name: json.loads(subprocess.check_output(["gh", "api", f"repos/{name}"]))
    )
    print("Planning federation contract verified")


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
