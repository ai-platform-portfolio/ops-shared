"""Compare declared federation inputs with GitHub metadata and the planning token."""

import base64
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen
from settings import relative_path


def validate_repository(repo, actual):
    for key in (
        "owner",
        "owner_id",
        "name",
        "repository_id",
        "plan_environment",
        "apply_environment",
    ):
        if not isinstance(repo.get(key), str) or not repo[key].strip():
            raise ValueError(f"Federation field is missing or empty: {key}")
    if repo["plan_environment"] == repo["apply_environment"]:
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


def verify(api):
    repositories = json.loads(Path(relative_path("TF_FEDERATION_FILE", "ci/github.auto.tfvars.json")).read_text())[
        "github_repositories"
    ]
    current = None
    for repo in repositories.values():
        name = f"{repo.get('owner', '')}/{repo.get('name', '')}"
        validate_repository(repo, api(name))
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


if __name__ == "__main__":
    verify(
        lambda name: json.loads(subprocess.check_output(["gh", "api", f"repos/{name}"]))
    )
    print("Planning federation contract verified")
