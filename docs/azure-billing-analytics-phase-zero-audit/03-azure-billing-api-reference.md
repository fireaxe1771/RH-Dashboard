# Supporting Doc 03 — Azure Billing API Reference

**Project:** RecoveryHub Dashboard System  
**Purpose:** Complete reference for all six Azure API namespaces used in this integration, including endpoints, request/response shapes, scopes, rate limits, and implementation notes.

---

## Authentication Header (All APIs)

All Azure management plane APIs use the same OAuth2 bearer token:

```
Authorization: Bearer <token>
```

Token acquisition scope: `https://management.azure.com/.default`  
Token endpoint: `https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token`  
Flow: `client_credentials` (no user interaction)

The `azure.identity.ClientSecretCredential` handles token acquisition and caching automatically.

---

## API Scope Reference

Scopes determine what data is visible in the response. Choose the most appropriate scope for your use case.

| Scope Type | Scope URI Pattern | Visibility |
|---|---|---|
| Subscription | `/subscriptions/{subscriptionId}` | All resources in one subscription |
| Resource Group | `/subscriptions/{subscriptionId}/resourceGroups/{rgName}` | One resource group |
| Management Group | `/providers/Microsoft.Management/managementGroups/{mgId}` | All child subscriptions |
| EA Billing Account | `/providers/Microsoft.Billing/billingAccounts/{accountId}` | Entire EA enrollment |
| EA Department | `/providers/Microsoft.Billing/billingAccounts/{id}/departments/{deptId}` | One EA department |
| EA Enrollment Account | `/providers/Microsoft.Billing/billingAccounts/{id}/enrollmentAccounts/{eaId}` | One EA account |
| MCA Billing Account | `/providers/Microsoft.Billing/billingAccounts/{accountId}` | Entire MCA account |
| MCA Billing Profile | `/providers/Microsoft.Billing/billingAccounts/{id}/billingProfiles/{profileId}` | One billing profile |
| MCA Invoice Section | `/providers/Microsoft.Billing/billingAccounts/{id}/billingProfiles/{pId}/invoiceSections/{isId}` | One invoice section |

**Recommended default scope for this implementation:** `/subscriptions/{AZURE_SUBSCRIPTION_ID}`

---

## 2.1 Microsoft.CostManagement

**Base URL:** `https://management.azure.com/{scope}/providers/Microsoft.CostManagement/`  
**API Version:** `2025-03-01`

### Query API (Aggregated Cost)

**Endpoint:** `POST /{scope}/providers/Microsoft.CostManagement/query?api-version=2025-03-01`

**Use for:** Aggregate cost by service, resource group, subscription, tag, location. Dashboard summary queries, trend data, top-spenders analysis.

**Request body:**
```json
{
  "type": "Usage",
  "timeframe": "Custom",
  "timePeriod": {
    "from": "2026-05-01T00:00:00+00:00",
    "to": "2026-05-31T00:00:00+00:00"
  },
  "dataset": {
    "granularity": "Monthly",
    "aggregation": {
      "totalCost": {
        "name": "PreTaxCost",
        "function": "Sum"
      }
    },
    "grouping": [
      {
        "type": "Dimension",
        "name": "ServiceName"
      }
    ],
    "filter": {
      "dimensions": {
        "name": "ChargeType",
        "operator": "In",
        "values": ["Usage", "Purchase", "Tax"]
      }
    }
  }
}
```

**Available `granularity` values:** `None`, `Daily`, `Monthly`

**Available `grouping.name` dimension values:**
- `ServiceName` — top-level Azure service (e.g., "Virtual Machines")
- `ServiceFamily` — service family (e.g., "Compute", "Storage")
- `ResourceGroupName` — resource group
- `ResourceId` — individual resource (use sparingly — high cardinality)
- `SubscriptionName` — subscription
- `MeterCategory` — billing meter category
- `MeterSubcategory` — billing meter subcategory
- `Location` — Azure region
- `ChargeType` — Usage, Purchase, Tax, Credit, Adjustment
- `PublisherType` — Azure, Marketplace, AWS
- `TagName` / `TagValue` — custom resource tags

**Response:**
```json
{
  "id": "...",
  "type": "Microsoft.CostManagement/query",
  "properties": {
    "nextLink": null,
    "columns": [
      {"name": "PreTaxCost", "type": "Number"},
      {"name": "ServiceName", "type": "String"},
      {"name": "Currency", "type": "String"}
    ],
    "rows": [
      [4821.44, "Virtual Machines", "USD"],
      [1203.12, "Azure App Service", "USD"]
    ]
  }
}
```

