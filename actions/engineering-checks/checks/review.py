"""Authorize policy adoption from a trusted owner's review of the checked-out head."""

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from checks.source import git


def owners_at_base(root: Path, base: str) -> set[str]:
    lines = [
        line.split("#", 1)[0].split()
        for line in git(root, "show", f"{base}:.github/CODEOWNERS").splitlines()
        if line.split("#", 1)[0].strip()
    ]
    if len(lines) != 1 or lines[0][0] != "*" or len(lines[0]) < 2:
        raise ValueError("Policy adoption requires one global CODEOWNERS rule in the base revision")
    if any(not re.fullmatch(r"@[A-Za-z0-9-]+", owner) for owner in lines[0][1:]):
        raise ValueError("Policy adoption currently supports individual GitHub owners, not teams")
    return {owner[1:].lower() for owner in lines[0][1:]}


def approved_owner(reviews: list[dict[str, Any]], head: str, owners: set[str]) -> str | None:
    latest: dict[str, dict[str, Any]] = {}
    for review in sorted(reviews, key=lambda item: item["id"]):
        login = review["user"]["login"].lower()
        if login in owners and review["user"]["type"] == "User":
            if review["state"] in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
                latest[login] = review
    if any(review["state"] == "CHANGES_REQUESTED" for review in latest.values()):
        return None
    return next(
        (
            login
            for login, review in latest.items()
            if review["state"] == "APPROVED" and review["commit_id"] == head
        ),
        None,
    )


def request(repository: str, path: str) -> Any:
    req = Request(
        f"https://api.github.com/repos/{repository}/{path}",
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urlopen(req, timeout=15) as response:
        return json.load(response)


def github_approval(root: Path, base: str, number: int) -> dict[str, str] | None:
    repository = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or number < 1:
        raise ValueError("Invalid GitHub repository or pull request")
    head = git(root, "rev-parse", "HEAD").strip()
    base_sha = git(root, "rev-parse", base + "^{commit}").strip()
    if git(root, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("Reviewed policy requires a clean checkout of the reviewed commit")
    pull = request(repository, f"pulls/{number}")
    if (
        pull["state"] != "open"
        or pull["head"]["sha"] != head
        or pull["base"]["sha"] != base_sha
        or pull["base"]["repo"]["full_name"].lower() != repository.lower()
    ):
        raise ValueError("Review does not match the open PR's current head and base")
    owners = owners_at_base(root, base) - {pull["user"]["login"].lower()}
    reviews = []
    for page in range(1, 101):
        batch = request(repository, f"pulls/{number}/reviews?per_page=100&page={page}")
        reviews.extend(batch)
        if len(batch) < 100:
            owner = approved_owner(reviews, head, owners)
            current = request(repository, f"pulls/{number}")
            if current != pull:
                raise ValueError("PR changed during review verification; rerun the check")
            return {"owner": owner, "head": head, "base": base_sha} if owner else None
    raise ValueError("Review pagination limit exceeded")
