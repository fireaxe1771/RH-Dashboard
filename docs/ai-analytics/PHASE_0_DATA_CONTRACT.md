# Phase 0 — AI Analytics Data Contract

**Verified:** 2026-08-12 against production `recoveryhub_prd` (Azure SQL) and
`AI_FEE_CALC_MULTI_AGENT_PROD` (MongoDB Atlas).

All queries were strictly read-only SELECT / find.

---

## 1. Critical Status Mapping Correction

The implementation plan (Section 3) assumed:
- Status 9 = active AI invoice review grid
- Status 5 = released (PHP `sendInvoice_set`)
- Status 4 = cancelled (PHP `sendInvoice_cancel`)

**Production data contradicts this.** Status 5 has **zero** records. Status 4
is the terminal status for **both** released and cancelled invoices.

### 1.1 Verified AIInvoiceProcessRHTemp status distribution

| Status | Count  | Meaning                              | Evidence |
|--------|--------|--------------------------------------|----------|
| 1      | 5      | Initial / ready state                | `rh_claim_status='Ready to Invoice Insurance'`, `amount_invoiced=0`, no terminal logs |
| 2      | 17,495 | Processing / pending (main queue)    | Only `Line Item Created` logs |
| 4      | 3,146  | **Terminal — released AND cancelled**| `Released` logs (2,058), `Cancelled` logs (764), cancellation records (570) |
| 7      | 8      | Post-release (legacy / intermediate) | `Released` logs, `rh_claim_status` = `Confirm the Receipt of Invoice` or `Payment Received` |
| 9      | 10     | Active review (line items created)   | Only `Line Item Created` logs; very recent |

**Status 5: 0 records. Does not exist in production.**

### 1.2 Business outcome classification (approved)

The distinction between released and cancelled within status 4 is determined
by process logs and cancellation records, NOT by the status field alone.

```
released:
    AI_inv_process_status IN (4, 7)
    AND has process log 'Invoice to Insurance - Released'
    AND NO cancellation record in AIClaimInvoiceCancellationDetails

cancelled_rejected:
    AI_inv_process_status = 4
    AND (has process log 'Invoice to Insurance - Cancelled'
         OR has cancellation record in AIClaimInvoiceCancellationDetails)

pending:
    AI_inv_process_status IN (1, 2, 9)

unknown:
    does not match any of the above
```

### 1.3 Process log text values (exhaustive)

| log_text                              | Count |
|---------------------------------------|-------|
| Line Item Created                     | 5,146 |
| Invoice to Insurance - Released       | 2,066 |
| Invoice to Insurance - Cancelled      | 764   |
| Automatic sending of invoice          | 2     |

### 1.4 Status × log_text cross-tab

| Status | log_text                        | log_count |
|--------|---------------------------------|-----------|
| 2      | Line Item Created               | 2,208     |
| 4      | Line Item Created               | 2,919     |
| 4      | Invoice to Insurance - Released | 2,058     |
| 4      | Invoice to Insurance - Cancelled| 764       |
| 4      | Automatic sending of invoice    | 2         |
| 7      | Line Item Created               | 8         |
| 7      | Invoice to Insurance - Released | 8         |
| 9      | Line Item Created               | 10        |

---

## 2. SQL Schema — Verified Columns

### 2.1 dbo.AIInvoiceProcessRHTemp (5 columns)

| Column                  | Type      | Nullable |
|-------------------------|-----------|----------|
| id                      | bigint    | NO       |
| claim_id                | int       | NO       |
| AI_inv_process_status   | int       | YES      |
| created_date            | datetime  | NO       |
| updated_time            | datetime  | YES      |

### 2.2 dbo.ai_claims_process_logs (6 columns)

| Column        | Type      | Nullable | Notes                |
|---------------|-----------|----------|----------------------|
| id            | bigint    | NO       | PK                   |
| claim_id      | int       | NO       |                      |
| log_text      | varchar   | YES      | max (-1)             |
| user_id       | int       | NO       |                      |
| user_type_id  | tinyint   | NO       |                      |
| created_date  | datetime  | NO       | **timestamp column** |

