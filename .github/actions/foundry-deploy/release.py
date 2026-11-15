"""Small adapters for native azd JSON/YAML; no custom Azure deployment API."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import yaml


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None  # Never forward the ARM bearer token to a redirect target.


def connect():
    """Resolve an existing project; never provision one or persist an ARM token."""
    endpoint = urlsplit(os.environ['FOUNDRY_PROJECT_ENDPOINT'])
    project = re.fullmatch(r'/api/projects/([A-Za-z0-9._-]+)/?', endpoint.path)
    if (endpoint.scheme != 'https' or not endpoint.hostname
            or not endpoint.hostname.endswith('.services.ai.azure.com') or not project
            or endpoint.username is not None or endpoint.port is not None or endpoint.query or endpoint.fragment):
        raise ValueError('Expected an existing public-Azure Foundry project endpoint')
    subscription = os.environ['AZURE_SUBSCRIPTION_ID']
    if not re.fullmatch(r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}', subscription):
        raise ValueError('Existing Azure subscription context is required')
    result = subprocess.run(['azd', 'auth', 'token', '--output', 'json', '--no-prompt',
                             '--scope', 'https://management.azure.com/.default'],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Native azd ARM authentication failed')
    try:
        token = json.loads(result.stdout)['token']
        if not isinstance(token, str) or not token:
            raise ValueError('Empty token')
    except (ValueError, KeyError, TypeError):
        raise RuntimeError('Native azd returned an invalid ARM credential') from None
    account = endpoint.hostname.removesuffix('.services.ai.azure.com')
    name = account + '/' + project[1]
    path = f'/subscriptions/{subscription}/resources'
    url = 'https://management.azure.com' + path + '?' + urlencode({
        'api-version': '2021-04-01', '$filter': "resourceType eq 'Microsoft.CognitiveServices/accounts/projects'"})
    matches = set()
    opener = build_opener(NoRedirect())
    for _ in range(10):
        page = urlsplit(url)
        if page.scheme != 'https' or page.netloc != 'management.azure.com' or page.path.lower() != path.lower() or page.fragment:
            raise ValueError('Refusing an unexpected ARM pagination URL')
        try:
            with opener.open(Request(url, headers={'Authorization': 'Bearer ' + token}), timeout=15) as response:
                body = response.read(1048577)
            if len(body) > 1048576:
                raise ValueError('ARM project response exceeds 1 MiB')
            resources = json.loads(body)
        except HTTPError as error:
            raise RuntimeError(f'Existing project lookup failed (HTTP {error.code})') from None
        except URLError:
            raise RuntimeError('Existing project lookup transport failed') from None
        for resource in resources['value']:
            if (resource['type'].lower() == 'microsoft.cognitiveservices/accounts/projects'
                    and resource['name'].lower() == name.lower()):
                resource_id = resource['id']
                expected = rf'/subscriptions/{re.escape(subscription)}/resourceGroups/[^/]+/providers/Microsoft.CognitiveServices/accounts/{re.escape(account)}/projects/{re.escape(project[1])}'
                if not re.fullmatch(expected, resource_id, re.IGNORECASE):
                    raise ValueError('Project lookup returned a mismatched resource identity')
                matches.add(resource_id)
        url = resources.get('nextLink')
        if not url:
            break
    else:
        raise ValueError('ARM project lookup exceeds 10 pages')
    if len(matches) != 1:
        raise ValueError('Expected exactly one existing project matching the endpoint in this subscription')
    # Keep resource/subscription IDs and the credential out of action outputs/logs.
    try:
        azd('env', 'set', 'AZURE_AI_PROJECT_ID=' + matches.pop())
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f'Native project configuration failed (exit {error.returncode})') from None
    print('Connected native deployment to the existing project.')


def azd(*arguments):
    result = subprocess.run(['azd', *arguments, '-e', os.environ['AZURE_ENV_NAME'], '--no-prompt'],
                            capture_output=True, text=True, timeout=180)
    if result.returncode:
        # Native CLI errors contain the actionable status/code. GitHub masks the
        # caller's registered secrets; redact identity IDs as well before logging.
        diagnostic = re.sub(r'(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b',
                            '[redacted-id]', result.stderr + result.stdout)
        print(diagnostic, file=sys.stderr)
        result.check_returncode()
    return result.stdout


def endpoint_contract(endpoint):
    protocols = endpoint.get('protocols') or list(endpoint.get('protocol_configuration', {}))
    return sorted(protocols), endpoint.get('authorization_schemes', [])


def prepare(service):
    path = Path('azure.yaml')
    config = yaml.safe_load(path.read_text())
    declaration = config['services'][service]
    # This CLI version validates the name before expanding environment templates.
    # Derive it from the caller's stable repository/agent environment identity.
    name = os.environ['AZURE_ENV_NAME']
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', name):
        raise ValueError('Environment must be a valid stable agent name')
    if declaration['name'] == '${AZURE_ENV_NAME}':
        declaration['name'] = name
    elif declaration['name'] != name:
        raise ValueError('Agent name must match the declared environment identity')
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    if os.getenv('PRESERVE_SERVING_VERSION') != 'true':
        return
    try:
        current = json.loads(azd('ai', 'agent', 'endpoint', 'show', service, '--output', 'json'))
    except subprocess.CalledProcessError as error:
        # Only an explicit missing agent permits the first-release bootstrap.
        if not re.search(r'\b404\b|\bAgentNotFound\b', (error.stderr or '') + (error.stdout or '')):
            raise
        print('First release: no existing serving version to preserve.')
        return
    actual = current['agent_endpoint']
    desired = declaration['agentEndpoint']
    desired_contract = sorted(desired['protocols']), desired['authorizationSchemes']
    if endpoint_contract(actual) != desired_contract:
        raise ValueError('Endpoint protocol/authentication differs; review before deployment')
    rules = actual.get('version_selector', {}).get('version_selection_rules', [])
    if len(rules) == 1 and rules[0].get('traffic_percentage') == 100:
        version = rules[0]['agent_version']
    else:
        raise ValueError('Existing endpoint needs an explicit single serving version; refusing an unpinned or split route')
    if not re.fullmatch(r'[1-9][0-9]*', version):
        raise ValueError('Serving version must be an explicit numeric version')
    desired['versionSelector'] = {'versionSelectionRules': [
        {'type': 'FixedRatio', 'agentVersion': version, 'trafficPercentage': 100}]}
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    azd('ai', 'agent', 'endpoint', 'update', service)
    after = json.loads(azd('ai', 'agent', 'endpoint', 'show', service, '--output', 'json'))['agent_endpoint']
    expected = [{'type': 'FixedRatio', 'agent_version': version, 'traffic_percentage': 100}]
    if (after.get('version_selector', {}).get('version_selection_rules') != expected
            or endpoint_contract(after) != endpoint_contract(actual)):
        raise ValueError('Serving route preservation could not be verified')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'serving-version={version}\n')


def record(service):
    current = json.loads(azd('ai', 'agent', 'show', service, '--output', 'json'))
    version = current['version']
    if not re.fullmatch(r'[1-9][0-9]*', version) or current.get('status', '').lower() not in ('active', 'idle'):
        raise ValueError('Candidate is not a ready numeric version')
    definition = current['definition']
    image = definition.get('container_configuration', {}).get('image') or definition.get('image')
    if os.getenv('IMAGE') and image != os.environ['IMAGE']:
        raise ValueError('Deployed image does not match the immutable release image')
    env = definition.get('environment_variables', {})
    if env.get('SOURCE_REVISION') != os.environ['SOURCE_REVISION']:
        raise ValueError('Deployed source revision does not match the release')
    record = {'agent': current['name'], 'version': version, 'image': image,
              'source_revision': os.environ['SOURCE_REVISION'],
              'instance_principal_id': (current.get('instance_identity') or {}).get('principal_id'),
              'endpoints': current.get('agent_endpoints', {})}
    target = Path(os.environ['RUNNER_TEMP']) / 'foundry-release.json'
    target.write_text(json.dumps(record, indent=2))
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'version={version}\nrecord={target}\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['connect', 'prepare', 'record'])
    parser.add_argument('service', nargs='?')
    args = parser.parse_args()
    if args.operation == 'connect':
        connect()
    elif args.service:
        (prepare if args.operation == 'prepare' else record)(args.service)
    else:
        parser.error('prepare/record requires a service')
