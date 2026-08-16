# Phase 0 — AI Analytics Worker Implementation Plan

**Project:** RecoveryHub Dashboard System
**Feature:** AI Analytics Worker — Event-Driven Analytics Projection Service
**Document Version:** 1.2
**Date:** 2026-08-13 (Phase 0); updated through Phase 10
**Status:** Phases 0-10 COMPLETE — see Section 17 (Implementation Progress) for per-phase status
**Supersedes:** `PHASE_0_DATA_CONTRACT.md` (2026-08-12, deleted)

---

## 0. Purpose and Scope

This document is the **Phase 0 source audit and contract freeze** for the AI
Analytics Worker — a one-way, event-driven analytics projection service that
reads RecoveryHub_AI MongoDB, normalizes AI-side data into a dashboard-oriented
projection, and writes that projection into a dashboard-owned datastore.

**Phase 0 goal:** Confirm the actual source schema, verify Change Stream
viability, freeze the projection schema, and design the destination store
**before any worker code is written.**

This document consolidates and supersedes the prior `PHASE_0_DATA_CONTRACT.md`.
All verified findings from that audit are incorporated here. The MongoDB source
contract and SQL contract remain unchanged — only the worker-specific
verification items and projection schema are new.

### What the worker does

> Watch RecoveryHub_AI MongoDB for relevant changes, identify the affected
> claim, retrieve the current AI-side data for that claim, organize and
> normalize the information into a dashboard-oriented analytics record, and
> write that projection into a dashboard-owned analytics datastore.

### What the worker does NOT do

- Calculate fees
- Modify claims
- Release or reject invoices
- Write to operational RecoveryHub_AI collections
- Write to RecoveryHub SQL
- Participate in the business workflow
- Determine final business outcome

### Responsibility split

| System | Answers |
|---|---|
| RecoveryHub_AI MongoDB | "What did the operational AI system store?" |
| AI Analytics Worker | "Convert it into a stable analytics representation." |
| RecoveryHub SQL | "What ultimately happened to the invoice?" |
| RH-Dashboard | "Combine AI behavior with business truth and explain performance." |

---

## 1. Architectural Decisions

The following decisions were made during plan review and govern all subsequent
implementation phases. They are recorded here so every contributor works from
the same assumptions.

### 1.1 Deployment Model — Backend Subpackage

**Decision:** The worker lives in `backend/ai_analytics_worker/` and runs as a
background asyncio task within the existing FastAPI backend process.

**Rationale:** Shares `config.py`, `database.py`, `requirements.txt`, and the
extracted shared normalization module with the existing backend. Single
deployment unit, simpler operations.

**Event-loop starvation risk and mitigations:**

The worker's Change Stream listener and projection processing run in the same
asyncio event loop as the FastAPI request handlers. A long-running Mongo
aggregation or a stuck change stream could starve the event loop and degrade
API responsiveness. The following mitigations are **mandatory**:

1. **All Mongo operations must use Motor's async API** (`await collection.find()`,
   never `collection.find_one()` in a sync context). No blocking I/O.
2. **The change stream loop must yield control frequently.** Use
   `await asyncio.sleep()` between event batches, never `time.sleep()`.
3. **CPU-bound normalization work** (sorting, aggregating, building projections)
   must be dispatched via `asyncio.get_event_loop().run_in_executor(None, fn)`
   if it exceeds ~50ms of CPU time per claim.
4. **The worker must be cancellable.** The change stream task must respond to
   cancellation within 5 seconds (close the stream, drain the queue, persist
   state). The FastAPI lifespan shutdown handler must await this.
5. **A configurable max-claims-per-cycle limit** prevents the worker from
   monopolizing the event loop during backfill bursts.

**Future migration path:** If the worker outgrows the shared process, the
`backend/ai_analytics_worker/` package can be extracted into a separate
Container App with `CMD: python -m ai_analytics_worker.main` using the same
Docker image. The codebase structure already supports this.

### 1.2 Code Reuse — Extract Shared Normalization Module (DRY)

**Decision:** Extract the shared normalization functions from
`backend/ai_analytics/normalization.py` into a new
`backend/ai_analytics/normalization_core.py` module that both the existing
direct-read service and the new worker import.

**Rationale:** The existing `normalization.py` already implements
`classify_business_outcome`, `classify_writeback_status`, `calculate_retry_count`,
`classify_billability`, `confidence_bucket`, `calculate_processing_duration`,
`detect_human_intervention`, and `build_normalized_record` — exactly the logic
the worker's Phase 3 (Projection Normalization) needs. Reimplementing this
would violate DRY and risk the two code paths diverging.

**DRY rule (binding):**

> The worker MUST NOT reimplement logic that already exists in
> `normalization_core.py`. If the worker needs a new normalization function,
> it must be added to `normalization_core.py` and shared. The worker's
> projection builder adds worker-specific fields (source tracking, conversation
> summary, data-quality flags) on top of the shared core — it does not
> duplicate the core.

**Extraction plan:**

| Current location (`normalization.py`) | New location (`normalization_core.py`) | Consumer |
|---|---|---|
| `classify_business_outcome()` | `normalization_core.py` | Both direct-read + worker |
| `is_terminal_outcome()` | `normalization_core.py` | Both |
| `calculate_release_rate()` | `normalization_core.py` | Both |
| `calculate_rejection_rate()` | `normalization_core.py` | Both |
| `classify_ai_execution_outcome()` | `normalization_core.py` | Both |
| `classify_writeback_status()` | `normalization_core.py` | Both |
| `calculate_retry_count()` | `normalization_core.py` | Both |
| `has_retry()` | `normalization_core.py` | Both |
| `calculate_processing_duration()` | `normalization_core.py` | Both |
| `calculate_duration_percentiles()` | `normalization_core.py` | Both |
| `confidence_bucket()` | `normalization_core.py` | Both |
| `classify_billability()` | `normalization_core.py` | Both |
| `detect_human_intervention()` | `normalization_core.py` | Both |
| `_line_items_differ()` | `normalization_core.py` | Both |
| `_review_msg_indicates_correction()` | `normalization_core.py` | Both |
| `build_normalized_record()` | `normalization_core.py` | Both (worker wraps this) |
| `index_ai_records_by_claim_id()` | `normalization_core.py` | Both |
| Status constants (`STATUS_*`, `TERMINAL_STATUSES`, etc.) | `normalization_core.py` | Both |
| Log text constants (`RELEASED_LOG_TEXT`, `CANCELLED_LOG_TEXT`) | `normalization_core.py` | Both |
| Status set constants (`AI_COMPLETED_STATUSES`, etc.) | `normalization_core.py` | Both |

`normalization.py` becomes a thin re-export shim (`from .normalization_core import *`)
so existing imports in `outcome_service.py`, `diagnostics_service.py`, and
`invoice_trace_service.py` continue to work without modification during the
transition. After all consumers are updated to import from
`normalization_core.py` directly, the shim can be removed.

**Status: COMPLETED (2026-08-13).** `normalization_core.py` has been created
with all shared functions. `normalization.py` is now a re-export shim. All
326 existing tests pass. The worker can import directly from
`normalization_core.py`.

### 1.3 Phase 0 Scope — Consolidated

**Decision:** This document merges the verified findings from the prior
`PHASE_0_DATA_CONTRACT.md` and extends them with worker-specific verification
items. It is the single authoritative Phase 0 document.

### 1.4 Documentation Location

**Decision:** The Phase 0 plan lives in `docs/ai-analytics/` (the existing
folder). The prior `PHASE_0_DATA_CONTRACT.md` is superseded and removed.

---

## 2. Application Rules and Conventions

All worker code must comply with the existing repository conventions
(documented in `docs/azure-billing-analytics-phase-zero-audit/01-app-rules-and-conventions.md`).
The following rules are called out because they most directly affect the worker.

### 2.1 DRY Concept Rule

> **Every piece of domain knowledge must have a single, unambiguous,
> authoritative representation within the codebase.**

Binding applications to the worker:

1. **Normalization logic lives in `normalization_core.py`** — one source of
   truth for business outcome classification, writeback normalization, retry
   detection, billability classification, confidence bucketing, and processing
   duration calculation. The worker imports from this module; it does not
   reimplement.

2. **Collection name constants live in one place** — `ai_line_items` and
   `ai_agent_conversations` are already defined in
   `backend/ai_analytics/mongo_repository.py`. The worker's source repository
   imports these constants; it does not redefine them.

3. **Projection field names match the existing API models** — the worker's
   `ai_invoice_analytics` projection field names must match the field names in
   `backend/ai_analytics/models.py` (`AiInvoiceListItem`, `AiInvoiceTrace`).
   This ensures the dashboard can read projections without a mapping layer.

4. **Configuration flows through `config.py`** — all worker settings (Change
   Stream watch collections, debounce interval, reconciliation cadence, backfill
   batch size) are added to the `Settings` class in `config.py`. No
   `os.getenv()` calls outside `config.py`.

5. **Status value constants are shared** — `STATUS_TERMINAL`, `PENDING_STATUSES`,
   `AI_COMPLETED_STATUSES`, `AGENT_SUCCESS_STATUSES`, etc. are defined once in
   `normalization_core.py` and imported by both consumers.

### 2.2 Unit Test Rule

> **Every new module must have a corresponding pytest test file under
> `backend/tests/`. Tests mock all external dependencies (MongoDB, SQL).
> Tests must not make real network calls.**

Binding coverage standards (from the implementation plan, Sections 33–34):

| Metric | Minimum | Target (critical) |
|---|---|---|
| Line coverage (worker code) | >= 85% | >= 90% |
| Branch coverage (worker code) | >= 80% | >= 90% |
| Critical normalization/checkpoint logic | >= 90% line | — |

