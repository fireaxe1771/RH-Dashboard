# Supporting Doc 08 — Infrastructure and Deployment

**Project:** RecoveryHub Dashboard System  
**Purpose:** Complete specification for all Terraform changes, GitHub Actions CI/CD additions, and Azure Container Apps secret configuration required for the billing integration.

---

## 1. New File: `terraform/billing_variables.tf`

Create this as a new file alongside the existing `variables.tf`. All billing-related Terraform variable declarations go here to keep the billing configuration separate and easy to identify.

```hcl
# --- Azure Billing Integration Variables ---

variable "azure_billing_client_id" {
  type        = string
  description = "Azure Entra ID Application (client) ID for the billing service principal."
}

variable "azure_billing_client_secret" {
  type        = string
  sensitive   = true
  description = "Client secret for the billing service principal. Never logged or printed."
}

variable "azure_subscription_id" {
  type        = string
  description = "Azure subscription ID to query for cost and usage data."
}

variable "azure_billing_account_id" {
  type        = string
  description = "Azure billing account ID (numeric string for EA, UUID for MCA)."
}

variable "azure_billing_account_type" {
  type        = string
  default     = "MOSP"
  description = "Billing account agreement type: EA, MCA, or MOSP."
  validation {
    condition     = contains(["EA", "MCA", "MOSP"], var.azure_billing_account_type)
    error_message = "azure_billing_account_type must be EA, MCA, or MOSP."
  }
}

variable "azure_management_group_id" {
  type        = string
  default     = ""
  description = "Optional Azure Management Group ID for multi-subscription cost queries."
}

# --- AI / Embeddings Variables ---

variable "openai_api_key" {
  type        = string
  sensitive   = true
  description = "OpenAI API key for text-embedding-3-small embeddings and GPT-4o-mini chat completions."
}

variable "openai_embedding_model" {
  type        = string
  default     = "text-embedding-3-small"
  description = "OpenAI embedding model to use for vectorization."
}

variable "openai_chat_model" {
  type        = string
  default     = "gpt-4o-mini"
  description = "OpenAI chat completion model for AI cost analysis."
}

# --- Billing Sync Configuration Variables ---

variable "billing_sync_enabled" {
  type        = string
  default     = "true"
  description = "Enable or disable the billing sync scheduler. Set to 'false' to disable."
}

variable "billing_daily_sync_hour" {
  type        = string
  default     = "2"
  description = "UTC hour (0-23) at which the daily billing sync runs."
}

variable "billing_history_months" {
  type        = string
  default     = "12"
  description = "Number of months of historical billing data to backfill on first run."
}
```

---

## 2. Modified File: `terraform/main.tf`

### 2.1 Add secrets to the backend Container App `secret` blocks

Add these new `secret` blocks inside the `azurerm_container_app.backend` resource, after the existing `secret` blocks:

```hcl
  secret {
    name  = "billing-client-secret"
    value = var.azure_billing_client_secret
  }

  secret {
    name  = "openai-api-key"
    value = var.openai_api_key
  }
```

### 2.2 Add env blocks to the backend Container App `template.container` block

Add these new `env` blocks inside the `template { container { ... } }` block of `azurerm_container_app.backend`, after the existing env blocks:

```hcl
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
      env {
        name        = "OPENAI_API_KEY"
        secret_name = "openai-api-key"
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
```

---

## 3. Modified File: `.github/workflows/deploy.yml`

### 3.1 Add GitHub Actions secret injections

In the `Terraform Init & Apply` step's `env:` block, add after the existing entries:

```yaml
          # Billing integration secrets
          TF_VAR_azure_billing_client_id: ${{ secrets.AZURE_BILLING_CLIENT_ID }}
          TF_VAR_azure_billing_client_secret: ${{ secrets.AZURE_BILLING_CLIENT_SECRET }}
          TF_VAR_azure_subscription_id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
          TF_VAR_azure_billing_account_id: ${{ secrets.AZURE_BILLING_ACCOUNT_ID }}
          TF_VAR_azure_billing_account_type: ${{ secrets.AZURE_BILLING_ACCOUNT_TYPE }}
          TF_VAR_azure_management_group_id: ${{ secrets.AZURE_MANAGEMENT_GROUP_ID }}
          # AI integration secrets
          TF_VAR_openai_api_key: ${{ secrets.OPENAI_API_KEY }}
```

