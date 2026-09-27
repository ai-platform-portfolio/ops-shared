network = {
  resource_group_name = "portfolio-shared-network-rg"
  location            = "uksouth"
  name                = "portfolio-vnet-uks"
  address_space       = ["10.50.0.0/16"]
  subnets = {
    aca = {
      name             = "aca-environment"
      address_prefixes = ["10.50.0.0/23"]
      delegations = {
        aca-delegation = {
          service = "Microsoft.App/environments"
          actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
        }
      }
    }
    private_endpoints = {
      name             = "private-endpoints"
      address_prefixes = ["10.50.2.0/24"]
    }
    functions = {
      name             = "functions"
      address_prefixes = ["10.50.3.0/26"]
      delegations = {
        functions-delegation = {
          service = "Microsoft.App/environments"
          actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
        }
      }
    }
  }
  private_dns_zones = {
    postgres = "privatelink.postgres.database.azure.com"
    blob     = "privatelink.blob.core.windows.net"
    openai   = "privatelink.openai.azure.com"
  }
}

deployment_identity = {
  resource_group_name = "ai-platform-ci-rg"
  name_prefix         = "ai-platform"
  location            = "uksouth"
  roles = {
    sandbox = {
      scope_suffix         = ""
      role_definition_name = "Owner"
    }
    state = {
      scope_suffix         = ""
      role_definition_name = "Storage Blob Data Contributor"
    }
  }
}

function_apps = {
  profile = {
    name                   = "ai-platform-portfolio-ops"
    create_host_containers = true
    queues                 = ["profile-sync", "profile-sync-poison"]
    secret_names           = ["ai-platform-portfolio-ops", "ai-platform-portfolio-ops-webhook"]
    settings = {
      AUTO_ENROLL_REPOSITORIES      = "true"
      GITHUB_ORGANIZATION           = "ai-platform-portfolio"
      GITHUB_APP_ID                 = "5094588"
      GITHUB_INSTALLATION_ID        = "165386168"
      GITHUB_APP_PRIVATE_KEY_SECRET = "ai-platform-portfolio-ops"
      WEBHOOK_SECRET_NAME           = "ai-platform-portfolio-ops-webhook"
    }
  }
}
