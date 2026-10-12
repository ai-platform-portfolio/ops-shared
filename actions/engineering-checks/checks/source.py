import re
import subprocess
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

LANGUAGES: dict[str, Literal["python", "typescript", "tsx", "hcl"]] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".tf": "hcl",
    ".tfvars": "hcl",
}


@dataclass
class Finding:
    rule: str
    file: str
    line: int
    message: str
    related_file: str | None = None
    symbol: str | None = None

    def json(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True)


def walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from walk(child)


def node_text(node: Node) -> str:
    if node.text is None:
        raise ValueError("Parser node has no source text")
    return node.text.decode()


def changed_lines(root: Path, base: str) -> dict[str, set[int]]:
    changes: dict[str, set[int]] = {}
    path = None
    for line in git(root, "diff", "--no-ext-diff", "--no-renames", "-U0", base, "--").splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            changes[path] = set()
        elif line.startswith("@@") and path:
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            assert match is not None
            start = int(match[1])
            changes[path].update(range(start, start + int(match[2] or 1)))
    for path in git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if path and Path(path).suffix in LANGUAGES:
            changes[path] = set(range(1, len((root / path).read_text().splitlines()) + 1))
    return changes


def source_files(root: Path) -> list[str]:
    paths = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    return sorted(
        {p for p in paths.split("\0") if Path(p).suffix in LANGUAGES and (root / p).is_file()}
    )


def parse(path: str, source: str) -> Node:
    return get_parser(LANGUAGES[Path(path).suffix]).parse(source.encode()).root_node


def comments(path: str, tree: Node, touched: set[int], policy: dict[str, Any]) -> Iterator[Finding]:
    blocks: list[tuple[int, int, str]] = []
    for node in walk(tree):
        is_doc = (
            Path(path).suffix == ".py"
            and node.type == "string"
            and node.parent is not None
            and node.parent.type == "expression_statement"
        )
        if "comment" in node.type or is_doc:
            start, end = node.start_point.row + 1, node.end_point.row + 1
            text = node_text(node)
            if blocks and start <= blocks[-1][1] + 1:
                previous = blocks.pop()
                blocks.append((previous[0], end, previous[2] + "\n" + text))
            else:
                blocks.append((start, end, text))
    for start, end, text in blocks:
        if not touched.intersection(range(start, end + 1)):
            continue
        if re.search(
            r"type:\s*ignore|\bnoqa\b|@ts-(?:ignore|nocheck|expect-error)|biome-ignore|eslint-disable|fmt:\s*off",
            text,
        ):
            yield Finding(
                "SUPPRESS001",
                path,
                start,
                "Inline tool suppression requires a reviewed policy exception.",
            )
        lines = [line for line in text.splitlines() if line.strip(" /*#\t\"'")]
        if len(lines) > policy["max_lines"] or len(text) > policy["max_characters"]:
            yield Finding(
                "COMMENT001",
                path,
                start,
                "Shorten the comment; move extended rationale to a maintained design document.",
            )
