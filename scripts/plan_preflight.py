"""Read-only local checks for live planning prerequisites; never print credentials."""

import json
import subprocess
from pathlib import Path


def problems(repo, environment, credentials):
    errors = []
    if environment.get("deployment_branch_policy") is not None:
        errors.append(
            "Planning environment restricts branches; PR and main plans need access."
        )
    if any(
        rule.get("type") != "branch_policy"
        for rule in environment.get("protection_rules", [])
    ):
        errors.append(
            "Planning environment has protection rules; plans must start without approval or delay."
        )
    subject = (
        f"repo:{repo['owner']}@{repo['owner_id']}/{repo['name']}@{repo['repository_id']}"
        f":environment:{repo['plan_environment']}"
    )
    if not any(
        item.get("subject") == subject
        and item.get("issuer") == "https://token.actions.githubusercontent.com"
        and item.get("audiences") == ["api://AzureADTokenExchange"]
        for item in credentials
    ):
        errors.append("Azure lacks the matching planning environment credential.")
    return errors


def read_json(command):
    return json.loads(
        subprocess.check_output(command, text=True, stderr=subprocess.PIPE)
    )


def main():
    repo = json.loads(Path("ci/github.auto.tfvars.json").read_text())[
        "github_repositories"
    ]["ops-shared"]
    try:
        environment = read_json(
            [
                "gh",
                "api",
                f"repos/{repo['owner']}/{repo['name']}/environments/{repo['plan_environment']}",
            ]
        )
        client_id = read_json(
            ["terraform", "-chdir=ci", "output", "-json", "deployment_client_id"]
        )
        identities = read_json(["az", "identity", "list", "--output", "json"])
        matches = [item for item in identities if item.get("clientId") == client_id]
        if len(matches) != 1:
            raise ValueError("Deployment identity not found in the active subscription")
        identity = matches[0]
        credentials = read_json(
            [
                "az",
                "identity",
                "federated-credential",
                "list",
                "--resource-group",
                identity["resourceGroup"],
                "--identity-name",
                identity["name"],
                "--output",
                "json",
            ]
        )
    except (OSError, subprocess.CalledProcessError, ValueError):
        print(
            "Preflight could not read deployment configuration. Check the initialized ci backend, gh and az login, and active Azure subscription."
        )
        return 1
    errors = problems(repo, environment, credentials)
    for error in errors:
        print(f"FAIL: {error}")
    if not errors:
        print(
            "Live planning prerequisites pass; GitHub CI must still verify actual OIDC exchange and backend access."
        )
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
