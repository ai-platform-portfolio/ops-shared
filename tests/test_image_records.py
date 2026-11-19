"""Exercise image identity and PR-preview lifecycle boundaries without Azure writes."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

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


SCRIPT = ROOT / '.github/actions/container-app-preview/preview.sh'
IDENTITY = {'id': '/identity/preview', 'principalId': 'preview-principal', 'clientId': 'preview-client',
            'tags': {'previewRepositoryId': '123', 'previewPullRequest': '7'}}
IMAGE = 'example.azurecr.io/site@sha256:' + 'a' * 64


class PreviewTests(unittest.TestCase):
    def run_preview(self, *, closed=False, existing=False, empty=False, unowned=False, stale=False, fail=False, repo='example/site', comment=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = {'pull_request': {'number': 7, 'head': {'sha': 'current'}}}
            (root / 'event.json').write_text(json.dumps(event))
            pr = {'state': 'closed' if closed else 'open', 'head': {'sha': 'newer' if stale else 'current', 'repo': {'full_name': repo}}}
            resource = {**IDENTITY, 'tags': {} if unowned else IDENTITY['tags']}
            # Log every native command, then return only the data requested by
            # this test. Failures are injected at the actual role-write boundary.
            stub = f'''#!{sys.executable}
import json,os,sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['COMMANDS'], 'a') as f: f.write(json.dumps([Path(sys.argv[0]).name, *args]) + '\\n')
if Path(sys.argv[0]).name == 'gh':
    if '/pulls/' in args[1]: print({json.dumps(json.dumps(pr))})
    elif '--jq' in args and {comment!r}: print('42')
    sys.exit()
if args[:2] in [['containerapp', 'list'], ['identity', 'list']]:
    print({json.dumps('' if empty else json.dumps(resource) if existing else 'null')}); sys.exit()
if args[:2] == ['acr', 'show']: print('{{"id":"/registry/example","server":"example.azurecr.io"}}')
elif args[:3] == ['containerapp', 'env', 'show']: print('uksouth')
elif args[:2] == ['identity', 'show']:
    if {fail!r}: sys.exit(1)
    print({json.dumps(json.dumps(IDENTITY))})
elif args[:2] == ['containerapp', 'show']: print('pv-123-pr-7.example.azurecontainerapps.io')
'''
            for command in ('az', 'gh', 'curl'):
                target = root / command
                target.write_text(stub)
                target.chmod(0o755)
            env = {**os.environ, 'PATH': f'{root}:{os.environ["PATH"]}', 'COMMANDS': str(root / 'commands'),
                   'GITHUB_EVENT_PATH': str(root / 'event.json'), 'GITHUB_REPOSITORY_ID': '123',
                   'GITHUB_REPOSITORY': 'example/site', 'GITHUB_EVENT_NAME': 'pull_request',
                   'GITHUB_SHA': 'c' * 40, 'GITHUB_STEP_SUMMARY': str(root / 'summary'),
                   'GITHUB_OUTPUT': str(root / 'output'), 'RESOURCE_GROUP': 'preview-group', 'ENVIRONMENT': 'existing',
                   'REGISTRY': 'example', 'IMAGE': IMAGE, 'MANAGED_IDENTITY': 'permanent',
                   'TARGET_PORT': '8080', 'HEALTH_PATH': '/healthz',
                   'ENV_VARS': json.dumps({'LITERAL': '$(touch compromised) with spaces'})}
            result = subprocess.run(['bash', str(SCRIPT)], env=env, cwd=root, capture_output=True, text=True)
            commands = [json.loads(line) for line in (root / 'commands').read_text().splitlines()]
            self.assertFalse((root / 'compromised').exists())
            output = (root / 'output').read_text() if (root / 'output').exists() else ''
            return result, commands, output

    def test_create_update_and_permanent_identity(self):
        result, commands, output = self.run_preview()
        self.assertEqual(result.returncode, 0, result.stderr)
        app = next(cmd for cmd in commands if cmd[:3] == ['az', 'containerapp', 'create'])
        self.assertIn('pv-123-pr-7', app)
        self.assertIn(IMAGE, app)
        self.assertEqual(app[app.index('--min-replicas') + 1], '0')
        self.assertEqual(app[app.index('--max-replicas') + 1], '1')
        self.assertIn('LITERAL=$(touch compromised) with spaces', app)
        self.assertFalse(any(cmd[:2] == ['az', 'role'] for cmd in commands))
        self.assertIn('/identity/preview', app)
        self.assertIn('url=https://pv-123-pr-7.example.azurecontainerapps.io', output)
        report = next(cmd for cmd in commands if '--method' in cmd)
        self.assertIn('PATCH', report)
        self.assertIn('repos/example/site/issues/comments/42', report)
        self.assertTrue(any('built revision `' + 'c' * 40 in arg for arg in report))
        result, commands, _ = self.run_preview(existing=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(cmd[:3] == ['az', 'containerapp', 'update'] for cmd in commands))
        self.assertFalse(any(cmd[:3] == ['az', 'identity', 'create'] for cmd in commands))

    def test_cleanup_is_ordered_and_absence_is_idempotent(self):
        result, commands, output = self.run_preview(closed=True, existing=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        app = next(i for i, cmd in enumerate(commands) if cmd[:3] == ['az', 'containerapp', 'delete'])
        self.assertEqual([cmd[:3] for cmd in commands if 'delete' in cmd], [['az', 'containerapp', 'delete']])
        self.assertFalse(any(cmd[:2] in [['az', 'role'], ['az', 'identity']] for cmd in commands))
        self.assertIn('operation=delete', output)
        result, commands, _ = self.run_preview(closed=True, empty=True, comment=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any('delete' in cmd for cmd in commands))
        report = next(cmd for cmd in commands if '--method' in cmd)
        self.assertIn('POST', report)
        self.assertIn('repos/example/site/issues/7/comments', report)

    def test_stale_fork_unowned_and_missing_identity_do_not_deploy(self):
        for options, success in [({'stale': True}, True), ({'repo': 'attacker/fork'}, False),
                                 ({'existing': True, 'unowned': True}, False), ({'fail': True}, False)]:
            result, commands, output = self.run_preview(**options)
            self.assertEqual(result.returncode == 0, success, result.stderr)
            self.assertFalse(any(cmd[:3] in [['az', 'containerapp', 'create'], ['az', 'containerapp', 'update'], ['az', 'containerapp', 'delete']] for cmd in commands))
            self.assertEqual(output, '')

    def test_remote_task_keeps_provenance_digest_and_native_failure(self):
        action = yaml.safe_load((ROOT / '.github/actions/acr-build/action.yml').read_text())
        step = action['runs']['steps'][0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            (source / 'Dockerfile').write_text('FROM scratch\n')
            (source / '.dockerignore').write_text('*\n')
            az = root / 'az'
            az.write_text(f'#!{sys.executable}\nimport json,os,sys\nfrom pathlib import Path\n'
                          "if sys.argv[1:3] == ['acr', 'build']:\n"
                          " task = Path(sys.argv[sys.argv.index('--file') + 1])\n"
                          " Path(os.environ['TASK']).write_text(task.read_text()); sys.exit(int(os.environ.get('FAIL', '0')))\n"
                          "print('sha256:' + 'a' * 64)\n")
            az.chmod(0o755)
            env = {**os.environ, 'PATH': f'{root}:{os.environ["PATH"]}', 'CONTEXT': 'source', 'DOCKERFILE': '',
                   'RUNNER_TEMP': directory, 'REGISTRY': 'localacrk8s',
                   'IMAGE': 'localacrk8s.azurecr.io/site:preview-7-commit', 'TASK': str(root / 'task.json'),
                   'GITHUB_OUTPUT': str(root / 'output'), 'GITHUB_SERVER_URL': 'https://github.com',
                   'GITHUB_REPOSITORY': 'example/site', 'SOURCE_SHA': 'c' * 40}
            result = subprocess.run(['bash', '-c', step['run']], env=env, cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            task = (root / 'task.json').read_text()
            self.assertTrue(task.startswith('FROM scratch\n'))
            self.assertIn('org.opencontainers.image.revision="' + 'c' * 40 + '"', task)
            self.assertIn('org.opencontainers.image.source="https://github.com/example/site"', task)
            self.assertEqual(list(root.glob('acr-build-*.Dockerfile')), [])
            self.assertEqual({item.name for item in source.iterdir()}, {'Dockerfile', '.dockerignore'})
            self.assertEqual((source / 'Dockerfile').read_text(), 'FROM scratch\n')
            self.assertIn('digest=sha256:' + 'a' * 64, (root / 'output').read_text())
            (root / 'output').unlink()
            failed = subprocess.run(['bash', '-c', step['run']], env={**env, 'FAIL': '1'}, cwd=root, capture_output=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse((root / 'output').exists())


class NightlyCleanupTests(unittest.TestCase):
    def test_only_confirmed_closed_owned_previews_are_deleted(self):
        spec = importlib.util.spec_from_file_location('cleanup', ROOT / '.github/actions/container-app-preview/preview_cleanup.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        apps = [{'name': 'portfolio'}, *[
            {'name': f'pv-123-pr-{number}', 'tags': {'previewRepositoryId': '123', 'previewPullRequest': str(number)}}
            for number in range(1, 6)
        ], {'name': 'unrelated', 'tags': {'previewRepositoryId': '123', 'previewPullRequest': '2'}}]
        deleted = []

        def request(url, method='GET', token=None):
            if method == 'DELETE':
                deleted.append(url)
                return None
            if 'management.azure.com' in url:
                return {'value': apps}
            number = int(url.rsplit('/', 1)[-1])
            if number == 3:
                raise OSError('GitHub unavailable')
            return {'number': number, 'state': 'open' if number == 1 else 'closed',
                    'base': {'repo': {'id': 999 if number == 5 else 123,
                                     'owner': {'login': 'another-owner' if number == 4 else 'example'}}}}

        credential = Mock()
        credential.get_token.return_value.token = 'not-a-real-token'
        with patch.object(module, 'request', side_effect=request):
            self.assertEqual(module.sweep('subscription', 'hosting', 'example', credential), 1)
        self.assertEqual(len(deleted), 1)
        self.assertIn('/containerApps/pv-123-pr-2?', deleted[0])
        deleted.clear()
        with patch.object(module, 'request', side_effect=request):
            self.assertEqual(module.sweep('subscription', 'hosting', 'example', credential, dry_run=True), 1)
        self.assertEqual(deleted, [])

    def test_arm_failures_and_foreign_pagination_fail_closed(self):
        spec = importlib.util.spec_from_file_location('cleanup', ROOT / '.github/actions/container-app-preview/preview_cleanup.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        credential = Mock()
        credential.get_token.return_value.token = 'not-a-real-token'
        with patch.object(module, 'request', side_effect=OSError('ARM unavailable')):
            with self.assertRaises(OSError):
                module.sweep('subscription', 'hosting', 'example', credential)
        with patch.object(module, 'request', return_value={'value': [], 'nextLink': 'https://untrusted.example/'}) as request:
            with self.assertRaises(ValueError):
                module.sweep('subscription', 'hosting', 'example', credential)
            self.assertEqual(request.call_count, 1)
