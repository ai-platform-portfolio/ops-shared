# Shared operations

Reusable GitHub Actions workflows for `ai-platform-portfolio`. Workload repositories
own their infrastructure, state, policy and deployment environments. This repository
owns shared workflow orchestration and its tests.

Migration is in progress. Consumers will pin complete commit SHAs. Terraform helpers
originate in `terraform-modules`; lint and review helpers originate in
`engineering-standards`. Existing caller approval requirements remain in force.

Run `make test` for helper tests. Live caller runs are required before declaring the
migration validated; local tests alone cannot prove OIDC, permissions or environment
approval behaviour.
