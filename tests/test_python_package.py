"""Artifact identity and non-overwriting GitHub release publication, without writes to GitHub."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[1]


class PythonPackageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.artifact = self.root / 'python-package'
        self.artifact.mkdir()
        self.wheel = self.artifact / 'second_package-0.1.0-py3-none-any.whl'
        with zipfile.ZipFile(self.wheel, 'w') as wheel:
            wheel.writestr('second_package-0.1.0.dist-info/METADATA', 'Name: second-package\nVersion: 0.1.0\n')
        self.env = {**os.environ, 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
                    'SOURCE_SHA': 'a' * 40, 'RUNNER_TEMP': str(self.root),
                    'ARTIFACT_DIRECTORY': str(self.artifact), 'RELEASE_PREFIX': 'second-v',
                    'GITHUB_REPOSITORY': 'example/another-library',
                    'GITHUB_OUTPUT': str(self.root / 'outputs'), 'REMOTE': str(self.root / 'remote')}
        (self.root / 'remote').mkdir()
        git = self.bin / 'git'
        git.write_text('#!/bin/bash\nif [[ "$1" == -C ]]; then shift 2; fi\n'
                       'case "$1" in rev-parse) echo "$SOURCE_SHA";; ls-remote) exit 2;; check-ref-format) exit 0;; *) exit 1;; esac\n')
        git.chmod(0o755)
        gh = self.bin / 'gh'
        gh.write_text(f'#!{sys.executable}\n' + '''import json, os, shutil, sys
from pathlib import Path
args=sys.argv[1:]; root=Path(os.environ['REMOTE']); state=root/'state.json'
if args[:2]==['auth','setup-git']:
    sys.exit(0)
if args[0]=='api':
    if not state.exists():
        print('gh: Not Found (HTTP 404)',file=sys.stderr);sys.exit(1)
    print(state.read_text());sys.exit(0)
operation=args[1]
if operation=='create':
    data={'target_commitish':args[args.index('--target')+1],'draft':True,'assets':[]}
else:
    data=json.loads(state.read_text())
if operation=='upload':
    file=Path(args[3]);shutil.copyfile(file,root/file.name);data['assets'].append({'name':file.name})
elif operation=='download':
    name=args[args.index('--pattern')+1];shutil.copyfile(root/name,Path(args[args.index('--dir')+1])/name)
elif operation=='edit':
    data['draft']=False
state.write_text(json.dumps(data))
''')
        gh.chmod(0o755)
        workflow = yaml.safe_load((ROOT / '.github/workflows/python-package.yml').read_text())
        script = next(s['run'] for s in workflow['jobs']['build']['steps'] if s.get('name') == 'Record wheel identity')
        subprocess.run(['bash', '-euo', 'pipefail', '-c', script], env=self.env, check=True)
        self.record = json.loads((self.artifact / 'python-package.json').read_text())
        self.env['EXPECTED_SHA256'] = self.record['sha256']

    def publish(self):
        return subprocess.run(['bash', str(ROOT / 'scripts/python-package.sh')], env=self.env,
                              capture_output=True, text=True)

    def test_metadata_binds_one_wheel_and_source_without_consumer_names(self):
        self.assertEqual(self.record['name'], 'second-package')
        self.assertEqual(self.record['revision'], 'a' * 40)
        self.assertEqual(self.record['sha256'], hashlib.sha256(self.wheel.read_bytes()).hexdigest())

    def test_publication_and_identical_retry_keep_existing_bytes(self):
        first = self.publish()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = {p.name: p.read_bytes() for p in (self.root / 'remote').iterdir()}
        again = self.publish()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.root / 'remote').iterdir()})
        self.assertFalse(json.loads((self.root / 'remote/state.json').read_text())['draft'])

    def test_tampered_build_and_changed_published_asset_cannot_overwrite(self):
        self.assertEqual(self.publish().returncode, 0)
        remote = self.root / 'remote' / self.wheel.name
        remote.write_bytes(b'changed')
        self.assertNotEqual(self.publish().returncode, 0)
        self.assertEqual(remote.read_bytes(), b'changed')
        self.wheel.write_bytes(b'tampered-build')
        self.assertNotEqual(self.publish().returncode, 0)
