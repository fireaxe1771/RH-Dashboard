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

# 1a. Create a User-Assigned Managed Identity for ACR pull access.
# This must exist BEFORE the container apps so they can pull images on first
# revision. With SystemAssigned the identity only exists after the container app
# is created, but the first revision needs to pull the image immediately — a
# chicken-and-egg problem that causes "Operation expired" after 20 minutes.
resource "azurerm_user_assigned_identity" "acr_pull" {
  name                = "rh-dashboard-acr-pull"
  resource_group_name = var.resource_group_name
  location            = var.location
}

# 1b. Assign AcrPull to the identity BEFORE creating container apps
resource "azurerm_role_assignment" "acr_pull_identity" {
  scope                = data.azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.acr_pull.principal_id
}

# 1c. Wait for Azure AD role assignment propagation before creating container
# apps. Without this delay, the container app's first revision tries to pull
# the image before AAD has replicated the AcrPull role, causing an immediate
# "unable to pull image using Managed identity" failure.
resource "time_sleep" "acr_pull_propagation" {
  depends_on      = [azurerm_role_assignment.acr_pull_identity]
  create_duration = "60s"
}

# 2. Deploy Backend Container App (FastAPI API)
resource "azurerm_container_app" "backend" {
  name                         = "recoveryhub-dashboard-api"
  container_app_environment_id = azurerm_container_app_environment.aca_env.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"

  # Wait for AcrPull role propagation before creating the container app,
  # otherwise the first revision can't pull the image.
  depends_on = [time_sleep.acr_pull_propagation]

  # Use the pre-provisioned user-assigned identity for ACR pull
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.acr_pull.id]
  }

  registry {
    server   = data.azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.acr_pull.id
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
      cpu    = "0.5"
      memory = "1.0Gi"

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
        value = var.mongodb_db_name
      }
      env {
        name  = "RECOVERYHUB_AI_MONGODB_DB_NAME"
        value = var.recoveryhub_ai_mongodb_db_name
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
        name  = "AZURE_SQL_AUTHENTICATION"
        value = var.azure_sql_authentication
      }
      env {
        name  = "AZURE_SQL_TENANT_ID"
        value = var.azure_sql_tenant_id
      }
      env {
        # The backend validates the SPA's idToken audience against this client
        # ID. This is intentional — the frontend sends the idToken
        # (aud=SPA client ID) as the bearer token, and the backend verifies
        # that same audience. This must match VITE_AZURE_CLIENT_ID baked into
        # the frontend image.
        name  = "AZURE_CLIENT_ID"
        value = var.azure_spa_client_id
      }
      env {
        name  = "AZURE_TENANT_ID"
        value = var.azure_tenant_id
      }
      env {
        # Restricts backend CORS to the deployed frontend origin. Must match
        # the frontend Container App's external FQDN (scheme + host).
        name  = "FRONTEND_URL"
        value = var.frontend_url
      }

      # --- Azure Billing Integration ---
      env {
        name  = "AZURE_BILLING_CLIENT_ID"
        value = var.azure_billing_client_id
      }
      env {
        name        = "AZURE_BILLING_CLIENT_SECRET"
        secret_name = "billing-client-secret"
      }
      env {
        name  = "AZURE_SUBSCRIPTION_ID"
        value = var.azure_subscription_id
      }
      env {
        name  = "AZURE_BILLING_ACCOUNT_ID"
        value = var.azure_billing_account_id
      }
      env {
        name  = "AZURE_BILLING_ACCOUNT_TYPE"
        value = var.azure_billing_account_type
      }
      env {
        name  = "AZURE_MANAGEMENT_GROUP_ID"
        value = var.azure_management_group_id
      }

      # --- AI / Embeddings ---
      # Azure OpenAI (Foundry) takes precedence when AZURE_OPENAI_ENDPOINT is set;
      # otherwise the app falls back to OpenAI.com via OPENAI_API_KEY.
      env {
        name        = "OPENAI_API_KEY"
        secret_name = "openai-api-key"
      }
      env {
        name  = "AZURE_OPENAI_ENDPOINT"
        value = var.azure_openai_endpoint
      }
      env {
        name        = "AZURE_OPENAI_API_KEY"
        secret_name = "azure-openai-api-key"
      }
      env {
        name  = "AZURE_OPENAI_API_VERSION"
        value = var.azure_openai_api_version
      }
      env {
        name  = "OPENAI_EMBEDDING_MODEL"
        value = var.openai_embedding_model
      }
      env {
        name  = "OPENAI_CHAT_MODEL"
        value = var.openai_chat_model
      }

      # --- Billing Sync Configuration ---
      env {
        name  = "BILLING_SYNC_ENABLED"
        value = var.billing_sync_enabled
      }
      env {
        name  = "BILLING_DAILY_SYNC_HOUR"
        value = var.billing_daily_sync_hour
      }
      env {
        name  = "BILLING_HISTORY_MONTHS"
        value = var.billing_history_months
      }
    }

    # APScheduler requires a persistent replica; min_replicas = 1 keeps the
    # billing scheduler running instead of scaling to zero (doc 08, section 6).
    min_replicas = 1
    max_replicas = 5

    # Scale based on HTTP requests
    http_scale_rule {
      name                = "http-scale"
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

  # Optional secrets — Azure Container Apps rejects empty secret values, so we
  # use a placeholder when the integration is not yet configured. The backend
  # ignores these unless the corresponding feature is enabled (e.g.
  # BILLING_SYNC_ENABLED=true or AZURE_OPENAI_ENDPOINT is set).
  secret {
    name  = "billing-client-secret"
    value = var.azure_billing_client_secret != "" ? var.azure_billing_client_secret : "not-configured"
  }

  secret {
    name  = "openai-api-key"
    value = var.openai_api_key != "" ? var.openai_api_key : "not-configured"
  }

  secret {
    name  = "azure-openai-api-key"
    value = var.azure_openai_api_key != "" ? var.azure_openai_api_key : "not-configured"
  }
}

# 3. Deploy Frontend Container App (React Served by Nginx)
resource "azurerm_container_app" "frontend" {
  name                         = "recoveryhub-dashboard-web"
  container_app_environment_id = azurerm_container_app_environment.aca_env.id
  resource_group_name          = var.resource_group_name
  revision_mode                = "Single"

  depends_on = [time_sleep.acr_pull_propagation, azurerm_container_app.backend]

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.acr_pull.id]
  }

  registry {
    server   = data.azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.acr_pull.id
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

      # nginx proxies /api/* to the backend container app. Without this the SPA
      # fallback returns index.html for API calls and the client fails to parse
      # the HTML as JSON.
      env {
        name  = "BACKEND_URL"
        value = "https://${azurerm_container_app.backend.ingress[0].fqdn}"
      }
    }

    # Minimum 1 replica ensures the app is always warm (no cold-start
    # latency that would compound with MSAL redirect timing).
    min_replicas = 1
    max_replicas = 5

    http_scale_rule {
      name                = "http-scale"
      concurrent_requests = "50"
    }
  }
}

# 4. AcrPull is assigned to the shared user-assigned identity in step 1b above,
# before the container apps are created. No per-app role assignments needed.
