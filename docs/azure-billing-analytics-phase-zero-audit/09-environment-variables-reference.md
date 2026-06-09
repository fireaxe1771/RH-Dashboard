# Supporting Doc 09 — Environment Variables Reference

**Project:** RecoveryHub Dashboard System  
**Purpose:** Complete reference for all environment variables (existing and new), their purpose, validation requirements, and the exact blocks to append to `.env.example`.

---

## Complete Variable Inventory

### Existing Variables (Do Not Change)

| Variable | Required | Default | Description |
|---|---|---|---|
| `PORT` | No | `8001` | HTTP port for Uvicorn |
| `MONGODB_URI` | Yes | — | MongoDB Atlas connection string |
| `MONGODB_DB_NAME` | No | `recoveryhub_dashboard` | MongoDB database name |
| `AZURE_SQL_HOST` | Yes | — | Azure SQL Server FQDN |
| `AZURE_SQL_PORT` | No | `1433` | Azure SQL port |
| `AZURE_SQL_DB` | Yes | — | SQL database name |
| `AZURE_SQL_USER` | Yes (basic auth) | — | SQL username |
| `AZURE_SQL_PASSWORD` | Yes (basic auth) | — | SQL password |
| `AZURE_SQL_AUTHENTICATION` | No | `basic` | `basic` or `azure-ad` |
| `AZURE_SQL_TENANT_ID` | Yes (azure-ad) | — | Tenant ID for Azure AD SQL auth |
| `AZURE_CLIENT_ID` | Yes (prod) | — | User-facing app registration client ID (MSAL login) |
| `AZURE_TENANT_ID` | Yes (prod) | — | Azure Entra ID tenant ID |
| `DEV_AUTH_BYPASS` | No | `false` | Set `true` to skip JWT validation locally |
| `VITE_AZURE_CLIENT_ID` | Yes (prod) | — | Frontend MSAL client ID (Vite build arg) |
| `VITE_AZURE_TENANT_ID` | Yes (prod) | — | Frontend MSAL tenant ID (Vite build arg) |
| `VITE_DEV_AUTH_BYPASS` | No | `false` | Frontend auth bypass (dev only) |

### New Variables — Azure Billing Integration

| Variable | Required | Default | Description |
|---|---|---|---|
| `AZURE_BILLING_CLIENT_ID` | Yes (if sync enabled) | — | App registration client ID for billing service principal |
| `AZURE_BILLING_CLIENT_SECRET` | Yes (if sync enabled) | — | Client secret for billing service principal |
| `AZURE_SUBSCRIPTION_ID` | Yes (if sync enabled) | — | Azure subscription ID to query |
| `AZURE_BILLING_ACCOUNT_ID` | Yes (if sync enabled) | — | Billing account ID (EA: numeric, MCA: UUID format) |
| `AZURE_BILLING_ACCOUNT_TYPE` | No | `MOSP` | `EA`, `MCA`, or `MOSP` |
| `AZURE_MANAGEMENT_GROUP_ID` | No | `""` | Management group ID (multi-subscription scope) |

### New Variables — AI / Embeddings

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | Yes (if sync enabled) | — | OpenAI platform API key |
| `OPENAI_EMBEDDING_MODEL` | No | `text-embedding-3-small` | OpenAI embedding model name |
| `OPENAI_CHAT_MODEL` | No | `gpt-4o-mini` | OpenAI chat model for AI query responses |

### New Variables — Sync Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `BILLING_SYNC_ENABLED` | No | `true` | Enable/disable billing sync scheduler on startup |
| `BILLING_DAILY_SYNC_HOUR` | No | `2` | UTC hour (0–23) for daily sync |
| `BILLING_HISTORY_MONTHS` | No | `12` | Months of history to backfill on first run |

---

## Validation Rules

The `config.py` `Settings` class validates billing variables by calling `validate_billing_settings()` from `validate_settings()`. This check only runs when:
- `BILLING_SYNC_ENABLED=true` (default: true)
- `TESTING` environment variable is not `"true"`

If `BILLING_SYNC_ENABLED=false`, no billing variables are required and the billing scheduler is not started.

