# -*- coding: utf-8 -*-
"""Shared outbound SIP call outcome mapping."""

from typing import Any


def sip_failure_reason(
    sip_status_code: Any = None,
    sip_status: Any = None,
    message: Any = None,
) -> str:
    """Map carrier/SIP failure details to stable API reasons."""
    try:
        code = int(str(sip_status_code)) if sip_status_code is not None else None
    except (TypeError, ValueError):
        code = None

    text = " ".join(str(item or "") for item in (sip_status, message)).lower()
    if code == 486 or "busy" in text:
        return "busy"
    if code == 408 or "timeout" in text or "timed out" in text:
        return "no_answer"
    if code == 603 or "decline" in text or "rejected" in text:
        return "rejected"
    if code in {401, 403, 500, 502, 503, 504} or "trunk" in text:
        return "trunk"
    if code in {480, 404, 410, 484, 604} or "unavailable" in text or "not found" in text:
        return "unreachable"
    return "sip_error"


def failure_status_for_reason(reason: str) -> str:
    """Convert stable failure reason to public call status."""
    return {
        "busy": "failed_busy",
        "no_answer": "failed_no_answer",
        "rejected": "failed_rejected",
        "unreachable": "failed_unreachable",
        "trunk": "failed_trunk",
    }.get(reason, "failed")
