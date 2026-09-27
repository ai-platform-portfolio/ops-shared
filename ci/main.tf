terraform {
  required_version = "~> 1.12.0"
  backend "azurerm" {}

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
  resource_provider_registrations = "none"
}

module "network" {
  source = "git::https://github.com/ai-platform-portfolio/terraform-modules.git//modules/network?ref=b0c5f712b8a760e4853fa4a6f417b4e1cbde2782"

  resource_group_name = var.network.resource_group_name
  location            = var.network.location
  name                = var.network.name
  address_space       = var.network.address_space
  subnets             = var.network.subnets
  private_dns_zones   = var.network.private_dns_zones
  tags                = var.network.tags
}

data "azurerm_subscription" "current" {}

locals {
  github_subjects = merge([
    for name, repo in var.github_repositories : {
      for phase, environment in { plan = repo.plan_environment, apply = repo.apply_environment } :
      "${name}-${phase}" => "repo:${repo.owner}@${repo.owner_id}/${repo.name}@${repo.repository_id}:environment:${environment}"
      if environment != null
    }
  ]...)
}

module "deployment_identity" {
  source = "git::https://github.com/ai-platform-portfolio/terraform-modules.git//modules/identity?ref=b0c5f712b8a760e4853fa4a6f417b4e1cbde2782"

  create_resource_group = true
  resource_group_name   = var.deployment_identity.resource_group_name
  location              = var.deployment_identity.location
  name_prefix           = var.deployment_identity.name_prefix
  oidc_issuer_url       = "https://token.actions.githubusercontent.com"
  workloads = {
    central-deployment = {
      federated_subject = local.github_subjects["ops-shared-plan"]
      extra_federated_subjects = {
        for key, subject in local.github_subjects : (key == "ops-shared-apply" ? "apply" : key) => subject
        if key != "ops-shared-plan"
      }
    }
  }
  role_assignments = {
    for key, role in var.deployment_identity.roles : key => {
      workload             = "central-deployment"
      scope                = key == "state" ? var.state_container_scope : "${data.azurerm_subscription.current.id}${role.scope_suffix}"
      role_definition_name = role.role_definition_name
    }
  }
}