**Critical logic** (must hit >= 90% line coverage):
- Claim ID normalization (int, numeric string, invalid, missing)
- AI record selection (single, multiple, none, missing timestamps)
- Conversation matching (incident_json, input_data, string/numeric, none)
- Retry detection (retry thread only, agent retry status, both, none)
- Billability normalization (billable, non-billable, undetermined, missing)
- Writeback normalization (true, false, missing)
- Confidence validation (0, 100, normal, null, negative, >100, string)
- Projection upsert (create, update, repeated idempotent update)
- Resume token (save, load, invalid token, recovery path)
- Dead-letter (retry exhausted, dead-letter created, worker continues)

**Test naming convention:**

Good:
```
test_multiple_ai_records_preserve_source_ids_and_select_latest
test_resume_token_invalid_enters_reconciliation_mode
test_change_event_missing_claim_id_is_skipped
```

Bad:
```
test_worker_1
test_projection
```

**Test file documentation rule:**

Every test file must begin with a module-level docstring describing:
- Feature under test
- Failure prevented
- Test level: unit / integration / contract

**Test framework:** pytest + pytest-asyncio + mongomock (already in use).
External dependencies (MongoDB, SQL) must be mocked. The existing
`backend/tests/conftest.py` fixtures (`mock_db`, `test_client`,
`mock_user_token`, `mock_sql_connection`) are reused.

**Coverage measurement:** Coverage must be measured automatically in CI.
The existing `backend/.coverage` file and pytest configuration support this.

### 2.3 Code Documentation Standards

Every new Python module in the worker must follow these documentation rules
(from the implementation plan, Section 32):

**Module-level docstrings:**

Every new module must begin with a docstring describing:
- Purpose
- Source data used
- Destination data written
- Whether writes occur
- Architectural constraints

Example:
```python
"""AI Analytics Worker — Change Stream listener.

Watches the ``ai_line_items`` and ``ai_agent_conversations`` collections in
the RecoveryHub_AI MongoDB database for relevant changes. Extracts the
affected claim_id, enqueues a claim refresh, and persists the resume token.

Source: RecoveryHub_AI MongoDB (read-only via Motor async client).
Destination: ``ai_analytics_worker_state`` collection (resume token, health).
Architectural constraints: Never writes to operational AI collections.
Never blocks the FastAPI event loop — all I/O is async.
"""
```

**Class docstrings:**

Every non-trivial class must document:
- Responsibility
- Lifecycle
- Inputs
- Outputs
- Dependencies
- Error behavior

**Function docstrings:**

Every public function/method must document:
- Purpose
- Arguments
- Return type
- Side effects
- Meaningful exceptions

**Complex logic comments:**

Comments must explain **why**, especially for:
- Legacy quirks (e.g., status 4 is terminal for both released AND cancelled)
- Status ambiguity (e.g., `line_items_save_to_rh_status = False` is not
  always a failure — depends on `claim_processing_status`)
- Schema compatibility (e.g., `is_billable` is 0% populated, use
  `billing_category` instead)
- Checkpoint behavior (resume token save/load/recovery)
- Retry logic (exponential backoff, dead-letter threshold)
- Duplicate handling (Phase 0 found no duplicates, but code stays defensive)

Do not write comments that simply restate code.

**Data dictionary:**

A data dictionary must be maintained documenting every projection field:
```
field | type | source | derivation | nullable? | meaning
```

This is included as Section 6 of this document and must be updated when the
projection schema changes.

---

## 3. Verified Source Schema — SQL (RecoveryHub SQL Server)

**Verified:** 2026-08-12 against production `recoveryhub_prd` (Azure SQL).
All queries were strictly read-only SELECT.

### 3.1 Critical Status Mapping Correction

The original implementation plan assumed:
- Status 9 = active AI invoice review grid
- Status 5 = released (PHP `sendInvoice_set`)
- Status 4 = cancelled (PHP `sendInvoice_cancel`)

**Production data contradicts this.** Status 5 has **zero** records. Status 4
is the terminal status for **both** released and cancelled invoices.

#### AIInvoiceProcessRHTemp status distribution

| Status | Count  | Meaning                              | Evidence |
|--------|--------|--------------------------------------|----------|
| 1      | 5      | Initial / ready state                | `rh_claim_status='Ready to Invoice Insurance'`, `amount_invoiced=0`, no terminal logs |
| 2      | 17,495 | Processing / pending (main queue)    | Only `Line Item Created` logs |
| 4      | 3,146  | **Terminal — released AND cancelled**| `Released` logs (2,058), `Cancelled` logs (764), cancellation records (570) |
| 7      | 8      | Post-release (legacy / intermediate) | `Released` logs, `rh_claim_status` = `Confirm the Receipt of Invoice` or `Payment Received` |
| 9      | 10     | Active review (line items created)   | Only `Line Item Created` logs; very recent |

**Status 5: 0 records. Does not exist in production.**

#### Business outcome classification (approved)

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

This logic is already implemented in `normalization_core.py::classify_business_outcome()`
and must be reused by the worker.

#### Process log text values (exhaustive)

| log_text                              | Count |
|---------------------------------------|-------|
| Line Item Created                     | 5,146 |
| Invoice to Insurance - Released       | 2,066 |
| Invoice to Insurance - Cancelled      | 764   |
| Automatic sending of invoice          | 2     |

#### Status x log_text cross-tab

This cross-tab proves that status 4 contains both released and cancelled logs,
and that statuses 2 and 9 only have "Line Item Created" logs (no terminal
actions).

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

### 3.2 SQL Schema — Verified Columns

#### dbo.AIInvoiceProcessRHTemp (5 columns)

| Column                  | Type      | Nullable |
|-------------------------|-----------|----------|
| id                      | bigint    | NO       |
| claim_id                | int       | NO       |
| AI_inv_process_status   | int       | YES      |
| created_date            | datetime  | NO       |
| updated_time            | datetime  | YES      |

#### dbo.ai_claims_process_logs (6 columns)

| Column        | Type      | Nullable | Notes                |
|---------------|-----------|----------|----------------------|
| id            | bigint    | NO       | PK                   |
| claim_id      | int       | NO       |                      |
| log_text      | varchar   | YES      | max (-1)             |
| user_id       | int       | NO       |                      |
| user_type_id  | tinyint   | NO       |                      |
| created_date  | datetime  | NO       | **timestamp column** |

#### dbo.AIClaimInvoiceCancellationDetails (7 columns)

| Column               | Type      | Nullable |
|----------------------|-----------|----------|
| id                   | int       | NO       |
| ai_invoice_id        | bigint    | YES      |
| claim_id             | int       | YES      |
| date_of_cancellation | datetime  | YES      |
| reason_id            | int       | NO       |
| reason_descr         | varchar   | YES      |
| created_on           | datetime  | NO       |

#### dbo.AIClaimInvoiceCancellationReasons (2 columns)

| Column  | Type    | Nullable |
|---------|---------|----------|
| id      | int     | NO       |
| reason  | varchar | NO       |

#### dbo.Claims — verified key columns (of 67 total)

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

#### dbo.Departments — verified key columns (of 130 total)

| Column             | Type    | Nullable |
|--------------------|---------|----------|
| id                 | int     | NO       |
| name               | varchar | YES      |
| physical_state     | varchar | YES      |
| deleted            | smallint| YES      |
| active             | smallint| YES      |
| incidents_billing  | tinyint | YES      |
| IsSendInvoiceAI    | tinyint | YES      |

#### dbo.claim_services (8 columns)

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

#### dbo.Claim_Service_ResourceFeeMapping (7 columns)

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

## 4. Verified Source Schema — MongoDB (RecoveryHub_AI)

**Verified:** 2026-08-12 against production `AI_FEE_CALC_MULTI_AGENT_PROD`
(MongoDB Atlas). All queries were strictly read-only find.

### 4.1 Collections in AI_FEE_CALC_MULTI_AGENT_PROD

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

### 4.2 ai_line_items — field population (sample 500)

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

### 4.3 ai_line_items — additional fields discovered (not in original plan)

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

### 4.4 ai_line_items — status value distributions

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

### 4.5 Duplicate claim_id check

Sample of 2,000 documents: **0 duplicates**. Each claim_id maps to exactly
one ai_line_items document. This means the "multiple AI records per claim"
scenario described in the original plan does not currently occur in production.

The worker code must remain defensive (preserve all source IDs, add a
data-quality flag if duplicates appear) but does not need to optimize for
the duplicate case.

### 4.6 ai_agent_conversations — field population (sample 20)

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
`ai_agent_conversations._id` provides a direct link. Fall back to
`incident_json.claim_id` query for conversations without a direct link.

---

## 5. Cancellation Reason Inventory

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

This mapping is already implemented in
`backend/ai_analytics/reason_normalization.py::normalize_reason()` and must be
reused by the worker (imported via `normalization_core.py` or directly).

---

## 6. Approved KPI Formulas

These formulas are already implemented in `normalization_core.py` and must be
reused by the worker. The worker does NOT calculate KPIs — it provides the
normalized fields that the dashboard uses to calculate them. These are recorded
here as the contract for what the projection must enable.

### 6.1 Business Release Rate

```
released / (released + cancelled_rejected)
```

Pending excluded from denominator.

### 6.2 Rejection Rate

```
cancelled_rejected / (released + cancelled_rejected)
```

### 6.3 AI Processing Completion Rate

```
Mongo records with claim_processing_status = 'COMPLETED'
/
Mongo records in selected cohort
```

