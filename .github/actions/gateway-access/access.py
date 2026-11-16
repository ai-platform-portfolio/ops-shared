"""Connect one native hosted consumer to chart-owned gateway credentials."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import yaml


def command(*arguments):
    result = subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=900)
    return result.stdout


def invoke(*arguments):
    result = json.loads(command('az', 'aks', 'command', 'invoke',
                                '--resource-group', os.environ['RESOURCE_GROUP'],
                                '--name', os.environ['CLUSTER'], *arguments, '--output', 'json'))
    if result.get('exitCode') != 0:
        raise RuntimeError(f'Gateway command failed; Azure operation: {result.get("id", "unavailable")}')
    return result['logs'].strip()


def render(values, *, registration=False):
    arguments = ['helm', 'template', values['name'], os.environ['CHART_DIRECTORY'],
                 '-f', str(directory() / 'values.json')]
    if registration:
        arguments += ['--show-only', 'templates/argocd/application.yaml']
    return command(*arguments)


def directory():
    return Path(os.environ['RUNNER_TEMP']) / ('gateway-' + os.environ['SERVICE'])


def configure():
    agent = Path(os.environ['AGENT_DIRECTORY']).resolve()
    workspace = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    if not agent.is_relative_to(workspace):
        raise ValueError('Agent intent must be inside the source checkout')
    access = yaml.safe_load((agent / 'config/agent.yaml').read_text())['gatewayAccess']
    if set(access) != {'policy'}:
        raise ValueError('Hosted consumer declares only gateway policy intent')
    metadata = json.loads(invoke('--command', 'kubectl get configmap litellm-consumer-defaults -n litellm -o json'))
    platform = json.loads(metadata['data']['values.json'])
    policy = platform['modelGateway']['policies'][access['policy']]
    if any(type(policy.get(k)) not in (int, float) or policy[k] <= 0 for k in ('maxBudget', 'rpm', 'tpm', 'parallel')):
        raise ValueError('Gateway admission blocked: operator must supply explicit ceilings')
    identity = os.environ['GITHUB_REPOSITORY'].lower() + '/' + os.environ['SERVICE']
    name = 'hosted-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
    platform['modelGateway'].update(consumerIdentity=os.environ['GITHUB_REPOSITORY'].lower(), connectionOnly=True)
    platform['argocd'] = {'enabled': True, 'repository': 'https://github.com/' + os.environ['GITHUB_REPOSITORY'] + '.git',
                         'revision': os.environ['GITHUB_SHA'], 'chartRevision': os.environ['CHART_REVISION'],
                         'valuesPath': (agent / 'config/agent.yaml').relative_to(workspace).as_posix()}
    platform['lifecycle'] = {'cleanup': {'enabled': True}}
    values = {'name': name, 'namespace': name, 'gatewayAccess': access, 'platform': platform}
    (directory() / 'values.json').write_text(json.dumps(values))
    metadata = next(r for r in yaml.safe_load_all(render(values))
                    if r and r['kind'] == 'ConfigMap' and 'connection.json' in r.get('data', {}))
    connection = json.loads(metadata['data']['connection.json'])
    if any('\n' in str(value) or '\r' in str(value) for value in connection.values()):
        raise ValueError('Connection metadata must be single-line values')
    path = directory() / 'connection.json'
    path.write_text(json.dumps(connection))
    with open(os.environ['GITHUB_ENV'], 'a') as output:
        output.writelines(f'{key}={value}\n' for key, value in connection.items())
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'connection={path}\n')


def apply():
    record = json.loads(Path(os.environ['RELEASE_RECORD']).read_text())
    principal = record.get('instance_principal_id', '')
    if (record.get('source_revision') != os.environ['GITHUB_SHA'] or record.get('agent') != os.environ['SERVICE']
            or not re.fullmatch(r'[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', principal)):
        raise ValueError('Release record must match source, agent and native principal')
    values = json.loads((directory() / 'values.json').read_text())
    name = values['name']
    values['platform']['modelGateway'].update(principalId=principal, connectionOnly=False)
    existing = invoke('--command', f'kubectl get application/{name} --namespace=argocd --ignore-not-found -o json')
    if existing:
        previous = json.loads(existing)['spec']['sources'][0]['helm']['valuesObject']['platform']['modelGateway']['principalId']
        if previous != principal:
            raise ValueError('Native principal changed; review old-reader cleanup before replacement')
    (directory() / 'values.json').write_text(json.dumps(values))
    registration = directory() / 'registration.yaml'
    registration.write_text(render(values, registration=True))
    invoke('--file', str(registration), '--command', f'''
set -eu
kubectl apply -f registration.yaml
kubectl wait -n argocd --for=jsonpath='{{.status.sync.comparedTo.sources[0].helm.valuesObject.platform.modelGateway.principalId}}'={principal} application/{name} --timeout=240s
kubectl wait -n argocd --for=jsonpath='{{.status.sync.status}}'=Synced application/{name} --timeout=240s
kubectl wait -n argocd --for=jsonpath='{{.status.health.status}}'=Healthy application/{name} --timeout=240s
''')
    # Separate JSON read avoids parsing mixed apply/wait output, and verifies the
    # full desired sources so stale Healthy status cannot admit a candidate.
    actual = json.loads(invoke('--command', f'kubectl get application/{name} --namespace=argocd -o json'))
    status = actual['status']
    if (status['sync']['status'] != 'Synced' or status['health']['status'] != 'Healthy'
            or status['sync']['comparedTo']['sources'] != actual['spec']['sources']):
        raise ValueError('Credential Application has not reconciled its current source')


def main():
    for key, pattern in [('SERVICE', r'[a-z0-9][a-z0-9-]{0,62}'), ('CHART_REVISION', r'[0-9a-f]{40}'),
                         ('GITHUB_SHA', r'[0-9a-f]{40}'), ('GITHUB_REPOSITORY', r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')]:
        if not re.fullmatch(pattern, os.environ[key]):
            raise ValueError(f'Invalid {key}')
    if command('git', '-C', os.environ['CHART_DIRECTORY'], 'rev-parse', 'HEAD').strip() != os.environ['CHART_REVISION']:
        raise ValueError('Chart checkout must match its immutable revision')
    directory().mkdir(mode=0o700, parents=True, exist_ok=True)
    operations = {'configure': configure, 'apply': apply}
    operations[os.environ['OPERATION']]()


if __name__ == '__main__':
    main()
