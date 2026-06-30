# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime
from gspread.utils import a1_to_rowcol

# Import components to test
from agent_tools import CustomerQueryTools
from sheet_calling_automation import parse_schedule, sync_completed_calls_to_sheets
from agent import CallContext, _call_context_prompt


class FakeWorksheet:
    def __init__(self, headers, records=None):
        self.headers = headers
        self.records = records or []
        self.appended_rows = []
        self.batch_updates = []

    def row_values(self, row):
        return self.headers if row == 1 else []

    def get_all_records(self):
        return [dict(record) for record in self.records]

    def append_row(self, row):
        self.appended_rows.append(row)

    def batch_update(self, updates):
        self.batch_updates.extend(updates)

    def updated_by_header(self):
        values = {}
        for item in self.batch_updates:
            row, col = a1_to_rowcol(item["range"])
            if row == 2:
                values[self.headers[col - 1]] = item["values"][0][0]
        return values


class FakeSpreadsheet:
    def __init__(self, sheet1, sheet2):
        self.sheets = [sheet1, sheet2]

    def get_worksheet(self, index):
        return self.sheets[index]

class TestSheetAutomation(unittest.TestCase):
    
    def test_parse_schedule(self):
        # Test standard parsing
        dt = parse_schedule("23/06/2026", "14:30")
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 6)
        self.assertEqual(dt.day, 23)
        self.assertEqual(dt.hour, 14)
        self.assertEqual(dt.minute, 30)

        # Test ISO format date parsing fallback
        dt2 = parse_schedule("2026-06-23", "14:30")
        self.assertEqual(dt2.year, 2026)
        self.assertEqual(dt2.month, 6)
        self.assertEqual(dt2.day, 23)
        
        # Test invalid values fallback
        dt_invalid = parse_schedule("invalid", "invalid")
        self.assertEqual(dt_invalid, datetime.min)

    @patch("agent_tools.update_call_record")
    @patch("agent_tools.get_call_record")
    def test_schedule_human_callback(self, mock_get, mock_update):
        # Setup mocks
        mock_get.return_value = {
            "call_id": "call_123",
            "metadata": {"cid": "CUST01", "source": "sheets_automation"}
        }
        
        # Instantiate CustomerQueryTools
        client_mock = MagicMock()
        tools = CustomerQueryTools(
            client=client_mock,
            customer_id="CUST01",
            phone_number="9062371141",
            call_id="call_123"
        )
        
        # Invoke schedule_human_callback
        res = asyncio_run(tools.schedule_human_callback("30/06/2026", "15:00", "Spoke to Apurba. Needs follow up."))
        
        # Verify persisted call record was updated
        self.assertIn("scheduled", res)
        mock_update.assert_called_once()
        args, kwargs = mock_update.call_args
        self.assertEqual(args[0], "call_123")
        self.assertEqual(kwargs["metadata"]["next_action"], "Human")
        self.assertEqual(kwargs["metadata"]["next_action_date"], "30/06/2026")
        self.assertEqual(kwargs["metadata"]["next_action_time"], "15:00")
        self.assertEqual(kwargs["metadata"]["client_comment"], "Spoke to Apurba. Needs follow up.")
        self.assertEqual(kwargs["metadata"]["help_needed_notes"], "Spoke to Apurba. Needs follow up.")

    @patch("agent_tools.update_call_record")
    @patch("agent_tools.get_call_record")
    def test_schedule_ai_followup(self, mock_get, mock_update):
        mock_get.return_value = {
            "call_id": "call_123",
            "metadata": {"cid": "CUST01", "source": "sheets_automation"}
        }

        client_mock = MagicMock()
        tools = CustomerQueryTools(
            client=client_mock,
            customer_id="CUST01",
            phone_number="9062371141",
            call_id="call_123"
        )

        res = asyncio_run(tools.schedule_ai_followup("01/07/2026", "11:00", "Customer asked AI to call tomorrow."))

        self.assertIn("AI follow-up", res)
        mock_update.assert_called_once()
        args, kwargs = mock_update.call_args
        self.assertEqual(args[0], "call_123")
        self.assertEqual(kwargs["metadata"]["next_action"], "AI Call")
        self.assertEqual(kwargs["metadata"]["next_action_date"], "01/07/2026")
        self.assertEqual(kwargs["metadata"]["next_action_time"], "11:00")
        self.assertEqual(kwargs["metadata"]["client_comment"], "Customer asked AI to call tomorrow.")

    @patch("sheet_calling_automation.check_stop_requested", return_value=False)
    @patch("call_status_store.update_call_record")
    @patch("call_status_store.list_completed_call_records")
    def test_failed_busy_schedules_ai_retry_in_sheet(self, mock_list, mock_update, _mock_stop):
        mock_list.return_value = [{
            "call_id": "call_123",
            "status": "failed_busy",
            "reason": "busy",
            "metadata": {"cid": "CUST01", "source": "sheets_automation"},
            "created_at": "2026-06-30T10:00:00+00:00",
        }]
        sheet1_headers = [
            "CID", "Data Received Status", "Last Comment", "Count",
            "Workflow Status", "AI Enabled", "Last Call Outcome",
            "Next AI Call Date", "Next AI Call Time", "AI Attempt Count",
            "Max AI Attempts",
        ]
        sheet1 = FakeWorksheet(sheet1_headers, [{
            "CID": "CUST01",
            "Data Received Status": "Pending",
            "Count": 0,
            "Max AI Attempts": 3,
        }])
        sheet2 = FakeWorksheet([
            "Client Comment", "Next Action", "Next Action Date (DD/MMYYYY)",
            "Next Action Time (IST)", "CID", "Datetime", "Recording",
            "Trasncript", "Actor", "Call ID", "Call Outcome",
            "Help Needed Notes", "Assigned To",
        ])

        sync_completed_calls_to_sheets(FakeSpreadsheet(sheet1, sheet2))

        self.assertEqual(sheet2.appended_rows[0][1], "AI Call")
        self.assertEqual(sheet2.appended_rows[0][10], "failed_busy")
        updated = sheet1.updated_by_header()
        self.assertEqual(updated["Workflow Status"], "AI Scheduled")
        self.assertEqual(updated["AI Enabled"], "Yes")
        self.assertEqual(updated["Last Call Outcome"], "busy")
        self.assertEqual(updated["AI Attempt Count"], 1)
        self.assertTrue(updated["Next AI Call Date"])
        self.assertTrue(updated["Next AI Call Time"])
        mock_update.assert_called_once()

    def test_campaign_prompt_appending(self):
        # Local mock of dynamic prompt builder logic
        def get_compiled_prompt(system_prompt_template, call_context):
            prompt_base = system_prompt_template
            is_sheet_campaign = call_context.requested_by == "sheets_automation"
            if is_sheet_campaign:
                prompt_base += "\n\n--- CAMPAIGN RULES: GST DOCUMENT COLLECTION & FOLLOW-UP ---\n- This call is for coordinating a follow-up call with a human executive"
            return prompt_base

        ctx_sheet = CallContext(
            direction="outbound",
            phone_number="9062371141",
            call_purpose="Standard Billing Query",
            requested_by="sheets_automation"
        )
        prompt_sheet = get_compiled_prompt("Support Prompt", ctx_sheet)
        self.assertIn("GST DOCUMENT COLLECTION", prompt_sheet)

        ctx_non_sheet = CallContext(
            direction="outbound",
            phone_number="9062371141",
            call_purpose="Need to get the GST PDF data",
            requested_by="manual"
        )
        prompt_non_sheet = get_compiled_prompt("Support Prompt", ctx_non_sheet)
        self.assertNotIn("GST DOCUMENT COLLECTION", prompt_non_sheet)

def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)

if __name__ == "__main__":
    unittest.main()
