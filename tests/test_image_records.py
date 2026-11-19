"""Exercise image identity and PR-preview lifecycle boundaries without Azure writes."""

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
elif args[:2] == ['identity', 'create']: print({json.dumps(json.dumps(IDENTITY))})
elif args[:3] == ['role', 'assignment', 'list']: print('/role/pull\\n/role/read' if '--all' in args else '0')
elif args[:3] == ['role', 'assignment', 'create'] and {fail!r}: sys.exit(1)
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
                   'REGISTRY': 'example', 'IMAGE': IMAGE, 'READER_SCOPE': '/subscriptions/example',
                   'TARGET_PORT': '8080', 'HEALTH_PATH': '/healthz',
                   'ENV_VARS': json.dumps({'LITERAL': '$(touch compromised) with spaces'})}
            result = subprocess.run(['bash', str(SCRIPT)], env=env, cwd=root, capture_output=True, text=True)
            commands = [json.loads(line) for line in (root / 'commands').read_text().splitlines()]
            self.assertFalse((root / 'compromised').exists())
            output = (root / 'output').read_text() if (root / 'output').exists() else ''
            return result, commands, output

    def test_create_update_and_read_only_identity(self):
        result, commands, output = self.run_preview()
        self.assertEqual(result.returncode, 0, result.stderr)
        app = next(cmd for cmd in commands if cmd[:3] == ['az', 'containerapp', 'create'])
        self.assertIn('pv-123-pr-7', app)
        self.assertIn(IMAGE, app)
        self.assertEqual(app[app.index('--min-replicas') + 1], '0')
        self.assertEqual(app[app.index('--max-replicas') + 1], '1')
        self.assertIn('LITERAL=$(touch compromised) with spaces', app)
        roles = [cmd[cmd.index('--role') + 1] for cmd in commands if cmd[:4] == ['az', 'role', 'assignment', 'create']]
        self.assertEqual(roles, ['AcrPull', 'Reader'])
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
        roles = [i for i, cmd in enumerate(commands) if cmd[:4] == ['az', 'role', 'assignment', 'delete']]
        identity = next(i for i, cmd in enumerate(commands) if cmd[:3] == ['az', 'identity', 'delete'])
        self.assertTrue(app < min(roles) <= max(roles) < identity)
        self.assertIn('operation=delete', output)
        result, commands, _ = self.run_preview(closed=True, empty=True, comment=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any('delete' in cmd for cmd in commands))
        report = next(cmd for cmd in commands if '--method' in cmd)
        self.assertIn('POST', report)
        self.assertIn('repos/example/site/issues/7/comments', report)

    def test_stale_fork_unowned_and_failed_grants_do_not_deploy(self):
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
