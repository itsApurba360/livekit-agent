# -*- coding: utf-8 -*-
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _install_livekit_stub():
    livekit_module = types.ModuleType("livekit")
    agents_module = types.ModuleType("livekit.agents")

    class ToolContext:
        def __init__(self, tools=None):
            self.tools = tools or []

    def function_tool(*decorator_args, **decorator_kwargs):
        def decorate(fn):
            fn.tool_description = decorator_kwargs.get("description")
            return fn

        if decorator_args and callable(decorator_args[0]) and len(decorator_args) == 1:
            return decorator_args[0]
        return decorate

    agents_module.llm = types.SimpleNamespace(
        ToolContext=ToolContext,
        function_tool=function_tool,
    )
    livekit_module.agents = agents_module

    sys.modules["livekit"] = livekit_module
    sys.modules["livekit.agents"] = agents_module


_stubbed_module_names = ["livekit", "livekit.agents", "frappe_client", "agent_tools"]
_previous_modules = {name: sys.modules.get(name) for name in _stubbed_module_names}

try:
    sys.modules.pop("agent_tools", None)
    _install_livekit_stub()

    frappe_client_module = types.ModuleType("frappe_client")

    class FrappeRestClient:
        pass

    frappe_client_module.FrappeRestClient = FrappeRestClient
    sys.modules["frappe_client"] = frappe_client_module

    from agent_tools import CustomerQueryTools  # noqa: E402
finally:
    for name, module in _previous_modules.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class FakeFrappeClient:
    def __init__(self):
        self.base_url = "https://frappe.example.test"
        self.resource_lists = {
            "Customer": [
                {
                    "name": "CUST-001",
                    "customer_name": "Acme Industries",
                    "mobile_no": "9876543210",
                }
            ],
            "Sales Order": [
                {
                    "name": "SO-001",
                    "status": "To Deliver and Bill",
                    "grand_total": 1500,
                    "billing_status": "Not Billed",
                    "delivery_status": "Not Delivered",
                    "transaction_date": "2026-06-25",
                }
            ],
            "Sales Invoice": [
                {
                    "name": "SI-001",
                    "outstanding_amount": 500,
                    "grand_total": 1500,
                    "due_date": "2026-07-01",
                },
                {
                    "name": "SI-002",
                    "outstanding_amount": 250,
                    "grand_total": 250,
                    "due_date": "2026-07-10",
                },
            ],
            "Print Format": [
                {"name": "Sales Order Format"},
                {"name": "Sales Order with payment details"},
            ],
            "Payment Entry Reference": [{"name": "PE-REF-001"}],
        }
        self.resources = {
            ("Customer", "CUST-001"): {
                "name": "CUST-001",
                "customer_name": "Acme Industries",
                "customer_group": "Commercial",
                "territory": "India",
                "custom_customer_status": "Active",
                "custom_contact_person": "Ravi Kumar",
                "mobile_no": "+91 98765 43210",
                "gstin": "29ABCDE1234F1Z5",
                "pan": "ABCDE1234F",
                "primary_address": "Bengaluru",
                "customer_primary_contact": "CONT-001",
            },
            ("Contact", "CONT-001"): {"email_id": "ravi@example.test"},
            ("Sales Order", "SO-001"): {
                "name": "SO-001",
                "customer": "CUST-001",
                "customer_name": "Acme Industries",
                "transaction_date": "2026-06-25",
                "status": "To Deliver and Bill",
                "billing_status": "Not Billed",
                "delivery_status": "Not Delivered",
                "grand_total": 1500,
                "currency": "INR",
                "delivery_date": "2026-07-05",
                "items": [
                    {
                        "item_name": "Widget",
                        "item_code": "ITEM-001",
                        "qty": 3,
                        "rate": 500,
                        "amount": 1500,
                        "delivered_qty": 0,
                        "billed_amt": 0,
                    }
                ],
            },
            ("Sales Order", "SO-OTHER"): {
                "name": "SO-OTHER",
                "customer": "CUST-OTHER",
            },
            ("Sales Invoice", "SI-001"): {
                "name": "SI-001",
                "customer": "CUST-001",
                "customer_name": "Acme Industries",
                "posting_date": "2026-06-20",
                "due_date": "2026-07-01",
                "status": "Unpaid",
                "outstanding_amount": 500,
                "grand_total": 1500,
                "currency": "INR",
                "payment_terms_template": "Net 10",
                "items": [
                    {
                        "item_name": "Widget",
                        "item_code": "ITEM-001",
                        "qty": 3,
                        "rate": 500,
                        "amount": 1500,
                    }
                ],
            },
            ("Sales Invoice", "SI-OTHER"): {
                "name": "SI-OTHER",
                "customer": "CUST-OTHER",
            },
        }
        self.sent_messages = []
        self.sent_files = []
        self.posts = []

    def get_resource_list(self, doctype, **kwargs):
        return list(self.resource_lists.get(doctype, []))

    def get_resource(self, doctype, docname):
        return dict(self.resources.get((doctype, docname), {}))

    def send_whatsapp_message(self, mobile_number, message):
        self.sent_messages.append({"mobile_number": mobile_number, "message": message})
        return {"status": True, "msg": "Sent"}

    def send_whatsapp_message_with_file(self, mobile_number, message, file_link):
        self.sent_files.append(
            {
                "mobile_number": mobile_number,
                "message": message,
                "file_link": file_link,
            }
        )
        return {"status": True, "msg": "Sent", "message_id": "MSG-001"}

    def _post(self, endpoint, json_data=None):
        self.posts.append({"endpoint": endpoint, "json_data": json_data})
        return {"data": {"name": "LOG-001"}}


class CustomerQueryToolsTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = FakeFrappeClient()
        self.callback_called = False
        self.tools = CustomerQueryTools(
            client=self.client,
            customer_id="CUST-001",
            phone_number="+91 98765 43210",
            on_verify_success=self._on_verify_success,
        )

    def _on_verify_success(self):
        self.callback_called = True

    def mark_verified(self):
        self.tools.is_verified = True

    async def test_get_customer_sales_orders_works_without_verification(self):
        result = await self.tools.get_customer_sales_orders()

        self.assertNotIn("Verification required", result)
        self.assertIn("Found 1 sales orders", result)
        self.assertIn("SO-001", result)

    async def test_get_customer_sales_orders_returns_recent_orders(self):
        result = await self.tools.get_customer_sales_orders()

        self.assertIn("Found 1 sales orders", result)
        self.assertIn("SO-001", result)
        self.assertIn("Not Delivered", result)

    async def test_get_customer_pending_amount_sums_unpaid_invoices(self):
        result = await self.tools.get_customer_pending_amount()

        self.assertIn("750.0", result)
        self.assertIn("SI-001", result)
        self.assertIn("SI-002", result)

    async def test_get_customer_details_returns_profile_and_contact(self):
        result = await self.tools.get_customer_details()

        self.assertIn("Acme Industries", result)
        self.assertIn("Commercial", result)
        self.assertIn("ravi@example.test", result)
        self.assertIn("29ABCDE1234F1Z5", result)

    async def test_get_sales_order_details_returns_itemized_order(self):
        result = await self.tools.get_sales_order_details("SO-001")

        self.assertIn("Details of Sales Order SO-001", result)
        self.assertIn("Widget", result)
        self.assertIn("Expected Delivery Date: 2026-07-05", result)

    async def test_get_sales_order_details_blocks_other_customer_order(self):
        self.mark_verified()

        result = await self.tools.get_sales_order_details("SO-OTHER")

        self.assertIn("does not belong to the linked customer", result)

    async def test_get_sales_invoice_details_returns_itemized_invoice(self):
        result = await self.tools.get_sales_invoice_details("SI-001")

        self.assertIn("Details of Sales Invoice SI-001", result)
        self.assertIn("Outstanding Amount: 500 INR", result)
        self.assertIn("Payment Terms: Net 10", result)

    async def test_get_sales_invoice_details_blocks_other_customer_invoice(self):
        self.mark_verified()

        result = await self.tools.get_sales_invoice_details("SI-OTHER")

        self.assertIn("does not belong to the linked customer", result)

    async def test_search_customer_requires_at_least_three_characters(self):
        result = await self.tools.search_customer("ab")

        self.assertIn("at least 3 characters", result)

    async def test_search_customer_returns_matches(self):
        result = await self.tools.search_customer("Acme")

        self.assertIn("Found 1 matching customer", result)
        self.assertIn("CUST-001", result)
        self.assertIn("Acme Industries", result)

    async def test_send_verification_otp_sends_whatsapp_and_stores_otp(self):
        with patch("agent_tools.random.randint", return_value=1234):
            result = await self.tools.send_verification_otp()

        self.assertIn("OTP has been sent", result)
        self.assertEqual(self.tools.generated_otp, "1234")
        self.assertEqual(self.client.sent_messages[0]["mobile_number"], "9876543210")
        self.assertIn("1234", self.client.sent_messages[0]["message"])

    async def test_verify_otp_rejects_incorrect_code(self):
        self.tools.generated_otp = "1234"

        result = await self.tools.verify_otp("0000")

        self.assertIn("Verification failed", result)
        self.assertFalse(self.tools.is_verified)
        self.assertFalse(self.callback_called)

    async def test_verify_otp_accepts_spoken_or_dtmf_format(self):
        self.tools.generated_otp = "1234"
        self.tools.dtmf_buffer = "12"

        result = await self.tools.verify_otp("1 2 3 4")

        self.assertIn("Verification successful", result)
        self.assertTrue(self.tools.is_verified)
        self.assertIsNone(self.tools.generated_otp)
        self.assertEqual(self.tools.dtmf_buffer, "")
        self.assertTrue(self.callback_called)

    async def test_send_pdf_whatsapp_requires_verification(self):
        result = await self.tools.send_pdf_whatsapp("Sales Invoice", "SI-001")

        self.assertIn("Verification required", result)

    async def test_send_pdf_whatsapp_rejects_unsupported_doctype(self):
        self.mark_verified()

        result = await self.tools.send_pdf_whatsapp("Delivery Note", "DN-001")

        self.assertIn("Only 'Sales Order' or 'Sales Invoice'", result)

    async def test_send_pdf_whatsapp_sends_invoice_pdf_and_logs(self):
        self.mark_verified()

        result = await self.tools.send_pdf_whatsapp("Sales Invoice", "SI-001")

        self.assertIn("successfully sent", result)
        self.assertEqual(self.client.sent_files[0]["mobile_number"], "9876543210")
        self.assertIn("doctype=Sales%20Invoice", self.client.sent_files[0]["file_link"])
        self.assertIn("name=SI-001", self.client.sent_files[0]["file_link"])
        self.assertEqual(self.client.posts[0]["endpoint"], "api/resource/WhatsApp Message Log")

    async def test_send_pdf_whatsapp_uses_payment_print_format_for_paid_order(self):
        self.mark_verified()

        result = await self.tools.send_pdf_whatsapp("Sales Order", "SO-001")

        self.assertIn("successfully sent", result)
        self.assertIn(
            "format=Sales%20Order%20with%20payment%20details",
            self.client.sent_files[0]["file_link"],
        )

    async def test_send_text_whatsapp_requires_verification(self):
        result = await self.tools.send_text_whatsapp("Your order ID is SO-001.")

        self.assertIn("Verification required", result)

    async def test_send_text_whatsapp_rejects_empty_message(self):
        self.mark_verified()

        result = await self.tools.send_text_whatsapp("   ")

        self.assertIn("Please provide the text message", result)

    async def test_send_text_whatsapp_sends_message(self):
        self.mark_verified()

        result = await self.tools.send_text_whatsapp("Your order ID is SO-001.")

        self.assertIn("successfully sent", result)
        self.assertEqual(self.client.sent_messages[-1]["mobile_number"], "9876543210")
        self.assertEqual(self.client.sent_messages[-1]["message"], "Your order ID is SO-001.")


if __name__ == "__main__":
    unittest.main()
