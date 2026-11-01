"""Focused checks for complete, consistent artifact records."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


WRITER = Path(__file__).resolve().parents[1] / "scripts/write-artifact-record.sh"


class ArtifactRecordTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "changes").mkdir()
        (self.root / "changes/atlas.sum").write_text("fixture checksum\n")
        self.sha = "a" * 40
        self.images = {"worker": {"image": "registry:5000/worker:commit-" + self.sha,
                                 "digest": "sha256:" + "b" * 64, "revision": self.sha}}
        self.migration = {"repository": "ledger-schema", "tag": self.sha,
                          "revision": self.sha, "published": True,
                          "checksum": hashlib.sha256(b"fixture checksum\n").hexdigest()}

    def run_writer(self, success=True):
        result = subprocess.run(["bash", str(WRITER)], cwd=self.root, capture_output=True, text=True,
            env={**os.environ, "SOURCE_SHA": self.sha, "MIGRATION_DIRECTORY": "changes",
                 "IMAGES_JSON": json.dumps(self.images), "MIGRATION_JSON": json.dumps(self.migration),
                 "GITHUB_REPOSITORY": "org/app", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"})
        self.assertEqual(result.returncode == 0, success, result.stderr)
        if not success:
            self.assertFalse((self.root / "artifacts.json").exists())

    def test_complete_record_preserves_named_images_and_registry_port(self):
        self.images["reports"] = {**self.images["worker"], "image": "registry/reports:release"}
        self.run_writer()
        record = json.loads((self.root / "artifacts.json").read_text())
        self.assertEqual(set(record["images"]), {"worker", "reports"})
        self.assertEqual(record["images"]["worker"]["reference"], "registry:5000/worker@sha256:" + "b" * 64)
        self.assertEqual(record["migrations"]["atlasSumSha256"], self.migration["checksum"])
        self.assertEqual(record["source"]["revision"], self.sha)

    def test_invalid_or_mixed_images_produce_no_record(self):
        for key, value in [("revision", "f" * 40), ("digest", ""), ("digest", "sha256:bad"), ("image", "")]:
            with self.subTest(key=key, value=value):
                original = self.images["worker"][key]
                self.images["worker"][key] = value
                self.run_writer(False)
                self.images["worker"][key] = original
        self.images = {}
        self.run_writer(False)

    def test_unpublished_or_mismatched_migration_produces_no_record(self):
        for key, value in [("published", False), ("revision", "f" * 40),
                           ("tag", "latest"), ("checksum", "f" * 64), ("repository", "")]:
            with self.subTest(key=key):
                original = self.migration[key]
                self.migration[key] = value
                self.run_writer(False)
                self.migration[key] = original


if __name__ == "__main__":
    unittest.main()