### 2.3 dbo.AIClaimInvoiceCancellationDetails (7 columns)

| Column               | Type      | Nullable |
|----------------------|-----------|----------|
| id                   | int       | NO       |
| ai_invoice_id        | bigint    | YES      |
| claim_id             | int       | YES      |
| date_of_cancellation | datetime  | YES      |
| reason_id            | int       | NO       |
| reason_descr         | varchar   | YES      |
| created_on           | datetime  | NO       |

### 2.4 dbo.AIClaimInvoiceCancellationReasons (2 columns)

| Column  | Type    | Nullable |
|---------|---------|----------|
| id      | int     | NO       |
| reason  | varchar | NO       |

### 2.5 dbo.Claims — verified key columns (of 67 total)

| Column             | Type      | Nullable |
|--------------------|-----------|----------|
| id                 | int       | NO       |
| submitted          | smallint  | YES      |
| created            | date      | YES      |
| archived           | smallint  | YES      |
| status             | varchar   | YES      |
| dept_id            | int       | YES      |
| original_run_id    | int       | YES      |
| amount_invoiced    | decimal   | YES      |
| invoice_number     | varchar   | YES      |
| run_number         | varchar   | YES      |
| run_date           | datetime2 | YES      |
| date_of_submitted  | datetime  | YES      |
| alarm_received     | varchar   | YES      |
| call_cleared       | varchar   | YES      |

### 2.6 dbo.Departments — verified key columns (of 130 total)

| Column             | Type    | Nullable |
|--------------------|---------|----------|
| id                 | int     | NO       |
| name               | varchar | YES      |
| physical_state     | varchar | YES      |
| deleted            | smallint| YES      |
| active             | smallint| YES      |
| incidents_billing  | tinyint | YES      |
| IsSendInvoiceAI    | tinyint | YES      |

### 2.7 dbo.claim_services (8 columns)

| Column        | Type    | Nullable |
|---------------|---------|----------|
| id            | int     | NO       |
| item          | varchar | YES      |
| rate          | decimal | YES      |
| quantity      | float   | YES      |
| description   | varchar | YES      |
| claim_id      | int     | YES      |
| order         | int     | YES      |
| isFeeFromNew  | bit     | NO       |

### 2.8 dbo.Claim_Service_ResourceFeeMapping (7 columns)

| Column         | Type    | Nullable |
|----------------|---------|----------|
| MappingId      | int     | NO       |
| ClaimServiceId | int     | YES      |
| FeeId          | int     | YES      |
| Quantity       | decimal | YES      |
| Amount         | decimal | YES      |
| unit           | decimal | YES      |
| resourceLabel  | varchar | YES      |

---

## 3. MongoDB Schema — Verified

### 3.1 Collections in AI_FEE_CALC_MULTI_AGENT_PROD

```
ConsumerPriceIndexList
agent_users
ai_agent_conversations     (18,048 docs)
ai_fees_result
ai_line_items               (17,732 docs)
api_token_usage             (27,238 docs)
bls_cpi_data
bls_cpi_data_old
commercial_vehicle_ai_reports
cpi_data_from_2010
cron_logs
department_billing_notes
department_fees_resources   (602 docs)
department_file_vectors
department_onboarding_files
fee_recalculation_audit
fees_resourcefee_db_backup
fees_resourcefee_dept_log
global_consumables
system_depts_documents
```

### 3.2 ai_line_items — field population (sample 500)

