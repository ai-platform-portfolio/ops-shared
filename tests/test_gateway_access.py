"""Execute the scalar action's admission boundary with two small native CLI stubs."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / '.github/actions/gateway-access/access.sh'


class GatewayAccessTests(unittest.TestCase):
    def configure(self, budget=1, exit_code=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = root / 'bin'
            tools.mkdir()
            chart = root / 'chart'
            chart.mkdir()
            subprocess.run(['git', 'init', '-q', str(chart)], check=True)
            subprocess.run(['git', '-C', str(chart), '-c', 'user.name=Test', '-c', 'user.email=test@example.test',
                            'commit', '-qm', 'fixture', '--allow-empty'], check=True)
            revision = subprocess.check_output(['git', '-C', str(chart), 'rev-parse', 'HEAD'], text=True).strip()
            (root / 'config').mkdir()
            (root / 'config/agent.yaml').write_text('gatewayAccess: {policy: bounded-agent}\n')
            defaults = {'modelGateway': {'policies': {'bounded-agent': {'maxBudget': budget, 'rpm': 1, 'tpm': 1000, 'parallel': 1}}}}
            log = json.dumps({'data': {'values.json': json.dumps(defaults)}})
            (root / 'response.json').write_text(json.dumps({'exitCode': exit_code, 'logs': log}))
            (root / 'render.yaml').write_text('kind: ConfigMap\ndata:\n  connection.json: \'{"MODEL_CONNECTION_MODE":"gateway","MODEL_API_KEY_SECRET_URI":"https://test.vault.azure.net/secrets/scoped"}\'\n')
            for name, source in {'az': 'cat "$AZ_RESPONSE"', 'helm': 'cat "$HELM_RESPONSE"'}.items():
                path = tools / name
                path.write_text('#!/bin/sh\n' + source + '\n')
                path.chmod(0o755)
            env = {**os.environ, 'PATH': str(tools) + ':' + os.environ['PATH'], 'OPERATION': 'configure',
                   'SERVICE': 'second-repo-agent', 'CHART_REVISION': revision, 'CHART_DIRECTORY': str(chart),
                   'GITHUB_SHA': 'a'*40, 'GITHUB_REPOSITORY': 'other/repo', 'GITHUB_WORKSPACE': str(root),
                   'AGENT_DIRECTORY': str(root), 'RUNNER_TEMP': str(root), 'RESOURCE_GROUP': 'example', 'CLUSTER': 'example',
                   'GITHUB_ENV': str(root / 'env'), 'GITHUB_OUTPUT': str(root / 'output'),
                   'AZ_RESPONSE': str(root / 'response.json'), 'HELM_RESPONSE': str(root / 'render.yaml')}
            result = subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True, text=True)
            return result, (root / 'env').read_text() if (root / 'env').exists() else ''

    def test_unset_ceilings_block_before_connection_or_credentials(self):
        result, exported = self.configure(budget=None)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('admission blocked', result.stderr)
        self.assertEqual(exported, '')

    def test_only_public_chart_connection_is_forwarded(self):
        result, exported = self.configure()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('MODEL_CONNECTION_MODE=gateway', exported)
        self.assertIn('MODEL_API_KEY_SECRET_URI=', exported)
        self.assertNotIn('MODEL_API_KEY=', exported)

    def test_remote_failure_does_not_export_a_connection(self):
        result, exported = self.configure(exit_code=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(exported, '')