**Pagination:** If `nextLink` is not null, POST to the `nextLink` URL with an empty body to get the next page.

**Rate limit:** 30 requests/minute per subscription scope. Back off on HTTP 429.

---

### Generate Cost Details Report (Unaggregated Line Items)

**Endpoint:** `POST /{scope}/providers/Microsoft.CostManagement/generateCostDetailsReport?api-version=2025-03-01`

**Use for:** Full unaggregated cost line items — the most granular data available. This is the primary data source for `azure_cost_details` collection.

**This is an asynchronous API.** The POST returns an operation URL; you must poll it until completion.

**Step 1 — Submit report request:**
```json
{
  "metric": "ActualCost",
  "timePeriod": {
    "start": "2026-05-01",
    "end": "2026-05-31"
  }
}
```

`metric` options: `ActualCost` (cash basis), `AmortizedCost` (spreads reserved instance costs)

**Step 1 — Response:** HTTP 202 Accepted with header:
```
Location: https://management.azure.com/subscriptions/{id}/providers/Microsoft.CostManagement/operationResults/{operationId}?api-version=2025-03-01
Retry-After: 30
```

**Step 2 — Poll the operation URL:**
```
GET {Location URL}
```

While running, returns HTTP 202 with another `Location` header.  
When complete, returns HTTP 200:
```json
{
  "id": "...",
  "name": "operationId",
  "type": "Microsoft.CostManagement/operationResults",
  "properties": {
    "downloadUrl": "https://ccmreportssa.blob.core.windows.net/armreports/...",
    "validTill": "2026-06-09T00:00:00Z",
    "manifest": {
      "manifestVersion": "2024-02-01",
      "dataFormat": "Csv",
      "blobCount": 1,
      "byteCount": 2845632,
      "compressData": false,
      "requestContext": {},
      "blobs": [
        {
          "blobLink": "https://ccmreportssa.blob.core.windows.net/armreports/.../report.csv",
          "byteCount": 2845632
        }
      ]
    }
  }
}
```

**Step 3 — Download the CSV:**
```
GET {blobLink}
```

No Authorization header needed for the blob download URL — it contains a SAS token.

**Polling strategy:** Poll every 30 seconds. Maximum wait: 15 minutes. If not complete after 15 minutes, log an error and retry the original POST.

**CSV columns returned (ActualCost):**
`InvoiceId`, `BillingAccountId`, `BillingAccountName`, `BillingPeriodStartDate`, `BillingPeriodEndDate`, `BillingProfileId`, `BillingProfileName`, `InvoiceSectionId`, `InvoiceSectionName`, `PartnerName`, `ResellerName`, `ResellerMpnId`, `IndirectCostActual`, `EnrollmentNumber`, `DepartmentName`, `AccountOwnerId`, `AccountName`, `SubscriptionId`, `SubscriptionName`, `Date`, `Product`, `PartNumber`, `MeterId`, `MeterName`, `MeterCategory`, `MeterSubCategory`, `MeterRegion`, `UnitOfMeasure`, `Quantity`, `EffectivePrice`, `Cost`, `UnitPrice`, `BillingCurrency`, `ResourceLocation`, `AvailabilityZone`, `ResourceGroupName`, `ResourceId`, `Tags`, `AdditionalInfo`, `ServiceInfo1`, `ServiceInfo2`, `StorageAccountSubscriptionId`, `IsAzureCreditEligible`, `SkuId`, `PlanName`, `ChargeType`, `Frequency`, `ReservationId`, `ReservationName`, `ProductOrderId`, `ProductOrderName`, `Term`, `PublisherType`, `PublisherName`, `PricingModel`, `ServiceFamily`, `PayGPrice`, `Benefitid`, `BenefitName`, `Provider`

**Important:** Map the CSV column `Cost` to `pre_tax_cost` and `Date` to `date` when storing in MongoDB.

---

### Forecast API

**Endpoint:** `POST /{scope}/providers/Microsoft.CostManagement/forecast?api-version=2025-03-01`