| Field                           | Populated | Notes |
|---------------------------------|-----------|-------|
| claim_id                        | 100%      | int — join key |
| draft_claim_id                  | ~100%     | int |
| run_number                      | ~100%     | string |
| department_id                   | ~100%     | int |
| department_name                 | ~100%     | string |
| billing_category                | 99.8%     | string |
| confidence_level                | 100%      | **int, range 0–100** |
| review_msg                      | 99.4%     | string (can be long) |
| claim_processing_status         | 100%      | string |
| line_items_save_to_rh_status    | 100%      | bool |
| agent_exec_status               | 100%      | string |
| invoice_total                   | 100%      | float |
| incident_duration_in_minutes    | 100%      | int |
| dept_ai_fee_mvi_status          | 100%      | string ('Active' / 'Not Active') |
| inserted_at                     | 100%      | **BSON datetime** |
| updated_at                      | 100%      | **BSON datetime** |
| run_date                        | 100%      | **BSON datetime** |
| **is_billable**                 | **0%**    | **NOT POPULATED — always None** |
| **is_billable_not_determined**  | **0%**    | **NOT POPULATED — always None** |
| **dept_ai_identify_billable_status** | **0%** | **NOT POPULATED** |
| **thread_id**                   | **0%**    | **NOT POPULATED** |
| **retry_thread_id**             | **0%**    | **NOT POPULATED** |
| **thread_id_is_billable**       | **0%**    | **NOT POPULATED** |

### 3.3 ai_line_items — additional fields discovered (not in plan)

| Field                          | Type      | Notes |
|--------------------------------|-----------|-------|
| dept_send_auto_invoice_status  | int       | AI send mode |
| update_count                   | int       | Number of updates |
| conversation_id                | string    | Links to `ai_agent_conversations._id` |
| processing_time_seconds        | float     | **AI processing duration** |
| retry_count                    | int       | **Retry count** |
| completed_at                   | datetime  | AI completion timestamp |
| incident_info                  | dict/str  | Full incident data (large) |
| line_items                     | list/None | AI-generated line items (large) |

### 3.4 ai_line_items — status value distributions

**claim_processing_status:**
- COMPLETED (vast majority)
- INITIATED (rare)
- BILLING_LEVEL_NOT_ENABLED (seen — department not enrolled)

**agent_exec_status:**
- success (vast majority)
- in_progress (rare)
- completed_with_issues (seen — partial success)

**line_items_save_to_rh_status:**
- True: 362/500 (72.4%)
- False: 138/500 (27.6%)

**billing_category:**
- Motor Vehicle Accident: 482
- Fire Suppression: 8
- General Response: 6
- Rescue Operation: 1
- Vehicle Fire: 1
- Hazardous Materials: 1
- None: 1

### 3.5 Duplicate claim_id check

Sample of 2,000 documents: **0 duplicates**. Each claim_id maps to exactly
one ai_line_items document. This means the "multiple AI records per claim"
scenario described in the plan does not currently occur in production.

### 3.6 ai_agent_conversations — field population (sample 20)

| Field                   | Populated | Notes |
|-------------------------|-----------|-------|
| agent                   | 100%      | All 'multi_agent_workflow' |
| status                  | 100%      | All 'completed' |
| created_at              | 100%      | BSON datetime |
| processing_stage        | 100%      | All 'completed_all_agents' |
| request_type            | 100%      | All 'incident_analysis' |
| incident_json           | 100%      | dict with claim_id |
| input_data              | 100%      | dict |
| results                 | 100%      | dict |
| output_data             | 100%      | dict (often empty) |
| **execution_time_seconds** | **0%** | **NOT POPULATED** |

**incident_json.claim_id**: 100% populated — reliable join key.

**Conversation linkage:** `ai_line_items.conversation_id` →
`ai_agent_conversations._id` provides a direct link.

---

## 4. Cancellation Reason Inventory

17 reasons total. Mapped to normalized analytics categories:

