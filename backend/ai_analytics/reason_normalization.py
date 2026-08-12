"""Maps exact RecoveryHub cancellation reasons to stable analytics categories.

The mapping is based on the Phase 0 data contract
(`docs/ai-analytics/PHASE_0_DATA_CONTRACT.md` Section 4) which enumerated all
17 production reason IDs and their usage counts.

Raw reason text and description are always preserved alongside the normalized
category — the normalization is additive, never destructive.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Reason ID → normalized category mapping
#
# Verified against production on 2026-08-12. See PHASE_0_DATA_CONTRACT.md.
# ---------------------------------------------------------------------------

REASON_ID_TO_CATEGORY: Dict[int, str] = {
    1: "line_item_accuracy",
    2: "fee_calculation",
    3: "fee_calculation",
    4: "line_item_accuracy",
    5: "level_classification",
    6: "line_item_accuracy",
    7: "fee_calculation",
    8: "line_item_accuracy",
    9: "fee_calculation",
    10: "test_removal",
    11: "time_on_scene",
    12: "time_on_scene",
    13: "department_data_issue",
    14: "nested_line_item_canceled",
    15: "nested_line_item_canceled",
    16: "command_error",
    17: "workflow_update",
}

# All valid normalized categories (used for validation and UI labels)
NORMALIZED_CATEGORIES = sorted(set(REASON_ID_TO_CATEGORY.values()))

# Human-readable labels for the UI
CATEGORY_LABELS: Dict[str, str] = {
    "fee_calculation": "Fee Calculation Error",
    "line_item_accuracy": "Line Item Accuracy",
    "level_classification": "Wrong Level Selected",
    "time_on_scene": "Additional Time on Scene",
    "department_data_issue": "Department Data Entry Issue",
    "workflow_update": "Workflow Update Required",
    "test_removal": "Test Removal",
    "nested_line_item_canceled": "Nested Line Item Cited When Canceled",
    "command_error": "Command Cited Incorrectly",
    "other": "Other",
    "unknown": "Unknown",
}


def normalize_reason(
    reason_id: Optional[int],
    raw_reason: Optional[str] = None,
    raw_description: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a normalized reason object preserving all raw values.

    Output shape::

        {
            "reason_id": 7,
            "raw_reason": "Miscalculated Nested Line Items",
            "raw_description": "...",
            "normalized_category": "fee_calculation"
        }
    """
    category: Optional[str] = None
    if reason_id is not None:
        category = REASON_ID_TO_CATEGORY.get(reason_id)

    if category is None:
        # Fallback: try fuzzy text matching on the raw reason
        category = _fuzzy_match(raw_reason)

    return {
        "reason_id": reason_id,
        "raw_reason": raw_reason,
        "raw_description": raw_description,
        "normalized_category": category or "unknown",
    }


def _fuzzy_match(raw_reason: Optional[str]) -> Optional[str]:
    """Fallback text-based matching when reason_id is missing.

    Uses keyword matching against known reason text patterns. This is a
    secondary mechanism — the primary mapping is by reason_id.
    """
    if not raw_reason:
        return None

    text = raw_reason.lower()

    if "miscalculated" in text or "consumable" in text or "fee wrong" in text:
        return "fee_calculation"
    if "incorrect" in text and "description" in text:
        return "line_item_accuracy"
    if "lacking nested" in text or "incorrect resource" in text:
        return "line_item_accuracy"
    if "wrong level" in text:
        return "level_classification"
    if "additional time" in text or "miscite" in text:
        return "time_on_scene"
    if "department data entry" in text:
        return "department_data_issue"
    if "update required" in text or "work item" in text:
        return "workflow_update"
    if "zco test" in text or "test removal" in text:
        return "test_removal"
    if "canceled on scene" in text:
        return "nested_line_item_canceled"
    if "command" in text:
        return "command_error"

    return None