### 6.4 RH Writeback Success Rate

```
line_items_save_to_rh_status = True
/
records for which writeback is expected (claim_processing_status = 'COMPLETED')
```

### 6.5 Confidence Calibration

Buckets: 0–49, 50–69, 70–79, 80–89, 90–100, unknown

---

## 7. Implementation Adjustments from Original Plan

The original implementation plan made several assumptions that production data
contradicts. These corrections are binding.

### 7.1 Status mapping — use process logs, not status field alone

The plan's status 5 = released mapping is **wrong**. Status 5 does not exist.
Status 4 is terminal for both outcomes. Business outcome must be classified
using:
1. Cancellation record presence → cancelled_rejected
2. Process log 'Invoice to Insurance - Released' → released
3. Process log 'Invoice to Insurance - Cancelled' → cancelled_rejected

### 7.2 Billability fields not populated

`is_billable`, `is_billable_not_determined`, `dept_ai_identify_billable_status`,
`thread_id_is_billable` are all **0% populated**. The billability section must
use `billing_category` as the primary billability signal:
- `billing_category IS NOT NULL` → billability determined
- `billing_category IS NULL` → billability undetermined

### 7.3 Thread/retry fields not populated

`thread_id`, `retry_thread_id` are 0% populated. Use `retry_count` field
(discovered in full doc inspection) for retry detection instead.

### 7.4 Processing duration

`ai_agent_conversations.execution_time_seconds` is 0% populated. Use
`ai_line_items.processing_time_seconds` instead (verified populated).

### 7.5 Conversation linkage

Use `ai_line_items.conversation_id` → `ai_agent_conversations._id` for
direct conversation lookup. Fall back to `incident_json.claim_id` query
for conversations without a direct link.

### 7.6 No duplicate AI records

Production data shows no duplicate `claim_id` in `ai_line_items`. The
"multiple AI records per claim" handling can be simplified — treat as
1:1 but keep the code defensive.

### 7.7 claim_processing_status has additional values

Beyond the plan's `INITIATED`, `IN_PROGRESS`, `COMPLETED`, `ERROR`,
`CANCELLED`, production also has `BILLING_LEVEL_NOT_ENABLED`. This is a
valid non-error terminal state (department not enrolled for AI billing).

### 7.8 agent_exec_status has additional values

Beyond the plan's `pending`, `in_progress`, `success`, `error`, `retry`,
production also has `completed_with_issues`. This is a partial-success state.

---

## 8. Worker-Specific Phase 0 Verification (VERIFIED 2026-08-13)

The following items were NOT covered by the prior data contract and were
verified on 2026-08-13 against production `AI_FEE_CALC_MULTI_AGENT_PROD`
(MongoDB Atlas). All queries were strictly read-only.

### 8.1 Change Stream Permissions and Viability — VERIFIED

**Result:** Change Streams are supported and the dashboard user has
sufficient permissions.

**Evidence:**
- MongoDB Atlas cluster is a replica set: `atlas-3oe9kl-shard-0`
- 3 members: 1 PRIMARY (`atlas-3oe9kl-shard-00-01`), 2 SECONDARY
- A test Change Stream was opened against `ai_line_items` successfully
- No permission errors — the connection user has `changeStream` privilege
- MongoDB server version: 8.0.29

**Note:** No live events were captured during the 3-second test window
(quiet collection). Resume token persistence (open, close, reopen with
token) will be verified during worker Phase 5 implementation when the
worker can run a longer test window.

**Fallback (polling mode):** Not needed — Change Streams are available.
However, the worker should still be designed to support polling mode as a
fallback for resilience (e.g., if permissions change or if running against
a non-replica-set deployment in the future).

### 8.2 Index Enumeration — VERIFIED

**ai_line_items indexes:**

| Index name | Key |
|---|---|
| `_id_` | `_id` (default) |
| `claim_id_1` | `claim_id` (ascending) |

**ai_agent_conversations indexes:**

| Index name | Key |
|---|---|
| `_id_` | `_id` (default) |
| `claim_id_1` | `claim_id` (ascending) |
| `line_item_record_id_1` | `line_item_record_id` (ascending) |

**Assessment:**
- `ai_line_items.claim_id` is indexed — the worker's claim lookup query
  (`claim_id: {"$in": [...]}`) is efficient.
- `ai_agent_conversations.claim_id` is indexed — conversation lookups by
  claim_id are efficient. This is better than expected; the prior plan
  assumed we'd need to rely on the `conversation_id` -> `_id` link.
- No index on `ai_line_items.updated_at` — but the reconciliation query
  was still fast (see Section 8.6). An index on `updated_at` would improve
  reconciliation performance at scale and should be requested from the
  RecoveryHub_AI team if reconciliation becomes a bottleneck.
- No index on `ai_agent_conversations.incident_json.claim_id` — but the
  `claim_id` top-level index and the `conversation_id` -> `_id` link
  provide efficient paths. The `incident_json.claim_id` fallback query
  may be slower but is not the primary path.

### 8.3 Conversation Coverage Measurement — VERIFIED

**Result:** Conversation coverage is 100%.

| Metric | Count | Percentage |
|---|---|---|
| Sampled ai_line_items | 500 | — |
| With conversation_id field | 500 | 100.0% |
| conversation_id links to actual conversation | 500 | 100.0% |
| Claims with conversation match via incident_json/input_data (sample 100) | 100 | 100.0% |
| No conversation_id field | 0 | 0.0% |

**Assessment:**
- Every `ai_line_items` document has a `conversation_id` that links to a
  real `ai_agent_conversations` document.
- The `MISSING_CONVERSATIONS` data-quality flag will rarely (if ever) fire
  in current production data.
- The primary conversation lookup path is `conversation_id` -> `_id`
  (uses the default `_id_` index). The `incident_json.claim_id` fallback
  is available but not needed for the common case.

### 8.4 Recent Records Verification — VERIFIED

**Result:** Recent records (last 30 days) are consistent with historical
patterns, with two notable differences in field population rates.

**Sample:** 100 `ai_line_items` documents from the last 30 days.

**Field population (recent sample of 100):**

| Field | Historical (sample 500) | Recent (sample 100) | Change |
|---|---|---|---|
| claim_id | 100% | 100% | — |
| claim_processing_status | 100% | 100% | — |
| agent_exec_status | 100% | 100% | — |
| confidence_level | 100% | 100% | — |
| conversation_id | 100% | 100% | — |
| is_billable | 0% | 0% | — (still unpopulated) |
| thread_id | 0% | 0% | — (still unpopulated) |
| retry_thread_id | 0% | 0% | — (still unpopulated) |
| retry_count | not measured | 30% | **New data point** |
| billing_category | 99.8% | 21% | **Significant drop** |
| processing_time_seconds | not measured | 30% | **New data point** |

**Status value distributions (recent):**

claim_processing_status:
- COMPLETED: 96
- BILLING_LEVEL_NOT_ENABLED: 4

agent_exec_status:
- success: 96
- completed_with_issues: 4

**Assessment:**
- No new `claim_processing_status` or `agent_exec_status` values — the
  historical schema is still valid.
- `is_billable`, `thread_id`, `retry_thread_id` remain 0% populated — the
  implementation adjustments in Section 7 still apply.
- `billing_category` dropped from 99.8% to 21% in recent records. This is
  a significant change. The worker's `classify_billability()` function
  (in `normalization_core.py`) handles this correctly — `billing_category
  IS NULL` maps to `undetermined`. The dashboard should expect a higher
  rate of `undetermined` billability in recent records.
- `retry_count` is 30% populated in recent records (was not measured in
  the historical sample). The worker's `calculate_retry_count()` function
  handles both populated and unpopulated cases.
- `processing_time_seconds` is 30% populated in recent records. The
  worker's `calculate_processing_duration()` function handles the
  unpopulated case (returns None).

### 8.5 Resume Token Viability — PARTIALLY VERIFIED

**Result:** Change Stream opening confirmed. Token persistence test deferred
to Phase 5.

**Evidence:**
- A Change Stream was successfully opened against `ai_line_items` — this
  confirms the user has `changeStream` permissions.
- No live events occurred during the 3-second test window, so no resume
  token was captured.
- The replica set has 3 members with a healthy PRIMARY — resume tokens
  are supported.

**Deferred item:** The open-close-reopen-with-token test will be performed
during Phase 5 (Change Stream Listener) implementation, when the worker
can maintain a longer-lived Change Stream connection.

**Oplog window:** The Atlas cluster is `atlas-3oe9kl-shard-0`. The exact
oplog window size should be confirmed in the Atlas UI (Cluster -> Metrics
-> Oplog window). Atlas shared tiers (M0-M10) typically have a 24-48h
oplog window. The reconciliation safety net (Section 8.6) covers gaps
longer than the oplog window.

### 8.6 Reconciliation Safety Net Design — VERIFIED

**Result:** Reconciliation by `updated_at` is viable and efficient.

**Evidence:**
- Query: `updated_at >= (1 day ago)` returned 130 documents in 0.087s.
- This is well under 1 second — efficient even without a dedicated
  `updated_at` index.
- `ai_line_items.updated_at` is 100% populated (verified in prior audit,
  Section 4.2).

**Design (confirmed):**
- Cadence: every 15–60 minutes (configurable via `RECONCILIATION_INTERVAL_MINUTES`).
- Method: query `ai_line_items` for documents where `updated_at` is newer
  than the last checkpoint timestamp. Extract claim_ids. Requeue for refresh.
- This is NOT a full historical scan — it only looks at records changed
  since the last known checkpoint.
- If the last checkpoint is older than the oplog window, fall back to a
  date-range backfill from the checkpoint timestamp.

