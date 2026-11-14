"""Check the wheel/source handoff; release operations use native GitHub CLI."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[1]


class PythonPackageTests(unittest.TestCase):
    def test_wheel_hash_and_source_are_bound_before_publication(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/python-package.yml').read_text())
        script = next(s['run'] for s in workflow['jobs']['build']['steps'] if s.get('name') == 'Record wheel identity')
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / 'python-package'
            artifact.mkdir()
            wheel = artifact / 'another_package-0.1.0-py3-none-any.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('another_package-0.1.0.dist-info/METADATA', 'Name: another-package\nVersion: 0.1.0\n')
            output = root / 'outputs'
            env = {**os.environ, 'RUNNER_TEMP': directory, 'GITHUB_OUTPUT': str(output), 'SOURCE_SHA': revision}
            subprocess.run(['bash', '-euo', 'pipefail', '-c', script], env=env, cwd=ROOT, check=True)
            recorded = dict(line.split('=', 1) for line in output.read_text().splitlines())
            self.assertEqual(recorded, {'wheel': wheel.name, 'version': '0.1.0', 'revision': revision,
                                        'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()})
            output.unlink()
            invalid = subprocess.run(['bash', '-euo', 'pipefail', '-c', script],
                                     env={**env, 'SOURCE_SHA': 'invalid'}, cwd=ROOT, capture_output=True)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertFalse(output.exists())
