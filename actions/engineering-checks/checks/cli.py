import argparse
import json
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml

from checks import dependencies, duplication, review, terraform, tooling
from checks.source import Finding, changed_lines, comments, git, parse, source_files


def load_policy(text: str) -> dict[str, Any]:
    policy = yaml.safe_load(text)
    if not isinstance(policy, dict) or policy.get("version") != 1:
        raise ValueError("engineering.yaml must declare version: 1")
    for name in ("terraform", "python", "typescript"):
        section = policy.get(name, {})
        for root in section.get("roots", []):
            if (
                not isinstance(root, str)
                or root.startswith("/")
                or ".." in root.split("/")
                or root.startswith("-")
            ):
                raise ValueError(f"Invalid {name} source root")
    for exception in policy.get("exceptions", []):
        if not all(exception.get(key) for key in ("rule", "file", "reason", "owner")):
            raise ValueError("Exceptions require rule, exact file, reason and owner")
    return policy


def evaluate(root: Path, base: str, policy: dict[str, Any], tools: Path) -> list[Finding]:
    changes = changed_lines(root, base)
    findings = []
    language_paths = []
    for path in source_files(root):
        if any(fnmatchcase(path, pattern) for pattern in policy.get("exclude", [])):
            continue
        language = (
            "terraform"
            if path.endswith((".tf", ".tfvars"))
            else "python"
            if path.endswith(".py")
            else "typescript"
        )
        section = policy.get(language, {})
        if not any(path.startswith(prefix + "/") for prefix in section.get("roots", [])):
            if path in changes:
                findings.append(
                    Finding(
                        "SCOPE001",
                        path,
                        1,
                        "Source is outside declared roots; owner review is required to expand scope.",
                    )
                )
            continue
        if (root / path).is_symlink():
            raise ValueError(f"Source symlinks are not supported: {path}")
        if language != "terraform":
            language_paths.append(path)
        if path not in changes:
            continue
        source = (root / path).read_text()
        tree = parse(path, source)
        if tree.has_error:
            findings.append(
                Finding(
                    "PARSE001",
                    path,
                    1,
                    "Source does not parse; resolve syntax errors before structural checks.",
                )
            )
            continue
        findings.extend(comments(path, tree, changes[path], policy["comments"]))
        if language == "terraform":
            findings.extend(terraform.check(path, source, tree, section))
        else:
            findings.extend(dependencies.check(path, tree, section))
    findings.extend(duplication.check(root, language_paths, changes, tools, policy["duplication"]))
    findings.extend(tooling.check(root, policy, tools))
    for path in git(root, "diff", "--name-only", base, "--").splitlines():
        if path in {"engineering.yaml", ".github/CODEOWNERS"} or any(
            fnmatchcase(path, pattern) for pattern in policy.get("protected_paths", [])
        ):
            findings.append(
                Finding(
                    "POLICY001",
                    path,
                    1,
                    "Protected changes require owner approval of this commit and --github-pr adoption.",
                )
            )
    exceptions = policy.get("exceptions", [])
    return [
        finding
        for finding in findings
        if not any(
            finding.rule != "POLICY001"
            and finding.rule == exception["rule"]
            and finding.file == exception["file"]
            and (not exception.get("symbol") or finding.symbol == exception["symbol"])
            for exception in exceptions
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True)
    parser.add_argument(
        "--policy-file", default="engineering.yaml", help="Tracked repository-relative policy path"
    )
    parser.add_argument(
        "--policy",
        type=Path,
        help="Trusted policy outside the candidate checkout; defaults to base revision",
    )
    parser.add_argument(
        "--tools", type=Path, default=Path(__file__).resolve().parent.parent / "node_modules/.bin"
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--github-pr", type=int, help="Allow policy adoption after exact-head owner review"
    )
    parser.add_argument(
        "--policy-mode",
        choices=("base", "candidate"),
        default="base",
        help="Use base policy, or validate candidate policy with protected changes reported for native PR review",
    )
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        policy_path = Path(args.policy_file)
        if (
            policy_path.is_absolute()
            or ".." in policy_path.parts
            or args.policy_file.startswith("-")
        ):
            raise ValueError("--policy-file must be a repository-relative path")
        git(root, "rev-parse", "--verify", args.base + "^{tree}")
        if args.policy_mode == "candidate" and (args.policy or args.github_pr):
            raise ValueError("Candidate mode cannot be combined with --policy or --github-pr")
        text = (
            args.policy.read_text()
            if args.policy
            else git(
                root,
                "show",
                f"{'HEAD' if args.policy_mode == 'candidate' else args.base}:{args.policy_file}",
            )
        )
        policy = load_policy(text)
        adoption = None
        changed = git(root, "diff", "--name-only", args.base, "--").splitlines()
        protected = [
            path
            for path in changed
            if path in {args.policy_file, "engineering.yaml", ".github/CODEOWNERS"}
            or any(fnmatchcase(path, pattern) for pattern in policy.get("protected_paths", []))
        ]
        if args.github_pr and protected:
            if args.policy:
                raise ValueError("--github-pr cannot be combined with an external --policy")
            adoption = review.github_approval(root, args.base, args.github_pr)
            if adoption:
                policy = load_policy(git(root, "show", f"HEAD:{args.policy_file}"))
        findings = evaluate(root, args.base, policy, args.tools.resolve())
        if args.policy_file in changed and not any(
            finding.rule == "POLICY001" and finding.file == args.policy_file for finding in findings
        ):
            findings.append(
                Finding("POLICY001", args.policy_file, 1, "Policy changes require owner approval.")
            )
        if adoption:
            findings = [finding for finding in findings if finding.rule != "POLICY001"]
        protected_changes = []
        if args.policy_mode == "candidate":
            protected_changes = [
                finding.json() for finding in findings if finding.rule == "POLICY001"
            ]
            findings = [finding for finding in findings if finding.rule != "POLICY001"]
        report: dict[str, Any] = {
            "status": "fail" if findings else "pass",
            "findings": [finding.json() for finding in findings],
            "policy_adoption": adoption,
            "policy_source": args.policy_mode,
            "protected_changes": protected_changes,
        }
        code = 1 if findings else 0
    except Exception as error:
        report = {"status": "error", "findings": [], "error": str(error)}
        code = 2
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    for finding in report["findings"]:
        print(f"{finding['rule']} {finding['file']}:{finding['line']}: {finding['message']}")
    for finding in report.get("protected_changes", []):
        print(f"Protected change for PR review: {finding['file']}")
    if report["status"] == "error":
        print(report["error"], file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