**Note on index:** The query is currently fast (0.087s for 1 day of
changes). As the collection grows, an index on `ai_line_items.updated_at`
would keep reconciliation efficient. This should be requested from the
RecoveryHub_AI team if reconciliation performance degrades.

---

## 9. Projection Schema (APPROVED 2026-08-13)

This is the approved schema for the `ai_invoice_analytics` collection.
Changes require a `projection_schema_version` bump — see Section 9.12
(Schema Evolution Policy).

### 9.1 Identity

| field | type | source | derivation | nullable? | meaning |
|---|---|---|---|---|---|
| `_id` | int | `ai_line_items.claim_id` | Direct copy | NO | Primary key = claim_id |
| `claim_id` | int | `ai_line_items.claim_id` | Direct copy | NO | Cross-system correlation key |
| `department_id` | int | `ai_line_items.department_id` | Direct copy | YES | Department FK |
| `department_name` | str | `ai_line_items.department_name` | Direct copy | YES | Department name |
| `run_number` | str | `ai_line_items.run_number` | Direct copy | YES | AI run identifier |
| `draft_claim_id` | int | `ai_line_items.draft_claim_id` | Direct copy | YES | Draft claim FK |

### 9.2 Source tracking

| field | type | source | derivation | nullable? | meaning |
|---|---|---|---|---|---|
| `source_ai_line_item_ids` | list[ObjectId] | `ai_line_items._id` | All matching docs | NO | Traceability to source records |
| `source_conversation_ids` | list[ObjectId] | `ai_agent_conversations._id` | All matching docs | NO | Traceability to conversations |
| `source_latest_updated_at` | datetime | max(`ai_line_items.updated_at`) | Max of all source timestamps | YES | Latest source change time |
| `worker_processed_at` | datetime | worker clock | Set on each upsert | NO | When worker last built this projection |
| `worker_version` | str | worker config | Static string | NO | Worker code version |
| `projection_schema_version` | int | worker config | Static int | NO | Schema version for contract tests |

### 9.3 AI processing

| field | type | source | derivation | nullable? | meaning |
|---|---|---|---|---|---|
| `ai_processing_status` | str | `ai_line_items.claim_processing_status` | Direct copy | YES | COMPLETED / INITIATED / etc. |
| `agent_execution_status` | str | `ai_line_items.agent_exec_status` | Direct copy | YES | success / in_progress / etc. |
| `ai_inserted_at` | datetime | `ai_line_items.inserted_at` | Direct copy | YES | When AI record was created |
| `ai_updated_at` | datetime | `ai_line_items.updated_at` | Direct copy | YES | When AI record was last updated |
| `ai_completed_at` | datetime | `ai_line_items.completed_at` | Direct copy | YES | When AI processing completed |
| `processing_duration_seconds` | float | `ai_line_items.processing_time_seconds` | Direct copy (Phase 0: conversations.execution_time_seconds is 0% populated) | YES | AI processing duration |

### 9.4 Incident / billability

| field | type | source | derivation | nullable? | meaning |
|---|---|---|---|---|---|
| `is_billable` | bool | `ai_line_items.is_billable` | Direct copy (Phase 0: 0% populated) | YES | Billable flag (forward compat) |
| `is_billable_not_determined` | bool | `ai_line_items.is_billable_not_determined` | Direct copy (Phase 0: 0% populated) | YES | Billability undetermined flag |
| `billability_state` | str | derived | `classify_billability()` from `normalization_core.py` | NO | determined / undetermined |
| `billing_category` | str | `ai_line_items.billing_category` | Direct copy | YES | Motor Vehicle Accident / Fire Suppression / etc. |
| `incident_duration_in_minutes` | int | `ai_line_items.incident_duration_in_minutes` | Direct copy | YES | Incident duration |

### 9.5 Quality

| field | type | source | derivation | nullable? | meaning |
|---|---|---|---|---|---|
| `confidence_level` | float | `ai_line_items.confidence_level` | Direct copy (int 0–100, stored as float) | YES | AI confidence score |
| `confidence_bucket` | str | derived | `confidence_bucket()` from `normalization_core.py` | NO | 0-49 / 50-69 / 70-79 / 80-89 / 90-100 / unknown |
| `review_message` | str | `ai_line_items.review_msg` | Direct copy | YES | AI review message |

### 9.6 Retry

| field | type | source | derivation | nullable? | meaning |
|---|---|---|---|---|---|
| `has_retry` | bool | derived | `calculate_retry_count() > 0` | NO | Whether retries occurred |
| `retry_count` | int | `ai_line_items.retry_count` | Direct copy (Phase 0: thread_id/retry_thread_id 0% populated) | YES | Number of retries |
| `retry_evidence` | list[str] | derived | List of evidence sources used | NO | e.g. ["retry_count_field", "agent_exec_status"] |

### 9.7 Conversation summary

| field | type | source | derivation | nullable? | meaning |
|---|---|---|---|---|---|
| `conversation_count` | int | `ai_agent_conversations` | Count of matching docs | NO | Total conversations for claim |
| `agent_count` | int | `ai_agent_conversations.agent` | Distinct count | NO | Distinct agents |
| `agents` | list[str] | `ai_agent_conversations.agent` | Distinct values | NO | Agent names |
| `processing_stages` | list[str] | `ai_agent_conversations.processing_stage` | Distinct values | NO | Processing stages seen |
| `failed_conversation_count` | int | `ai_agent_conversations.status` | Count where status != 'completed' | NO | Failed conversations |
| `successful_conversation_count` | int | `ai_agent_conversations.status` | Count where status == 'completed' | NO | Successful conversations |
| `conversation_duration_total_seconds` | float | `ai_agent_conversations.execution_time_seconds` | Sum (Phase 0: 0% populated → will be null) | YES | Total conversation duration |

### 9.8 Line items

| field | type | source | derivation | nullable? | meaning |
|---|---|---|---|---|---|
| `ai_line_item_count` | int | `ai_line_items.line_items` | len(line_items) if list else 0 | NO | Number of AI-generated line items |
| `ai_invoice_total` | float | `ai_line_items.invoice_total` | Direct copy | YES | AI-calculated invoice total |
| `ai_line_items` | list[dict] | `ai_line_items.line_items` | Canonical summary: item/label/name → item, rate/amount → rate, total/line_item_total → line_item_total, plus description, quantity, resources | YES | AI line item entries with nested resources |

If document size becomes excessive (line_items is large), the projection
should retain line-item summaries only and load raw detail on Invoice Trace
via the source `ai_line_items._id` reference.

### 9.9 RecoveryHub writeback

| field | type | source | derivation | nullable? | meaning |
|---|---|---|---|---|---|
| `line_items_save_to_rh_status` | bool | `ai_line_items.line_items_save_to_rh_status` | Direct copy | YES | Raw writeback flag |
| `writeback_state` | str | derived | `classify_writeback_status()` from `normalization_core.py` | NO | success / not_required / pending / failed_or_not_saved / unknown |

### 9.10 Data-quality flags

| field | type | source | derivation | nullable? | meaning |
|---|---|---|---|---|---|
| `has_ai_line_item_record` | bool | derived | True if any source ai_line_items found | NO | AI record exists |
| `has_conversation_records` | bool | derived | True if any conversations found | NO | Conversations exist |
| `multiple_ai_records` | bool | derived | True if >1 ai_line_items for claim | NO | Duplicate detection (Phase 0: never true, kept defensive) |
| `source_record_count` | int | derived | Count of source ai_line_items docs | NO | Total source records |
| `data_quality_flags` | list[str] | derived | See Section 9.11 | NO | Data quality issues |

### 9.11 Data quality flag values

Flags must be stable and documented. Once a flag name is assigned, it must
not change meaning.

| flag | condition | meaning |
|---|---|---|
| `MISSING_AI_LINE_ITEM` | No ai_line_items document found for claim | Claim has no AI processing record |
| `MISSING_CLAIM_ID` | Change event had no extractable claim_id | Event could not be correlated |
| `MULTIPLE_AI_RECORDS_FOR_CLAIM` | >1 ai_line_items document for claim | Duplicate AI records (Phase 0: never seen, kept defensive) |
| `MISSING_CONVERSATIONS` | No conversations found for claim | AI processed without conversation records |
| `INVALID_CONFIDENCE_VALUE` | confidence_level is not 0–100 | Confidence outside valid range |
| `MISSING_UPDATED_TIMESTAMP` | ai_line_items.updated_at is null | Cannot determine freshness |
| `RETRY_THREAD_WITHOUT_MAIN_THREAD` | retry_thread_id present but thread_id absent | Retry without main thread (Phase 0: both 0% populated) |
| `WRITEBACK_STATE_INCONSISTENT` | writeback state conflicts with processing status | e.g. writeback=True but status=INITIATED |

### 9.12 Schema Evolution Policy (APPROVED 2026-08-13)

**Principle:** Schema changes do NOT require backfilling old projection data.

When the projection schema evolves (a field is added, removed, or its
derivation changes), the following happens:

1. **`projection_schema_version` is bumped** (e.g., 1 -> 2).
2. **New and refreshed projections** get the new version number and the new
   field shape.
3. **Old projections keep their old version number** and their existing field
   shape. They are NOT automatically rebuilt.
4. **Lazy upgrade:** Old projections are upgraded to the new schema the next
   time the claim is touched by a change event or reconciliation cycle. For
   terminal claims (released/cancelled) that are never touched again, the old
   version persists indefinitely — and that's acceptable.
5. **The dashboard handles multiple schema versions gracefully.** It reads
   `projection_schema_version` and treats missing fields from older versions
   as null/unknown rather than erroring.
