# Network migration validation

Prepared on 2026-09-26 from the existing backend's `auth0.tfstate`, serial 14,
using Terraform 1.12.2 and AzureRM 4.81.0.

- Ten network instances moved into `module.network` in local state copies.
- All managed resource IDs and attributes preserved across the split.
- Nine legacy managed instances and four data sources preserved, including Auth0
  and Key Vault entries. No secret values were printed or committed.
- A refreshed Azure plan against the local network state returned exit code 0:
  no creates, updates, deletes or replacements; output values also unchanged.
- `central-devops.tfstate` did not exist before publishing.

## Completed transfer

After explicit owner approval on 2026-09-26, the destination was created and
verified before the reduced source state was written. Source ETag and backup
bytes were checked again; conditional writes and temporary Azure Blob leases
protected the transfer. Both remote files matched the prepared copies exactly.
Both leases were released. No workload resources were changed.

A subsequent refreshed plan from the published `ci/` root against the new
Azure backend returned exit code 0 with no changes. The legacy state retains
nine managed instances, four data sources and compatibility output snapshots.
The decommissioned root must not be applied again with its old network declarations.

Local state backups, ETag metadata and saved plan are access-restricted under
ignored `.migration/`. They are not committed or uploaded as CI artifacts.
