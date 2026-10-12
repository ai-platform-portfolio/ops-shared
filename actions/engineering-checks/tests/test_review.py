import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from checks.cli import main


class PolicyAdoption(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root.parent / (self.root.name + ".json")
        self.addCleanup(self.report.unlink, missing_ok=True)
        self.git("init", "-q")
        (self.root / ".github").mkdir()
        (self.root / "ci").mkdir()
        self.write(".github/CODEOWNERS", "* @trusted\n")
        self.policy = {
            "version": 1,
            "terraform": {"roots": ["ci"], "profile": "module", "approved_repositories": []},
            "comments": {"max_lines": 5, "max_characters": 400},
            "duplication": {"min_tokens": 50, "min_lines": 5},
            "protected_paths": [".github/*"],
        }
        self.write("engineering.yaml", yaml.safe_dump(self.policy))
        self.base = self.commit()
        self.write(
            "ci/main.tf", 'module "deployment_identity" { source = "../modules/azure/identity" }\n'
        )
        self.write(".github/workflow.yml", "name: changed\n")
        self.write(".github/CODEOWNERS", "* @candidate\n")
        self.policy["exceptions"] = [
            dict(
                rule="TF002",
                file="ci/main.tf",
                symbol="deployment_identity",
                owner="trusted",
                reason="Same repository composition",
            )
        ]
        self.write("engineering.yaml", yaml.safe_dump(self.policy))
        self.head = self.commit(self.base)

    def git(self, *args, input=None):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], input=input, text=True
        ).strip()

    def write(self, path, text):
        (self.root / path).write_text(text)

    def commit(self, parent=None):
        self.git("add", ".")
        tree = self.git("write-tree")
        body = f"tree {tree}\n" + (f"parent {parent}\n" if parent else "")
        body += "author Test <test@example.invalid> 1 +0000\ncommitter Test <test@example.invalid> 1 +0000\n\nfixture\n"
        sha = self.git("hash-object", "-t", "commit", "-w", "--stdin", input=body)
        self.git("update-ref", "HEAD", sha)
        return sha

    def run_check(self, reviews, *, author="author", head=None, error=False, policy_file=None, candidate=False):
        pull = {
            "state": "open",
            "head": {"sha": head or self.head},
            "base": {"sha": self.base, "repo": {"full_name": "example/repo"}},
            "user": {"login": author},
        }

        def api(repository, path):
            if error:
                raise OSError("API unavailable")
            return reviews if "/reviews?" in path else pull

        argv = [
            "checks",
            "--root",
            str(self.root),
            "--base",
            self.base,
            "--github-pr",
            "3",
            "--report",
            str(self.report),
        ]
        if candidate:
            index = argv.index("--github-pr")
            argv[index:index + 2] = ["--policy-mode", "candidate"]
        if policy_file:
            argv += ["--policy-file=" + policy_file]
        with (
            patch.dict(os.environ, GITHUB_REPOSITORY="example/repo"),
            patch("checks.review.request", side_effect=api),
            patch("sys.argv", argv),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = main()
        return code, json.loads(self.report.read_text())

    def review(self, state="APPROVED", owner="trusted", commit=None, id=1):
        return {
            "id": id,
            "state": state,
            "user": {"login": owner, "type": "User"},
            "commit_id": commit or self.head,
        }

    def test_candidate_mode_reports_protected_changes_and_preserves_code_failures(self):
        code, report = self.run_check([], candidate=True)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["policy_source"], "candidate")
        self.assertIsNone(report["policy_adoption"])
        self.assertEqual({f["file"] for f in report["protected_changes"]},
                         {"engineering.yaml", ".github/CODEOWNERS", ".github/workflow.yml"})
        self.write("ci/bad.tf", 'module "bad" { source = "../unapproved" }\n')
        self.head = self.commit(self.head)
        code, report = self.run_check([], candidate=True)
        self.assertEqual(code, 1, report)
        self.assertEqual({f["rule"] for f in report["findings"]}, {"TF002"})

    def test_review_adopts_exact_exception_but_preserves_other_findings(self):
        code, report = self.run_check([self.review()])
        self.assertEqual(code, 0, report)
        self.assertEqual(report["policy_adoption"]["owner"], "trusted")
        self.write("ci/bad.tf", 'module "bad" { source = "../unapproved" }\n')
        self.head = self.commit(self.head)
        code, report = self.run_check([self.review()])
        self.assertEqual(code, 1, report)
        self.assertEqual({f["rule"] for f in report["findings"]}, {"TF002"})

    def test_untrusted_or_outdated_reviews_cannot_adopt(self):
        cases = [
            [],
            [self.review(owner="candidate")],
            [self.review(commit=self.base)],
            [self.review("DISMISSED")],
            [self.review("CHANGES_REQUESTED")],
            [self.review(), self.review("CHANGES_REQUESTED", id=2)],
        ]
        for reviews in cases:
            with self.subTest(reviews=reviews):
                code, report = self.run_check(reviews)
                self.assertEqual(code, 1, report)
                self.assertIsNone(report["policy_adoption"])
                self.assertIn("POLICY001", {f["rule"] for f in report["findings"]})
        self.assertEqual(self.run_check([self.review()], author="trusted")[0], 1)

    def test_verification_errors_fail_closed(self):
        self.assertEqual(self.run_check([self.review()], error=True)[0], 2)
        self.assertEqual(self.run_check([self.review()], head=self.base)[0], 2)
        self.write("ci/dirty.tf", "")
        self.assertEqual(self.run_check([self.review()])[0], 2)

    def test_nested_policy_requires_current_owner_review(self):
        self.git("reset", "--hard", self.base)
        (self.root / "gate-example").mkdir()
        self.git("mv", "engineering.yaml", "gate-example/engineering.yaml")
        self.base = self.commit(self.base)
        self.write(".github/workflow.yml", "name: changed\n")
        self.head = self.commit(self.base)
        code, report = self.run_check([], policy_file="gate-example/engineering.yaml")
        self.assertEqual(code, 1, report)
        code, report = self.run_check([self.review()], policy_file="gate-example/engineering.yaml")
        self.assertEqual(code, 0, report)
        self.assertEqual(self.run_check([self.review(commit=self.base)], policy_file="gate-example/engineering.yaml")[0], 1)

    def test_policy_path_cannot_escape_checkout(self):
        for path in ("../policy.yml", "/tmp/policy.yml", "-policy.yml"):
            self.assertEqual(self.run_check([], policy_file=path)[0], 2)
