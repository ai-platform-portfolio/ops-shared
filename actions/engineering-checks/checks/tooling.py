import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from checks.source import Finding


def check(root: Path, policy: dict[str, Any], tools: Path) -> list[Finding]:
    commands = []
    python = policy.get("python", {}).get("roots", [])
    typescript = policy.get("typescript", {}).get("roots", [])
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        if python:
            commands.extend(
                [
                    (
                        "PY001",
                        [
                            sys.executable,
                            "-m",
                            "ruff",
                            "check",
                            "--isolated",
                            "--no-respect-gitignore",
                            "--select",
                            "E4,E7,E9,F,I,B,UP",
                            *python,
                        ],
                    ),
                    (
                        "PY002",
                        [
                            sys.executable,
                            "-m",
                            "ruff",
                            "format",
                            "--isolated",
                            "--no-respect-gitignore",
                            "--check",
                            *python,
                        ],
                    ),
                    (
                        "PY003",
                        [
                            sys.executable,
                            "-m",
                            "mypy",
                            "--config-file",
                            "/dev/null",
                            "--strict",
                            "--no-incremental",
                            "--cache-dir",
                            str(directory / "mypy"),
                            *python,
                        ],
                    ),
                ]
            )
        if typescript:
            biome = directory / "biome.json"
            biome.write_text(
                json.dumps(
                    {
                        "formatter": {"indentStyle": "space"},
                        "organizeImports": {"enabled": True},
                        "linter": {"enabled": True, "rules": {"recommended": True}},
                    }
                )
            )
            tsconfig = directory / "tsconfig.json"
            tsconfig.write_text(
                json.dumps(
                    {
                        "compilerOptions": {
                            "strict": True,
                            "noEmit": True,
                            "target": "ES2022",
                            "module": "ESNext",
                            "moduleResolution": "bundler",
                            "jsx": "react-jsx",
                            "skipLibCheck": False,
                        },
                        "include": [str(root / path / "**/*") for path in typescript],
                    }
                )
            )
            commands.extend(
                [
                    (
                        "TS001",
                        [str(tools / "biome"), "check", "--config-path", str(biome), *typescript],
                    ),
                    ("TS002", [str(tools / "tsc"), "--project", str(tsconfig)]),
                ]
            )
        findings = []
        for rule, command in commands:
            result = subprocess.run(command, cwd=root, capture_output=True, text=True)
            if result.returncode:
                if result.returncode != 1:
                    # TypeScript uses 2 for type errors with emit disabled.
                    if not (rule == "TS002" and result.returncode == 2):
                        raise RuntimeError(
                            f"{command[0]} exited {result.returncode}: {result.stderr}{result.stdout}"
                        )
                findings.append(Finding(rule, ".", 1, (result.stdout + result.stderr).strip()))
        return findings
