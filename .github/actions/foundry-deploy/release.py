"""Small adapters for native azd JSON/YAML; no custom Azure deployment API."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

import yaml


def azd(*arguments):
    return subprocess.run(['azd', *arguments, '-e', os.environ['AZURE_ENV_NAME'], '--no-prompt'],
                          check=True, capture_output=True, text=True).stdout


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
        if not re.search(r'\b404\b|\bAgentNotFound\b', error.stderr):
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
              'endpoints': current.get('agent_endpoints', {})}
    target = Path(os.environ['RUNNER_TEMP']) / 'foundry-release.json'
    target.write_text(json.dumps(record, indent=2))
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'version={version}\nrecord={target}\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'record'])
    parser.add_argument('service')
    args = parser.parse_args()
    (prepare if args.operation == 'prepare' else record)(args.service)
