"""Keep the existing App key in masked step outputs, never artifacts or logs."""
import os
import subprocess
from uuid import uuid4

result = subprocess.run(['az', 'keyvault', 'secret', 'show', '--vault-name', os.environ['VAULT_NAME'],
                         '--name', os.environ['KEY_NAME'], '--query', 'value', '--output', 'tsv'],
                        capture_output=True, text=True, timeout=30)
if result.returncode:
    raise RuntimeError('Existing GitHub App key could not be read from Key Vault')
key = result.stdout.strip()
if 'PRIVATE KEY-----' not in key:
    raise ValueError('Key Vault secret is not an App private key')
print('::add-mask::' + key.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A'))
marker = uuid4().hex
with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
    output.write(f'pem<<{marker}\n{key}\n{marker}\n')
