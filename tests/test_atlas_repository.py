"""Exercise the action's registry lifecycle with a deterministic Atlas CLI stub."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / '.github/actions/atlas-repository/ensure.sh'


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='atlas-repository-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        stub = self.root / 'atlas'
        stub.write_text(f'#!{sys.executable}\n' + '''
import json, os, sys
from pathlib import Path
root = Path(os.environ['CAPTURE'])
mode = os.environ['MODE']
args = sys.argv[1:]
with (root / 'calls').open('a') as stream: stream.write(json.dumps(args) + '\\n')
command = args[2]
name = os.environ['ATLAS_REPOSITORY']
if command == 'list':
    if mode == 'list-error': raise SystemExit('sensitive-server-error')
    page = int(args[args.index('--page') + 1])
    exists = mode in ('existing', 'wrong-type', 'wrong-driver', 'malformed', 'paginated')
    rows = [{'Slug': name}] if exists and (mode != 'paginated' or page == 2) else []
    print('not-json' if mode == 'malformed' else json.dumps({'Repos': rows,
          'PageInfo': {'page': page, 'pageSize': 1, 'totalItems': 2 if mode == 'paginated' else len(rows)}}))
elif command == 'create':
    if mode in ('race', 'create-error'): raise SystemExit('sensitive-server-error')
    (root / 'created').write_text(name)
elif command == 'describe':
    if mode in ('create-error', 'describe-error'): raise SystemExit('sensitive-server-error')
    print(json.dumps({'Slug': name, 'Type': 'MIGRATION_DIRECTORY' if mode == 'wrong-type'
          else os.environ['ATLAS_REPOSITORY_TYPE'].upper(),
          'Driver': 'MYSQL' if mode == 'wrong-driver' else 'POSTGRESQL'}))
else: raise SystemExit(2)
''')
        stub.chmod(0o755)

    def run_action(self, mode, success=True, **overrides):
        for name in ('calls', 'output', 'created'):
            (self.root / name).unlink(missing_ok=True)
        env = {**os.environ, 'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
               'ATLAS_REPOSITORY': 'example-roles', 'ATLAS_REPOSITORY_TYPE': 'schema',
               'ATLAS_DRIVER': 'postgres', 'GITHUB_OUTPUT': str(self.root / 'output'),
               'CAPTURE': str(self.root), 'MODE': mode, **overrides}
        result = subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        self.assertNotIn('sensitive-server-error', result.stdout + result.stderr)
        calls = [json.loads(line) for line in (self.root / 'calls').read_text().splitlines()] if (self.root / 'calls').exists() else []
        if success:
            self.assertEqual((self.root / 'output').read_text(), 'name=example-roles\nurl=atlas://example-roles\n')
        else:
            self.assertFalse((self.root / 'output').exists())
        return calls

    def test_existing_repository_is_verified_without_creation(self):
        calls = self.run_action('existing')
        self.assertEqual([args[2] for args in calls], ['list', 'describe'])

    def test_creation_and_concurrent_creation_are_verified(self):
        for mode in ('missing', 'race'):
            with self.subTest(mode=mode):
                calls = self.run_action(mode)
                self.assertEqual([args[2] for args in calls], ['list', 'create', 'describe'])
                self.assertEqual(calls[1], ['cloud', 'repo', 'create', '--name', 'example-roles', '--type', 'schema', '--driver', 'postgres'])

    def test_pagination_finds_existing_repository_before_creation(self):
        calls = self.run_action('paginated')
        self.assertEqual([args[2] for args in calls], ['list', 'list', 'describe'])
        self.assertIn('2', calls[1])

    def test_mismatches_and_api_errors_fail_closed(self):
        for mode in ('wrong-type', 'wrong-driver', 'list-error', 'create-error', 'describe-error', 'malformed'):
            with self.subTest(mode=mode):
                calls = self.run_action(mode, False)
                if mode in ('wrong-type', 'wrong-driver', 'list-error', 'malformed'):
                    self.assertNotIn('create', [args[2] for args in calls])

    def test_invalid_inputs_do_not_call_atlas(self):
        for change in ({'ATLAS_REPOSITORY': '--bad'}, {'ATLAS_REPOSITORY_TYPE': 'migration'}, {'ATLAS_DRIVER': ''}):
            with self.subTest(change=change):
                self.assertEqual(self.run_action('missing', False, **change), [])

    def test_migration_directory_type_is_supported(self):
        self.run_action('existing', ATLAS_REPOSITORY_TYPE='migration_directory')


if __name__ == '__main__':
    unittest.main()