**Request body:**
```json
{
  "type": "Usage",
  "timeframe": "Custom",
  "timePeriod": {
    "from": "2026-06-01T00:00:00+00:00",
    "to": "2026-09-30T00:00:00+00:00"
  },
  "dataset": {
    "granularity": "Monthly",
    "aggregation": {
      "totalCost": {"name": "PreTaxCost", "function": "Sum"}
    }
  },
  "includeActualCost": false,
  "includeFreshPartialCost": false
}
```

**Response:** Same structure as Query API — columns and rows with predicted cost values.

---

### Budgets API

**Endpoint:** `GET /{scope}/providers/Microsoft.CostManagement/budgets?api-version=2025-03-01`

**Response (abbreviated):**
```json
{
  "value": [
    {
      "name": "Production-Monthly",
      "properties": {
        "category": "Cost",
        "amount": 50000.00,
        "timeGrain": "Monthly",
        "timePeriod": {"startDate": "2026-01-01", "endDate": "2026-12-31"},
        "currentSpend": {"amount": 42831.12, "unit": "USD"},
        "forecastSpend": {"amount": 47500.00, "unit": "USD"},
        "notifications": {
          "Actual_GreaterThan_80_Percent": {
            "enabled": true,
            "operator": "GreaterThan",
            "threshold": 80.0,
            "contactEmails": ["admin@streamlineas.com"],
            "thresholdType": "Actual"
          }
        }
      }
    }
  ]
}
```

---

### Alerts API

**Endpoint:** `GET /{scope}/providers/Microsoft.CostManagement/alerts?api-version=2025-03-01`

**Response:**
```json
{
  "value": [
    {
      "id": "...",
      "name": "alertId",
      "type": "Microsoft.CostManagement/alerts",
      "properties": {
        "definition": {
          "type": "Budget",
          "category": "Cost",
          "criteria": "CostThresholdExceeded"
        },
        "description": "Budget threshold exceeded",
        "source": "Budget",
        "details": {
          "timeGrainType": "Monthly",
          "periodStartDate": "06/01/2026 00:00:00",
          "triggeredBy": "Actual_GreaterThan_80_Percent",
          "resourceGroupFilter": [],
          "amount": 40001.00,
          "unit": "USD",
          "currentSpend": 42831.12,
          "budgetName": "Production-Monthly",
          "budgetId": "..."
        },
        "costEntityId": "Production-Monthly",
        "status": "Active",
        "creationTime": "2026-06-02T00:00:00.000Z",
        "closeTime": null,
        "statusModificationTime": "2026-06-02T00:00:00.000Z"
      }
    }
  ]
}
```

---

## 2.2 Microsoft.Consumption

**Base URL:** `https://management.azure.com/{scope}/providers/Microsoft.Consumption/`  
**API Version:** `2024-08-01`

### Reservation Details

**Endpoint:** `GET /{scope}/providers/Microsoft.Consumption/reservationDetails?api-version=2024-08-01&$filter=properties/usageDate ge '2026-05-01' AND properties/usageDate le '2026-05-31'`

**Scope for reservations:** Use billing account scope or reservation order scope.

**Response (abbreviated):**
```json
{
  "value": [
    {
      "properties": {
        "reservationOrderId": "xxxxxxxx-...",
        "reservationId": "xxxxxxxx-...",
        "skuName": "Standard_D4s_v3",
        "reservedHours": 24.0,
        "usageDate": "2026-05-15T00:00:00-08:00",
        "usedHours": 18.5,
        "instanceFlexibilityGroup": "DSv3 Series",
        "instanceFlexibilityRatio": 1.0,
        "totalReservedQuantity": 2.0,
        "kind": "Microsoft.Compute"
      }
    }
  ]
}
```

---

### Reservation Recommendations

**Endpoint:** `GET /{scope}/providers/Microsoft.Consumption/reservationRecommendations?api-version=2024-08-01&$filter=properties/lookBackPeriod eq 'Last30Days' AND properties/scope eq 'Single'`

**Response (abbreviated):**
```json
{
  "value": [
    {
      "location": "eastus",
      "kind": "legacy",
      "properties": {
        "lookBackPeriod": "Last30Days",
        "instanceFlexibilityRatio": 1.0,
        "instanceFlexibilityGroup": "DSv3 Series",
        "normalizedSize": "Standard_D4s_v3",
        "recommendedQuantityNormalized": 3.0,
        "meterId": "xxxxxxxx-...",
        "resourceType": "virtualMachines",
        "term": "P1Y",
        "costWithNoReservedInstances": 2047.20,
        "recommendedQuantity": 3,
        "totalCostWithReservedInstances": 1200.00,
        "netSavings": 847.20,
        "firstUsageDate": "2026-03-01T00:00:00-08:00",
        "scope": "Single",
        "skuProperties": [
          {"name": "OfferTermsYears", "value": "1"},
          {"name": "Product", "value": "Standard_D4s_v3"}
        ]
      }
    }
  ]
}
```