| reason_id | reason                                        | usage_count | normalized_category          |
|-----------|-----------------------------------------------|-------------|------------------------------|
| 7         | Miscalculated Nested Line Items               | 185         | fee_calculation              |
| 1         | Incorrect line item description               | 123         | line_item_accuracy           |
| 5         | Wrong Level Selected                          | 60          | level_classification         |
| 8         | +1 Hour - Lacking Nested Line Items           | 51          | line_item_accuracy           |
| 9         | +1 Hour - Miscalculated Nested Line Items     | 44          | fee_calculation              |
| 17        | Update Required - Work Item Submitted         | 41          | workflow_update              |
| 10        | ZCO test removal                              | 20          | test_removal                 |
| 2         | Fee Wrong in Fees and Billables AI            | 15          | fee_calculation              |
| 3         | Incorrect consumable calculation              | 12          | fee_calculation              |
| 12        | +1 Hour - Miscite Additional Time on Scene    | 11          | time_on_scene                |
| 4         | Incorrect Resource Attached                   | 8           | line_item_accuracy           |
| 6         | Lacking Nested Line Items                     | 2           | line_item_accuracy           |
| 13        | Department Data Entry Issue                   | 2           | department_data_issue        |
| 11        | Miscite Additional Time on Scene              | 1           | time_on_scene                |
| 14        | Nested Line Item Cited When Canceled On Scene | 0           | nested_line_item_canceled    |
| 15        | +1 Hour - Nested Line Item Cited When Canceled On Scene | 0   | nested_line_item_canceled    |
| 16        | +1 Hour - Command Cited Incorrectly           | 0           | command_error                |

---

## 5. Approved KPI Formulas

### 5.1 Business Release Rate

```
released / (released + cancelled_rejected)
```

Pending excluded from denominator.

### 5.2 Rejection Rate

```
cancelled_rejected / (released + cancelled_rejected)
```

### 5.3 AI Processing Completion Rate

```
Mongo records with claim_processing_status = 'COMPLETED'
/
Mongo records in selected cohort
```

### 5.4 RH Writeback Success Rate

```
line_items_save_to_rh_status = True
/
records for which writeback is expected (claim_processing_status = 'COMPLETED')
```

### 5.5 Confidence Calibration

Buckets: 0–49, 50–69, 70–79, 80–89, 90–100, unknown

---

## 6. Implementation Adjustments from Plan

### 6.1 Status mapping — use process logs, not status field alone

The plan's status 5 = released mapping is **wrong**. Status 5 does not exist.
Status 4 is terminal for both outcomes. Business outcome must be classified
using:
1. Cancellation record presence → cancelled_rejected
2. Process log 'Invoice to Insurance - Released' → released
3. Process log 'Invoice to Insurance - Cancelled' → cancelled_rejected

### 6.2 Billability fields not populated

`is_billable`, `is_billable_not_determined`, `dept_ai_identify_billable_status`,
`thread_id_is_billable` are all **0% populated**. The billability section
(Phase 4) must use `billing_category` as the primary billability signal:
- `billing_category IS NOT NULL` → billability determined
- `billing_category IS NULL` → billability undetermined

### 6.3 Thread/retry fields not populated

`thread_id`, `retry_thread_id` are 0% populated. Use `retry_count` field
(discovered in full doc inspection) for retry detection instead.

### 6.4 Processing duration

`ai_agent_conversations.execution_time_seconds` is 0% populated. Use
`ai_line_items.processing_time_seconds` instead (verified populated).

### 6.5 Conversation linkage

Use `ai_line_items.conversation_id` → `ai_agent_conversations._id` for
direct conversation lookup. Fall back to `incident_json.claim_id` query
for conversations without a direct link.

### 6.6 No duplicate AI records

Production data shows no duplicate `claim_id` in `ai_line_items`. The
"multiple AI records per claim" handling can be simplified — treat as
1:1 but keep the code defensive.

### 6.7 claim_processing_status has additional values

Beyond the plan's `INITIATED`, `IN_PROGRESS`, `COMPLETED`, `ERROR`,
`CANCELLED`, production also has `BILLING_LEVEL_NOT_ENABLED`. This is a
valid non-error terminal state (department not enrolled for AI billing).

### 6.8 agent_exec_status has additional values

Beyond the plan's `pending`, `in_progress`, `success`, `error`, `retry`,
production also has `completed_with_issues`. This is a partial-success state.
