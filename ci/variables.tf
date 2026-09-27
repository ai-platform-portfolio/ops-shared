variable "network" {
  description = "Existing shared network; address allocations require owner approval."
  type = object({
    resource_group_name = string
    location            = string
    name                = string
    address_space       = list(string)
    subnets = map(object({
      name             = string
      address_prefixes = list(string)
      delegations = optional(map(object({
        service = string
        actions = list(string)
      })), {})
    }))
    private_dns_zones = map(string)
    tags              = optional(map(string), {})
  })
}

variable "deployment_identity" {
  type = object({
    resource_group_name = string
    name_prefix         = string
    location            = string
    roles = map(object({
      scope_suffix         = string
      role_definition_name = string
    }))
  })
}

variable "state_container_scope" {
  description = "Existing Terraform state container resource ID, supplied from a secret."
  type        = string
  sensitive   = true
}

variable "github_repositories" {
  description = "Repositories trusted by the shared CI identity; planning trust is optional for code-only deployment."
  type = map(object({
    owner             = string
    owner_id          = string
    name              = string
    repository_id     = string
    plan_environment  = optional(string)
    apply_environment = string
  }))
  validation {
    condition     = try(var.github_repositories["ops-shared"].plan_environment != null, false) && sum([for repo in values(var.github_repositories) : repo.plan_environment == null ? 1 : 2]) <= 20
    error_message = "Include ops-shared with planning trust and stay within the MI's 20-credential limit."
  }
  validation {
    condition = alltrue([for repo in values(var.github_repositories) :
      can(regex("^[1-9][0-9]*$", repo.owner_id)) && can(regex("^[1-9][0-9]*$", repo.repository_id)) &&
      can(regex("^[A-Za-z0-9-]+$", repo.owner)) && can(regex("^[A-Za-z0-9_.-]+$", repo.name)) &&
      (repo.plan_environment == null ? true : can(regex("^[A-Za-z0-9_-]+$", repo.plan_environment))) &&
      can(regex("^[A-Za-z0-9_-]+$", repo.apply_environment)) && repo.plan_environment != repo.apply_environment
    ])
    error_message = "Repository names, positive numeric immutable IDs and distinct plan/apply environments must be present and valid."
  }
}

variable "function_storage" {
  description = "Existing storage identifiers supplied from GitHub Secrets."
  type        = object({ name = string, resource_group_name = string })
  sensitive   = true
}

variable "function_vault" {
  description = "Existing vault reference supplied from GitHub Secrets; no secret values."
  type        = object({ name = string, id = string })
  sensitive   = true
}

variable "function_apps" {
  type = map(object({
    name                   = string
    create_host_containers = optional(bool, false)
    queues                 = set(string)
    secret_names           = set(string)
    settings               = map(string)
  }))
}
