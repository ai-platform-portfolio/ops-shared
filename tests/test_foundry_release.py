"""Fail-closed release boundaries around native azd, without Azure mutations."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('foundry_release', ROOT / '.github/actions/foundry-deploy/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class FoundryReleaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.previous = Path.cwd()
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, self.previous)
        self.env = patch.dict(os.environ, {
            'AZURE_ENV_NAME': 'second-catalog-helper', 'PRESERVE_SERVING_VERSION': 'true',
            'SOURCE_REVISION': 'b' * 40, 'IMAGE': 'example.azurecr.io/helper@sha256:' + 'a' * 64,
            'GITHUB_OUTPUT': str(Path.cwd() / 'outputs'), 'RUNNER_TEMP': str(Path.cwd()),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        Path('azure.yaml').write_text(yaml.safe_dump({'services': {'helper': {
            'name': '${AZURE_ENV_NAME}', 'agentEndpoint': {
                'protocols': ['invocations'], 'authorizationSchemes': [{'type': 'Entra'}]},
        }}}))
        self.endpoint = {'protocol_configuration': {'invocations': {}},
                         'authorization_schemes': [{'type': 'Entra'}],
                         'version_selector': {'version_selection_rules': [
                             {'type': 'FixedRatio', 'agent_version': '7', 'traffic_percentage': 100}]}}

    def test_existing_route_remains_pinned_and_contract_preserved(self):
        with patch.object(release, 'azd', side_effect=[json.dumps({'agent_endpoint': self.endpoint}), '',
                                                     json.dumps({'agent_endpoint': self.endpoint})]) as cli:
            release.prepare('helper')
        declaration = yaml.safe_load(Path('azure.yaml').read_text())['services']['helper']
        self.assertEqual(declaration['name'], 'second-catalog-helper')
        self.assertEqual(declaration['agentEndpoint']['authorizationSchemes'], [{'type': 'Entra'}])
        self.assertEqual(declaration['agentEndpoint']['versionSelector']['versionSelectionRules'][0]['agentVersion'], '7')
        self.assertEqual(cli.call_args_list[1].args[:4], ('ai', 'agent', 'endpoint', 'update'))

    def test_only_explicit_not_found_allows_bootstrap(self):
        with patch.object(release, 'azd', side_effect=subprocess.CalledProcessError(1, 'azd', stderr='HTTP 404')):
            release.prepare('helper')
        with patch.object(release, 'azd', side_effect=subprocess.CalledProcessError(1, 'azd', stderr='HTTP 403')):
            with self.assertRaises(subprocess.CalledProcessError):
                release.prepare('helper')

    def test_native_error_on_stdout_allows_only_explicit_missing_agent(self):
        for message in ('HTTP 404: AgentNotFound', 'HTTP 403: Forbidden'):
            error = subprocess.CalledProcessError(1, 'azd', output=message, stderr='')
            with patch.object(release, 'azd', side_effect=error):
                if '404' in message:
                    release.prepare('helper')
                else:
                    with self.assertRaises(subprocess.CalledProcessError):
                        release.prepare('helper')

    def test_cli_failure_surfaces_status_without_identity_ids(self):
        result = subprocess.CompletedProcess(['azd'], 1,
            'HTTP 403: Forbidden for 12345678-1234-1234-1234-123456789abc', '')
        diagnostic = io.StringIO()
        with patch.object(release.subprocess, 'run', return_value=result), patch('sys.stderr', diagnostic):
            with self.assertRaises(subprocess.CalledProcessError):
                release.azd('ai', 'agent', 'show', 'helper')
        self.assertIn('HTTP 403', diagnostic.getvalue())
        self.assertIn('[redacted-id]', diagnostic.getvalue())
        self.assertNotIn('12345678-', diagnostic.getvalue())

    def test_unpinned_split_or_different_auth_route_is_not_overwritten(self):
        for change in ('unpinned', 'split', 'auth'):
            endpoint = copy.deepcopy(self.endpoint)
            if change == 'auth':
                endpoint['authorization_schemes'] = []
            elif change == 'unpinned':
                endpoint.pop('version_selector')
            else:
                endpoint['version_selector']['version_selection_rules'][0]['traffic_percentage'] = 50
            with patch.object(release, 'azd', return_value=json.dumps({'agent_endpoint': endpoint})) as cli:
                with self.assertRaises(ValueError):
                    release.prepare('helper')
                self.assertEqual(cli.call_count, 1)

    def test_failed_route_readback_blocks_candidate_deployment(self):
        changed = copy.deepcopy(self.endpoint)
        changed['version_selector']['version_selection_rules'][0]['agent_version'] = '8'
        with patch.object(release, 'azd', side_effect=[json.dumps({'agent_endpoint': self.endpoint}), '',
                                                     json.dumps({'agent_endpoint': changed})]):
            with self.assertRaisesRegex(ValueError, 'verified'):
                release.prepare('helper')

    def test_record_binds_ready_version_image_and_revision(self):
        candidate = {'name': 'second-catalog-helper', 'version': '8', 'status': 'Active',
                     'definition': {'container_configuration': {'image': os.environ['IMAGE']},
                                    'environment_variables': {'SOURCE_REVISION': os.environ['SOURCE_REVISION']}}}
        with patch.object(release, 'azd', return_value=json.dumps(candidate)):
            release.record('helper')
        self.assertEqual(json.loads(Path('foundry-release.json').read_text())['version'], '8')
        for invalid in ('image', 'revision', 'status'):
            altered = copy.deepcopy(candidate)
            if invalid == 'status':
                altered['status'] = 'Failed'
            elif invalid == 'image':
                altered['definition']['container_configuration']['image'] = 'example.azurecr.io/helper:latest'
            else:
                altered['definition']['environment_variables']['SOURCE_REVISION'] = 'c' * 40
            with patch.object(release, 'azd', return_value=json.dumps(altered)), self.assertRaises(ValueError):
                release.record('helper')


class FoundryConnectTests(unittest.TestCase):
    def setUp(self):
        self.subscription = '00000000-0000-4000-8000-000000000001'
        self.resource = {'name': 'account-one/demo', 'type': 'Microsoft.CognitiveServices/accounts/projects',
                         'id': f'/subscriptions/{self.subscription}/resourceGroups/platform/providers/Microsoft.CognitiveServices/accounts/account-one/projects/demo'}
        self.environment = patch.dict(os.environ, {'AZURE_ENV_NAME': 'another-consumer',
            'AZURE_SUBSCRIPTION_ID': self.subscription,
            'FOUNDRY_PROJECT_ENDPOINT': 'https://account-one.services.ai.azure.com/api/projects/demo'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.auth = patch.object(release.subprocess, 'run', return_value=subprocess.CompletedProcess(
            [], 0, json.dumps({'token': 'private-arm-token'}), ''))
        self.command = self.auth.start()
        self.addCleanup(self.auth.stop)
        self.factory = patch.object(release, 'build_opener')
        self.opener = self.factory.start().return_value
        self.addCleanup(self.factory.stop)
        self.write = patch.object(release, 'azd')
        self.cli = self.write.start()
        self.addCleanup(self.write.stop)
        self.page({'value': [self.resource]})

    def page(self, value):
        self.opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(value).encode()

    def test_existing_project_is_resolved_and_forwarded_before_deploy_without_logging_ids(self):
        console = io.StringIO()
        with patch('sys.stdout', console):
            release.connect()
        self.cli.assert_called_once_with('env', 'set', 'AZURE_AI_PROJECT_ID=' + self.resource['id'])
        self.assertEqual(self.command.call_args.args[0], ['azd', 'auth', 'token', '--output', 'json', '--no-prompt',
            '--scope', 'https://management.azure.com/.default'])
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer private-arm-token')
        self.assertTrue(request.full_url.startswith('https://management.azure.com/subscriptions/' + self.subscription + '/resources?'))
        self.assertNotIn(self.subscription, console.getvalue())
        self.assertNotIn('private-arm-token', console.getvalue())
        action = yaml.safe_load((ROOT / '.github/actions/foundry-deploy/action.yml').read_text())
        commands = [step.get('run', '') for step in action['runs']['steps']]
        connect = next(index for index, command in enumerate(commands) if command.endswith('release.py" connect'))
        deploy = next(index for index, command in enumerate(commands) if 'azd deploy ' in command)
        self.assertLess(connect, deploy)

    def test_missing_ambiguous_or_wrong_subscription_projects_never_configure_deployment(self):
        for resources in ([], [self.resource, {**self.resource, 'id': self.resource['id'].replace('/platform/', '/other/')}],
                          [{**self.resource, 'id': self.resource['id'].replace(self.subscription, '00000000-0000-4000-8000-000000000002')}],
                          [{**self.resource, 'name': 'different-account/demo'}]):
            with self.subTest(resources=resources):
                self.page({'value': resources})
                with self.assertRaises(ValueError):
                    release.connect()
                self.cli.assert_not_called()

    def test_pagination_stays_on_arm_and_never_forwards_a_token_to_redirects(self):
        from unittest.mock import MagicMock
        next_url = f'https://management.azure.com/subscriptions/{self.subscription}/resources?continuation=next'
        first, second = MagicMock(), MagicMock()
        first.__enter__.return_value.read.return_value = json.dumps({'value': [], 'nextLink': next_url}).encode()
        second.__enter__.return_value.read.return_value = json.dumps({'value': [self.resource]}).encode()
        self.opener.open.side_effect = [first, second]
        with patch('sys.stdout', io.StringIO()):
            release.connect()
        self.assertEqual(self.opener.open.call_count, 2)
        self.opener.open.reset_mock(side_effect=True)
        self.cli.reset_mock()
        self.page({'value': [], 'nextLink': 'https://untrusted.example/subscriptions/resources'})
        with self.assertRaises(ValueError):
            release.connect()
        self.assertEqual(self.opener.open.call_count, 1)
        self.cli.assert_not_called()
        self.assertIsNone(release.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://untrusted.example'))

    def test_auth_lookup_and_native_configuration_errors_do_not_disclose_tokens_or_ids(self):
        from urllib.error import HTTPError
        self.command.return_value = subprocess.CompletedProcess([], 1, 'private-arm-token', 'private-error')
        with self.assertRaisesRegex(RuntimeError, 'ARM authentication failed'):
            release.connect()
        self.opener.open.assert_not_called()
        self.command.return_value = subprocess.CompletedProcess([], 0, json.dumps({'token': 'private-arm-token'}), '')
        self.opener.open.side_effect = HTTPError('https://management.azure.com/' + self.subscription, 403,
                                               'private-error', {}, None)
        with self.assertRaisesRegex(RuntimeError, '^Existing project lookup failed [(]HTTP 403[)]$'):
            release.connect()
        self.cli.assert_not_called()
        self.opener.open.side_effect = None
        self.cli.side_effect = subprocess.CalledProcessError(1, ['azd', self.resource['id']], output='private-error')
        with self.assertRaisesRegex(RuntimeError, '^Native project configuration failed [(]exit 1[)]$'):
            release.connect()

    def test_invalid_endpoints_fail_before_authentication(self):
        for endpoint in ('http://account-one.services.ai.azure.com/api/projects/demo',
                         'https://user:secret@account-one.services.ai.azure.com/api/projects/demo',
                         'https://account-one.services.ai.azure.com/api/projects/demo?token=secret',
                         'https://untrusted.example/api/projects/demo'):
            with patch.dict(os.environ, {'FOUNDRY_PROJECT_ENDPOINT': endpoint}), self.assertRaises(ValueError):
                release.connect()
        self.command.assert_not_called()
