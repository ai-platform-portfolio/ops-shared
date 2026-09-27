"""Reconcile the public repository catalogue without copying webhook content."""

import base64
import hashlib
import hmac
import json
import re
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

START = "<!-- repositories:start -->"
END = "<!-- repositories:end -->"


def accept_event(body, signature, event, secret, organization, installation_id):
    if not secret or len(body) > 1_000_000:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False
    payload = json.loads(body)
    if event == "ping":
        return True
    return (
        event in {"repository", "installation", "installation_repositories"}
        and payload.get("organization", {}).get("login") == organization
        and payload.get("installation", {}).get("id") == installation_id
    )


def catalogue(repositories, organization):
    lines = [START, "| Repository | Purpose |", "| --- | --- |"]
    for repo in sorted(repositories, key=lambda item: item["name"]):
        if repo.get("visibility") != "public" or repo.get("private") is not False:
            continue
        name = repo["name"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise ValueError("Unexpected repository name")
        # Public descriptions are escaped as text, never interpreted as Markdown.
        description = " ".join((repo.get("description") or "No description provided.").split())
        description = "".join(
            f"&#{ord(character)};" if character in "&<>|[]*_`\\!" else character
            for character in description
        )
        lines.append(f"| [{name}](https://github.com/{organization}/{name}) | {description} |")
    return "\n".join([*lines, END])


def update_readme(readme, block):
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("README must have exactly one catalogue marker pair")
    start, end = readme.index(START), readme.index(END)
    if start >= end:
        raise ValueError("README catalogue markers are reversed")
    return readme[:start] + block + readme[end + len(END):]


class GitHub:
    def __init__(self, token):
        self.token = token

    def request(self, path, method="GET", data=None):
        request = Request(
            "https://api.github.com/" + path,
            method=method,
            data=None if data is None else json.dumps(data).encode(),
            headers={
                **({"Authorization": "Bearer " + self.token} if self.token else {}),
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "ai-platform-portfolio-ops",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                return None if response.status == 204 else json.load(response)
        except HTTPError as error:
            # Never log API bodies: they may include repository content or credentials.
            raise RuntimeError(f"GitHub request failed ({error.code})") from None


def installation_client(app_id, installation_id, private_key, repository=".github"):
    import jwt

    now = int(time.time())
    token = jwt.encode({"iat": now - 60, "exp": now + 300, "iss": app_id}, private_key, algorithm="RS256")
    installation = GitHub(token).request(
        f"app/installations/{installation_id}/access_tokens", "POST",
        {"repositories": [repository], "permissions": {"contents": "write"}},
    )
    return GitHub(installation["token"])


def request_onboarding(organization, github):
    if organization != "ai-platform-portfolio":
        raise ValueError("Automatic federation onboarding is scoped to ai-platform-portfolio")
    github.request(f"repos/{organization}/terraform-modules/dispatches", "POST", {
        "event_type": "repository-onboarding",
    })


def public_repositories(organization, fetch):
    repos = []
    for page in range(1, 101):
        batch = fetch(f"orgs/{organization}/repos?type=public&per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("Invalid public repository response")
        repos.extend(batch)
        if len(batch) < 100:
            return repos
    raise ValueError("Public repository pagination limit exceeded")


def reconcile(organization, github, public_fetch):
    path = f"repos/{organization}/.github/contents/profile/README.md"
    existing = github.request(path + "?ref=main")
    readme = base64.b64decode(existing["content"]).decode()
    block = catalogue(public_repositories(organization, public_fetch), organization)
    updated = update_readme(readme, block)
    if updated == readme:
        return False
    github.request(path, "PUT", {
        "message": "Refresh public repository catalogue",
        "branch": "main",
        "sha": existing["sha"],
        "content": base64.b64encode(updated.encode()).decode(),
    })
    return True
