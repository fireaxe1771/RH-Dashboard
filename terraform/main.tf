# Retrieve details of the existing Azure Container Registry
data "azurerm_container_registry" "acr" {
  name                = var.acr_name
  resource_group_name = var.resource_group_name
}

# 1. Create Container App Environment
resource "azurerm_container_app_environment" "aca_env" {
  name                       = var.environment_name
  location                   = var.location
  resource_group_name        = var.resource_group_name
  log_analytics_workspace_id = null # Automatically creates default if omitted
}

# 2. Deploy Backend Container App (FastAPI API)
resource "azurerm_container_app" "backend" {
  name                         = "recoveryhub-dashboard-api"
  container_app_environment_id = azurerm_container_app_environment.aca_env.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"

  # Access registry via Managed Identity
  identity {
    type = "SystemAssigned"
  }

  registry {
    server   = data.azurerm_container_registry.acr.login_server
    identity = "system"
  }

  ingress {
    target_port      = 8000
    external_enabled = true
    transport        = "auto"
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    container {
      name   = "api"
      image  = "${data.azurerm_container_registry.acr.login_server}/rh-dashboard-backend:${var.backend_image_tag}"
      cpu    = "0.25"
      memory = "0.5Gi"

      env {
        name  = "PORT"
        value = "8000"
      }
      env {
        name        = "MONGODB_URI"
        secret_name = "mongodb-uri"
      }
      env {
        name  = "MONGODB_DB_NAME"
        value = "recoveryhub_dashboard"
      }
      env {
        name  = "AZURE_SQL_HOST"
        value = var.azure_sql_host
      }
      env {
        name  = "AZURE_SQL_PORT"
        value = "1433"
      }
      env {
        name  = "AZURE_SQL_DB"
        value = var.azure_sql_db
      }
      env {
        name  = "AZURE_SQL_USER"
        value = var.azure_sql_user
      }
      env {
        name        = "AZURE_SQL_PASSWORD"
        secret_name = "sql-password"
      }
      env {
        name  = "AZURE_CLIENT_ID"
        value = var.azure_client_id
      }
      env {
        name  = "AZURE_TENANT_ID"
        value = var.azure_tenant_id
      }
    }

    # SCALE TO ZERO RULES
    min_replicas = 0
    max_replicas = 5

    # Scale based on HTTP requests
    http_scale_rule {
      name             = "http-scale"
      concurrent_requests = "50"
    }
  }

  secret {
    name  = "mongodb-uri"
    value = var.mongodb_uri
  }

  secret {
    name  = "sql-password"
    value = var.azure_sql_password
  }
}

# 3. Deploy Frontend Container App (React Served by Nginx)
resource "azurerm_container_app" "frontend" {
  name                         = "recoveryhub-dashboard-web"
  container_app_environment_id = azurerm_container_app_environment.aca_env.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"

  identity {
    type = "SystemAssigned"
  }

  registry {
    server   = data.azurerm_container_registry.acr.login_server
    identity = "system"
  }

  ingress {
    target_port      = 80
    external_enabled = true
    transport        = "auto"
    traffic_weight {
      percentage      = 100
      latest_revision = true
    }
  }

  template {
    container {
      name   = "web"
      image  = "${data.azurerm_container_registry.acr.login_server}/rh-dashboard-frontend:${var.frontend_image_tag}"
      cpu    = "0.25"
      memory = "0.5Gi"
    }

    # SCALE TO ZERO RULES
    min_replicas = 0
    max_replicas = 5

    http_scale_rule {
      name             = "http-scale"
      concurrent_requests = "50"
    }
  }
}

# 4. Assign AcrPull permissions to System Identities on the existing Registry
resource "azurerm_role_assignment" "acr_pull_backend" {
  scope                = data.azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_container_app.backend.identity[0].principal_id
}

resource "azurerm_role_assignment" "acr_pull_frontend" {
  scope                = data.azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_container_app.frontend.identity[0].principal_id
}