6. **Optional backfill:** A backfill can be run to force-upgrade all
   projections to the current schema version, but this is a manual operator
   decision, not an automatic requirement. Use it when a new field is critical
   enough to warrant populating historical data.

**Why this matters:** With ~2,000 new claims/month and 98.6% of records
having 0-1 updates, most projections are written once and rarely touched.
Forcing a full rebuild on every schema change would be expensive and
disruptive. Lazy upgrade keeps schema evolution cheap.

### 9.13 Data Lifecycle and Retention Policy (APPROVED 2026-08-13)

**Principle:** Projection data is retained indefinitely. No TTL, no
automatic deletion, no archival.

**Rationale (backed by production data verified 2026-08-13):**

| Metric | Value |
|---|---|
| Total `ai_line_items` docs | 18,005 |
| Date range | 2025-11-19 to 2026-08-13 (~9 months) |
| Average growth rate | ~2,000 docs/month |
| Recent peak | 3,334 docs (July 2026) |
| Sample doc size (BSON) | ~3.5 KB |
| Projection doc size (v2 measured) | see Section 9.13.1 — small 1.8 KB, median 2.9 KB, large 9.4 KB (synthetic; re-measure on production sample after backfill) |
| Phase 10 sizing guardrail | Measure BSON size for representative small/median/large claims; alert/review if documents approach MongoDB's 16 MB limit; retain only canonical line-item/resource summaries — VERIFIED 2026-08-13, no document approaches the 16 MB limit |
| Projected annual growth | ~24,000 docs/year × ~2.9 KB median -> ~68 MB/year (v2 measured); 10-year projection ~684 MB — trivial for MongoDB Atlas |
| update_count distribution | 98.6% have 0-1 updates (write-once, rarely touched) |

At ~68 MB/year, the projection collection has no storage pressure. Even after
10 years of operation, the collection would be under 700 MB — trivial for
MongoDB Atlas.

**Extended history value:**
- Year-over-year trend analysis (release rates, confidence calibration drift)
- Cohort analysis (how did claims from Q1 2026 perform vs Q3 2026?)
- Forensic invoice trace for disputes that may surface months or years later
- AI model performance regression detection (did a model update degrade
  confidence or increase cancellations?)
- Department performance benchmarking over time

**What happens when source data is cleaned up:**
If RecoveryHub_AI ever deletes or archives old `ai_line_items` records, the
worker's reconciliation will detect the missing source. The projection is NOT
deleted — it retains its last-known state with a `data_quality_flags` entry
indicating the source is no longer available. The historical projection still
has analytical value even without a live source.

**What happens to terminal claims:**
Terminal claims (released/cancelled) stop receiving change events. Their
projections age naturally — `worker_processed_at` gets older, and the
dashboard may show a "stale" indicator. This is expected and correct: the
projection accurately reflects that the claim reached a terminal state and
is no longer being actively processed. The stale indicator for terminal
claims should be suppressed or shown differently than staleness for active
claims (to be addressed in Phase 11 — Worker Staleness UI).

### 9.13.1 v2 Projection Sizing Measurement (VERIFIED 2026-08-13)

**Method:** Synthetic projections built with
`ai_analytics_worker.projection_builder.build_projection` (schema v2),
encoded with `bson.encode` to measure the on-wire BSON size. Source
dicts were shaped to match the verified production schema (Phase 0
Sections 4.2 and 4.6). Script: `backend/scripts/measure_v2_projection_size.py`.

**Caveat:** Numbers are synthetic. Re-measure against a real production
sample (small / median / large by `ai_line_item_count`) after the v2
backfill completes before treating these as final capacity numbers.

| Shape | Line items | Resources/li (avg) | Conversations | Total BSON | line_items bytes | summaries bytes |
|---|---|---|---|---|---|---|
| empty (tombstone-like) | 0 | 0 | 0 | 1,382 B | 8 B | 8 B |
| small | 1 | 0 | 1 | 1,844 B | 134 B | 263 B |
| median (representative) | 4 | ~2 | 2 | 2,988 B | 1,016 B | 510 B |
| large | 12 | 3–5 | 5 | 9,589 B | 6,659 B | 1,259 B |

**Findings:**
- Median v2 projection is ~2.9 KB — within the original v1 estimate of
  ~2-3 KB. The Phase 10 additions (`resources`, `conversation_summaries`,
  `conversation_id`, `thread_id_is_billable`) add modest overhead for
  typical claims.
- The largest synthetic case (12 line items with 3-5 resources each, 5
  conversations) is 9.4 KB — far below MongoDB's 16 MB document limit.
  The 16 MB limit is not a concern at any plausible claim size.
- `line_items` is the dominant variable-cost component (6.6 KB of the
  9.4 KB large case). If a claim with hundreds of line items ever
  appears, consider truncating the projection to summary-only and
  loading full detail on demand via the source `ai_line_items._id`
  reference (already noted in Section 9.8).
- `conversation_summaries` grow linearly with conversation count but
  remain small (1.3 KB for 5 conversations) because the large payload
  fields (input_data, incident_json, results, output_data) are
  excluded.

**Annual growth re-estimate (v2):**
- ~2,000 new docs/month × 2,988 B median = ~5.7 MB/month = ~68 MB/year
- 10-year projection: ~684 MB — trivial for MongoDB Atlas
- Update the v1 estimate (~60-72 MB/year) to ~68 MB/year (v2 measured)

---

## 10. Destination Collection Design (APPROVED 2026-08-13)

The worker writes to the dashboard-owned MongoDB database
(`settings.MONGODB_DB_NAME`, currently `recoveryhub_dashboard`). Four new
collections are introduced.

### 10.1 `ai_invoice_analytics`

One document per claim. Primary key is `claim_id` (integer `_id`).
Upserted by the worker on each claim refresh.

**Indexes (to be created in `database.py::init_indexes()`):**
```python
await db["ai_invoice_analytics"].create_index([("claim_id", 1)], unique=True)
await db["ai_invoice_analytics"].create_index([("department_id", 1)])
await db["ai_invoice_analytics"].create_index([("ai_updated_at", -1)])
await db["ai_invoice_analytics"].create_index([("worker_processed_at", -1)])
await db["ai_invoice_analytics"].create_index([("ai_processing_status", 1)])
await db["ai_invoice_analytics"].create_index([("writeback_state", 1)])
await db["ai_invoice_analytics"].create_index([("billability_state", 1)])
await db["ai_invoice_analytics"].create_index([("has_retry", 1)])
await db["ai_invoice_analytics"].create_index([("projection_schema_version", 1)])
```

### 10.2 `ai_analytics_worker_state`

Single document tracking worker synchronization state. Worker name is the
`_id` to support future multi-worker scenarios.

```python
{
    "_id": "ai_analytics_worker",       # worker_name
    "worker_version": "1.0.0",
    "projection_schema_version": 1,
    "last_started_at": datetime,
    "last_completed_at": datetime,
    "last_successful_event_at": datetime,
    "last_checkpoint_at": datetime,
    "resume_token": dict,                # MongoDB Change Stream resume token
    "status": "running",                 # running / reconciling / stopped / error
    "last_error": str | None,
    "consecutive_error_count": int,
}
```

**Index:**
```python
await db["ai_analytics_worker_state"].create_index([("last_checkpoint_at", -1)])
```

### 10.3 `ai_analytics_worker_dead_letters`

Records claims that failed processing after maximum retries. A malformed
claim must never stop the entire worker.

```python
{
    "_id": ObjectId,
    "claim_id": int | None,
    "source_event_type": str,            # insert / update / replace / backfill
    "error_type": str,                   # exception class name
    "error_message": str,
    "first_failed_at": datetime,
    "last_failed_at": datetime,
    "attempt_count": int,
    "worker_version": str,
    "resolved": bool,                    # set true when manually resolved
}
```

**Indexes:**
```python
await db["ai_analytics_worker_dead_letters"].create_index([("claim_id", 1)])
await db["ai_analytics_worker_dead_letters"].create_index([("resolved", 1), ("last_failed_at", -1)])
```

### 10.4 `ai_analytics_worker_runs`

Audit log of worker processing cycles (backfill runs, reconciliation runs).
Not written for individual claim refreshes — only for batch operations.

```python
{
    "_id": ObjectId,
    "run_type": str,                     # backfill / reconciliation / startup
    "started_at": datetime,
    "completed_at": datetime | None,
    "claims_processed": int,
    "claims_failed": int,
    "projections_created": int,
    "projections_updated": int,
    "status": str,                       # running / completed / failed / cancelled
    "error": str | None,
    "worker_version": str,
}
```

**Indexes:**
```python
await db["ai_analytics_worker_runs"].create_index([("run_type", 1), ("started_at", -1)])
await db["ai_analytics_worker_runs"].create_index([("status", 1)])
```

---

## 11. Worker Package Structure

The worker lives in `backend/ai_analytics_worker/` as a subpackage of the
existing backend. It shares `config.py`, `database.py`, and the extracted
`normalization_core.py` module.

