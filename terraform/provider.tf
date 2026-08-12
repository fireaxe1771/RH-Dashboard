terraform {
  required_version = ">= 1.3.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.9"
    }
  }

  backend "azurerm" {
    resource_group_name  = "ContainerApplications"
    storage_account_name = "rhtfstate001"
    container_name       = "tfstate"
    key                  = "rh-dashboard.tfstate"
    use_oidc             = true
  }
}

provider "azurerm" {
  features {}
}