---

### Marketplace Charges

**Endpoint:** `GET /{scope}/providers/Microsoft.Consumption/marketplaces?api-version=2024-08-01&$filter=properties/usageStart ge '2026-05-01' AND properties/usageEnd le '2026-05-31'`

---

### Price Sheet (EA / MCA Only)

**Endpoint:** `GET /{scope}/providers/Microsoft.Consumption/pricesheets/default?api-version=2024-08-01`

**Note:** The response can be very large (hundreds of MB for large EA enrollments). It uses `$skiptoken` for pagination. Parse incrementally — do not load the full response into memory.

---

## 2.3 Microsoft.Advisor

**Base URL:** `https://management.azure.com/{scope}/providers/Microsoft.Advisor/`  
**API Version:** `2025-01-01`

### Recommendations List

**Endpoint:** `GET /subscriptions/{subscriptionId}/providers/Microsoft.Advisor/recommendations?api-version=2025-01-01`

**Filter by category:** `&$filter=Category eq 'Cost'`

**Available categories:** `Cost`, `Security`, `Performance`, `HighAvailability`, `OperationalExcellence`

**Response:**
```json
{
  "nextLink": null,
  "value": [
    {
      "id": "/subscriptions/{id}/resourceGroups/rh-prod-rg/providers/microsoft.compute/virtualMachines/rh-prod-vm-01/providers/Microsoft.Advisor/recommendations/{recId}",
      "name": "{recId}",
      "type": "Microsoft.Advisor/recommendations",
      "properties": {
        "category": "Cost",
        "impact": "High",
        "impactedField": "Microsoft.Compute/virtualMachines",
        "impactedValue": "rh-prod-vm-01",
        "lastUpdated": "2026-06-07T00:00:00Z",
        "recommendationTypeId": "e10b1381-5f0a-47ff-8c7b-37bd13d7c974",
        "shortDescription": {
          "problem": "Reduce costs by resizing or shutting down underutilized virtual machines",
          "solution": "Resize or shut down underutilized virtual machines"
        },
        "extendedProperties": {
          "rollingAverageValue": "2.0",
          "rollingAverageValueUnit": "Percentage",
          "maxCpuP95Value": "6.0",
          "maxCpuP95ValueUnit": "Percentage",
          "currentSku": "Standard_D4s_v3",
          "targetSku": "Standard_D2s_v3",
          "savingsAmount": "158.40",
          "savingsCurrency": "USD",
          "annualSavingsAmount": "1900.80",
          "savingsFrequency": "Monthly"
        },
        "resourceMetadata": {
          "resourceId": "/subscriptions/{id}/resourceGroups/rh-prod-rg/providers/Microsoft.Compute/virtualMachines/rh-prod-vm-01",
          "singular": "virtual machine",
          "plural": "virtual machines"
        },
        "suppressionIds": []
      }
    }
  ]
}
```

**Pagination:** Use `nextLink` if present.

**Key field extraction for MongoDB storage:**
- `properties.extendedProperties.savingsAmount` → `estimated_monthly_savings` (parse to float)
- `properties.extendedProperties.savingsCurrency` → `savings_currency`
- `properties.impact` → `impact` (`High`, `Medium`, `Low`)
- `properties.shortDescription.problem` + `solution` → natural language text for AI documents

---

## 2.4 Microsoft.Billing

**Base URL:** `https://management.azure.com/providers/Microsoft.Billing/`  
**API Version:** `2020-05-01`

### Billing Accounts List

**Endpoint:** `GET /providers/Microsoft.Billing/billingAccounts?api-version=2020-05-01`

**Use for:** Discovering billing account IDs and types on startup/config validation.

**Response:**
```json
{
  "value": [
    {
      "id": "/providers/Microsoft.Billing/billingAccounts/12345678",
      "name": "12345678",
      "type": "Microsoft.Billing/billingAccounts",
      "properties": {
        "accountStatus": "Active",
        "accountType": "Enterprise",
        "agreementType": "EnterpriseAgreement",
        "displayName": "RecoveryHub Enterprise",
        "soldTo": {}
      }
    }
  ]
}
```