---

## 4. GitHub Actions Secrets to Add

The following secrets must be added to the GitHub repository before the CI/CD pipeline can successfully deploy.

**Path:** GitHub Repository → Settings → Secrets and variables → Actions → New repository secret

| Secret Name | Description | Sensitive |
|---|---|---|
| `AZURE_BILLING_CLIENT_ID` | App registration Application (client) ID for billing service principal | No |
| `AZURE_BILLING_CLIENT_SECRET` | Client secret for billing service principal | **Yes** |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID to query | No |
| `AZURE_BILLING_ACCOUNT_ID` | Billing account ID (EA numeric or MCA UUID) | No |
| `AZURE_BILLING_ACCOUNT_TYPE` | `EA`, `MCA`, or `MOSP` | No |
| `AZURE_MANAGEMENT_GROUP_ID` | Management group ID (can be empty string `""` if not used) | No |
| `OPENAI_API_KEY` | OpenAI platform API key | **Yes** |

---

## 5. Container Resource Considerations

The existing backend Container App is configured with `cpu = "0.25"` and `memory = "0.5Gi"`. The billing sync jobs (particularly the full historical backfill) are CPU and memory-intensive.

**Recommendation:** For production environments running with billing sync enabled, consider increasing the backend container resources:

```hcl
      cpu    = "0.5"
      memory = "1.0Gi"
```

This change is optional for initial deployment but may be necessary if the backfill job fails due to OOM errors or CPU throttling.

Update the same values in `docker-compose.yml` if local resource constraints are an issue during development.

---

## 6. Scale-to-Zero Consideration

The existing Terraform configuration uses `min_replicas = 0` (scale to zero). This means the Container App may have **no running instances** at the time the scheduler is supposed to fire a job.

When there are zero replicas, the APScheduler instance does not run — there is no container to execute it. A scheduled job will simply not fire.

**Mitigation options (choose one):**

**Option A — Set `min_replicas = 1` for production (recommended):**

```hcl
    min_replicas = 1   # Changed from 0
    max_replicas = 5
```

This ensures the billing scheduler is always running. The cost is approximately $4-8/month for a single 0.25 vCPU/0.5Gi container at Azure Container Apps pricing.

**Option B — Use Azure Container Apps Jobs (separate concern, future scope):**

A Container Apps Job could be used to run sync jobs on a schedule independently of the main API app. This is more complex but ideal for truly serverless deployments. This is out of scope for this implementation plan.

**Option C — Accept best-effort scheduling:**

Leave `min_replicas = 0` and accept that daily syncs may occasionally be missed when the app is scaled to zero. The idempotent sync design means the next sync will catch up. The manual trigger endpoint allows forced syncs.

**Decision: Default to Option A (min_replicas = 1) in the Terraform configuration**, as billing sync reliability is important. Document this in the Terraform comments.

---

## 7. Docker Compose Changes (`docker-compose.yml`)

No changes are required to `docker-compose.yml` for local development. The billing sync is controlled by `BILLING_SYNC_ENABLED=true` in the `.env` file.

For local development, consider setting `BILLING_SYNC_ENABLED=false` to avoid accidental API calls to live Azure billing APIs during development. Set it to `true` only when testing the billing integration locally.

---

## 8. Terraform Validation

Before applying, run validation locally:

```bash
cd terraform
terraform init
terraform validate
terraform plan \
  -var="azure_billing_client_id=test-value" \
  -var="azure_billing_client_secret=test-secret" \
  -var="azure_subscription_id=test-sub-id" \
  -var="azure_billing_account_id=test-account-id" \
  -var="openai_api_key=test-openai-key" \
  -var="resource_group_name=test-rg" \
  -var="acr_name=testacr" \
  -var="mongodb_uri=mongodb://localhost:27017" \
  -var="azure_sql_host=test.database.windows.net" \
  -var="azure_sql_db=testdb" \
  -var="azure_sql_user=testuser" \
  -var="azure_sql_password=testpass" \
  -var="azure_client_id=test-client-id" \
  -var="azure_tenant_id=test-tenant-id"
```

The `terraform validate` and `terraform plan` commands must complete without errors before merging to `main`.
