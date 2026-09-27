"""Split a downloaded state locally. Never connects to Azure or pushes state."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = {"shared_vnet_id", "aca_subnet_id", "private_endpoint_subnet_id", "private_dns_zone_ids"}


def normalized(resources):
    resources = json.loads(json.dumps(resources))
    for resource in resources:
        for instance in resource["instances"]:
            instance.setdefault("identity_schema_version", 0)
    return resources


def instances(state):
    result = {}
    for resource in state["resources"]:
        if resource["mode"] != "managed":
            continue
        for instance in resource["instances"]:
            resource_id = (resource["type"], instance["attributes"]["id"])
            if resource_id in result:
                raise ValueError("Duplicate resource ID in state")
            result[resource_id] = instance["attributes"]
    return result


def prepare(source, directory):
    original = json.loads(source.read_text())
    if not OUTPUTS.issubset(original["outputs"]):
        raise ValueError("Source lacks expected network outputs")
    moves = json.loads((ROOT / "ci/migrations/network.json").read_text())
    existing = {
        resource["type"] + "." + resource["name"]
        for resource in original["resources"]
        if resource["mode"] == "managed" and "module" not in resource
    }
    if not set(moves).issubset(existing):
        raise ValueError("Source lacks expected network addresses")
    directory.mkdir(mode=0o700)
    source_copy = directory / "auth0.tfstate"
    target = directory / "central-devops.tfstate"
    shutil.copyfile(source, source_copy)
    for before, after in moves.items():
        subprocess.run(
            ["terraform", "state", "mv", "-state=" + str(source_copy),
             "-state-out=" + str(target), before, after],
            cwd=directory, check=True, capture_output=True,
        )
    remaining = json.loads(source_copy.read_text())
    network = json.loads(target.read_text())
    old_ids, remaining_ids, network_ids = map(instances, (original, remaining, network))
    if remaining_ids.keys() & network_ids.keys() or old_ids != remaining_ids | network_ids:
        raise ValueError("Split changed resource attributes or lost/duplicated ownership")
    untouched = [r for r in original["resources"] if not (
        r["mode"] == "managed" and "module" not in r
        and r["type"] + "." + r["name"] in moves)]
    if normalized(remaining["resources"]) != normalized(untouched) or remaining["outputs"] != original["outputs"]:
        raise ValueError("Split changed legacy resources or compatibility outputs")
    if remaining["lineage"] != original["lineage"] or network["lineage"] == original["lineage"]:
        raise ValueError("Unexpected state lineage")
    network["outputs"] = {key: original["outputs"][key] for key in sorted(OUTPUTS)}
    target.write_text(json.dumps(network, indent=2) + "\n")
    print(f"Prepared {len(network_ids)} network resources; {len(remaining_ids)} legacy resources unchanged.")
    print("Remote state unchanged. Files contain secrets; never commit or upload them as CI artifacts.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        prepare(args.source.resolve(), args.directory.resolve())
    except ValueError as error:
        parser.exit(1, f"Preparation failed: {error}; no remote writes performed.\n")
    except (OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Preparation failed ({type(error).__name__}); no remote writes performed.\n")


if __name__ == "__main__":
    main()
