"""Publication safety checks using temporary Git and a fake GitHub API."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="publication-test-")
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Publication fixture")
        (self.repo / "db/changes").mkdir(parents=True)
        (self.repo / "db/changes/atlas.sum").write_text("fixture checksum\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.sha = self.git("rev-parse", "HEAD")
        self.checksum = hashlib.sha256((self.repo / "db/changes/atlas.sum").read_bytes()).hexdigest()
        bin_dir = self.repo / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text("#!/bin/sh\nprintf '%s\\n' \"$FAKE_GITHUB_RESPONSE\"\n")
        gh.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "SOURCE_SHA": self.sha,
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REF": "refs/pull/42/merge",
            "GITHUB_REPOSITORY": "org/app",
            "PR_NUMBER": "42",
            "FAKE_GITHUB_RESPONSE": json.dumps({"state": "open", "head": {"sha": self.sha}}),
            "CHECK_CURRENT": "true",
            "GITHUB_OUTPUT": str(self.repo / "outputs"),
            "MIGRATION_DIRECTORY": "db/changes",
            "IMAGES_JSON": json.dumps({
                "worker": {"image": "registry:5000/worker:commit-" + self.sha,
                           "digest": "sha256:" + "a" * 64, "revision": self.sha},
                "reports": {"image": "registry/reports:commit-" + self.sha,
                            "digest": "sha256:" + "b" * 64, "revision": self.sha},
            }),
            "MIGRATION_JSON": json.dumps({
                "repository": "accounting-schema", "tag": self.sha,
                "revision": self.sha, "checksum": self.checksum, "published": True,
            }),
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "2",
        }

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, text=True).strip()

    def run_script(self, name, success=True, **overrides):
        result = subprocess.run(
            ["bash", str(ROOT / (".github/actions/check-source/check-source.sh" if name == "check-source.sh" else "scripts/" + name))], cwd=self.repo,
            env={**self.env, **overrides}, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)

    def test_current_pr_and_main(self):
        self.run_script("check-source.sh")
        self.run_script("check-source.sh", GITHUB_EVENT_NAME="push",
                        GITHUB_REF="refs/heads/main", FAKE_GITHUB_RESPONSE=self.sha)

    def test_stale_closed_and_malformed_pr(self):
        for response in [
            {"state": "open", "head": {"sha": "f" * 40}},
            {"state": "closed", "head": {"sha": self.sha}},
            {},
        ]:
            with self.subTest(response=response):
                self.run_script("check-source.sh", False,
                                FAKE_GITHUB_RESPONSE=json.dumps(response))
        self.run_script("check-source.sh", False, PR_NUMBER="bad")

    def test_checkout_mismatch_and_advanced_main(self):
        self.run_script("check-source.sh", False, SOURCE_SHA="f" * 40)
        self.run_script("check-source.sh", False, GITHUB_EVENT_NAME="push",
                        GITHUB_REF="refs/heads/main", FAKE_GITHUB_RESPONSE="f" * 40)

    def test_matching_artifact_record(self):
        self.run_script("write-artifact-record.sh")
        record = json.loads((self.repo / "artifacts.json").read_text())
        self.assertEqual(record["source"]["revision"], self.sha)
        self.assertEqual(record["images"]["worker"]["reference"], "registry:5000/worker@sha256:" + "a" * 64)
        self.assertEqual(record["images"]["reports"]["reference"], "registry/reports@sha256:" + "b" * 64)
        self.assertEqual(record["migrations"]["repository"], "accounting-schema")
        self.assertEqual(record["migrations"]["tag"], self.sha)
        self.assertEqual(record["migrations"]["atlasSumSha256"], self.checksum)
        self.assertEqual(record["workflow"], {"runId": "123", "attempt": "2"})

    def test_optional_freshness_and_revision_output(self):
        self.run_script("check-source.sh", CHECK_CURRENT="false", FAKE_GITHUB_RESPONSE="{}")
        self.assertEqual((self.repo / "outputs").read_text(), "revision=" + self.sha + "\n")
        self.run_script("check-source.sh", False, CHECK_CURRENT="invalid")
        self.run_script("check-source.sh", False, SOURCE_SHA=self.sha[:12])

    def test_incomplete_or_mixed_artifacts_are_rejected(self):
        for key, value in [("revision", "f" * 40), ("image", ""),
                           ("digest", ""), ("digest", "sha256:bad")]:
            images = json.loads(self.env["IMAGES_JSON"])
            images["reports"][key] = value
            with self.subTest(image_field=key):
                self.run_script("write-artifact-record.sh", False, IMAGES_JSON=json.dumps(images))
        for key, value in [("revision", "f" * 40), ("tag", "latest"),
                           ("checksum", "f" * 64), ("published", False), ("repository", "")]:
            migration = json.loads(self.env["MIGRATION_JSON"])
            migration[key] = value
            with self.subTest(migration_field=key):
                self.run_script("write-artifact-record.sh", False, MIGRATION_JSON=json.dumps(migration))
        for invalid in ["{}", "[]", "not-json"]:
            with self.subTest(invalid=invalid):
                self.run_script("write-artifact-record.sh", False, IMAGES_JSON=invalid)
        self.assertFalse((self.repo / "artifacts.json").exists())


if __name__ == "__main__":
    unittest.main()
