# Terraform remote state backend (Azure Storage).
#
# State holds generated secrets (PostgreSQL/Redis/Storage keys, Django secret) and MUST NOT live on a
# laptop or in git. scripts/bootstrap-tfstate.ps1 provisions this backend (a dedicated, locked, versioned,
# AAD-only storage account in its own resource group) and fills in storage_account_name below.
#
#   First time:   pwsh ./scripts/bootstrap-tfstate.ps1     # creates the backend + patches this file
#                 cd terraform; terraform init             # initialise the remote backend
#
# use_azuread_auth = true -> Terraform authenticates to the state account with your Azure AD identity
# (az login), so no storage account key is cached under .terraform/. The deployer (and any CI principal
# that runs Terraform) needs "Storage Blob Data Contributor" on the account; the bootstrap script grants it.
terraform {
  backend "azurerm" {
    resource_group_name  = "harmonaize-tfstate-rg"
    storage_account_name = "REPLACE_VIA_BOOTSTRAP" # set by scripts/bootstrap-tfstate.ps1
    container_name       = "tfstate"
    key                  = "harmonaize.prod.tfstate"
    use_azuread_auth     = true
  }
}
