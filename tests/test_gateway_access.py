"""Execute the scalar action's admission boundary with two small native CLI stubs."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / '.github/actions/gateway-access/access.py'


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
            result = subprocess.run([os.environ.get('PYTHON', 'python3'), str(SCRIPT)], env=env, capture_output=True, text=True)
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


class ApplyTests(unittest.TestCase):
    def setUp(self):
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('gateway_access', SCRIPT)
        self.access = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.access)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.principal = '00000000-0000-0000-0000-000000000003'
        self.record = root / 'release.json'
        self.record.write_text(json.dumps({'agent': 'consumer', 'source_revision': 'a'*40, 'instance_principal_id': self.principal}))
        environment = patch.dict(os.environ, {'RUNNER_TEMP': str(root), 'SERVICE': 'consumer',
                                              'GITHUB_SHA': 'a'*40, 'RELEASE_RECORD': str(self.record)})
        environment.start()
        self.addCleanup(environment.stop)
        self.access.directory().mkdir()
        (self.access.directory() / 'values.json').write_text(json.dumps({'name': 'hosted-consumer', 'platform': {'modelGateway': {}}}))
        self.actual = {'spec': {'sources': ['reviewed-source']}, 'status': {'sync': {
            'status': 'Synced', 'comparedTo': {'sources': ['reviewed-source']}}, 'health': {'status': 'Healthy'}}}

    def test_mismatched_release_stops_before_any_cluster_command(self):
        from unittest.mock import patch
        self.record.write_text(json.dumps({'agent': 'other-consumer'}))
        with patch.object(self.access, 'invoke') as invoke, self.assertRaises(ValueError):
            self.access.apply()
        invoke.assert_not_called()

    def test_principal_replacement_stops_before_registration(self):
        from unittest.mock import patch
        previous = {'spec': {'sources': [{'helm': {'valuesObject': {'platform': {'modelGateway': {'principalId': 'another-principal'}}}}}]}}
        with patch.object(self.access, 'invoke', return_value=json.dumps(previous)) as invoke, self.assertRaisesRegex(ValueError, 'principal changed'):
            self.access.apply()
        self.assertEqual(invoke.call_count, 1)

    def test_stale_healthy_status_cannot_admit_candidate(self):
        from unittest.mock import patch
        self.actual['status']['sync']['comparedTo']['sources'] = ['previous-source']
        with patch.object(self.access, 'render', return_value='registration'), \
             patch.object(self.access, 'invoke', side_effect=['', '', json.dumps(self.actual)]), \
             self.assertRaisesRegex(ValueError, 'current source'):
            self.access.apply()

    def test_matching_current_source_and_principal_complete_registration(self):
        from unittest.mock import patch
        with patch.object(self.access, 'render', return_value='registration'), \
             patch.object(self.access, 'invoke', side_effect=['', '', json.dumps(self.actual)]) as invoke:
            self.access.apply()
        self.assertEqual(invoke.call_count, 3)
        remote_command = invoke.call_args_list[1].args[-1]
        self.assertIn('kubectl apply -f registration.yaml', remote_command)
        for call in invoke.call_args_list:
            self.assertNotIn(' application/', call.args[-1])
        self.assertIn('applications.argoproj.io/hosted-consumer', remote_command)
        self.assertIn('applications.argoproj.io/hosted-consumer', invoke.call_args_list[0].args[-1])
        self.assertIn('applications.argoproj.io/hosted-consumer', invoke.call_args_list[2].args[-1])
        self.assertIn('set -eu', remote_command)
        values = json.loads((self.access.directory() / 'values.json').read_text())
        self.assertEqual(values['platform']['modelGateway']['principalId'], self.principal)
        self.assertFalse(values['platform']['modelGateway']['connectionOnly'])
