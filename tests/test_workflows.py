"""Exercise the code embedded in the exact callable workflows under review."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LINEAR = ROOT / '.github/workflows/linear-issue-check.yml'
VALIDATE = ROOT / '.github/workflows/validate.yml'


def linear_module():
    body = LINEAR.read_text().split("          python3 - <<'PY'\n", 1)[1]
    body = body.rsplit('          PY\n', 1)[0]
    module = types.ModuleType('linear_workflow')
    exec(compile(textwrap.dedent(body), str(LINEAR), 'exec'), module.__dict__)
    return module


class LinearTests(unittest.TestCase):
    def setUp(self):
        self.validator = linear_module()

    def test_extracts_and_deduplicates_all_issue_ids(self):
        self.assertEqual(self.validator.issue_ids('user/ai-50-ai-49-ai-50-work'), ['AI-49', 'AI-50'])

    def test_rejects_branch_without_issue(self):
        with self.assertRaises(self.validator.ValidationError):
            self.validator.issue_ids('feature/no-issue')

    def test_missing_credentials_fail_without_network(self):
        with self.assertRaises(self.validator.ValidationError):
            self.validator.validate('ai-50-work', None, None, request=lambda *args: self.fail('network called'))

    def test_oauth_scope_and_all_issue_matches(self):
        calls = []

        def request(url, data, headers):
            calls.append((url, data, headers))
            if url.endswith('/oauth/token'):
                return {'access_token': 'test-only-token'}
            identifier = json.loads(data)['variables']['id']
            return {'data': {'issue': {'id': 'test-id', 'identifier': identifier}}}

        self.assertEqual(self.validator.validate('ai-50-ai-49-work', 'test-client', 'test-secret', request), ['AI-49', 'AI-50'])
        self.assertIn(b'scope=read', calls[0][1])
        self.assertIn(b'grant_type=client_credentials', calls[0][1])
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1][2]['Authorization'], 'Bearer test-only-token')

    def test_invalid_token_and_mismatched_issues_fail(self):
        for response in [{'error': 'invalid_client'}, {'data': {'issue': None}},
                         {'data': {'issue': {'id': 'test-id', 'identifier': 'AI-99'}}},
                         {'errors': [{'message': 'denied'}]}]:
            def request(url, data, headers):
                if url.endswith('/oauth/token') and 'error' not in response:
                    return {'access_token': 'test-only-token'}
                return response
            with self.subTest(response=response), self.assertRaises(self.validator.ValidationError):
                self.validator.validate('ai-50-work', 'test-client', 'test-secret', request)

    def test_transport_error_is_sanitized(self):
        def fail(*args, **kwargs):
            raise OSError('sensitive upstream response')
        with self.assertRaisesRegex(self.validator.ValidationError, '^Linear request failed or returned invalid JSON; validation cannot pass.$'):
            self.validator.request_json('https://example.invalid', b'{}', {}, opener=fail)

    def test_wrong_event_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'event.json'
            path.write_text('{}')
            with patch.dict(os.environ, {'GITHUB_EVENT_PATH': str(path), 'GITHUB_EVENT_NAME': 'push'}):
                self.assertEqual(self.validator.main(), 1)


class HelmTests(unittest.TestCase):
    def render(self, **inputs):
        script = textwrap.dedent(VALIDATE.read_text().split('        run: |\n', 1)[1])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shim = root / 'helm'
            shim.write_text('#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\nPath(os.environ["ARGS_FILE"]).write_text(json.dumps(sys.argv[1:]))\nprint("rendered")\n')
            shim.chmod(0o755)
            env = dict(os.environ, PATH=f'{directory}:{os.environ["PATH"]}',
                       RUNNER_TEMP=directory, ARGS_FILE=str(root / 'args.json'),
                       CHART='', REPOSITORY='', CHART_VERSION='', RELEASE='test-release',
                       NAMESPACE='test-namespace', VALUES_FILE='')
            env.update(inputs)
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', script], env=env, text=True, capture_output=True)
            args = json.loads((root / 'args.json').read_text()) if (root / 'args.json').exists() else None
            return result, args

    def test_remote_chart_passes_values_as_arguments(self):
        result, args = self.render(CHART='argo-cd', REPOSITORY='https://argoproj.github.io/argo-helm',
                                   CHART_VERSION='10.10.5', VALUES_FILE='path with spaces/values.yaml')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, ['template', 'test-release', 'argo-cd', '--namespace', 'test-namespace',
                                '--repo', 'https://argoproj.github.io/argo-helm', '--version', '10.10.5',
                                '--values', 'path with spaces/values.yaml'])

    def test_remote_chart_requires_pinned_version(self):
        result, args = self.render(CHART='oci://example.invalid/chart')
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(args)

    def test_local_chart_can_omit_repository_and_version(self):
        chart = str(ROOT / 'tests/fixtures/chart')
        result, args = self.render(CHART=chart)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args, ['template', 'test-release', chart, '--namespace', 'test-namespace'])


if __name__ == '__main__':
    unittest.main()