`agreementType` values: `EnterpriseAgreement`, `MicrosoftCustomerAgreement`, `MicrosoftOnlineServicesProgram`

---

### Billing Periods (Subscription-Level)

**Endpoint:** `GET /subscriptions/{subscriptionId}/providers/Microsoft.Billing/billingPeriods?api-version=2017-04-24-preview`

**Response:**
```json
{
  "value": [
    {
      "name": "202605",
      "properties": {
        "billingPeriodStartDate": "2026-05-01",
        "billingPeriodEndDate": "2026-05-31",
        "invoiceIds": ["/subscriptions/{id}/providers/Microsoft.Billing/invoices/INV-XXXXX"]
      }
    }
  ]
}
```

---

### Invoices

**EA Endpoint:** `GET /providers/Microsoft.Billing/billingAccounts/{accountId}/invoices?api-version=2020-05-01`

**MCA Endpoint:** `GET /providers/Microsoft.Billing/billingAccounts/{accountId}/billingProfiles/{profileId}/invoices?api-version=2020-05-01`

**Response (abbreviated):**
```json
{
  "value": [
    {
      "id": "/providers/Microsoft.Billing/billingAccounts/{id}/invoices/INV-2026-05-XXXXX",
      "name": "INV-2026-05-XXXXX",
      "properties": {
        "invoiceDate": "2026-06-15",
        "dueDate": "2026-07-15",
        "amountDue": {"currency": "USD", "value": 32831.12},
        "billedAmount": {"currency": "USD", "value": 42831.12},
        "azurePrepaymentApplied": {"currency": "USD", "value": 10000.00},
        "creditAmount": {"currency": "USD", "value": 0.0},
        "invoicePeriodStartDate": "2026-05-01",
        "invoicePeriodEndDate": "2026-05-31",
        "status": "Due",
        "subscriptionDisplayName": "RecoveryHub Production",
        "documents": [
          {
            "documentType": "Invoice",
            "url": "https://..."
          }
        ]
      }
    }
  ]
}
```

---

### Transactions (MCA Only)

**Endpoint:** `GET /providers/Microsoft.Billing/billingAccounts/{accountId}/invoices/{invoiceId}/transactions?api-version=2020-05-01`

Returns individual line items on an invoice.

---

## 2.5 Azure Resource Graph

**Base URL:** `https://management.azure.com/providers/Microsoft.ResourceGraph/resources`  
**API Version:** `2021-03-01`  
**Method:** `POST`

### Query Resources

**Request body:**
```json
{
  "subscriptions": ["xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"],
  "query": "Resources | project id, name, type, location, resourceGroup, subscriptionId, sku, tags, kind, properties.provisioningState | limit 1000",
  "options": {
    "$top": 1000,
    "$skip": 0,
    "resultFormat": "objectArray"
  }
}
```

**Useful KQL queries:**

All resources with metadata:
```kql
Resources
| project id, name, type, location, resourceGroup, subscriptionId, sku, tags, kind
| where type !startswith 'microsoft.resources'
| order by type asc
```

Stopped/deallocated VMs (cost saving candidates):
```kql
Resources
| where type == 'microsoft.compute/virtualmachines'
| extend powerState = properties.extended.instanceView.powerState.code
| where powerState =~ 'PowerState/deallocated' or powerState =~ 'PowerState/stopped'
| project id, name, resourceGroup, location, sku.name, powerState, tags
```

Untagged resources (cost allocation gap):
```kql
Resources
| where isnull(tags) or array_length(bag_keys(tags)) == 0
| where type !startswith 'microsoft.resources'
| project id, name, type, resourceGroup, location
| summarize count() by type
| order by count_ desc
```

**Response:**
```json
{
  "totalRecords": 247,
  "count": 247,
  "data": [
    {
      "id": "/subscriptions/.../resourceGroups/rh-prod-rg/providers/...",
      "name": "rh-prod-app-service",
      "type": "microsoft.web/sites",
      "location": "eastus",
      "resourceGroup": "rh-prod-rg",
      "subscriptionId": "xxxxxxxx-...",
      "sku": {"name": "P1v3", "tier": "PremiumV3"},
      "tags": {"environment": "prod"},
      "kind": "app"
    }
  ],
  "$skipToken": null
}
```

