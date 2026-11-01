"""Use real Helm lint/package/render; replace only the network push."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / ".github/actions/publish-release/publish.sh"
SHA = "a" * 40


class ReleasePublicationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="release-publisher-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.helm = os.environ.get("HELM_BIN") or shutil.which("helm")
        self.assertTrue(self.helm, "Install Helm or set HELM_BIN")
        self.chart = self.root / "central chart"
        (self.chart / "templates").mkdir(parents=True)
        (self.chart / "Chart.yaml").write_text("apiVersion: v2\nname: demo\nversion: 0.1.0\n")
        (self.chart / "values.yaml").write_text("message: default\nreplicas: 2\n")
        (self.chart / "templates/config.yaml").write_text(
            'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: demo\ndata:\n'
            '  message: {{ .Values.message | quote }}\n  replicas: {{ .Values.replicas | quote }}\n')
        (self.root / "release.json").write_text(json.dumps({"message": "selected artifact"}))
        self.env = {**os.environ, "REAL_HELM": str(self.helm),
                    "CHART": str(self.chart), "VALUES": str(self.root / "release.json"),
                    "DESTINATION": "oci://registry.example/releases", "VERSION": "0.12.1-g" + SHA,
                    "SOURCE_SHA": SHA, "REGISTRY_CONFIG": "", "GITHUB_OUTPUT": str(self.root / "output"),
                    "CAPTURE": str(self.root / "published.tgz"), "PUSH_ARGS": str(self.root / "push.json")}
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "helm"
        stub.write_text(f"#!{sys.executable}\n" + '''
import json, os, shutil, sys
from pathlib import Path
if sys.argv[1] != "push":
    os.execv(os.environ["REAL_HELM"], [os.environ["REAL_HELM"], *sys.argv[1:]])
Path(os.environ["PUSH_ARGS"]).write_text(json.dumps(sys.argv[1:]))
if os.environ.get("PUSH_FAIL"):
    raise SystemExit("Registry rejected the push")
shutil.copyfile(sys.argv[2], os.environ["CAPTURE"])
print("Pushed: registry.example/releases/demo:" + os.environ["VERSION"])
print("Digest: " + ("bad" if os.environ.get("BAD_DIGEST") else "sha256:" + "b"*64))
''')
        stub.chmod(0o755)
        self.env["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]

    def run_publisher(self, success=True, **overrides):
        (self.root / "output").write_text("")
        result = subprocess.run(["bash", str(SCRIPT)], env={**self.env, **overrides},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return (self.root / "output").read_text()

    def test_package_preserves_chart_defaults_and_embeds_selected_values(self):
        original = {p.relative_to(self.chart): p.read_bytes() for p in self.chart.rglob("*") if p.is_file()}
        (self.chart / ".git").mkdir()
        (self.chart / ".git/config").write_text("not release content")
        output = self.run_publisher()
        self.assertIn("reference=registry.example/releases/demo:" + self.env["VERSION"], output)
        self.assertIn("digest=sha256:" + "b"*64, output)
        with tarfile.open(self.root / "published.tgz") as archive:
            self.assertFalse(any("/.git/" in name for name in archive.getnames()))
            self.assertIn("appVersion: " + SHA, archive.extractfile("demo/Chart.yaml").read().decode())
            self.assertEqual(json.loads(archive.extractfile("demo/release-values.yaml").read()),
                             {"message": "selected artifact"})
        for name, content in original.items():
            self.assertEqual((self.chart / name).read_bytes(), content)
        self.assertFalse((self.chart / "release-values.yaml").exists())
        rendered = subprocess.check_output([self.helm, "template", "demo", str(self.root / "published.tgz"),
                                           "-f", str(self.root / "release.json")], text=True)
        self.assertIn('message: "selected artifact"', rendered)
        self.assertIn('replicas: "2"', rendered)

    def test_native_validation_prevents_invalid_releases(self):
        for overrides in [{"SOURCE_SHA": "short"}, {"VERSION": ""}, {"VERSION": "not-semver"},
                          {"DESTINATION": "https://registry.example"},
                          {"VALUES": str(self.root / "missing")}, {"CHART": str(self.root / "missing")}]:
            with self.subTest(overrides=overrides):
                self.run_publisher(False, **overrides)
                self.assertFalse((self.root / "push.json").exists())

    def test_invalid_values_and_excluded_overlay_do_not_push(self):
        (self.root / "release.json").write_text("message: [invalid")
        self.run_publisher(False)
        self.assertFalse((self.root / "push.json").exists())
        (self.root / "release.json").write_text("{}")
        (self.chart / ".helmignore").write_text("release-values.yaml\n")
        self.run_publisher(False)
        self.assertFalse((self.root / "push.json").exists())

    def test_existing_overlay_is_not_overwritten(self):
        overlay = self.chart / "release-values.yaml"
        overlay.write_text("reserved")
        self.run_publisher(False)
        self.assertEqual(overlay.read_text(), "reserved")
        self.assertFalse((self.root / "push.json").exists())

    def test_registry_config_is_forwarded_and_failed_push_has_no_outputs(self):
        self.run_publisher(REGISTRY_CONFIG="/private/registry config.json")
        self.assertEqual(json.loads((self.root / "push.json").read_text())[-2:],
                         ["--registry-config", "/private/registry config.json"])
        self.assertEqual(self.run_publisher(False, PUSH_FAIL="true"), "")
        self.assertEqual(self.run_publisher(False, BAD_DIGEST="true"), "")


if __name__ == "__main__":
    unittest.main()