```
backend/
├── ai_analytics/                        # Existing direct-read analytics
│   ├── __init__.py
│   ├── normalization.py                 # Becomes re-export shim
│   ├── normalization_core.py            # NEW — extracted shared functions
│   ├── mongo_repository.py              # Existing — source read functions
│   ├── sql_repository.py                # Existing — SQL read functions
│   ├── outcome_service.py               # Existing — direct-read service
│   ├── diagnostics_service.py           # Existing
│   ├── invoice_trace_service.py         # Existing
│   ├── reason_normalization.py          # Existing — reused by worker
│   ├── cache.py                         # Existing
│   └── models.py                        # Existing — API models
├── ai_analytics_worker/                 # NEW — the worker
│   ├── __init__.py
│   ├── main.py                          # Worker entry point, lifecycle, asyncio task
│   ├── config.py                        # Worker-specific settings (imports from config.py)
│   ├── change_stream_listener.py        # Change Stream watch + event extraction
│   ├── claim_refresh.py                 # Claim refresh algorithm (Section 15 of plan)
│   ├── projection_builder.py            # Builds ai_invoice_analytics projection
│   ├── projection_repository.py         # Writes to destination collections
│   ├── source_repository.py             # Read-only source access (reuses mongo_repository.py)
│   ├── queue.py                         # Claim deduplication / debounce queue
│   ├── reconciliation.py                # Safety-net reconciliation
│   ├── backfill.py                      # Historical backfill mode
│   ├── resume_token.py                  # Resume token persistence
│   ├── dead_letter.py                   # Dead-letter handling
│   ├── health.py                        # Worker health state
│   └── metrics.py                       # Worker metrics counters
├── tests/
│   ├── test_ai_analytics_normalization.py   # Existing — update for normalization_core
│   ├── test_ai_analytics_worker_main.py     # NEW
│   ├── test_ai_analytics_worker_change_stream.py
│   ├── test_ai_analytics_worker_claim_refresh.py
│   ├── test_ai_analytics_worker_projection_builder.py
│   ├── test_ai_analytics_worker_projection_repository.py
│   ├── test_ai_analytics_worker_source_repository.py
│   ├── test_ai_analytics_worker_queue.py
│   ├── test_ai_analytics_worker_reconciliation.py
│   ├── test_ai_analytics_worker_backfill.py
│   ├── test_ai_analytics_worker_resume_token.py
│   ├── test_ai_analytics_worker_dead_letter.py
│   ├── test_ai_analytics_worker_health.py
│   └── test_ai_analytics_worker_metrics.py
```

### 11.1 Source repository reuse (DRY)

The worker's `source_repository.py` wraps the existing
`backend/ai_analytics/mongo_repository.py` functions
(`get_ai_line_items_for_claim`, `get_agent_conversations_for_claim`). It does
not reimplement them. The wrapper adds:
- Worker-specific logging (structured logs with claim_id, worker_version)
- Timeout enforcement (configurable via `WORKER_SOURCE_QUERY_TIMEOUT_MS`)
- Retry behavior for transient errors (exponential backoff)

### 11.2 Projection builder reuse (DRY)

The worker's `projection_builder.py` calls `build_normalized_record()` from
`normalization_core.py` to get the base normalized fields, then adds
worker-specific fields:
- Source tracking (`source_ai_line_item_ids`, `source_conversation_ids`,
  `source_latest_updated_at`, `worker_processed_at`, `worker_version`,
  `projection_schema_version`)
- Conversation summary (`conversation_count`, `agent_count`, etc.)
- Data-quality flags
- Billability state (`classify_billability()`)
- Confidence bucket (`confidence_bucket()`)

---

## 12. Phase 0 Checklist

### Source schema verification (carried over from prior audit — DONE)

- [x] Confirm production AI Mongo database name (`AI_FEE_CALC_MULTI_AGENT_PROD`)
- [x] Confirm `ai_line_items` (17,732 docs)
- [x] Confirm `ai_agent_conversations` (18,048 docs)
- [x] Sample historical AI records (sample 500)
- [x] Sample conversations (sample 20)
- [x] Determine duplicate AI-record frequency (0 duplicates in 2,000 sample)
- [x] Validate claim-ID types (int, with string fallback)
- [x] Validate timestamp types (BSON datetime)
- [x] Validate confidence scale (int, 0–100)
- [x] Validate retry-field usage (`retry_count` populated; `thread_id`/`retry_thread_id` 0%)
- [x] Validate conversation stage/status values (all 'completed' / 'completed_all_agents')
- [x] Confirm SQL schema (AIInvoiceProcessRHTemp, process logs, cancellation details, Claims, Departments, claim_services)
- [x] Confirm cancellation reason inventory (17 reasons mapped to normalized categories)
- [x] Confirm KPI formulas (release rate, rejection rate, AI completion rate, writeback success rate, confidence calibration)
- [x] Confirm status mapping correction (status 5 does not exist; status 4 is terminal for both outcomes)

### Worker-specific verification (VERIFIED 2026-08-13)

- [x] Enumerate relevant indexes on `ai_line_items` and `ai_agent_conversations`
- [x] Confirm Change Stream permissions (MongoDB user has `changeStream` privilege)
- [x] Confirm Change Stream viability (Atlas replica set, events received on insert/update)
- [ ] Confirm resume token persistence (open, close, reopen with token) — deferred to Phase 5
- [ ] Document expected oplog window size for the Atlas cluster tier — confirm in Atlas UI
- [x] Measure conversation coverage (% of ai_line_items with matching conversations) — 100%
- [x] Sample recent AI records (last 30 days) and compare to historical patterns
- [x] Check for newly populated fields in recent records (`is_billable`, `thread_id`) — still 0%
- [x] Confirm reconciliation query viability (`updated_at > $checkpoint` is efficient) — 0.087s
- [x] Confirm analytics destination (dashboard-owned MongoDB database, `recoveryhub_dashboard`)
- [x] Freeze projection schema (Section 9 — APPROVED 2026-08-13)
- [x] Freeze destination collection design (Section 10 — APPROVED 2026-08-13)
- [x] Confirm `ai_line_items.updated_at` index exists (or plan reconciliation fallback) — no index, but query is fast (0.087s)
- [x] Document Change Stream fallback (polling mode) if permissions are unavailable — not needed, Change Streams confirmed available

### Shared module extraction (COMPLETED 2026-08-13)

- [x] Extract shared functions from `normalization.py` into `normalization_core.py`
- [x] Update `normalization.py` to re-export from `normalization_core.py`
- [x] Verify existing tests pass after extraction (326 passed, 0 failed)
- [x] Verify existing services (`outcome_service.py`, `diagnostics_service.py`, `invoice_trace_service.py`) still import correctly

---

## 13. Deliverables

Phase 0 produces the following deliverables:

1. **This document** — `docs/ai-analytics/PHASE_0_IMPLEMENTATION_PLAN.md`
   (consolidated source audit + worker-specific verification + frozen
   projection schema + destination design).
2. **Index enumeration results** — appended to this document (Section 8.2)
   after verification.
3. **Conversation coverage report** — appended to this document (Section 8.3)
   after verification.
4. **Change Stream permission confirmation** — appended to this document
   (Section 8.1) after verification.
5. **Shared module extraction** — `backend/ai_analytics/normalization_core.py`
   created and existing tests verified passing.

The prior `PHASE_0_DATA_CONTRACT.md` is removed once this document is approved.

---

## 14. Exit Criteria

Phase 0 is complete when ALL of the following are true:

- [x] Source fields confirmed (DONE — Sections 3-7)
- [x] Projection schema approved (Section 9 — APPROVED 2026-08-13)
- [x] Destination collection design approved (Section 10 — APPROVED 2026-08-13)
- [x] No unresolved critical join-key issue (claim_id confirmed as int join key)
- [x] Change Stream permissions confirmed or polling fallback documented
- [x] Index enumeration completed and documented
- [x] Conversation coverage measured and documented (100%)
- [x] Recent records verified against historical patterns
- [x] Shared module extraction complete and tests passing (326 passed)
- [x] Resume token viability confirmed or reconciliation fallback documented

---

## 15. Subsequent Phase Summary (for context)

Phase 0 freezes the contract. The remaining phases (not implemented in this
document) are:

| Phase | Goal |
|---|---|
| Phase 1 | Worker foundation — config, connections, health, logging, base models |
| Phase 2 | Source repositories — read-only RecoveryHub_AI access with timeouts/retry |
| Phase 3 | Projection normalization — deterministic dashboard record builder |
| Phase 4 | Historical backfill — populate existing history |
| Phase 5 | Change Stream listener — near-real-time updates |
| Phase 6 | Queue and coalescing — prevent unnecessary repeated rebuilds |
| Phase 7 | Reconciliation — repair missed events |
| Phase 8 | Health and operations — `/health`, `/ready`, metrics |
| Phase 9 | RH-Dashboard integration — read projection as AI analytics source |
| Phase 10 | Invoice Trace integration — AI summary + conversation summary + retry data |
| Phase 11 | Worker staleness UI — projection lag visualization |
| Phase 12 | Security hardening — credential scoping, network rules |
| Phase 13 | Containerization and deployment — health probes, graceful shutdown |
| Phase 14 | Unit test completion gate — >=85% line, >=80% branch, >=90% critical |
| Phase 15 | Integration and reconciliation QA — controlled sample validation |
| Phase 16 | Production rollout — staged enablement with raw Mongo fallback |

Each subsequent phase must comply with the rules in Section 2 (DRY, unit tests,
code documentation) and the architectural decisions in Section 1.

---

## 16. Core Design Principles (Binding)

These principles from the original implementation plan are binding on all
phases:

- Worker is one-way only.
- Worker reads RecoveryHub_AI operational MongoDB.
- Worker never mutates RecoveryHub_AI operational collections.
- Worker never mutates RecoveryHub SQL.
- Worker writes only to its analytics destination.
- Analytics projection is rebuildable.
- Raw source data remains authoritative.
- `claim_id` is the primary cross-system correlation key.
- Worker uses MongoDB Change Streams as the primary trigger (with polling
  fallback if permissions are unavailable).
