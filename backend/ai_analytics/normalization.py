"""Re-export shim for backward compatibility.

All normalization logic has been extracted to ``normalization_core.py`` as
the single source of truth (DRY) shared between the direct-read analytics
services and the AI Analytics Worker.

This shim preserves existing imports (``from .normalization import ...``) so
that ``outcome_service.py``, ``diagnostics_service.py``,
``invoice_trace_service.py``, and the test suite continue to work without
modification. Once all consumers are updated to import from
``normalization_core.py`` directly, this shim can be removed.
"""

from .normalization_core import *  # noqa: F401,F403
from .normalization_core import (  # noqa: F401  (explicit re-export for clarity)
    # Constants
    RELEASED_LOG_TEXT,
    CANCELLED_LOG_TEXT,
    STATUS_INITIAL,
    STATUS_PROCESSING,
    STATUS_TERMINAL,
    STATUS_POST_RELEASE,
    STATUS_ACTIVE_REVIEW,
    TERMINAL_STATUSES,
    PENDING_STATUSES,
    AI_COMPLETED_STATUSES,
    AI_NOT_ENABLED_STATUSES,
    AGENT_SUCCESS_STATUSES,
    AGENT_ERROR_STATUSES,
    AGENT_IN_PROGRESS_STATUSES,
    CONFIDENCE_BUCKETS,
    AI_SEND_OPTIONS,
    ELIGIBILITY_ELIGIBLE,
    ELIGIBILITY_NOT_CONFIGURED,
    ELIGIBILITY_UNKNOWN,
    AI_RESULT_SAVED,
    AI_RESULT_OUTPUT_REJECTED,
    AI_RESULT_EXECUTION_FAILED,
    AI_RESULT_STUCK,
    AI_RESULT_IN_PROGRESS,
    AI_RESULT_NOT_REQUIRED,
    AI_RESULT_UNKNOWN,
    TERMINAL_AI_RESULTS,
    # Functions
    classify_business_outcome,
    is_terminal_outcome,
    calculate_release_rate,
    calculate_rejection_rate,
    classify_ai_execution_outcome,
    classify_ai_result,
    classify_writeback_status,
    calculate_retry_count,
    has_retry,
    calculate_processing_duration,
    calculate_duration_percentiles,
    confidence_bucket,
    detect_human_intervention,
    _line_items_differ,
    _review_msg_indicates_correction,
    classify_billability,
    classify_fees,
    classify_ai_eligibility,
    build_normalized_record,
    index_ai_records_by_claim_id,
)
