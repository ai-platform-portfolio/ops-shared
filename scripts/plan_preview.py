"""Run an OpenTofu PR preview and update one redacted GitHub comment."""

import json
import os
import re
import subprocess
from pathlib import Path

from federation_contract import verify
from settings import command, init_args, plan_args

MARKER = "<!-- central-infrastructure-plan -->"


def redact(text, secrets, plan=None):
    hidden = {value for value in secrets if value}

    def visit(value):
        if isinstance(value, dict):
            if value.get("type") in ("azurerm_storage_account", "azurerm_key_vault"):
                for attributes in (
                    value.get("values", {}),
                    value.get("change", {}).get("before"),
                    value.get("change", {}).get("after"),
                ):
                    if attributes and attributes.get("name"):
                        hidden.add(attributes["name"])
            for key, item in value.items():
                if key in {"storage_account_name", "key_vault_name"} and isinstance(
                    item, str
                ):
                    hidden.add(item)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(plan)
    for value in sorted(hidden, key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    text = re.sub(
        r"(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", "[redacted-id]", text
    )
    return text.replace("```", "'''")


def comment_body(head, status, text):
    if len(text) > 50000:
        text = text[:50000] + "\n[Plan truncated at the comment size limit.]"
    return (
        f"{MARKER}\n### Infrastructure plan ({status})\n\n"
        f"commit `{head}`\n\n<details><summary>Show plan</summary>\n\n"
        f"```hcl\n{text}\n```\n</details>\n"
    )


def api(path, method="GET", body=None, paginate=False):
    command = [
        "gh",
        "api",
        "--method",
        method,
        f"repos/{os.environ['GITHUB_REPOSITORY']}/{path}",
    ]
    if paginate:
        command += ["--paginate", "--slurp"]
    if body is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(body) if body else None,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def publish(number, head, body):
    if api(f"pulls/{number}")["head"]["sha"] != head:
        return
    pages = api(f"issues/{number}/comments?per_page=100", paginate=True)
    existing = next(
        (
            item
            for page in pages
            for item in page
            if item["user"]["login"] == "github-actions[bot]"
            and item["body"].startswith(MARKER)
        ),
        None,
    )
    path = (
        f"issues/comments/{existing['id']}" if existing else f"issues/{number}/comments"
    )
    api(path, "PATCH" if existing else "POST", {"body": body})


def preview(saved):
    def repository(name):
        return json.loads(
            subprocess.check_output(["gh", "api", f"repos/{name}"], text=True)
        )

    try:
        verify(repository)
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError):
        return (
            "failed",
            "Federation contract check failed: verify required fields, GitHub repository IDs and the actual PR token claims.",
        )
    for stage, args in [
        ("initialization/authentication", init_args()),
        (
            "plan",
            plan_args(saved),
        ),
    ]:
        result = subprocess.run(
            [*command(), *args], capture_output=True, text=True
        )
        if result.returncode:
            codes = sorted(set(re.findall(r"AADSTS\d+", result.stdout + result.stderr)))
            detail = ", ".join(codes) or f"exit {result.returncode}"
            return (
                "failed",
                f"OpenTofu {stage} failed ({detail}). No successful plan was produced.\nRaw diagnostics are withheld to avoid publishing credentials or resource identifiers.",
            )
    output = subprocess.check_output(
        [*command(), "show", "-no-color", str(saved)], text=True
    )
    plan = json.loads(
        subprocess.check_output(
            [*command(), "show", "-json", str(saved)], text=True
        )
    )
    secrets = [
        value
        for key, value in os.environ.items()
        if key.startswith(("ARM_", "TF_STATE_", "TF_VAR_"))
        and value not in {"true", "false"}
    ]
    return "ok", redact(output, secrets, plan)


def main():
    saved = Path(os.environ["RUNNER_TEMP"]) / "pr-preview.tfplan"
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    pull = event["pull_request"]
    try:
        try:
            status, text = preview(saved)
        except (OSError, subprocess.CalledProcessError, ValueError):
            status, text = (
                "failed",
                "OpenTofu preview could not complete. No successful plan was produced.",
            )
        body = comment_body(pull["head"]["sha"], status, text)
        publish(pull["number"], pull["head"]["sha"], body)
        Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(body)
        print(f"OpenTofu preview: {status}; result posted to PR #{pull['number']}.")
        return 0 if status == "ok" else 1
    finally:
        saved.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