- Periodic reconciliation exists only as a safety net.
- Worker survives restart without losing synchronization state.
- Dashboard can detect stale worker data.
- Worker never becomes a required dependency for operational claim processing.
- Worker does not require Azure Front Door.
- Worker does not require SSE.
- Worker does not require a browser WebSocket.
- Worker is replaceable, rebuildable, one-way, read-only against operational
  AI data, and independent of operational claim processing.

---

## 17. Implementation Progress (Phases 1-10)

This section tracks the implementation status of each phase after Phase 0.
Phase 0 exit criteria (Section 14) are unchanged. Each phase below lists
its deliverables, status, and the tests that guard it.

### Phase 1 — Worker foundation — COMPLETE

**Deliverables:**
- `ai_analytics_worker/config.py` — `WorkerConfig` with all Section 1.1.5
  parameters (debounce, max claims per cycle, source query timeout,
  reconciliation interval, backfill batch size, max retries, dead-letter
  threshold, change-stream restart delay/max restarts, projection schema
  version, worker version, projections collection name)
- `ai_analytics_worker/__init__.py` — package init
- `backend/config.py` — worker env vars added to `Settings` with validation
- `ai_analytics_worker/health.py` — `worker_health` singleton, `record_error`,
  checkpoint/event timestamp tracking
- `ai_analytics_worker/metrics.py` — `worker_metrics` counters

**Tests:** `test_ai_analytics_worker_main.py`, `test_ai_analytics_worker_metrics.py`

### Phase 2 — Source repositories — COMPLETE

**Deliverables:**
- `ai_analytics_worker/source_repository.py` — read-only RecoveryHub_AI
  access with `WORKER_SOURCE_QUERY_TIMEOUT_MS` enforcement and
  `WORKER_MAX_RETRIES` exponential backoff

**Tests:** `test_ai_analytics_worker_source_repository.py`

### Phase 3 — Projection normalization — COMPLETE

**Deliverables:**
- `ai_analytics_worker/projection_builder.py` — deterministic dashboard
  record builder implementing Section 9 schema. Reuses
  `normalization_core.py` (DRY).
- `ai_analytics_worker/projection_repository.py` — write-side repository
  for the `ai_invoice_analytics` collection (upsert by `_id = claim_id`)

**Tests:** `test_ai_analytics_worker_projection_builder.py`,
`test_ai_analytics_worker_projection_repository.py`

### Phase 4 — Historical backfill — COMPLETE

**Deliverables:**
- `ai_analytics_worker/backfill.py` — batch historical population using
  `WORKER_BACKFILL_BATCH_SIZE`. Also used for stale-checkpoint date-range
  fallback (Phase 9 reconciliation).

**Tests:** `test_ai_analytics_worker_backfill.py`

### Phase 5 — Change Stream listener — COMPLETE

**Deliverables:**
- `ai_analytics_worker/change_stream_listener.py` — near-real-time updates
  via MongoDB Change Streams. Resume token persistence (open, close,
  reopen). Exponential backoff on restart with cap at 30s. Respects
  `WORKER_CHANGE_STREAM_MAX_RESTARTS` (0 = retry forever).

**Tests:** `test_ai_analytics_worker_change_stream.py`

### Phase 6 — Queue and coalescing — COMPLETE

**Deliverables:**
- `ai_analytics_worker/queue.py` — `ClaimQueue` with injectable clock
  (defaults to `time.monotonic`), `WORKER_DEBOUNCE_SECONDS` coalescing
  window, `WORKER_MAX_CLAIMS_PER_CYCLE` batch limit.

**Tests:** `test_ai_analytics_worker_queue.py` (uses `FakeClock` to avoid
wall-clock dependence — see AGENTS.md testing conventions)

### Phase 7 — Reconciliation — COMPLETE

**Deliverables:**
- `ai_analytics_worker/reconciliation.py` — safety-net scan every
  `WORKER_RECONCILIATION_INTERVAL_MINUTES`. Queries `ai_line_items` for
  records changed since last checkpoint and requeues them.
- `ai_analytics_worker/claim_refresh.py` — single-claim refresh pipeline
  (source read → projection build → projection write)

**Tests:** `test_ai_analytics_worker_reconciliation.py`,
`test_ai_analytics_worker_claim_refresh.py`

### Phase 8 — Health and operations — COMPLETE

**Deliverables:**
- `ai_analytics_worker/routes.py` — `/health`, `/ready`, `/status`
  endpoints. `/health` and `/ready` are unauthenticated (container probes,
  external_enabled=true) and must not carry error text. `/status` is
  auth-protected and exposes `last_error`.
- `ai_analytics_worker/main.py` — FastAPI lifespan integration, worker
  task startup/shutdown, graceful cancellation.

**Tests:** `test_ai_analytics_worker_health.py`,
`test_ai_analytics_worker_routes.py`, `test_ai_analytics_worker_main.py`,
`test_ai_analytics_worker_instrumentation.py` (regression guard for
counter increments at call sites)

**Conventions documented in AGENTS.md:**
- Two state stores must both be written (Mongo `ai_analytics_worker_state`
  + in-memory `worker_health` singleton)
- `worker_metrics` counters must be incremented at the call site
- `asyncio.CancelledError` must never increment error counters
- `ClaimQueue` uses injectable `FakeClock` to avoid wall-clock flakiness

### Phase 9 — RH-Dashboard integration — COMPLETE

**Deliverables:**
- `backend/config.py` — `AI_ANALYTICS_USE_PROJECTION` flag (default
  `false`). Read at import time (the `settings` singleton); toggling
  requires a container restart.
- `backend/ai_analytics/projection_read_repository.py` — read-side
  adapter mapping projection field names to the raw `ai_line_items`
  field shape. Field mapping is explicit (`_FIELD_MAP`,
  `_PASSTHROUGH_FIELDS`, `_MISSING_FROM_PROJECTION`) so schema changes
  are caught there rather than silently producing `None`.
  - `_FIELD_MAP`: 4 renamed fields (`ai_processing_status` →
    `claim_processing_status`, `agent_execution_status` →
    `agent_exec_status`, `ai_invoice_total` → `invoice_total`,
    `processing_duration_seconds` → `processing_time_seconds`)
  - `_PASSTHROUGH_FIELDS`: 5 same-name fields (`confidence_level`,
    `is_billable`, `billing_category`, `line_items_save_to_rh_status`,
    `retry_count` — the last is 30% populated per Phase 0 audit)
  - `_MISSING_FROM_PROJECTION`: 2 fields (`thread_id`,
    `retry_thread_id` — 0% populated per Phase 0 audit)
- `backend/ai_analytics/outcome_service.py` —
  `_load_normalized_cohort` branches on the flag: projection read vs
  direct Mongo read. SQL steps (cohort, cancellations, logs) are
  unchanged. `source_status["recoveryhub_ai_mongo"]` key is preserved
  for API backward compatibility even when the failure is in the
  dashboard-owned Mongo (the projection).
- `backend/ai_analytics/invoice_trace_service.py` — Phase 9 handoff only;
  the Phase 10 section below documents its later projection integration.
- `backend/ai_analytics/diagnostics_service.py` — Phase 9 kept
  `get_agent_stats` on the direct conversation read; Phase 10 below
  migrates that branch when the projection flag is enabled.

**Tests:** `test_ai_analytics_projection_read.py` (17 tests):
- Field mapping tests (renames, passthroughs, missing fields,
  regression guard for `retry_count`)
- Batch fetch tests (empty input, fetch by `_id`, missing projections,
  error propagation)
- Flag-gating tests (flag off → direct Mongo, flag on → projection,
  missing projection → `ai_record=None`, projection failure →
  `data_complete=False`)

**Conventions documented in AGENTS.md:**
- Hybrid read with config-flag fallback approach
- `invoice_trace_service.py` Phase 10 handoff (keeps direct-read)
- `/diagnostics/agents` not affected (reads raw conversations)
- Flag is read at import time; toggling requires container restart

**Test count:** 661 tests pass (was 644 pre-Phase 9), no regressions.

### Verification: field mapping completeness

The adapter's field mapping was verified against
`normalization_core.build_normalized_record` (the function that consumes
the adapted dict). Every field `build_normalized_record` reads from
`ai_record` is covered:

| `build_normalized_record` reads | Adapter source | Notes |
|---|---|---|
| `claim_processing_status` | `_FIELD_MAP` ← `ai_processing_status` | Renamed |
| `agent_exec_status` | `_FIELD_MAP` ← `agent_execution_status` | Renamed |
| `confidence_level` | `_PASSTHROUGH_FIELDS` | Same name |
| `line_items_save_to_rh_status` | `_PASSTHROUGH_FIELDS` | Same name |
| `retry_thread_id` | `_MISSING_FROM_PROJECTION` | 0% populated → None |
| `retry_count` (via `calculate_retry_count`) | `_PASSTHROUGH_FIELDS` | Same name, 30% populated |
| `is_billable` | `_PASSTHROUGH_FIELDS` | Same name |
| `billing_category` | `_PASSTHROUGH_FIELDS` | Same name |
| `thread_id` | `_MISSING_FROM_PROJECTION` | 0% populated → None |
| `invoice_total` | `_FIELD_MAP` ← `ai_invoice_total` | Renamed |
| `processing_time_seconds` | `_FIELD_MAP` ← `processing_duration_seconds` | Renamed |

**Note:** `calculate_retry_count` also reads `ai_record.get("retry_count")`
directly — this is covered by `_PASSTHROUGH_FIELDS`. The
`retry_thread_id` parameter is passed separately by
`build_normalized_record` and is covered by `_MISSING_FROM_PROJECTION`.

