"""Fingerprint a complete Terraform plan without publishing its values."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from settings import command


def fingerprint(plan):
    content = {key: value for key, value in plan.items() if key != "timestamp"}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify(plan, expected):
    if fingerprint(plan) != expected:
        raise ValueError("Plan changed since review. Start a new run and obtain fresh approval.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--expected")
    args = parser.parse_args()
    result = subprocess.run(
        [*command(), "show", "-json", str(args.plan.resolve())],
        check=True, capture_output=True, text=True,
    )
    plan = json.loads(result.stdout)
    if args.expected:
        verify(plan, args.expected)
        print("Reviewed plan fingerprint matches; saved plan is eligible for apply.")
        return
    changes = [item for item in plan.get("resource_changes", []) if item["change"]["actions"] != ["no-op"]]
    output_changes = any(item["actions"] != ["no-op"] for item in plan.get("output_changes", {}).values())
    digest = fingerprint(plan)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write(f"fingerprint={digest}\nchanges={str(bool(changes or output_changes)).lower()}\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
        stream.write(f"## Central infrastructure plan\n\nCommit: `{os.environ['GITHUB_SHA']}`\n\n")
        stream.write(f"Plan fingerprint: `{digest}`\n\n")
        stream.write("Review the Terraform plan log and the committed configuration before approving deployment.\n\n")
        for item in changes:
            stream.write(f"- `{item['address']}`: {', '.join(item['change']['actions'])}\n")
        if not changes and not output_changes:
            stream.write("No changes; the apply job will be skipped.\n")


if __name__ == "__main__":
    main()
