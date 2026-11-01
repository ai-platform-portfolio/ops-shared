"""Exercise the publisher against a stateful fake GitHub Contents/Refs API."""

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / ".github/actions/publish-preview/publish.sh"
SHA = "a" * 40
FAKE_GH = r'''
import base64, json, os, sys
from pathlib import Path
p = Path(os.environ["FAKE_STATE"])
s = json.loads(p.read_text())
args = sys.argv[1:]
method = args[args.index("--method") + 1]
endpoint = next(a for a in args if a.startswith("repos/"))
data = json.loads(Path(args[args.index("--input") + 1]).read_text()) if "--input" in args else {}
code, body = 200, {}
s.setdefault("calls", []).append([method, endpoint])
if "/pulls/" in endpoint:
    s["checks"] = s.get("checks", 0) + 1
    if s.get("advance_on_check") == s["checks"]:
        s["pr"]["head"]["sha"] = "b" * 40
    body = s["pr"]
elif "/git/ref/heads/" in endpoint:
    code = s.get("branch_status", 200 if s.get("branch") else 404)
    body = {"object": {"sha": "d" * 40}}
elif endpoint.endswith("/git/refs"):
    s["branch"] = True
    code = 422 if s.get("branch_race") else 201
elif "/contents/" in endpoint:
    name = endpoint.split("/contents/", 1)[1]
    files = s.setdefault("files", {})
    if method == "GET":
        if name in files:
            body = {"type":"file", "encoding":"base64", "sha":"e"*40,
                    "content":base64.encodebytes(files[name].encode()).decode()}
        else:
            code = 404
    elif s.get("conflicts", 0):
        s["conflicts"] -= 1
        code = 409
        files["previews/pr-99.json"] = '{"other":"preview"}'
        if s.get("close_on_conflict"):
            s["pr"]["state"] = "closed"
    elif s.get("write_status"):
        code = s["write_status"]
    else:
        if (name in files) != (data.get("sha") == "e"*40):
            raise RuntimeError("Contents API requires the existing file SHA")
        files[name] = base64.b64decode(data["content"]).decode()
        code, body = 201, {"commit":{"sha":"c"*40}}
        if s.get("advance_after_write"):
            s["pr"]["head"]["sha"] = "b"*40
else:
    raise RuntimeError("Unexpected API request")
p.write_text(json.dumps(s))
print(f"HTTP/2.0 {code} Test\r\nContent-Type: application/json\r\n\r\n" + json.dumps(body))
sys.exit(0 if code < 400 else 1)
'''


class PreviewPublicationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="preview-publisher-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        gh = self.root / "gh"
        gh.write_text(f"#!{sys.executable}\n" + FAKE_GH)
        gh.chmod(0o755)
        self.env = {**os.environ, "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                    "RECORD": str(self.root / "record.json"), "RECORD_BRANCH": "preview-records",
                    "PR_NUMBER": "42", "SOURCE_SHA": SHA, "GITHUB_REPOSITORY": "org/app",
                    "GH_TOKEN": "fixture", "GITHUB_OUTPUT": str(self.root / "output"),
                    "FAKE_STATE": str(self.root / "state.json")}
        self.reset()

    def reset(self):
        self.record = {"source": {"repository": "org/app", "revision": SHA},
                       "arbitrary": {"worker": "registry/worker@sha256:example"}}
        self.state = {"pr": {"state": "open", "head": {"sha": SHA, "ref": "feature",
                        "repo": {"full_name": "org/app"}},
                        "base": {"sha": "f"*40, "ref": "main",
                        "repo": {"full_name": "org/app", "default_branch": "main"}}}}

    def run_publisher(self, success=True, raw=None, **overrides):
        (self.root / "record.json").write_text(json.dumps(self.record) if raw is None else raw)
        (self.root / "state.json").write_text(json.dumps(self.state))
        (self.root / "output").write_text("")
        result = subprocess.run(["bash", str(SCRIPT)], env={**self.env, **overrides},
                                text=True, capture_output=True, timeout=15)
        self.state = json.loads((self.root / "state.json").read_text())
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return (self.root / "output").read_text()

    def test_initialization_and_arbitrary_record(self):
        output = self.run_publisher()
        self.assertIn("commit=" + "c"*40, output)
        self.assertIn("path=previews/pr-42.json", output)
        self.assertEqual(json.loads(self.state["files"]["previews/pr-42.json"]), self.record)
        self.assertTrue(self.state["branch"])

    def test_repeat_is_a_no_op_and_changed_record_uses_file_sha(self):
        self.run_publisher()
        self.state["calls"] = []
        self.run_publisher()
        self.assertFalse(any(method == "PUT" for method, _ in self.state["calls"]))
        self.record["arbitrary"]["another-component"] = "another-artifact"
        self.run_publisher()
        self.assertEqual(json.loads(self.state["files"]["previews/pr-42.json"]), self.record)

    def test_stale_closed_and_fork_prs_never_write(self):
        for field, value in [("sha", "b"*40), ("state", "closed"), ("repo", "fork/app")]:
            with self.subTest(field=field):
                self.reset()
                if field == "sha": self.state["pr"]["head"]["sha"] = value
                elif field == "repo": self.state["pr"]["head"]["repo"]["full_name"] = value
                else: self.state["pr"]["state"] = value
                self.run_publisher(False)
                self.assertFalse(any(method in ("POST", "PUT") for method, _ in self.state["calls"]))

    def test_invalid_identity_record_and_destination_rejected(self):
        for overrides, raw in [({"PR_NUMBER": "../42"}, None), ({"SOURCE_SHA": "short"}, None),
                               ({"RECORD_BRANCH": "bad..branch"}, None),
                               ({"RECORD_BRANCH": "main"}, None),
                               ({"RECORD_BRANCH": "feature"}, None),
                               ({}, "{}"), ({}, "not-json"),
                               ({}, json.dumps(self.record) + json.dumps(self.record)),
                               ({}, json.dumps({"source":{"repository":"other/app","revision":SHA}}))]:
            with self.subTest(overrides=overrides, raw=raw):
                self.reset()
                self.run_publisher(False, raw=raw, **overrides)
                self.assertFalse(self.state.get("branch"))

    def test_freshness_checked_immediately_before_write(self):
        self.state.update(branch=True, advance_on_check=2)
        self.run_publisher(False)
        self.assertFalse(any(method == "PUT" for method, _ in self.state["calls"]))

    def test_concurrent_pr_write_is_preserved_on_retry(self):
        self.state.update(branch=True, conflicts=1)
        self.run_publisher()
        self.assertEqual(json.loads(self.state["files"]["previews/pr-99.json"]), {"other":"preview"})
        self.assertIn("previews/pr-42.json", self.state["files"])

    def test_branch_initialization_race(self):
        self.state["branch_race"] = True
        self.run_publisher()

    def test_conflict_retries_recheck_source_and_are_bounded(self):
        self.state.update(branch=True, conflicts=1, close_on_conflict=True)
        self.run_publisher(False)
        self.assertNotIn("previews/pr-42.json", self.state["files"])
        self.reset()
        self.state.update(branch=True, conflicts=4)
        self.run_publisher(False)
        self.assertEqual(sum(method == "PUT" for method, _ in self.state["calls"]), 3)

    def test_permission_errors_do_not_initialize_or_retry(self):
        self.state["branch_status"] = 403
        self.run_publisher(False)
        self.assertFalse(self.state.get("branch"))
        self.reset()
        self.state.update(branch=True, write_status=403)
        self.run_publisher(False)
        self.assertEqual(sum(method == "PUT" for method, _ in self.state["calls"]), 1)

    def test_head_advance_after_commit_fails_without_success_outputs(self):
        self.state.update(branch=True, advance_after_write=True)
        self.assertEqual(self.run_publisher(False), "")
        self.assertIn("previews/pr-42.json", self.state["files"])


if __name__ == "__main__":
    unittest.main()
