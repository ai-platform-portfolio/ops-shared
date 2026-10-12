#!/usr/bin/env python3
"""Apply the saved per-repository settings to every active repository in the org."""

import argparse
import json
from pathlib import Path
import subprocess

ORGANIZATION = "ai-platform-portfolio"
SETTINGS_FILE = Path(__file__).resolve().parents[1] / "functions" / "repository-settings.json"


def github(path, settings=None):
    command = ["gh", "api", path]
    if settings is not None:
        command += ["--method", "PATCH", "--input", "-"]
    return json.loads(subprocess.check_output(
        command, input=json.dumps(settings) if settings is not None else None, text=True,
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes; default is preview")
    args = parser.parse_args()
    settings = json.loads(SETTINGS_FILE.read_text())
    if not settings or any(type(value) is not bool for value in settings.values()):
        parser.error("repository-settings.json must contain boolean repository settings")
    pages = json.loads(subprocess.check_output([
        "gh", "api", f"orgs/{ORGANIZATION}/repos?per_page=100", "--paginate", "--slurp",
    ], text=True))
    print("Scope: PER REPOSITORY; mode:", "apply" if args.apply else "preview")
    for repository in sorted((repo for page in pages for repo in page), key=lambda repo: repo["name"]):
        if repository["archived"]:
            continue
        endpoint = "repos/" + repository["full_name"]
        current = github(endpoint)
        changes = {key: value for key, value in settings.items() if current.get(key) != value}
        print(repository["full_name"], changes or "already configured")
        if args.apply and changes:
            github(endpoint, changes)
            actual = github(endpoint)
            if any(actual.get(key) != value for key, value in settings.items()):
                raise RuntimeError("Settings verification failed: " + repository["full_name"])


if __name__ == "__main__":
    main()
