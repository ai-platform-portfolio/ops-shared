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
