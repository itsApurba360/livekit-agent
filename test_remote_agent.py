# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock, patch
import asyncio
import urllib.parse
from frappe_client import FrappeRestClient
from agent_tools import CustomerQueryTools

class TestFrappeRestClient(unittest.TestCase):
    def setUp(self):
        self.client = FrappeRestClient(
            base_url="http://mock-frappe-server.local",
            api_key="mock_key",
            api_secret="mock_secret"
        )

    def test_auth_headers(self):
        headers = self.client.session.headers
        self.assertEqual(headers.get("Authorization"), "token mock_key:mock_secret")
        self.assertEqual(headers.get("Content-Type"), "application/json")

    @patch("requests.Session.get")
    def test_lookup_caller_customer_match(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": [{"name": "Cust-001", "customer_name": "Lokesh Associates"}]
        }
        mock_get.return_value = mock_response

        res = self.client.lookup_caller("9062371141")
        self.assertEqual(res.get("status"), "Customer")
        self.assertEqual(res.get("customer_id"), "Cust-001")
        self.assertEqual(res.get("name"), "Lokesh Associates")

    @patch("requests.Session.get")
    def test_lookup_caller_lead_match(self, mock_get):
        # Setup mock returns:
        # First 3 customer field lookups fail/return empty list
        # Contact Phone returns empty list
        # Then Lead lookup returns matched lead list
        mock_resp_empty = MagicMock()
        mock_resp_empty.json.return_value = {"data": []}
        
        mock_resp_lead = MagicMock()
        mock_resp_lead.json.return_value = {
            "data": [{"name": "Lead-001", "lead_name": "John Doe", "company_name": "Doe Corp"}]
        }
        
        mock_get.side_effect = [
            mock_resp_empty, # customer mobile_no
            mock_resp_empty, # customer custom_primary_mobile_no
            mock_resp_empty, # customer custom_alt_mobile_no
            mock_resp_empty, # contact phone
            mock_resp_lead,  # lead mobile_no
        ]

        res = self.client.lookup_caller("9876543210")
        self.assertEqual(res.get("status"), "Lead")
        self.assertEqual(res.get("lead_id"), "Lead-001")
        self.assertEqual(res.get("name"), "John Doe")

class TestCustomerQueryTools(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock(spec=FrappeRestClient)
        self.mock_client.base_url = "http://mock-frappe-server.local"
        self.on_verify_success_called = False
        
        def callback():
            self.on_verify_success_called = True
            
        self.tools = CustomerQueryTools(
            client=self.mock_client,
            customer_id="Cust-001",
            phone_number="9062371141",
            on_verify_success=callback
        )

    def test_initial_state(self):
        self.assertFalse(self.tools.is_verified)
        self.assertIsNone(self.tools.generated_otp)
        self.assertEqual(self.tools.dtmf_buffer, "")

    def test_verification_enforcement(self):
        loop = asyncio.get_event_loop()
        res = loop.run_until_complete(self.tools.get_customer_details())
        self.assertIn("Verification required", res)

        res = loop.run_until_complete(self.tools.get_customer_sales_orders())
        self.assertIn("Verification required", res)

        res = loop.run_until_complete(self.tools.get_customer_pending_amount())
        self.assertIn("Verification required", res)

    def test_send_verification_otp(self):
        self.mock_client.send_whatsapp_message.return_value = {"status": True, "msg": "Sent"}
        loop = asyncio.get_event_loop()
        res = loop.run_until_complete(self.tools.send_verification_otp())
        
        self.assertIn("OTP has been sent", res)
        self.assertIsNotNone(self.tools.generated_otp)
        self.assertEqual(len(self.tools.generated_otp), 4)
        
        # Verify mock client call
        self.mock_client.send_whatsapp_message.assert_called_once()

    def test_verify_otp_flow(self):
        self.mock_client.send_whatsapp_message.return_value = {"status": True, "msg": "Sent"}
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.tools.send_verification_otp())
        
        otp = self.tools.generated_otp
        
        # Incorrect OTP
        res = loop.run_until_complete(self.tools.verify_otp("0000"))
        self.assertIn("Verification failed", res)
        self.assertFalse(self.tools.is_verified)
        self.assertFalse(self.on_verify_success_called)

        # Correct OTP
        res = loop.run_until_complete(self.tools.verify_otp(otp))
        self.assertIn("Verification successful", res)
        self.assertTrue(self.tools.is_verified)
        self.assertTrue(self.on_verify_success_called)

    def test_send_pdf_whatsapp_verified(self):
        self.tools.is_verified = True
        self.mock_client.get_resource.return_value = {"customer": "Cust-001"}
        self.mock_client.get_resource_list.return_value = []
        self.mock_client.send_whatsapp_message_with_file.return_value = {"status": True, "msg": "Sent"}

        loop = asyncio.get_event_loop()
        res = loop.run_until_complete(self.tools.send_pdf_whatsapp("Sales Invoice", "LSA/26-27/0008"))
        self.assertIn("successfully sent", res)
        
        # Verify URL construction matches base_url
        self.mock_client.send_whatsapp_message_with_file.assert_called_once()
        args, kwargs = self.mock_client.send_whatsapp_message_with_file.call_args
        self.assertIn("http://mock-frappe-server.local", kwargs["file_link"])

if __name__ == "__main__":
    unittest.main()
