import re
from collections.abc import Iterator
from fnmatch import fnmatchcase
from typing import Any

from hcl2.api import loads
from tree_sitter import Node

from checks.source import Finding, node_text, walk


def check(path: str, source: str, tree: Node, policy: dict[str, Any]) -> Iterator[Finding]:
    document = loads(source)
    for resource in document.get("resource", []):
        for kind, instances in resource.items():
            for name, attributes in instances.items():
                for collection in policy.get("collections", []):
                    if kind == collection["type"] and fnmatchcase(path, collection["files"]):
                        if attributes.get("for_each") != "${" + collection["expression"] + "}":
                            yield Finding(
                                "TF003",
                                path,
                                1,
                                f"Use for_each = {collection['expression']} for this resource family.",
                                symbol=f"{kind}.{name}",
                            )
    for node in walk(tree):
        if node.type != "block":
            continue
        match = re.match(r'(resource|module)\s+"([^"]+)"(?:\s+"([^"]+)")?', node_text(node))
        if not match:
            continue
        kind, name, label = match.groups()
        symbol = f"{name}.{label}" if label else name
        if kind == "resource" and policy["profile"] == "consumer":
            yield Finding(
                "TF001",
                path,
                node.start_point.row + 1,
                "Managed resources belong in the approved module repository.",
                symbol=symbol,
            )
    for module in document.get("module", []):
        for name, attributes in module.items():
            source = attributes.get("source", "")
            match = re.fullmatch(
                r"git::https://github.com/([^?]+)\.git//modules/([\w/-]+)\?ref=([a-f0-9]{40})",
                source,
            )
            if (
                not match
                or match[1] not in policy["approved_repositories"]
                or ".." in match[2].split("/")
            ):
                yield Finding(
                    "TF002",
                    path,
                    1,
                    "Use an approved GitHub module source pinned to a full commit SHA.",
                    symbol=name,
                )