**Pagination:** If `$skipToken` is not null, include it in the next POST as `options.$skipToken`.

---

## 2.6 Azure Retail Prices API (Public)

**Base URL:** `https://prices.azure.com/api/retail/prices`  
**Authentication:** None — fully public API  
**Method:** `GET`

### Query Retail Prices

**Example request:**
```
GET https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview&$filter=serviceName eq 'Virtual Machines' and armRegionName eq 'eastus' and currencyCode eq 'USD' and priceType eq 'Consumption'
```

**OData filter operators:** `eq`, `ne`, `lt`, `le`, `gt`, `ge`, `and`, `or`

**Available filter fields:** `serviceName`, `serviceFamily`, `armRegionName`, `location`, `meterName`, `productName`, `skuName`, `currencyCode`, `priceType`, `isPrimaryMeterRegion`

**`priceType` values:** `Consumption` (pay-as-you-go), `Reservation` (reserved instance pricing)

**Response:**
```json
{
  "BillingCurrency": "USD",
  "CustomerEntityId": "Default",
  "CustomerEntityType": "Retail",
  "Items": [
    {
      "currencyCode": "USD",
      "tierMinimumUnits": 0.0,
      "retailPrice": 0.1600,
      "unitPrice": 0.1600,
      "armRegionName": "eastus",
      "location": "US East",
      "effectiveStartDate": "2023-02-01T00:00:00Z",
      "meterId": "xxxxxxxx-...",
      "meterName": "P1 v3 App Service Hours",
      "productId": "DZH318Z0BP04",
      "skuId": "DZH318Z0BP04/00G4",
      "productName": "Azure App Service Premium v3 Plan - Windows",
      "skuName": "P1 v3",
      "serviceName": "Azure App Service",
      "serviceId": "DZH313Z7MMC8",
      "serviceFamily": "Compute",
      "unitOfMeasure": "1 Hour",
      "type": "Consumption",
      "isPrimaryMeterRegion": true,
      "armSkuName": "P1v3"
    }
  ],
  "NextPageLink": "https://prices.azure.com/api/retail/prices?...",
  "Count": 500
}
```

**Pagination:** Follow `NextPageLink` until null.

---

## Rate Limits and Throttling Reference

| API | Rate Limit | Behavior on Limit |
|---|---|---|
| CostManagement/query | 30 req/min per subscription scope | HTTP 429 with `Retry-After` header |
| CostManagement/generateCostDetailsReport | 10 concurrent reports per billing account | HTTP 429 |
| CostManagement/forecast | 30 req/min per subscription | HTTP 429 |
| CostManagement/budgets | 30 req/min | HTTP 429 |
| Microsoft.Consumption/* | 30 req/min | HTTP 429 |
| Microsoft.Advisor | Undocumented, ~30 req/min | HTTP 429 |
| Microsoft.Billing | 30 req/min | HTTP 429 |
| Resource Graph | 15 req/3 sec burst, 300 req/min | HTTP 429 |
| Retail Prices | No published limit | HTTP 429 if abusive |

**Universal retry strategy:**
1. On HTTP 429: read `Retry-After` header (seconds). If absent, use exponential backoff: `min(2 ** attempt, 60)` seconds.
2. On HTTP 503: exponential backoff, same formula.
3. Maximum 5 retry attempts before raising `BillingAPIError`.
4. On HTTP 403 `AuthorizationFailed`: do not retry — raise `BillingAPIError` with a clear message about RBAC roles.
5. On HTTP 404: log a warning and return an empty result (not an error — the resource may not exist yet).

---

## Error Response Shape

All Azure management APIs return errors in this format:
```json
{
  "error": {
    "code": "RBACAccessDenied",
    "message": "The client 'xxxxxxxx-...' with object id 'xxxxxxxx-...' does not have authorization to perform action 'Microsoft.CostManagement/query/action'..."
  }
}
```

Map `error.code` to log messages:
- `RBACAccessDenied` / `AuthorizationFailed` → RBAC role not assigned or not propagated
- `BillingAccountNotFound` → `AZURE_BILLING_ACCOUNT_ID` is incorrect
- `IndirectCostDisabled` → The billing account has restricted access — requires portal policy change
- `BudgetNotFound` → No budgets configured for this scope (return empty, not error)
