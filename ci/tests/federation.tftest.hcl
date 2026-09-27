mock_provider "azurerm" {
  mock_data "azurerm_subscription" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000"
    }
  }
  # Generated ids must still parse as Azure resource ids, or resources that
  # reference them fail before any assertion runs.
  mock_resource "azurerm_virtual_network" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/virtualNetworks/test"
    }
  }
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.ManagedIdentity/userAssignedIdentities/test"
    }
  }
  mock_resource "azurerm_private_dns_zone" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Network/privateDnsZones/test"
    }
  }
}

variables {
  state_container_scope = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/test/providers/Microsoft.Storage/storageAccounts/test/blobServices/default/containers/state"
  function_storage = {
    name                = "fixturestorage"
    resource_group_name = "fixture"
  }
  function_vault = {
    name = "fixture-vault"
    id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/fixture/providers/Microsoft.KeyVault/vaults/fixture-vault"
  }
  function_apps = {}
}

run "github_subject_matches_observed_repository_claim" {
  command = plan

  assert {
    condition = local.github_subjects == {
      ops-shared-apply        = "repo:ai-platform-portfolio@334196300/ops-shared@1389842744:environment:central-apply"
      ops-shared-plan         = "repo:ai-platform-portfolio@334196300/ops-shared@1389842744:environment:central-plan"
      terraform-modules-apply = "repo:ai-platform-portfolio@334196300/terraform-modules@1389557192:environment:central-apply"
    }
    error_message = "Only ops-shared plans infrastructure; org-wide optional apply enrollment remains intact."
  }
}

run "another_repository_reuses_the_identity" {
  command = plan

  variables {
    github_repositories = {
      ops-shared = {
        owner = "example", owner_id = "123", name = "ops-shared", repository_id = "456", plan_environment = "central-plan", apply_environment = "central-apply"
      }
      workload = {
        owner = "example", owner_id = "123", name = "workload", repository_id = "789", plan_environment = "plan", apply_environment = "deploy"
      }
    }
  }

  assert {
    condition     = length(local.github_subjects) == 4 && length(module.deployment_identity.workloads) == 1 && local.github_subjects["workload-plan"] == "repo:example@123/workload@789:environment:plan" && local.github_subjects["workload-apply"] == "repo:example@123/workload@789:environment:deploy"
    error_message = "Adding a repository must add exactly two subjects without adding another managed identity."
  }
}