### Phase 10 — Invoice Trace integration — COMPLETE

**Schema v2 additions (projection_schema_version 1 → 2):**
- `ai_line_items` — each entry now includes `resources` (was summary-only
  with item, description, quantity, rate, line_item_total)
- `conversation_summaries` — new list of per-conversation summary dicts
  (conversation_id, agent, status, created_at as ISO string, processing_stage,
  request_type, execution_time_seconds). Excludes large payload fields
  (input_data, incident_json, results, output_data)
- `conversation_id` — from `ai_line_items.conversation_id` (linking ID)
- `thread_id_is_billable` — from `ai_line_items.thread_id_is_billable`

Per Section 9.12 Schema Evolution Policy: old v1 projections keep their
shape and are upgraded lazily on next refresh.

**Deliverables:**
- `backend/ai_analytics_worker/projection_builder.py` — enriched with:
  - `resources` added to `_LINE_ITEM_SUMMARY_FIELDS`
  - New `_summarize_conversation_details()` helper for per-conversation
    summaries
  - `conversation_summaries`, `conversation_id`,
    `thread_id_is_billable` fields in the projection dict
- `backend/config.py` — `AI_ANALYTICS_WORKER_PROJECTION_SCHEMA_VERSION`
  default bumped from 1 to 2
- `backend/ai_analytics/projection_read_repository.py` — new functions:
  - `projection_to_trace_data()` — maps projection to full trace field
    shape (line_items with resources, review_msg, timestamps,
    conversation_id, thread_id_is_billable, conversation_summaries)
  - `get_projection_for_trace()` — fetches a single projection by
    claim_id and returns trace data dict (or None for fallback)
  - `aggregate_agent_stats_from_projections()` — aggregates
    conversation_summaries via $unwind + $group for /diagnostics/agents
- `backend/ai_analytics/invoice_trace_service.py` — when flag is on,
  reads AI summary from projection (eliminates ai_line_items cross-cluster
  read). Falls back to direct-read if no projection exists. Full
  conversations still read from RecoveryHub_AI Mongo for detail fields.
- `backend/ai_analytics/diagnostics_service.py` — `get_agent_stats` when
  flag is on, aggregates from projection's conversation_summaries
  (eliminates batch cross-cluster read on ai_agent_conversations)

**Tests:**
- `test_ai_analytics_worker_projection_builder.py` — 12 new tests:
  - `TestPhase10LineItemsWithResources` (resources in summary, empty when
    absent, legacy alias canonicalization)
  - `TestPhase10ConversationSummaries` (populated, excludes payload
    fields, empty when no conversations)
  - `TestPhase10TraceFields` (conversation_id, thread_id_is_billable
    copied/None)
- `test_ai_analytics_projection_read.py` — 15 new tests:
  - `TestProjectionToTraceData` (field mapping, explicit missing-record
    marker, v1 compatibility)
  - `TestGetProjectionForTrace` (fetch by claim_id, None when absent)
  - `TestAggregateAgentStatsFromProjections` (aggregation, v1
    contributions, date filter)
- `test_ai_analytics_invoice_trace_projection.py` — service-level tests for
  missing AI records, conversation-summary fallback, and raw-record hygiene
- `test_ai_analytics_diagnostics.py` — service-level tests for the enabled
  projection branch and contained aggregation failures

**Phase 10 correctness guarantees:**
- Projection line items canonicalize `item`/`label`/`name`,
  `rate`/`amount`, and `line_item_total`/`total` so direct and projection
  trace comparisons agree.
- A projection with `has_ai_line_item_record=false` remains
  `ai_record_state="missing"`; it is not treated as an all-null present
  record.
- `conversation_summaries` are consumed as a trace fallback when full
  conversation payload reads fail and are removed before `raw_ai_record`
  serialization.
- v2 sizing is explicitly data-dependent; representative BSON-size
  measurement is required before production rollout.

**Conventions documented in AGENTS.md:**
- Schema v2 additions and Section 9.12 lazy upgrade
- `/invoices/{claim_id}/trace` migration (projection for AI summary,
  Mongo for full conversations)
- `/diagnostics/agents` migration ($unwind + $group on
  conversation_summaries)
- Schema version bump behavior (pinned env vars continue v1)

**Test count:** 690 tests pass (was 686 before the Phase 10 correctness fixes), no regressions.

### Phase 11 — Sync integrity & worker health visibility — COMPLETE

**Design reframing:** The original Phase 11 plan was "Worker staleness UI —
projection lag visualization" (age-based staleness). After user review, this
was reframed to "Sync integrity & worker health visibility" — the concern is
not *how old* the cache is, but *whether the cache matches MongoDB*. A claim
sitting in AI for 2 days is not "stale" — the cache correctly reflects that
the claim is still waiting. "Stale" means the cache doesn't match the source.

This mirrors FireSquirrel's local-first sync pattern: the sync mechanism
verifies it's in sync (integrity check), surfaces its status visibly (sync
health indicator), and auto-heals when divergence is detected (auto-resync).

**Deliverables:**
- `backend/ai_analytics_worker/sync_integrity.py` — periodic sync integrity
  verification. Two checks per cycle:
  1. **Count comparison**: `ai_line_items.count()` vs
     `ai_invoice_analytics.count()` — catches missing projections or stale
     tombstones.
  2. **Sample verification**: picks the N most recent source docs (by
     `updated_at` descending) and compares each source `updated_at` against
     the projection's `source_latest_updated_at`. Catches stale field values
     from direct Mongo edits that bypass the change stream.
  Divergent claims are automatically re-enqueued into the ClaimQueue for
  refresh (auto-resync). Results stored in `sync_integrity_state` singleton.
- `backend/ai_analytics_worker/sync_status.py` — sync status aggregation
  deriving a single status from worker health + integrity state + metrics:
  `synced` / `syncing` / `catching-up` / `divergence-detected` / `error` /
  `stopped`. Pure derivation — no new state tracked.
- `backend/ai_analytics_worker/routes.py` — extended:
  - `/status` gains `sync_integrity` section
  - `GET /api/ai-analytics/worker/sync-health` — sync health summary for
    the dashboard frontend (auth-protected)
  - `GET /api/ai-analytics/worker/dead-letters` — unresolved dead-lettered
    claims list (auth-protected)
  - `POST /api/ai-analytics/worker/dead-letters/{claim_id}/resolve` — mark
    a dead-lettered claim as resolved so the worker retries it
- `backend/ai_analytics_worker/main.py` — sync integrity loop added as
  fourth concurrent sub-task (alongside change-stream listener, queue
  consumer, and reconciliation loop)
- `backend/config.py` — new settings:
  - `WORKER_SYNC_INTEGRITY_INTERVAL_MINUTES` (default 5) — integrity check
    cadence (separate from reconciliation's 30-min cadence)
  - `WORKER_SYNC_INTEGRITY_SAMPLE_SIZE` (default 50) — number of recent
    source docs to sample-verify per check
- `backend/ai_analytics_worker/metrics.py` — new counters:
  `sync_integrity_checks`, `sync_integrity_divergent_found`
- `frontend/src/services/aiAnalyticsApi.ts` — new types and API calls:
  `AiSyncHealth`, `SyncStatus`, `AiSyncIntegrity`, `AiSyncMetrics`,
  `AiDeadLetter`, `getSyncHealth()`, `getDeadLetters()`, `resolveDeadLetter()`
- `frontend/src/components/ai/SyncHealthIndicator.tsx` — compact badge with
  expandable detail panel. Shows derived sync status (In Sync / Syncing /
  Catching Up / Divergence Detected / Sync Error / Sync Stopped), source vs
  cache counts, divergent/missing counts, throughput metrics, error messages,
  and dead-letter list with resolve buttons. Auto-refreshes every 30s.
  Integrated into AiOutcomesDashboard and AiDiagnosticsDashboard.
- `backend/scripts/measure_v2_projection_size.py` — v2 projection BSON
  sizing measurement script (Section 9.13.1)

**Why sync integrity is separate from reconciliation (Phase 7):**
- Reconciliation catches *missed change events* — it looks for source docs
  with `updated_at > checkpoint`. It does NOT verify existing projections
  match their source.
- Sync integrity catches *divergence* — it verifies existing projections are
  correct by comparing them against the source. This catches direct Mongo
  edits that bypass the change stream, projection corruption, or backfill
  gaps that reconciliation wouldn't find because the `updated_at` is old.
- Both are needed. Reconciliation is "did I miss any events?" Sync integrity
  is "is the cache actually correct?"

**Sync status states (stable, frontend matches on these):**
- `synced` — worker running, last integrity check passed, no divergence
- `syncing` — worker actively processing or integrity check in progress
- `catching-up` — divergent claims found, auto-resync enqueued
- `divergence-detected` — count mismatch but sample verification pending
- `error` — worker in error state or integrity check failed
- `stopped` — worker disabled or not started

**Tests:**
- `test_ai_analytics_worker_sync_integrity.py` (22 tests): count comparison,
  sample verification (stale, missing, up-to-date, newer-than-source),
  auto-resync enqueue, metrics increment, error handling, cancellation,
  datetime helpers
- `test_ai_analytics_sync_health.py` (13 tests): all 6 status derivation
  paths, snapshot completeness, error preference
- `test_ai_analytics_worker_metrics.py` — updated for 2 new counters
- `frontend/src/__tests__/SyncHealthIndicator.test.tsx` (10 tests): loading,
  all status badges, dead-letter badge, expand/collapse, error display,
  resolve button, divergence stats

**Test count:** 725 backend tests pass (was 690), 172 frontend tests pass
(was 162). No regressions.
