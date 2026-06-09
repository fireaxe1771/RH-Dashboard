# Supporting Doc 02 — Azure Auth Setup Guide

**Project:** RecoveryHub Dashboard System  
**Purpose:** Step-by-step manual instructions for a human operator to complete in the Azure Portal before any code implementation begins.

> **This is a manual prerequisite document.** No code can be implemented until these steps are complete and all values are recorded.

---

## Overview

The billing integration requires a dedicated Azure service principal (App Registration) with:
1. A client secret for programmatic authentication
2. `Cost Management Reader` role at the subscription scope
3. `Reader` role at the subscription scope (for Resource Graph)
4. Billing-scoped access at the billing account level

This is **separate** from the existing `AZURE_CLIENT_ID` app registration, which is used for user-interactive login (MSAL popup flow). Mixing daemon-service credentials into the user-login app registration is an anti-pattern.

---

## Step 1 — Determine Your Billing Account Type

**Why:** Different billing account types use different API scopes and role assignment mechanisms.

1. Sign in to the [Azure Portal](https://portal.azure.com)
2. In the search bar, type **Cost Management + Billing** and select it
3. In the left menu, select **Billing accounts**
4. Look at the **Type** column for your account:

| Type Shown | Account Type | Variable Value |
|---|---|---|
| Microsoft Online Services Program | MOSP / Web Direct | `MOSP` |
| Enterprise Agreement | EA | `EA` |
| Microsoft Customer Agreement | MCA | `MCA` |

5. Record this value for `AZURE_BILLING_ACCOUNT_TYPE`.
6. Click on your billing account and note the **Billing account ID** shown in the Overview or Properties pane. Record this as `AZURE_BILLING_ACCOUNT_ID`.
   - EA format: numeric string (e.g., `12345678`)
   - MCA format: `XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX:XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX_YYYY-MM-DD`
   - MOSP: the billing account scope is typically the subscription itself

---

## Step 2 — Create the Billing Service Principal

1. In the Azure Portal search bar, type **Microsoft Entra ID** and select it
2. In the left menu, select **App registrations**
3. Click **+ New registration**
4. Fill in the form:
   - **Name:** `rh-dashboard-billing` (or `RecoveryHub-Billing-Service`)
   - **Supported account types:** Select **Accounts in this organizational directory only (Single tenant)**
   - **Redirect URI:** Leave blank
5. Click **Register**
6. On the Overview page that appears, record:
   - **Application (client) ID** → this becomes `AZURE_BILLING_CLIENT_ID`
   - **Directory (tenant) ID** → confirm it matches your existing `AZURE_TENANT_ID`
   - **Object ID** → record separately (needed for EA role assignment in Step 4)

---

## Step 3 — Create a Client Secret

1. In your new app registration, select **Certificates & secrets** in the left menu
2. Click **+ New client secret**
3. Fill in:
   - **Description:** `rh-dashboard-billing-production`
   - **Expires:** Select **24 months** (or per your organization's key rotation policy)
4. Click **Add**
5. **Immediately copy the Value column** — this is only shown once. Store it securely.
   - This becomes `AZURE_BILLING_CLIENT_SECRET`

> **Security note:** Store this secret in Azure Key Vault or your CI/CD secrets manager immediately. Do not write it in any file, chat, or email.

---

## Step 4 — Find Your Subscription ID

1. In the Azure Portal search bar, type **Subscriptions** and select it
2. Select the subscription that hosts your RecoveryHub infrastructure
3. In the Overview pane, copy the **Subscription ID**
   - This becomes `AZURE_SUBSCRIPTION_ID`
   - Format: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`

---

## Step 5 — Assign RBAC Roles at Subscription Scope

These roles grant access to cost data, resource metadata, and Advisor recommendations.

### 5.1 — Cost Management Reader

1. In the Azure Portal, navigate to **Subscriptions** → select your subscription
2. In the left menu, select **Access control (IAM)**
3. Click **+ Add** → **Add role assignment**
4. In the **Role** tab, search for `Cost Management Reader` and select it
5. Click **Next**
6. In the **Members** tab:
   - Assign access to: **User, group, or service principal**
   - Click **+ Select members**
   - In the search box, type the name of your new app registration (`rh-dashboard-billing`)
   - Select it from the results
7. Click **Review + assign** twice to confirm

### 5.2 — Reader

Repeat the same steps for the **Reader** role on the same subscription. This is required for Azure Resource Graph queries.

1. IAM → **+ Add** → **Add role assignment**
2. Select the **Reader** role
3. Assign to `rh-dashboard-billing` service principal
4. Confirm

---

## Step 6 — Assign Billing Account Access

The process depends on your billing account type.

### For MOSP (Web Direct / Pay-as-you-go)

1. Navigate to **Cost Management + Billing** → **Billing accounts** → select your account
2. In the left menu, select **Access control (IAM)**
3. Click **+ Add** → **Add role assignment**
4. Search for and select **Billing account reader**
5. Assign to `rh-dashboard-billing`
6. Confirm

### For Microsoft Customer Agreement (MCA)

1. Navigate to **Cost Management + Billing** → **Billing accounts** → select your account
2. In the left menu, select **Access control (IAM)**
3. Click **+ Add** → **Add role assignment**
4. Search for and select **Billing account reader**
5. Assign to `rh-dashboard-billing`
6. Confirm
7. Optionally, for invoice access: also assign **Billing profile reader** at the Billing Profile scope

### For Enterprise Agreement (EA)

EA role assignments **cannot** be completed through the standard Azure Portal IAM blade. You must use either:

**Option A: Azure Portal EA Section (if you have Enterprise Administrator access)**

1. Navigate to **Cost Management + Billing** → **Billing accounts** → select your EA account
2. In the left menu, select **Access control (IAM)**
3. If available, add the **Enrollment Reader** role to `rh-dashboard-billing`

**Option B: PowerShell (if portal method is unavailable)**

First, authenticate as an EA Enterprise Administrator:
```powershell
# Install module if needed
Install-Module -Name Az -AllowClobber -Scope CurrentUser

# Connect to Azure
Connect-AzAccount -TenantId "YOUR_TENANT_ID"

# Get your EA billing account ID (numeric string)
$billingAccountId = "YOUR_NUMERIC_EA_BILLING_ACCOUNT_ID"

# The service principal Object ID (from Step 2)
$spObjectId = "YOUR_SERVICE_PRINCIPAL_OBJECT_ID"
$tenantId = "YOUR_TENANT_ID"

# Enrollment Reader role definition ID for EA (this is a fixed Microsoft-defined ID)
$enrollmentReaderRoleId = "24f8edb6-1668-4659-b5e2-40bb5f3a7d7e"

# Create a new role assignment GUID
$roleAssignmentName = [System.Guid]::NewGuid().ToString()

# Build the URI for the role assignment
$uri = "https://management.azure.com/providers/Microsoft.Billing/billingAccounts/$billingAccountId/billingRoleAssignments/$roleAssignmentName`?api-version=2020-05-01"

# Get an access token
$token = (Get-AzAccessToken -ResourceUrl "https://management.azure.com").Token

# Build the request body
$body = @{
    properties = @{
        roleDefinitionId = "/providers/Microsoft.Billing/billingAccounts/$billingAccountId/billingRoleDefinitions/$enrollmentReaderRoleId"
        principalId = $spObjectId
        principalTenantId = $tenantId
    }
} | ConvertTo-Json

# Make the REST call
Invoke-RestMethod -Method PUT -Uri $uri -Headers @{Authorization = "Bearer $token"; "Content-Type" = "application/json"} -Body $body
```

**Option C: Azure CLI**

```bash
# Login
az login --tenant YOUR_TENANT_ID

# Get access token for management plane
TOKEN=$(az account get-access-token --resource https://management.azure.com --query accessToken -o tsv)

BILLING_ACCOUNT_ID="YOUR_NUMERIC_EA_BILLING_ACCOUNT_ID"
SP_OBJECT_ID="YOUR_SERVICE_PRINCIPAL_OBJECT_ID"
TENANT_ID="YOUR_TENANT_ID"
ROLE_DEF_ID="24f8edb6-1668-4659-b5e2-40bb5f3a7d7e"
ASSIGNMENT_NAME=$(python3 -c "import uuid; print(uuid.uuid4())")

curl -X PUT \
  "https://management.azure.com/providers/Microsoft.Billing/billingAccounts/${BILLING_ACCOUNT_ID}/billingRoleAssignments/${ASSIGNMENT_NAME}?api-version=2020-05-01" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "properties": {
      "roleDefinitionId": "/providers/Microsoft.Billing/billingAccounts/'"${BILLING_ACCOUNT_ID}"'/billingRoleDefinitions/'"${ROLE_DEF_ID}"'",
      "principalId": "'"${SP_OBJECT_ID}"'",
      "principalTenantId": "'"${TENANT_ID}"'"
    }
  }'
```

---

## Step 7 — Optional: Management Group Scope (Multi-Subscription)

If your organization uses Azure Management Groups and you want cost visibility across multiple subscriptions from a single scope, assign the `Cost Management Reader` role at the management group level:

1. In the Azure Portal, search for **Management groups** and select it
2. Select the management group that contains all relevant subscriptions
3. **Access control (IAM)** → **+ Add** → **Add role assignment**
4. Assign **Cost Management Reader** to `rh-dashboard-billing`
5. Note the Management Group ID (visible in the Properties pane or URL):
   - This becomes `AZURE_MANAGEMENT_GROUP_ID`

---

## Step 8 — Verify Access (Smoke Test)

Before proceeding with implementation, verify the service principal can acquire a token and reach the Cost Management API.

**PowerShell verification:**
```powershell
$tenantId = "YOUR_TENANT_ID"
$clientId = "YOUR_AZURE_BILLING_CLIENT_ID"
$clientSecret = "YOUR_AZURE_BILLING_CLIENT_SECRET"
$subscriptionId = "YOUR_AZURE_SUBSCRIPTION_ID"

# Get token using client credentials flow
$body = @{
    grant_type    = "client_credentials"
    client_id     = $clientId
    client_secret = $clientSecret
    scope         = "https://management.azure.com/.default"
}
$tokenResponse = Invoke-RestMethod -Method POST `
    -Uri "https://login.microsoftonline.com/$tenantId/oauth2/v2.0/token" `
    -Body $body `
    -ContentType "application/x-www-form-urlencoded"

$token = $tokenResponse.access_token
Write-Output "Token acquired: $($token.Substring(0,20))..."

# Test Cost Management Query API
$queryBody = @{
    type = "Usage"
    timeframe = "MonthToDate"
    dataset = @{
        granularity = "Monthly"
        aggregation = @{
            totalCost = @{ name = "PreTaxCost"; function = "Sum" }
        }
    }
} | ConvertTo-Json -Depth 10

$response = Invoke-RestMethod -Method POST `
    -Uri "https://management.azure.com/subscriptions/$subscriptionId/providers/Microsoft.CostManagement/query?api-version=2025-03-01" `
    -Headers @{Authorization = "Bearer $token"; "Content-Type" = "application/json"} `
    -Body $queryBody

Write-Output "API call succeeded. Total cost rows returned: $($response.properties.rows.Count)"
```

**Azure CLI verification:**
```bash
# Get token
TOKEN=$(curl -s -X POST \
  "https://login.microsoftonline.com/${AZURE_TENANT_ID}/oauth2/v2.0/token" \
  -d "grant_type=client_credentials&client_id=${AZURE_BILLING_CLIENT_ID}&client_secret=${AZURE_BILLING_CLIENT_SECRET}&scope=https://management.azure.com/.default" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Test query
curl -s -X POST \
  "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/providers/Microsoft.CostManagement/query?api-version=2025-03-01" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"type":"Usage","timeframe":"MonthToDate","dataset":{"granularity":"Monthly","aggregation":{"totalCost":{"name":"PreTaxCost","function":"Sum"}}}}' \
  | python3 -m json.tool
```

Expected result: a JSON response with `properties.columns` and `properties.rows` containing cost data. A `403 Forbidden` with `RBACAccessDenied` means the role assignment has not propagated yet (wait up to 5 minutes and retry).

---

## Step 9 — Record All Values

Collect and securely store the following. These become environment variables and GitHub Actions secrets.

| Variable | Description | Where to Find |
|---|---|---|
| `AZURE_BILLING_CLIENT_ID` | App registration Application (client) ID | Entra ID → App Registrations → `rh-dashboard-billing` → Overview |
| `AZURE_BILLING_CLIENT_SECRET` | App registration client secret value | Copied in Step 3 (only visible once) |
| `AZURE_TENANT_ID` | Directory (tenant) ID | Already known — same as existing `AZURE_TENANT_ID` |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID | Subscriptions → select subscription → Overview |
| `AZURE_BILLING_ACCOUNT_ID` | Billing account ID | Cost Management + Billing → Billing accounts → Properties |
| `AZURE_BILLING_ACCOUNT_TYPE` | `EA`, `MCA`, or `MOSP` | Determined in Step 1 |
| `AZURE_MANAGEMENT_GROUP_ID` | Management group ID (optional) | Management Groups → Properties (only if Step 7 completed) |

---

## Notes on Role Propagation

Azure RBAC role assignments can take **up to 5 minutes** to propagate across all Azure services. If you receive `403 AuthorizationFailed` or `RBACAccessDenied` errors immediately after assigning roles, wait 5 minutes and retry.

## Notes on EA Portal Access

For Enterprise Agreement customers, some billing data (department allocations, enrollment-level spending) is also accessible through the legacy EA Portal at `ea.azure.com`. However, the REST APIs documented in this implementation plan access EA data through the standard Azure Resource Manager endpoint (`management.azure.com`), not through the legacy EA Portal APIs. No separate access to the EA Portal is needed for this integration.
