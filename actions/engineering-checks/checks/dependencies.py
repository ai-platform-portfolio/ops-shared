import posixpath
from collections.abc import Iterator
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Any

from tree_sitter import Node

from checks.source import Finding, node_text, walk


def imports(tree: Node, python: bool) -> Iterator[tuple[str, int]]:
    for node in walk(tree):
        if python and node.type == "import_from_statement":
            module = node.child_by_field_name("module_name")
            if module:
                yield node_text(module), node.start_point.row + 1
        elif python and node.type == "import_statement":
            for child in node.named_children:
                name = (
                    child.child_by_field_name("name") if child.type == "aliased_import" else child
                )
                if name is not None:
                    yield node_text(name), node.start_point.row + 1
        elif not python and node.type in {"import_statement", "export_statement"}:
            source = node.child_by_field_name("source")
            if source:
                yield node_text(source)[1:-1], node.start_point.row + 1
        elif node.type in {"call", "call_expression"}:
            function = node.child_by_field_name("function")
            if function and node_text(function) in {
                "require",
                "import",
                "__import__",
                "importlib.import_module",
            }:
                args = node.child_by_field_name("arguments")
                strings = (
                    [child for child in args.named_children if child.type == "string"]
                    if args
                    else []
                )
                yield (
                    node_text(strings[0]).strip("'\"") if strings else "<dynamic>",
                    node.start_point.row + 1,
                )


def destination(path: str, name: str, roots: list[str]) -> str:
    if path.endswith(".py"):
        if name.startswith("."):
            levels = len(name) - len(name.lstrip("."))
            prefix = str(PurePosixPath(path).parent)
            for _ in range(levels - 1):
                prefix = posixpath.dirname(prefix)
            return prefix + "/" + name.lstrip(".").replace(".", "/")
        root = next((r for r in roots if path.startswith(r + "/")), "")
        return root + "/" + name.replace(".", "/")
    if name.startswith("."):
        return posixpath.normpath(str(PurePosixPath(path).parent / name))
    return name


def check(path: str, tree: Node, policy: dict[str, Any]) -> Iterator[Finding]:
    for name, line in imports(tree, path.endswith(".py")):
        target = destination(path, name, policy["roots"])
        if name == "<dynamic>":
            yield Finding(
                "DEP002", path, line, "Dynamic module names require a reviewed exception."
            )
        for rule in policy.get("boundaries", []):
            if fnmatchcase(path, rule["from"]) and any(
                fnmatchcase(value, pattern) for value in (name, target) for pattern in rule["deny"]
            ):
                yield Finding("DEP001", path, line, rule["message"], related_file=target)
