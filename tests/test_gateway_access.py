"""Execute the scalar action's admission boundary with two small native CLI stubs."""
import base64
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
            log = json.dumps({'data': {'values.json': json.dumps(defaults), 'hosted-generation': '1'*24, 'traces-endpoint': 'https://otel.example.test/v1/traces'}})
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
            published = root / 'output'
            self.assertEqual('traces-endpoint=https://otel.example.test/v1/traces' in
                             (published.read_text() if published.exists() else ''), result.returncode == 0)
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


class PublicationTests(unittest.TestCase):
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
        self.name = 'hosted-' + 'b'*24
        self.record = root / 'release.json'
        self.release = {'agent': 'repo-agent', 'source_revision': 'a'*40, 'version': '1',
                        'instance_principal_id': self.principal, 'image': 'registry/image@sha256:'+'1'*64}
        self.record.write_text(json.dumps(self.release))
        routing = root / 'routing.json'
        routing.write_text(json.dumps({'smoke': {'deployment': self.release}}))
        (root/'deploy').mkdir()
        (root/'deploy/hosted.yaml').write_text('hostedAgents: {agent: {config: agents/agent/config/agent.yaml, alerts: {readiness: true}}}')
        environment = patch.dict(os.environ, {'RUNNER_TEMP': str(root), 'SERVICE': 'repo-agent',
                                              'GITHUB_REPOSITORY': 'org/repo', 'GITHUB_WORKSPACE': str(root),
                                              'AGENT_DIRECTORY': str(root/'agents/agent'),
                                              'GITHUB_SHA': 'a'*40, 'RELEASE_RECORD': str(self.record),
                                              'ROUTING_RECORD': str(routing)})
        environment.start()
        self.addCleanup(environment.stop)
        self.access.directory().mkdir()
        (self.access.directory() / 'generation').write_text('1'*24)
        values = {'name': self.name, 'platform': {'modelGateway': {},
                  'telemetry': {'namespace': 'preview-one', 'workload': 'api'}}}
        (self.access.directory() / 'values.json').write_text(json.dumps(values))
        self.actual = {'metadata': {
            'ownerReferences': [{'kind': 'ApplicationSet', 'name': 'application-hosted', 'controller': True}],
            'annotations': {'platform.ai-platform-portfolio/hosted-principal': self.principal,
                            'platform.ai-platform-portfolio/hosted-source': 'a'*40,
                            'platform.ai-platform-portfolio/hosted-version': '1',
                            'platform.ai-platform-portfolio/hosted-alerts': 'false'}},
            'spec': {'sources': ['reviewed-source']}, 'status': {'sync': {
            'status': 'Synced', 'comparedTo': {'sources': ['reviewed-source']}}, 'health': {'status': 'Healthy'}}}

    def test_successful_command_output_can_be_null_empty_or_text(self):
        from unittest.mock import patch
        for logs, expected in [(None, ''), ('', ''), ('  result\n', 'result')]:
            with self.subTest(logs=logs), \
                 patch.dict(os.environ, {'RESOURCE_GROUP': 'example', 'CLUSTER': 'example'}), \
                 patch.object(self.access, 'command', return_value=json.dumps({'exitCode': 0, 'logs': logs})):
                self.assertEqual(self.access.invoke('--command', 'kubectl get --ignore-not-found'), expected)

    def test_failed_command_with_null_output_still_raises(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {'RESOURCE_GROUP': 'example', 'CLUSTER': 'example'}), \
             patch.object(self.access, 'command', return_value=json.dumps({'exitCode': 1, 'logs': None})), \
             self.assertRaisesRegex(RuntimeError, 'Gateway command failed'):
            self.access.invoke('--command', 'kubectl get')

    def test_only_exact_safe_accounting_code_is_exposed(self):
        from unittest.mock import patch
        for log, expected in [
                ('apply complete\nACCOUNTING_RESTORE_REQUIRED\nprivate-api-response', 'ACCOUNTING_RESTORE_REQUIRED'),
                ('private-api-response ACCOUNTING_RESTORE_REQUIRED', 'Gateway command failed'),
                ('UNKNOWN_CODE\nprivate-api-response', 'Gateway command failed')]:
            with self.subTest(log=log), \
                 patch.dict(os.environ, {'RESOURCE_GROUP': 'example', 'CLUSTER': 'example'}), \
                 patch.object(self.access, 'command', return_value=json.dumps({'exitCode': 1, 'logs': log})), \
                 self.assertRaisesRegex(RuntimeError, expected) as error:
                self.access.invoke('--command', 'kubectl wait')
            self.assertNotIn('private-api-response', str(error.exception))

    def test_mismatched_release_stops_before_any_cluster_command(self):
        from unittest.mock import patch
        self.record.write_text(json.dumps({**self.release, 'agent': 'other-consumer'}))
        with patch.object(self.access, 'invoke') as invoke, self.assertRaises(ValueError):
            self.access.publish('candidate')
        invoke.assert_not_called()

    def test_principal_replacement_stops_before_registration(self):
        from unittest.mock import patch
        previous = {'metadata': {}, 'spec': {'sources': [{'helm': {'valuesObject': {'platform': {'modelGateway': {'principalId': 'another-principal'}}}}}]}}
        with patch.object(self.access, 'invoke', return_value=json.dumps(previous)) as invoke, self.assertRaisesRegex(ValueError, 'principal changed'):
            self.access.publish('candidate')
        self.assertEqual(invoke.call_count, 1)

    def reconcile(self, snapshots, *, time_step):
        from unittest.mock import patch
        root = Path(self.temp.name)
        (root / 'snapshots').write_text(''.join(json.dumps(item) + '\n' for item in snapshots))
        (root / 'reads').write_text('0\n')
        with patch.object(self.access, 'render', return_value='kind: Job\nmetadata: {name: credential-job, namespace: litellm}'), \
             patch.object(self.access, 'publish_to_git', side_effect=lambda generation, facts: facts), \
             patch.object(self.access, 'invoke', side_effect=['', '']) as invoke:
            self.access.publish('candidate')
        self.assertEqual(invoke.call_count, 2)
        remote_command = invoke.call_args_list[1].args[-1]
        stubs = '''
now=0
date() { printf '%s\n' "$now"; }
sleep() { now=$((now + TIME_STEP)); }
kubectl() {
  case "$1" in
    apply|patch|create) exit 90 ;;
    get)
      count=$(($(cat reads) + 1))
      printf '%s\n' "$count" > reads
      sed -n "${count}p" snapshots ;;
    logs) printf '%s\n' 'private log message' 'Gateway credential operation failed: ACCOUNTING_RESTORE_REQUIRED' ;;
  esac
}
'''
        return subprocess.run(['sh', '-c', stubs + remote_command], cwd=root, capture_output=True,
                              text=True, env={**os.environ, 'TIME_STEP': str(time_step)}), int((root / 'reads').read_text())

    def test_stale_healthy_status_cannot_admit_candidate(self):
        stale = json.loads(json.dumps(self.actual))
        stale['status']['sync']['comparedTo']['sources'] = ['previous-source']
        result, reads = self.reconcile([stale], time_step=240)
        self.assertEqual(reads, 1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), ['ACCOUNTING_RESTORE_REQUIRED', 'CREDENTIAL_APPLICATION_NOT_READY'])

    def test_matching_argo_owner_current_source_and_principal_admit_candidate(self):
        stale = json.loads(json.dumps(self.actual))
        stale['status']['sync']['comparedTo']['sources'] = ['previous-source']
        wrong_source = {'spec': {'sources': ['different-source']},
                        'status': {'sync': {'status': 'Synced', 'comparedTo': {'sources': ['different-source']}},
                                   'health': {'status': 'Healthy'}}}
        result, reads = self.reconcile([stale, wrong_source, self.actual], time_step=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(reads, 3)
        self.assertEqual(result.stdout, '')
        values = json.loads((self.access.directory() / 'values.json').read_text())
        self.assertEqual(values['platform']['modelGateway']['principalId'], self.principal)
        self.assertFalse(values['platform']['modelGateway']['connectionOnly'])

    def test_publication_is_generation_scoped_and_contains_only_release_facts(self):
        from unittest.mock import patch
        values = json.loads((self.access.directory() / 'values.json').read_text())
        facts = self.access.release_facts(values, 'candidate')
        self.assertFalse(facts['alerts'])
        self.assertTrue(self.access.release_facts(values, 'serving')['alerts'])
        with patch.object(self.access, 'github', side_effect=[{'ref': 'refs/heads/delivery'}, None, {}]) as github:
            self.access.publish_to_git('1'*24, facts)
        method, path, payload = github.call_args.args
        self.assertEqual(method, 'PUT')
        self.assertEqual(path, 'repos/org/repo/contents/deploy/hosted/'+'1'*24+'/agent.json')
        self.assertEqual(payload['branch'], 'delivery')
        self.assertEqual(json.loads(base64.b64decode(payload['content'])), facts)
        self.assertEqual(set(facts), {'agent', 'sourceRevision', 'principalId', 'version', 'alerts', 'telemetry'})

    def test_candidate_retry_preserves_an_identical_verified_serving_publication(self):
        from unittest.mock import patch
        values = json.loads((self.access.directory() / 'values.json').read_text())
        candidate = self.access.release_facts(values, 'candidate')
        serving = self.access.release_facts(values, 'serving')
        content = base64.b64encode(json.dumps(serving).encode()).decode()
        with patch.object(self.access, 'github', side_effect=[{'ref': 'delivery'}, {'content': content}]) as github:
            self.assertEqual(self.access.publish_to_git('1'*24, candidate), serving)
        self.assertEqual(github.call_count, 2)

    def test_serving_publication_rejects_a_different_smoke_version(self):
        Path(os.environ['ROUTING_RECORD']).write_text(json.dumps({'smoke': {'deployment': {**self.release, 'version': '2'}}}))
        values = json.loads((self.access.directory() / 'values.json').read_text())
        with self.assertRaisesRegex(ValueError, 'Stable smoke must match'):
            self.access.release_facts(values, 'serving')
