"""Native CLI JSON adapter; no model, event or application policy lives here."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def command(*arguments):
    result = subprocess.run(['azd', *arguments, '-e', os.environ['AZURE_ENV_NAME'], '--no-prompt'],
                            capture_output=True, text=True, timeout=210)
    if result.returncode:
        # Keep status/native error lines, never dump raw headers or model output.
        diagnostic = '\n'.join(re.findall(r'(?m)^(?:ERROR:|HTTP/\S+ [45][0-9]{2}\b).*$', result.stderr + result.stdout))
        diagnostic = re.sub(r'(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', '[redacted-id]', diagnostic)
        diagnostic = re.sub(r'(?i)Bearer\s+[A-Za-z0-9._~+/-]+', 'Bearer [redacted]', diagnostic)
        if diagnostic:
            print(diagnostic[:8192], file=sys.stderr)
        raise RuntimeError(f'Native azd operation failed (exit {result.returncode})')
    return result.stdout


def main():
    service = os.environ['SERVICE']
    # Materialize only the already-declared stable name, as in foundry-deploy.
    path = Path('azure.yaml')
    environment = os.environ['AZURE_ENV_NAME']
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', environment):
        raise ValueError('Invalid stable environment identity')
    path.write_text(path.read_text().replace('${AZURE_ENV_NAME}', environment))
    version = os.getenv('VERSION')
    if not version:
        endpoint = json.loads(command('ai', 'agent', 'endpoint', 'show', service, '--output', 'json'))['agent_endpoint']
        rules = endpoint.get('version_selector', {}).get('version_selection_rules', [])
        if len(rules) != 1 or rules[0].get('traffic_percentage') != 100:
            raise ValueError('Invocation requires an explicitly pinned serving version')
        version = rules[0]['agent_version']
    if not re.fullmatch(r'[1-9][0-9]*', version):
        raise ValueError('Invocation requires a numeric version')
    raw = command('ai', 'agent', 'invoke', service, '--version', version, '--new-session',
                  '--protocol', 'invocations', '--input-file', os.environ['REQUEST_FILE'],
                  '--output', 'raw', '--timeout', '180')
    headers, separator, body = raw.partition('\n\n')
    if not separator or not re.match(r'^HTTP/\S+ 2[0-9]{2}\b', headers):
        raise RuntimeError('Hosted invocation did not return a successful response')
    response = Path(os.environ['RUNNER_TEMP']) / 'foundry-response.json'
    response.write_text(json.dumps(json.loads(body)))
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'response={response}\nversion={version}\n')


if __name__ == '__main__':
    main()
