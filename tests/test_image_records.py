"""Check immutable image handoff boundaries without registry or Azure access."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ImageRecordTests(unittest.TestCase):
    def test_records_keep_each_image_digest_and_source_together(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/docker.yml').read_text())
        script = next(s['run'] for s in workflow['jobs']['docker']['steps']
                      if s.get('name') == 'Record published image identity')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'image.json'
            for agent, digest in [('one', 'a' * 64), ('two', 'b' * 64)]:
                image = f'example.azurecr.io/agents/owner/catalog/{agent}:build'
                env = {**os.environ, 'RUNNER_TEMP': directory, 'IMAGE': image,
                       'DIGEST': 'sha256:' + digest, 'REVISION': 'c' * 40,
                       'GITHUB_REPOSITORY': 'owner/catalog'}
                subprocess.run(['bash', '-euo', 'pipefail', '-c', script], env=env, check=True)
                self.assertEqual(json.loads(target.read_text()), {
                    'image': image, 'digest': 'sha256:' + digest,
                    'revision': 'c' * 40, 'repository': 'owner/catalog',
                })
            target.unlink()
            failed = subprocess.run(['bash', '-euo', 'pipefail', '-c', script],
                                    env={**env, 'DIGEST': ''}, capture_output=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse(target.exists())

    def test_native_prebuilt_selection_rejects_mutable_images(self):
        action = yaml.safe_load((ROOT / '.github/actions/foundry-deploy/action.yml').read_text())
        script = next(s['run'] for s in action['runs']['steps']
                      if s.get('name') == 'Select prebuilt image when supplied')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            azd = root / 'azd'
            azd.write_text(f'#!{sys.executable}\n' +
                           'import json,os,sys\n' +
                           'open(os.environ["CAPTURE"],"w").write(json.dumps(sys.argv[1:]))\n')
            azd.chmod(0o755)
            capture = root / 'capture'
            image = 'example.azurecr.io/agent@sha256:' + 'a' * 64
            env = {**os.environ, 'PATH': directory + os.pathsep + os.environ['PATH'],
                   'IMAGE': image, 'AZURE_ENV_NAME': 'catalog-agent', 'CAPTURE': str(capture)}
            subprocess.run(['bash', '-euo', 'pipefail', '-c', script], env=env, check=True)
            self.assertEqual(json.loads(capture.read_text()), [
                'env', 'set', 'HOSTED_AGENT_IMAGE=' + image, 'AZD_AGENT_SKIP_ACR=true',
                '--environment', 'catalog-agent', '--no-prompt',
            ])
            capture.unlink()
            for invalid in ('example.azurecr.io/agent:latest', 'agent@sha256:' + 'a' * 64):
                failed = subprocess.run(['bash', '-euo', 'pipefail', '-c', script],
                                        env={**env, 'IMAGE': invalid}, capture_output=True)
                self.assertNotEqual(failed.returncode, 0)
                self.assertFalse(capture.exists())
