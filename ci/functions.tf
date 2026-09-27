data "azurerm_storage_account" "functions" {
  name                = var.function_storage.name
  resource_group_name = var.function_storage.resource_group_name
}

module "functions" {
  for_each = var.function_apps
  source   = "git::https://github.com/ai-platform-portfolio/terraform-modules.git//modules/function-app?ref=b0c5f712b8a760e4853fa4a6f417b4e1cbde2782"

  name                = each.value.name
  resource_group_name = var.deployment_identity.resource_group_name
  location            = var.deployment_identity.location
  subnet_id           = module.network.subnet_ids["functions"]
  storage = {
    id            = data.azurerm_storage_account.functions.id
    name          = data.azurerm_storage_account.functions.name
    blob_endpoint = data.azurerm_storage_account.functions.primary_blob_endpoint
  }
  queues                 = each.value.queues
  create_host_containers = each.value.create_host_containers
  app_settings = merge(each.value.settings, {
    KEY_VAULT_URL = "https://${var.function_vault.name}.vault.azure.net/"
  })
  role_assignments = {
    for name in each.value.secret_names : name => {
      scope = "${var.function_vault.id}/secrets/${name}"
      role  = "Key Vault Secrets User"
    }
  }
}
