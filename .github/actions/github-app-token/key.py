"""Keep the existing App key in masked step outputs, never artifacts or logs."""
import os
import re
import subprocess
import sys
from uuid import uuid4

result = subprocess.run(['az', 'keyvault', 'secret', 'show', '--vault-name', os.environ['VAULT_NAME'],
                         '--name', os.environ['KEY_NAME'], '--query', 'value', '--output', 'tsv'],
                        capture_output=True, text=True, timeout=30)
if result.returncode:
    diagnostic = re.sub(r'(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b',
                        '[redacted-id]', result.stderr)
    diagnostic = re.sub(r'(?i)Bearer\s+[A-Za-z0-9._~+/-]+', 'Bearer [redacted]', diagnostic)
    print(diagnostic[:4096], file=sys.stderr)
    raise RuntimeError('Existing GitHub App key could not be read from Key Vault')
key = result.stdout.strip()
if 'PRIVATE KEY-----' not in key:
    raise ValueError('Key Vault secret is not an App private key')
print('::add-mask::' + key.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A'))
marker = uuid4().hex
with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
    output.write(f'pem<<{marker}\n{key}\n{marker}\n')
