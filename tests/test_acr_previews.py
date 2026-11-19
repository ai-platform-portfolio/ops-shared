"""Exercise native command ordering, ownership and ACR handoff without Azure writes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.github/actions/container-app-preview/preview.sh'
IDENTITY = {'id': '/identity/preview', 'principalId': 'preview-principal', 'clientId': 'preview-client',
            'tags': {'previewRepositoryId': '123', 'previewPullRequest': '7'}}
IMAGE = 'example.azurecr.io/site@sha256:' + 'a' * 64


class PreviewTests(unittest.TestCase):
    def run_preview(self, *, closed=False, existing=False, empty=False, unowned=False, stale=False, fail=False, repo='example/site'):
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
if Path(sys.argv[0]).name == 'gh': print({json.dumps(json.dumps(pr))}); sys.exit()
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
        result, commands, _ = self.run_preview(closed=True, empty=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any('delete' in cmd for cmd in commands))

    def test_stale_fork_unowned_and_failed_grants_do_not_deploy(self):
        for options, success in [({'stale': True}, True), ({'repo': 'attacker/fork'}, False),
                                 ({'existing': True, 'unowned': True}, False), ({'fail': True}, False)]:
            result, commands, output = self.run_preview(**options)
            self.assertEqual(result.returncode == 0, success, result.stderr)
            self.assertFalse(any(cmd[:3] in [['az', 'containerapp', 'create'], ['az', 'containerapp', 'update'], ['az', 'containerapp', 'delete']] for cmd in commands))
            self.assertEqual(output, '')

    def test_remote_task_keeps_provenance_digest_and_native_failure(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/docker.yml').read_text())
        step = next(item for item in workflow['jobs']['docker']['steps'] if item.get('id') == 'remote')
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
                   'RUNNER_TEMP': directory,
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


if __name__ == '__main__':
    unittest.main()
