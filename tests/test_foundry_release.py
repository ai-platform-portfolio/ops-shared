"""Fail-closed release boundaries around native azd, without Azure mutations."""
import copy
import importlib.util
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
