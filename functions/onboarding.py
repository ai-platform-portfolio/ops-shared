"""Apply per-repository defaults and GitHub environment trust after signed delivery."""

import json
import os
from pathlib import Path
import re

from sync import installation_client

SETTINGS_FILE = Path(__file__).with_name("repository-settings.json")
ORGANIZATION = "ai-platform-portfolio"
OWNER_ID = "334196300"


def onboard(repository, repository_id, private_key, azure):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid repository name")
    github = installation_client(os.environ["GITHUB_APP_ID"], os.environ["GITHUB_INSTALLATION_ID"],
                                 private_key, repository=repository, permissions={"administration": "write"})
    path = f"repos/{ORGANIZATION}/{repository}"
    actual = github.request(path)
    if (actual["id"] != repository_id or actual["owner"]["login"] != ORGANIZATION
            or str(actual["owner"]["id"]) != OWNER_ID):
        raise ValueError("Repository identity does not match signed delivery")
    if actual["archived"]:
        return
    settings = json.loads(SETTINGS_FILE.read_text())
    changes = {key: value for key, value in settings.items() if actual.get(key) != value}
    if changes:
        github.request(path, "PATCH", changes)
        verified = github.request(path)
        if any(verified.get(key) != value for key, value in settings.items()):
            raise RuntimeError("Repository settings verification failed")
    # Preserve the existing public-only trust policy; private repos need explicit review.
    if actual.get("private") is not False or actual.get("visibility") != "public":
        return
    identity = os.environ["FEDERATION_IDENTITY_ID"]
    credentials_path = identity + "/federatedIdentityCredentials"
    inventory = azure(credentials_path)
    if inventory.get("nextLink"):
        raise ValueError("Incomplete federated credential inventory")
    credentials = inventory["value"]
    subjects = [f"repo:{ORGANIZATION}@{OWNER_ID}/{repository}@{repository_id}:environment:central-{phase}"
                for phase in ("plan", "apply")]
    missing = [subject for subject in subjects if not any(
        item["properties"].get("subject") == subject
        and item["properties"].get("issuer") == "https://token.actions.githubusercontent.com"
        and item["properties"].get("audiences") == ["api://AzureADTokenExchange"]
        for item in credentials)]
    if len(credentials) + len(missing) > 20:
        raise ValueError("Federated credential capacity exceeded; review identity design")
    for subject in missing:
        phase = subject.rsplit("-", 1)[1]
        name = f"github-{repository_id}-{phase}"
        if any(item["name"] == name for item in credentials):
            raise ValueError("Federated credential name already belongs to different trust")
        properties = {"issuer": "https://token.actions.githubusercontent.com",
                      "audiences": ["api://AzureADTokenExchange"], "subject": subject}
        result = azure(credentials_path + "/" + name, "PUT", {"properties": properties})
        if any(result["properties"].get(key) != value for key, value in properties.items()):
            raise RuntimeError("Federated credential verification failed")


def remove_repository(repository_id, azure):
    """Remove only this immutable repository ID's GitHub environment credentials."""
    if type(repository_id) is not int or repository_id <= 0:
        raise ValueError("Invalid deleted repository ID")
    path = os.environ["FEDERATION_IDENTITY_ID"] + "/federatedIdentityCredentials"
    pattern = re.compile(
        rf"repo:{ORGANIZATION}@{OWNER_ID}/[A-Za-z0-9_.-]+@{repository_id}:environment:central-(plan|apply)"
    )

    def matches(item):
        properties = item["properties"]
        return (pattern.fullmatch(properties.get("subject", "")) is not None
                and properties.get("issuer") == "https://token.actions.githubusercontent.com"
                and properties.get("audiences") == ["api://AzureADTokenExchange"])

    def inventory():
        result = azure(path)
        if result.get("nextLink"):
            raise ValueError("Incomplete federated credential inventory")
        return result["value"]

    for item in inventory():
        if matches(item):
            name = item["name"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise ValueError("Invalid credential name")
            azure(path + "/" + name, "DELETE")
    if any(matches(item) for item in inventory()):
        raise RuntimeError("Repository credential deletion is not complete; retry required")


def azure_request(path, method="GET", data=None):
    from azure.identity import ManagedIdentityCredential
    import requests

    with ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"]) as credential:
        token = credential.get_token("https://management.azure.com/.default").token
    response = requests.request(method, "https://management.azure.com" + path,
                                params={"api-version": "2023-01-31"}, json=data,
                                headers={"Authorization": "Bearer " + token}, timeout=30)
    if method == "DELETE" and (response.ok or response.status_code == 404):
        return None
    if not response.ok:
        raise RuntimeError(f"Azure federation request failed ({response.status_code})")
    return response.json()
