import asyncio

import agent_tools
from sheet_calling_automation import SHEET2_EXTRA_HEADERS, _normalize_campaign_type


def _record_outcome(monkeypatch, **kwargs):
    saved = []
    monkeypatch.setattr(agent_tools, "get_call_record", lambda _call_id: {"metadata": {}})
    monkeypatch.setattr(
        agent_tools,
        "update_call_record",
        lambda call_id, **values: saved.append((call_id, values)),
    )
    monkeypatch.setattr(agent_tools, "_now_ist", lambda: agent_tools.datetime(2026, 7, 16, 12, 0))
    tools = agent_tools.CustomerQueryTools(call_id="call_test")
    result = asyncio.run(tools.record_itr_collection_outcome(**kwargs))
    return result, saved[0][1]["metadata"]


def test_itr_promise_schedules_next_working_day(monkeypatch):
    result, metadata = _record_outcome(
        monkeypatch,
        receipt_status="Received",
        promised_date="18/07/2026",
        delivery_mode="WhatsApp",
    )

    assert "20/07/2026 at 11:00" in result
    assert metadata["next_action"] == "AI Call"
    assert metadata["next_action_date"] == "20/07/2026"
    assert metadata["next_action_time"] == "11:00"
    assert metadata["whatsapp_receipt_status"] == "Received"
    assert metadata["delivery_mode"] == "WhatsApp"


def test_missing_whatsapp_message_routes_to_human(monkeypatch):
    _, metadata = _record_outcome(monkeypatch, receipt_status="Not Received")

    assert metadata["next_action"] == "Human"
    assert "resend" in metadata["issue_help_required"]


def test_campaign_types_and_sheet_outcome_columns():
    assert _normalize_campaign_type("GST") == "gst"
    assert _normalize_campaign_type("Income Tax Return") == "itr"
    assert _normalize_campaign_type("payroll") is None
    assert SHEET2_EXTRA_HEADERS[-5:] == [
        "WhatsApp Received",
        "Promised Date",
        "Delivery Mode",
        "Help/Issue",
        "Callback Time",
    ]
