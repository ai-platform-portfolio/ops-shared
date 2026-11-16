import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('foundry_invoke', Path(__file__).resolve().parents[1] / '.github/actions/foundry-invoke/invoke.py')
invoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(invoke)


class InvocationTests(unittest.TestCase):
    def test_raw_response_with_native_update_notice_preserves_the_json(self):
        notice = ("WARNING: A new version of extension 'Foundry agents' is available: 1.0.0 -> 1.1.0\n"
                  '  • To update: azd extension update azure.ai.agents\n'
                  '  • To update all: azd extension update --all\n\n')
        result = {'answer': 'Réponse containing WARNING: without truncating the JSON'}
        for suffix in ('', notice):
            with self.subTest(suffix=bool(suffix)):
                self.assertEqual(invoke.response_json('HTTP/2.0 200 OK\n\n' + json.dumps(result) + suffix), result)

    def test_failed_malformed_and_unrecognized_output_remain_rejected(self):
        for raw in ('HTTP/2.0 403 Forbidden\n\n{"ok":true}', 'no HTTP response',
                    'HTTP/2.0 200 OK\n\n{"truncated":', 'HTTP/2.0 200 OK\n\n[]',
                    'HTTP/2.0 200 OK\n\n{"ok":true}{"other":true}',
                    'HTTP/2.0 200 OK\n\n{"ok":true} unexpected-private-output'):
            with self.subTest(raw=raw[:25]), self.assertRaises((RuntimeError, ValueError)) as error:
                invoke.response_json(raw)
            self.assertNotIn('unexpected-private-output', str(error.exception))

    def test_explicit_candidate_version_does_not_follow_a_changed_route(self):
        with tempfile.TemporaryDirectory() as directory:
            old = Path.cwd()
            os.chdir(directory)
            try:
                Path('azure.yaml').write_text('name: ${AZURE_ENV_NAME}\n')
                with patch.dict(os.environ, {'AZURE_ENV_NAME': 'repo-agent', 'SERVICE': 'agent', 'VERSION': '7',
                                             'REQUEST_FILE': '/tmp/request.json', 'RUNNER_TEMP': directory,
                                             'GITHUB_OUTPUT': str(Path(directory) / 'outputs')}):
                    with patch.object(invoke, 'command', return_value='HTTP/2.0 200 OK\n\n{"ok":true}') as command:
                        invoke.main()
                    command.assert_called_once()
                    args = command.call_args.args
                    self.assertEqual(args[args.index('--version') + 1], '7')
                    self.assertEqual(json.loads(Path('foundry-response.json').read_text()), {'ok': True})
            finally:
                os.chdir(old)
