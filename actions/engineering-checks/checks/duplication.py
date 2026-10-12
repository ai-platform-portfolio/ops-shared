import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from checks.source import Finding


def check(
    root: Path, paths: list[str], changes: dict[str, set[int]], tools: Path, policy: dict[str, Any]
) -> list[Finding]:
    if not paths:
        return []
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "jscpd.json"
        config.write_text(json.dumps({"ignore": []}))
        command = [
            str(tools / "jscpd"),
            "--config",
            str(config),
            "--silent",
            "--reporters",
            "json",
            "--output",
            directory,
            "--min-tokens",
            str(policy["min_tokens"]),
            "--min-lines",
            str(policy["min_lines"]),
            "--mode",
            "mild",
            "--threshold",
            "100",
            *paths,
        ]
        result = subprocess.run(command, cwd=root, text=True, capture_output=True)
        report = Path(directory) / "jscpd-report.json"
        if result.returncode or not report.exists():
            raise RuntimeError(f"jscpd failed: {result.stdout}{result.stderr}")
        findings = []
        for duplicate in json.loads(report.read_text())["duplicates"]:
            first, second = duplicate["firstFile"], duplicate["secondFile"]
            for item, other in ((first, second), (second, first)):
                item_path = Path(item["name"])
                path = (
                    str(item_path.relative_to(root)) if item_path.is_absolute() else str(item_path)
                )
                related_path = Path(other["name"])
                related = (
                    str(related_path.relative_to(root))
                    if related_path.is_absolute()
                    else str(related_path)
                )
                if changes.get(path, set()).intersection(range(item["start"], item["end"] + 1)):
                    findings.append(
                        Finding(
                            "DUP001",
                            path,
                            item["start"],
                            "New matching code: reuse the existing implementation or request a specific exception.",
                            related_file=related,
                        )
                    )
                    break
        return findings
