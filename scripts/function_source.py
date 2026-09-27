"""Resolve a reviewed, immutable Function package without shell interpolation."""

import json
import os
from pathlib import Path, PurePosixPath
import re

from deploy import api, approval_controls
from federation_contract import verify as verify_federation


def source(config, application):
    app = config["function_apps"][application]
    if app["source"].get("format") != "zip":
        raise ValueError("Flex Consumption requires a ZIP package; custom images are unsupported")
    result = {"name": app["name"], **app["source"]}
    if not re.fullmatch(r"[a-z0-9-]+", result["name"]):
        raise ValueError("Invalid Function name")
    path = PurePosixPath(result["path"])
    if path.is_absolute() or ".." in path.parts or not re.fullmatch(r"[A-Za-z0-9_./-]+", str(path)):
        raise ValueError("Invalid Function source path")
    return result


if __name__ == "__main__":
    repository = os.environ["GITHUB_REPOSITORY"]
    if repository != "ai-platform-portfolio/ops-shared" or os.environ["GITHUB_REF"] != "refs/heads/main":
        raise ValueError("Function deployment must run from ops-shared main")
    prefix = f"repos/{repository}/environments/central-apply"
    approval_controls(api(prefix), api(prefix + "/deployment-branch-policies"), "michaelalinks")
    repositories = json.loads(Path("ci/github.auto.tfvars.json").read_text())["github_repositories"]
    os.environ["TF_PHASE"] = "apply"
    verify_federation(lambda name: api(f"repos/{name}"), repositories)
    config = json.loads(Path("functions/apps.json").read_text())
    resolved = source(config, os.environ["APPLICATION"])
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write("".join(f"{key}={value}\n" for key, value in resolved.items()))
