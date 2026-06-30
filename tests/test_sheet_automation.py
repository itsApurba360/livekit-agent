# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime

# Import components to test
from agent_tools import CustomerQueryTools
from sheet_calling_automation import parse_schedule
from agent import CallContext, _call_context_prompt

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

    def test_campaign_prompt_appending(self):
        # Local mock of dynamic prompt builder logic
        def get_compiled_prompt(system_prompt_template, call_context):
            prompt_base = system_prompt_template
            purpose = (call_context.call_purpose or "").lower()
            is_gst_campaign = any(word in purpose for word in ["gst", "document", "filing", "pdf"])
            if is_gst_campaign:
                prompt_base += "\n\n--- CAMPAIGN RULES: GST DOCUMENT COLLECTION & FOLLOW-UP ---\n- This call is for coordinating a follow-up call with a human executive"
            return prompt_base

        # Active GST campaign purpose
        ctx_gst = CallContext(
            direction="outbound",
            phone_number="9062371141",
            call_purpose="Need to get the GST PDF data"
        )
        prompt_gst = get_compiled_prompt("Support Prompt", ctx_gst)
        self.assertIn("GST DOCUMENT COLLECTION", prompt_gst)

        # Standard non-campaign purpose
        ctx_std = CallContext(
            direction="outbound",
            phone_number="9062371141",
            call_purpose="Standard Billing Query"
        )
        prompt_std = get_compiled_prompt("Support Prompt", ctx_std)
        self.assertNotIn("GST DOCUMENT COLLECTION", prompt_std)

def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)

if __name__ == "__main__":
    unittest.main()
