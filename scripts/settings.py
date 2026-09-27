"""Read the explicit workflow contract without interpreting shell commands."""

import os
from pathlib import Path


def relative_path(name, default):
    value = os.environ.get(name, default)
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or value.startswith("-"):
        raise ValueError(f"{name} must be a repository-relative path")
    return value


def command():
    return ["tofu", "-chdir=" + relative_path("TF_ROOT", "ci")]


def init_args():
    args = ["init", "-input=false", "-lockfile=readonly", "-no-color"]
    args.append("-backend-config=" + relative_path("TF_BACKEND_FILE", "backend.hcl"))
    for key, variable in {
        "resource_group_name": "TF_STATE_RESOURCE_GROUP",
        "storage_account_name": "TF_STATE_STORAGE_ACCOUNT",
        "container_name": "TF_STATE_CONTAINER",
    }.items():
        args.append(f"-backend-config={key}={os.environ[variable]}")
    return args


def plan_args(saved):
    args = ["plan", "-input=false", "-lock-timeout=5m", "-no-color", f"-out={saved}"]
    if os.environ.get("TF_VARS_FILE"):
        args.append("-var-file=" + relative_path("TF_VARS_FILE", ""))
    return args
