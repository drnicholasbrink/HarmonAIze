terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
    # Used to provision Azure Managed Redis (Microsoft.Cache/redisEnterprise with the new
    # Balanced_* SKUs), which the pinned azurerm 3.x provider does not yet support.
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.5"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.11"
    }
    # Used by modules/analysis_vm to generate the VM's SSH key pair. Declared
    # explicitly so the version is constrained rather than silently auto-installed.
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {
    key_vault {
      # false: a prod Key Vault has purge protection enabled, which makes an automatic purge-on-destroy
      # fail and abort `terraform destroy`. We soft-delete instead (it auto-purges after the retention
      # window; the random name suffix means re-deploys are unaffected). For dev, purge manually if reusing a name.
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
}

provider "random" {}

provider "azapi" {}