**Required when `BILLING_SYNC_ENABLED=true`:**
- `AZURE_BILLING_CLIENT_ID`
- `AZURE_BILLING_CLIENT_SECRET`
- `AZURE_SUBSCRIPTION_ID`
- `OPENAI_API_KEY`

**Optional regardless:**
- `AZURE_BILLING_ACCOUNT_ID` (required for invoice API; sync proceeds without it but invoices skip)
- `AZURE_MANAGEMENT_GROUP_ID` (only needed for management group scope queries)

---

## `.env.example` Additions

The following block must be appended to `.env.example` exactly as shown, after the existing `# --- Entra ID Authentication configuration ---` block. Follow the existing comment formatting style.

```bash
# --- Azure Billing Integration ---
# Dedicated service principal for programmatic billing API access.
# This is SEPARATE from the AZURE_CLIENT_ID used for user-interactive login.
# See docs/supporting/02-azure-auth-setup-guide.md for setup instructions.
AZURE_BILLING_CLIENT_ID=00000000-0000-0000-0000-000000000000
AZURE_BILLING_CLIENT_SECRET=your_billing_service_principal_client_secret

# Azure subscription and billing account identifiers
AZURE_SUBSCRIPTION_ID=00000000-0000-0000-0000-000000000000
AZURE_BILLING_ACCOUNT_ID=12345678
# Billing account agreement type: EA (Enterprise Agreement), MCA (Microsoft Customer Agreement),
# or MOSP (Microsoft Online Services Program / Web Direct / Pay-as-you-go)
AZURE_BILLING_ACCOUNT_TYPE=MOSP

# Optional: Management group ID for multi-subscription cost queries
# Leave blank if querying a single subscription only
AZURE_MANAGEMENT_GROUP_ID=

# --- AI / Cost Analysis ---
# OpenAI API key for generating text embeddings (text-embedding-3-small) and
# AI cost analysis responses (gpt-4o-mini). Required for the AI Cost Analyst feature.
OPENAI_API_KEY=sk-...
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_CHAT_MODEL=gpt-4o-mini

# --- Billing Sync Scheduler Configuration ---
# Set to 'false' to disable the background billing sync scheduler entirely.
# Useful for local development when you don't want live Azure API calls.
BILLING_SYNC_ENABLED=true
# UTC hour (0-23) at which the daily cost sync runs (default: 2 AM UTC)
BILLING_DAILY_SYNC_HOUR=2
# Number of months of historical data to backfill on first startup (max: 12)
BILLING_HISTORY_MONTHS=12
```

---

## Local Development Recommendations

For local development and testing without Azure billing access:

```bash
# Disable billing sync to avoid accidental API calls
BILLING_SYNC_ENABLED=false
```

With `BILLING_SYNC_ENABLED=false`:
- The billing scheduler does not start
- No billing variables are validated
- The billing API endpoints still exist and work (they just return empty results from MongoDB)
- The `POST /api/billing/sync/trigger` endpoint returns a `503` if called

For end-to-end billing testing locally, all billing variables must be real values — there is no mock/offline mode for the Azure APIs themselves. Use a non-production Azure subscription with real cost data.

---

## Secret Handling in Production

In Azure Container Apps (production):
- `AZURE_BILLING_CLIENT_SECRET` is stored as a Container App secret (not a plain env var) — see `terraform/main.tf`
- `OPENAI_API_KEY` is stored as a Container App secret
- All other billing variables are stored as plain env vars (not secrets — they are not sensitive)

In GitHub Actions:
- Both `AZURE_BILLING_CLIENT_SECRET` and `OPENAI_API_KEY` are repository secrets
- They are passed to Terraform as `TF_VAR_azure_billing_client_secret` and `TF_VAR_openai_api_key`
- Terraform marks these variables as `sensitive = true` so they are never printed in plan/apply output

Never commit real values of `AZURE_BILLING_CLIENT_SECRET` or `OPENAI_API_KEY` to the repository. The `.gitignore` already excludes `.env` files, but verify before committing.
