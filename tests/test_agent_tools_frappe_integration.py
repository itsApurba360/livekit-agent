# -*- coding: utf-8 -*-
import os
import sys
import unittest
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent_tools import CustomerQueryTools  # noqa: E402
from frappe_client import FrappeRestClient  # noqa: E402


WHATSAPP_TEST_PHONE = "+919062371141"
WHATSAPP_TEST_PHONE_LAST_10 = "9062371141"


async def call_function_tool(tool, *args, **kwargs):
    return await tool._func(tool._instance, *args, **kwargs)


class TestCustomerQueryToolsFrappeIntegration(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        load_dotenv(PROJECT_ROOT / ".env")

        cls.site_url = os.environ.get("FRAPPE_SITE_URL")
        cls.api_key = os.environ.get("FRAPPE_API_KEY")
        cls.api_secret = os.environ.get("FRAPPE_API_SECRET")

        if not all([cls.site_url, cls.api_key, cls.api_secret]):
            raise unittest.SkipTest("Frappe integration credentials are not configured in .env")

        cls.client = FrappeRestClient(
            base_url=cls.site_url,
            api_key=cls.api_key,
            api_secret=cls.api_secret,
        )
        cls.customer = cls._first_customer()
        if not cls.customer:
            raise unittest.SkipTest("No Customer records are available for read-only tool checks")

    @classmethod
    def _first_customer(cls):
        customers = cls.client.get_resource_list(
            "Customer",
            fields=["name", "customer_name", "mobile_no"],
            order_by="modified desc",
            limit=1,
        )
        return customers[0] if customers else None

    def setUp(self):
        self.customer_id = self.customer["name"]
        self.tools = CustomerQueryTools(
            client=self.client,
            customer_id=self.customer_id,
            phone_number=self.customer.get("mobile_no"),
        )

    def require_whatsapp_send_tests_enabled(self):
        if os.environ.get("RUN_WHATSAPP_SEND_TESTS") != "1":
            self.skipTest(
                "Set RUN_WHATSAPP_SEND_TESTS=1 to send real WhatsApp test messages"
            )

    def first_submitted_document_for_pdf(self):
        for doctype, date_field in [
            ("Sales Invoice", "posting_date"),
            ("Sales Order", "transaction_date"),
        ]:
            docs = self.client.get_resource_list(
                doctype,
                filters=[["docstatus", "=", 1]],
                fields=["name", "customer"],
                order_by=f"{date_field} desc",
                limit=1,
            )
            if docs:
                return doctype, docs[0]
        return None, None

    async def test_search_customer_by_id(self):
        result = await call_function_tool(self.tools.search_customer, self.customer_id)

        self.assertNotIn("Error searching customer", result)
        self.assertIn(self.customer_id, result)

    async def test_get_customer_details(self):
        result = await call_function_tool(self.tools.get_customer_details)

        self.assertNotIn("Error retrieving customer details", result)
        self.assertIn("Customer Details", result)
        self.assertIn(self.customer_id, result)

    async def test_get_customer_sales_orders(self):
        result = await call_function_tool(self.tools.get_customer_sales_orders)

        self.assertNotIn("Error retrieving sales orders", result)
        self.assertIn(self.customer_id, result)

    async def test_get_customer_pending_amount(self):
        result = await call_function_tool(self.tools.get_customer_pending_amount)

        self.assertNotIn("Error retrieving pending amount", result)
        self.assertIn(self.customer_id, result)

    async def test_get_sales_order_details_when_customer_has_order(self):
        orders = self.client.get_resource_list(
            "Sales Order",
            filters=[["docstatus", "=", 1]],
            fields=["name", "customer"],
            order_by="transaction_date desc",
            limit=1,
        )
        if not orders:
            self.skipTest("No submitted Sales Order found")

        order_id = orders[0]["name"]
        order_customer = orders[0]["customer"]
        tools = CustomerQueryTools(client=self.client, customer_id=order_customer)

        result = await call_function_tool(tools.get_sales_order_details, order_id)

        self.assertNotIn("Error retrieving sales order details", result)
        self.assertIn(order_id, result)
        self.assertIn(order_customer, result)

    async def test_get_sales_invoice_details_when_customer_has_invoice(self):
        invoices = self.client.get_resource_list(
            "Sales Invoice",
            filters=[["docstatus", "=", 1]],
            fields=["name", "customer"],
            order_by="posting_date desc",
            limit=1,
        )
        if not invoices:
            self.skipTest("No submitted Sales Invoice found")

        invoice_id = invoices[0]["name"]
        invoice_customer = invoices[0]["customer"]
        tools = CustomerQueryTools(client=self.client, customer_id=invoice_customer)

        result = await call_function_tool(tools.get_sales_invoice_details, invoice_id)

        self.assertNotIn("Error retrieving sales invoice details", result)
        self.assertIn(invoice_id, result)
        self.assertIn(invoice_customer, result)

    async def test_send_verification_otp_to_fixed_whatsapp_number(self):
        self.require_whatsapp_send_tests_enabled()
        tools = CustomerQueryTools(
            client=self.client,
            customer_id=self.customer_id,
            phone_number=WHATSAPP_TEST_PHONE,
        )

        result = await call_function_tool(tools.send_verification_otp)

        self.assertNotIn("Error sending WhatsApp OTP", result)
        self.assertNotIn("Failed to send WhatsApp OTP", result)
        self.assertIn("OTP has been sent", result)
        self.assertIn(WHATSAPP_TEST_PHONE_LAST_10, result)
        self.assertIsNotNone(tools.generated_otp)
        self.assertEqual(len(tools.generated_otp), 4)

    async def test_send_pdf_whatsapp_to_fixed_whatsapp_number(self):
        self.require_whatsapp_send_tests_enabled()
        doctype, doc = self.first_submitted_document_for_pdf()
        if not doc:
            self.skipTest("No submitted Sales Invoice or Sales Order found for PDF send")

        tools = CustomerQueryTools(
            client=self.client,
            customer_id=doc["customer"],
            phone_number=WHATSAPP_TEST_PHONE,
        )
        tools.is_verified = True

        result = await call_function_tool(
            tools.send_pdf_whatsapp,
            doctype,
            doc["name"],
        )

        self.assertNotIn("Error sending PDF", result)
        self.assertNotIn("Failed to send PDF", result)
        self.assertIn("successfully sent", result)
        self.assertIn(WHATSAPP_TEST_PHONE_LAST_10, result)


if __name__ == "__main__":
    unittest.main()
