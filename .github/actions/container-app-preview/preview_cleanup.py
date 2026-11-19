"""Nightly fallback: remove owned apps only when their public PR is closed."""

import json
import logging
import os
import re
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


API_VERSION = "2025-07-01"


def request(url, method="GET", token=None):
    headers = {"Accept": "application/json", "User-Agent": "container-app-preview-cleanup"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers, method=method), timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else None


def sweep(subscription, group, owner, credential, dry_run=False):
    base = (
        "https://management.azure.com/subscriptions/"
        f"{quote(subscription, safe='')}/resourceGroups/{quote(group, safe='')}"
        "/providers/Microsoft.App/containerApps"
    )
    url = f"{base}?api-version={API_VERSION}"
    deleted = 0
    while url:
        if not url.startswith(base + "?"):
            raise ValueError("Unexpected ARM pagination URL")
        page = request(url, token=credential.get_token("https://management.azure.com/.default").token)
        for app in page["value"]:
            tags = app.get("tags") or {}
            repo, number = tags.get("previewRepositoryId", ""), tags.get("previewPullRequest", "")
            if not all(re.fullmatch(r"[1-9][0-9]{0,9}", value) for value in (repo, number)):
                continue
            name = f"pv-{repo}-pr-{number}"
            if app["name"] != name:
                continue
            try:
                # Public API reads need no GitHub credential. A 404, rate limit
                # or network failure never counts as evidence that a PR closed.
                pr = request(f"https://api.github.com/repositories/{repo}/pulls/{number}")
                repository = pr["base"]["repo"]
                if (pr["state"] != "closed" or pr["number"] != int(number) or str(repository["id"]) != repo
                        or repository["owner"]["login"].casefold() != owner.casefold()):
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                logging.warning("Skipping %s: PR state could not be verified", name)
                continue
            if not dry_run:
                try:
                    request(f"{base}/{name}?api-version={API_VERSION}", method="DELETE",
                            token=credential.get_token("https://management.azure.com/.default").token)
                except HTTPError as error:
                    if error.code != 404:
                        raise
            logging.info("%s %s", "Would delete" if dry_run else "Deletion requested for", name)
            deleted += 1
        url = page.get("nextLink")
    return deleted


if __name__ == "__main__":
    from azure.identity import ManagedIdentityCredential

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    with ManagedIdentityCredential(client_id=os.environ["AZURE_CLIENT_ID"]) as credential:
        count = sweep(os.environ["AZURE_SUBSCRIPTION_ID"], os.environ["PREVIEW_RESOURCE_GROUP"],
                      os.environ["PREVIEW_GITHUB_OWNER"], credential,
                      dry_run=os.environ.get("PREVIEW_CLEANUP_DRY_RUN") == "true")
    logging.info("Finished: %d closed-PR previews selected", count)
