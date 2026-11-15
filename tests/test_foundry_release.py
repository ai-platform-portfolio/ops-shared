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

from azure.core.exceptions import ClientAuthenticationError, HttpResponseError
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
            'FOUNDRY_PROJECT_ENDPOINT': 'https://example.services.ai.azure.com/api/projects/demo',
            'SOURCE_REVISION': 'b' * 40, 'IMAGE': 'example.azurecr.io/helper@sha256:' + 'a' * 64,
            'GITHUB_OUTPUT': str(Path.cwd() / 'outputs'), 'RUNNER_TEMP': str(Path.cwd()),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.credential_factory = patch.object(release, 'AzureDeveloperCliCredential')
        self.credential = self.credential_factory.start()
        self.addCleanup(self.credential_factory.stop)
        self.project_factory = patch.object(release, 'AIProjectClient')
        self.sdk = self.project_factory.start()
        self.addCleanup(self.project_factory.stop)
        self.project = self.sdk.return_value.__enter__.return_value
        self.bootstrap = {'name': 'second-catalog-helper',
                          'versions': {'latest': {'version': '1', 'status': 'Active'}}}
        self.project.agents.get.return_value.as_dict.return_value = self.bootstrap
        Path('azure.yaml').write_text(yaml.safe_dump({'services': {'helper': {
            'name': '${AZURE_ENV_NAME}', 'agentEndpoint': {
                'protocols': ['invocations'], 'authorizationSchemes': [{'type': 'Entra'}]},
        }}}))
        self.endpoint = {'protocol_configuration': {'invocations': {}},
                         'authorization_schemes': [{'type': 'Entra'}],
                         'version_selector': {'version_selection_rules': [
                             {'type': 'FixedRatio', 'agent_version': '7', 'traffic_percentage': 100}]}}

    def test_runtime_grant_is_project_scoped_stable_and_records_checked_assignment(self):
        project = '/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/example/providers/Microsoft.CognitiveServices/accounts/example/projects/demo'
        principal, role = '22222222-2222-2222-2222-222222222222', '33333333-3333-3333-3333-333333333333'
        record = Path('release.json')
        record.write_text(json.dumps({'instance_principal_id': principal}))
        commands = []

        def run(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps({
                'principalId': principal, 'scope': project, 'roleDefinitionId': '/roles/' + role,
                'id': project + '/providers/Microsoft.Authorization/roleAssignments/' + command[5]}))

        with patch.dict(os.environ, PROJECT_ID=project, RUNTIME_PROJECT_ROLE_ID=role, RELEASE_RECORD=str(record)), \
             patch.object(release.subprocess, 'run', side_effect=run):
            release.grant('helper')
            release.grant('helper')
            self.assertEqual(commands[0], commands[1])
            self.assertEqual(commands[0][commands[0].index('--scope') + 1], project)
            self.assertIn('--assignee-object-id', commands[0])
            self.assertIn('runtime_role_assignment_id', json.loads(record.read_text()))
            os.environ['PROJECT_ID'] = '/subscriptions/11111111-1111-1111-1111-111111111111'
            with self.assertRaises(ValueError):
                release.grant('helper')
            self.assertEqual(len(commands), 2)

    def test_runtime_grant_rejects_mismatched_readback_and_hides_cli_diagnostics(self):
        record = Path('release.json')
        record.write_text(json.dumps({'instance_principal_id': '22222222-2222-2222-2222-222222222222'}))
        project = '/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/example/providers/Microsoft.CognitiveServices/accounts/example/projects/demo'
        with patch.dict(os.environ, PROJECT_ID=project, RUNTIME_PROJECT_ROLE_ID='33333333-3333-3333-3333-333333333333', RELEASE_RECORD=str(record)):
            with patch.object(release.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps({
                    'principalId': 'another-principal', 'scope': project, 'roleDefinitionId': '/roles/other'}))):
                with self.assertRaises(ValueError):
                    release.grant('helper')
            with patch.object(release.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'private detail')):
                with self.assertRaises(RuntimeError) as error:
                    release.grant('helper')
                self.assertNotIn('private detail', str(error.exception))

    def test_existing_route_remains_pinned_and_contract_preserved(self):
        with patch.object(release, 'azd', side_effect=[json.dumps({'agent_endpoint': self.endpoint}), '',
                                                     json.dumps({'agent_endpoint': self.endpoint})]) as cli:
            release.prepare('helper')
        declaration = yaml.safe_load(Path('azure.yaml').read_text())['services']['helper']
        self.assertEqual(declaration['name'], 'second-catalog-helper')
        self.assertEqual(declaration['agentEndpoint']['authorizationSchemes'], [{'type': 'Entra'}])
        self.assertEqual(declaration['agentEndpoint']['versionSelector']['versionSelectionRules'][0]['agentVersion'], '7')
        self.assertEqual(cli.call_args_list[1].args[:4], ('ai', 'agent', 'endpoint', 'update'))

    def test_native_bootstrap_alias_is_pinned_and_verified_before_deployment(self):
        initial = copy.deepcopy(self.endpoint)
        initial['version_selector']['version_selection_rules'][0]['agent_version'] = '@latest'
        pinned = copy.deepcopy(self.endpoint)
        pinned['version_selector']['version_selection_rules'][0]['agent_version'] = '1'
        with patch.object(release, 'azd', side_effect=[json.dumps({'agent_endpoint': initial}),
                '', json.dumps({'agent_endpoint': pinned})]) as cli:
            release.prepare('helper')
        self.project.agents.get.assert_called_once_with('second-catalog-helper')
        self.credential.assert_called_once_with(process_timeout=30)
        self.sdk.assert_called_once_with(endpoint=os.environ['FOUNDRY_PROJECT_ENDPOINT'],
            credential=self.credential.return_value.__enter__.return_value,
            connection_timeout=5, read_timeout=15, retry_total=0)
        self.assertEqual(cli.call_args_list[1].args, ('ai', 'agent', 'endpoint', 'update', 'helper'))
        self.assertFalse(Path('.azure').exists())
        declaration = yaml.safe_load(Path('azure.yaml').read_text())['services']['helper']['agentEndpoint']
        self.assertEqual(declaration['versionSelector']['versionSelectionRules'][0]['agentVersion'], '1')
        self.assertEqual(declaration['authorizationSchemes'], [{'type': 'Entra'}])
        self.assertEqual(Path(os.environ['GITHUB_OUTPUT']).read_text(), 'serving-version=1\n')

    def test_latest_alias_cannot_freeze_later_unready_or_different_agents(self):
        endpoint = copy.deepcopy(self.endpoint)
        endpoint['version_selector']['version_selection_rules'][0]['agent_version'] = '@latest'
        for changed in ({'version': '2'}, {'version': None}, {'name': 'another-agent'}, {'status': 'Failed'}):
            agent = copy.deepcopy(self.bootstrap)
            if 'name' in changed:
                agent.update(changed)
            else:
                agent['versions']['latest'].update(changed)
            self.project.agents.get.return_value.as_dict.return_value = agent
            with self.subTest(changed=changed), patch.object(release, 'azd', return_value=json.dumps({'agent_endpoint': endpoint})) as cli:
                with self.assertRaisesRegex(ValueError, 'review the serving route'):
                    release.prepare('helper')
                self.assertEqual(cli.call_count, 1)
                self.assertNotIn('versionSelector', yaml.safe_load(Path('azure.yaml').read_text())['services']['helper']['agentEndpoint'])

    def test_other_aliases_and_selector_types_are_not_overwritten(self):
        for alias, kind in (('latest', 'FixedRatio'), ('@preview', 'FixedRatio'), ('@latest', 'Unknown')):
            endpoint = copy.deepcopy(self.endpoint)
            endpoint['version_selector']['version_selection_rules'][0].update(agent_version=alias, type=kind)
            with self.subTest(alias=alias, kind=kind), patch.object(release, 'azd', return_value=json.dumps({'agent_endpoint': endpoint})) as cli:
                with self.assertRaisesRegex(ValueError, 'explicit numeric version'):
                    release.prepare('helper')
                self.assertEqual(cli.call_count, 1)

    def test_bootstrap_alias_readback_failure_blocks_release_output(self):
        initial = copy.deepcopy(self.endpoint)
        initial['version_selector']['version_selection_rules'][0]['agent_version'] = '@latest'
        with patch.object(release, 'azd', side_effect=[json.dumps({'agent_endpoint': initial}),
                '', json.dumps({'agent_endpoint': initial})]):
            with self.assertRaisesRegex(ValueError, 'verified'):
                release.prepare('helper')
        self.assertFalse(Path(os.environ['GITHUB_OUTPUT']).exists())

    def test_sdk_read_failure_cannot_fall_back_to_local_state_or_change_route(self):
        endpoint = copy.deepcopy(self.endpoint)
        endpoint['version_selector']['version_selection_rules'][0]['agent_version'] = '@latest'
        for error in (ClientAuthenticationError('fixture authentication failure'), HttpResponseError('fixture read failure')):
            self.project.agents.get.side_effect = error
            with self.subTest(error=type(error).__name__), patch.object(release, 'azd', return_value=json.dumps({'agent_endpoint': endpoint})) as cli:
                with self.assertRaisesRegex(RuntimeError, 'Bootstrap metadata read failed') as failure:
                    release.prepare('helper')
                self.assertNotIn('fixture', str(failure.exception))
                self.assertEqual(cli.call_count, 1)
                self.assertFalse(Path(os.environ['GITHUB_OUTPUT']).exists())
                self.assertNotIn('versionSelector', yaml.safe_load(Path('azure.yaml').read_text())['services']['helper']['agentEndpoint'])

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
