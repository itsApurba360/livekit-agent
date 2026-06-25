# -*- coding: utf-8 -*-
import logging
import random
from typing import Optional, Callable
from livekit.agents import llm
from frappe_client import FrappeRestClient

logger = logging.getLogger("agent-tools")

class CustomerQueryTools(llm.ToolContext):
    """
    A standalone LLM ToolContext that implements all customer information lookup
    and WhatsApp OTP/PDF sending logic using a remote Frappe REST client instead of local ORM imports.
    """
    def __init__(self, client: FrappeRestClient, customer_id: Optional[str] = None, phone_number: Optional[str] = None, on_verify_success: Optional[Callable] = None):
        super().__init__(tools=[])
        self.client = client
        self.customer_id = customer_id
        self.phone_number = phone_number
        self.is_verified = False
        self.generated_otp = None
        self.dtmf_buffer = ""
        self.on_verify_success = on_verify_success

    @llm.function_tool(description="Get the list and status of sales orders for the current customer.")
    async def get_customer_sales_orders(self, customer_id: Optional[str] = None):
        """
        Args:
            customer_id: Optional customer ID. If not provided, the customer linked to this call will be used.
        """
        if not self.is_verified:
            return "Verification required. Please send a verification OTP first by calling send_verification_otp."
        target_customer = customer_id or self.customer_id
        if not target_customer:
            return "No customer is linked or provided. Please search for the customer first using search_customer."
        try:
            orders = self.client.get_resource_list(
                "Sales Order",
                filters=[["customer", "=", target_customer], ["docstatus", "=", 1]],
                fields=["name", "status", "grand_total", "billing_status", "delivery_status", "transaction_date"],
                order_by="transaction_date desc",
                limit=10
            )
            if not orders:
                return f"No sales orders found for customer {target_customer}."

            res_lines = [f"Found {len(orders)} sales orders for customer {target_customer}:"]
            for o in orders:
                res_lines.append(
                    f"- Order {o.get('name')} on {o.get('transaction_date')}: Amount {o.get('grand_total')}, Status: {o.get('status')}, "
                    f"Billing: {o.get('billing_status')}, Delivery: {o.get('delivery_status')}"
                )
            return "\n".join(res_lines)
        except Exception as e:
            logger.error(f"Failed to retrieve sales orders: {e}")
            return f"Error retrieving sales orders: {str(e)}"

    @llm.function_tool(description="Get the total pending / outstanding billing amount for the current customer from unpaid invoices.")
    async def get_customer_pending_amount(self, customer_id: Optional[str] = None):
        """
        Args:
            customer_id: Optional customer ID. If not provided, the customer linked to this call will be used.
        """
        if not self.is_verified:
            return "Verification required. Please send a verification OTP first by calling send_verification_otp."
        target_customer = customer_id or self.customer_id
        if not target_customer:
            return "No customer is linked or provided. Outstanding balance cannot be calculated."
        try:
            invoices = self.client.get_resource_list(
                "Sales Invoice",
                filters=[["customer", "=", target_customer], ["docstatus", "=", 1], ["status", "!=", "Paid"]],
                fields=["name", "outstanding_amount", "grand_total", "due_date"]
            )
            total_outstanding = sum(float(inv.get("outstanding_amount") or 0) for inv in invoices)

            if not invoices:
                return f"The customer {target_customer} has no outstanding pending invoices. The outstanding balance is 0."

            res_lines = [
                f"Total pending/outstanding invoice balance for customer {target_customer} is {total_outstanding}.",
                "Details of unpaid invoices:"
            ]
            for inv in invoices:
                res_lines.append(
                    f"- Invoice {inv.get('name')}: Outstanding {inv.get('outstanding_amount')} out of {inv.get('grand_total')} (Due: {inv.get('due_date')})"
                )
            return "\n".join(res_lines)
        except Exception as e:
            logger.error(f"Failed to retrieve pending amount: {e}")
            return f"Error retrieving pending amount: {str(e)}"

    @llm.function_tool(description="Get detailed customer information such as name, ID, contact details, address, GST, PAN, customer group, and territory.")
    async def get_customer_details(self, customer_id: Optional[str] = None):
        """
        Args:
            customer_id: Optional customer ID. If not provided, the customer linked to this call will be used.
        """
        if not self.is_verified:
            return "Verification required. Please send a verification OTP first by calling send_verification_otp."
        target_customer = customer_id or self.customer_id
        if not target_customer:
            return "No customer is linked or provided. Please search for the customer first using search_customer."
        try:
            cust = self.client.get_resource("Customer", target_customer)
            if not cust:
                return f"Customer '{target_customer}' does not exist in the database."

            details = [
                f"Customer Details for {cust.get('customer_name')} ({cust.get('name')}):",
                f"- Customer Group: {cust.get('customer_group') or 'N/A'}",
                f"- Territory: {cust.get('territory') or 'N/A'}",
                f"- Customer Status: {cust.get('custom_customer_status') or 'N/A'}",
                f"- Primary Contact Person: {cust.get('custom_contact_person') or 'N/A'}",
                f"- Mobile Number: {cust.get('mobile_no') or cust.get('custom_primary_mobile_no') or cust.get('custom_alt_mobile_no') or 'N/A'}",
                f"- GSTIN: {cust.get('gstin') or cust.get('custom_gst_no') or 'N/A'}",
                f"- PAN: {cust.get('pan') or cust.get('custom_pan_no') or 'N/A'}",
                f"- Primary Address: {cust.get('primary_address') or 'N/A'}"
            ]
            if cust.get("customer_primary_contact"):
                contact_card = cust.get("customer_primary_contact")
                try:
                    contact = self.client.get_resource("Contact", contact_card)
                    details.append(f"- Primary Contact Card: {contact_card} (Email: {contact.get('email_id') or 'N/A'})")
                except Exception:
                    details.append(f"- Primary Contact Card: {contact_card}")

            return "\n".join(details)
        except Exception as e:
            logger.error(f"Failed to retrieve customer details: {e}")
            return f"Error retrieving customer details: {str(e)}"

    @llm.function_tool(description="Get itemized details and billing/delivery status of a specific Sales Order.")
    async def get_sales_order_details(self, sales_order_id: str):
        """
        Args:
            sales_order_id: The exact ID of the Sales Order (e.g. SAL-ORD-2026-01261).
        """
        if not self.is_verified:
            return "Verification required. Please send a verification OTP first by calling send_verification_otp."
        if not sales_order_id:
            return "Please provide a valid Sales Order ID."
        try:
            so = self.client.get_resource("Sales Order", sales_order_id)
            if not so:
                return f"Sales Order '{sales_order_id}' does not exist in the database."

            if self.customer_id and so.get("customer") != self.customer_id:
                return f"Sales Order '{sales_order_id}' does not belong to the linked customer."

            res = [
                f"Details of Sales Order {so.get('name')}:",
                f"- Customer: {so.get('customer_name')} ({so.get('customer')})",
                f"- Order Date: {so.get('transaction_date')}",
                f"- Status: {so.get('status')}",
                f"- Billing Status: {so.get('billing_status')}",
                f"- Delivery Status: {so.get('delivery_status')}",
                f"- Grand Total: {so.get('grand_total')} {so.get('currency') or 'INR'}",
                "Ordered Items:"
            ]
            for item in so.get("items", []):
                res.append(
                    f"  * {item.get('item_name')} ({item.get('item_code')}): Quantity {item.get('qty')}, Rate {item.get('rate')}, Total {item.get('amount')} "
                    f"(Delivered: {item.get('delivered_qty')}, Billed Amount: {item.get('billed_amt')})"
                )

            if so.get("delivery_date"):
                res.append(f"- Expected Delivery Date: {so.get('delivery_date')}")
            return "\n".join(res)
        except Exception as e:
            logger.error(f"Failed to retrieve sales order details: {e}")
            return f"Error retrieving sales order details: {str(e)}"

    @llm.function_tool(description="Get detailed information about a specific Sales Invoice including item list, outstanding amount, and due date.")
    async def get_sales_invoice_details(self, invoice_id: str):
        """
        Args:
            invoice_id: The exact ID of the Sales Invoice (e.g. LSA/26-27/0008).
        """
        if not self.is_verified:
            return "Verification required. Please send a verification OTP first by calling send_verification_otp."
        if not invoice_id:
            return "Please provide a valid Sales Invoice ID."
        try:
            si = self.client.get_resource("Sales Invoice", invoice_id)
            if not si:
                return f"Sales Invoice '{invoice_id}' does not exist."

            if self.customer_id and si.get("customer") != self.customer_id:
                return f"Sales Invoice '{invoice_id}' does not belong to the linked customer."

            res = [
                f"Details of Sales Invoice {si.get('name')}:",
                f"- Customer: {si.get('customer_name')} ({si.get('customer')})",
                f"- Invoice Date: {si.get('posting_date')}",
                f"- Due Date: {si.get('due_date')}",
                f"- Status: {si.get('status')}",
                f"- Outstanding Amount: {si.get('outstanding_amount')} {si.get('currency') or 'INR'} (Grand Total: {si.get('grand_total')})",
                "Invoiced Items:"
            ]
            for item in si.get("items", []):
                res.append(
                    f"  * {item.get('item_name')} ({item.get('item_code')}): Quantity {item.get('qty')}, Rate {item.get('rate')}, Total {item.get('amount')}"
                )

            if si.get("payment_terms_template"):
                res.append(f"- Payment Terms: {si.get('payment_terms_template')}")
            return "\n".join(res)
        except Exception as e:
            logger.error(f"Failed to retrieve sales invoice details: {e}")
            return f"Error retrieving sales invoice details: {str(e)}"

    @llm.function_tool(description="Search for a customer by their name, ID, or phone number to find their customer ID.")
    async def search_customer(self, search_query: str):
        """
        Args:
            search_query: Search text (e.g. name of the company, contact person, or phone number).
        """
        if not search_query or len(search_query.strip()) < 3:
            return "Please provide a search query with at least 3 characters."
        try:
            # Clean and look up using dynamic or_filters in GET resource list
            # We filter Customer table where ID, Customer Name, or mobile numbers match
            or_filters = [
                ["name", "like", f"%{search_query}%"],
                ["customer_name", "like", f"%{search_query}%"],
                ["mobile_no", "like", f"%{search_query}%"],
                ["custom_primary_mobile_no", "like", f"%{search_query}%"]
            ]
            customers = self.client.get_resource_list(
                "Customer",
                fields=["name", "customer_name", "mobile_no"],
                or_filters=or_filters,
                limit=5
            )

            if not customers:
                return f"No customer found matching query '{search_query}'."

            res_lines = [f"Found {len(customers)} matching customer(s):"]
            for c in customers:
                res_lines.append(f"- ID: {c.get('name')}, Name: {c.get('customer_name')}, Mobile: {c.get('mobile_no') or 'N/A'}")
            return "\n".join(res_lines)
        except Exception as e:
            logger.error(f"Failed to search customer: {e}")
            return f"Error searching customer: {str(e)}"

    @llm.function_tool(description="Send a 4-digit verification OTP to the customer's WhatsApp. Call this when customer verification is required.")
    async def send_verification_otp(self, customer_id: Optional[str] = None):
        """
        Args:
            customer_id: Optional customer ID to send the OTP to. If not provided, the customer linked to this call will be used.
        """
        if self.is_verified:
            return "Customer is already verified; no OTP needed."

        target_customer = customer_id or self.customer_id
        if not target_customer:
            return "No customer is linked to this call. Please search for the customer first using search_customer."

        if not self.phone_number:
            try:
                cust = self.client.get_resource("Customer", target_customer)
                self.phone_number = cust.get("mobile_no") or cust.get("custom_primary_mobile_no") or cust.get("custom_alt_mobile_no")
            except Exception as e:
                logger.error(f"Error fetching customer phone number over REST: {e}")

        if not self.phone_number:
            return f"Could not find a valid phone number for customer {target_customer}. Ask the customer to provide their phone number."

        cleaned_phone = "".join(c for c in self.phone_number if c.isdigit())
        if len(cleaned_phone) < 10:
            return f"Invalid phone number '{self.phone_number}' for customer. Cannot send OTP."

        last_10 = cleaned_phone[-10:]
        otp = f"{random.randint(1000, 9999)}"
        self.generated_otp = otp
        self.dtmf_buffer = ""
        self.is_verified = False

        message = f"Your verification code for LSA Office is {otp}. Please tell or type this code to the voice assistant."

        try:
            res = self.client.send_whatsapp_message(mobile_number=last_10, message=message)
            if res.get("status"):
                logger.info(f"OTP {otp} successfully sent to {last_10} via WhatsApp REST API.")
                return f"OTP has been sent to the customer's WhatsApp at {last_10}. Please ask the customer to tell you the 4-digit OTP or type it on their phone keypad."
            else:
                logger.error(f"Failed to send WhatsApp message over REST: {res.get('msg')}")
                return f"Failed to send WhatsApp OTP: {res.get('msg')}. The number may not be registered on WhatsApp."
        except Exception as e:
            logger.error(f"Error sending WhatsApp OTP: {e}")
            return f"Error sending WhatsApp OTP: {str(e)}"

    @llm.function_tool(description="Verify the 4-digit OTP spoken by the customer.")
    async def verify_otp(self, otp: str):
        """
        Args:
            otp: The 4-digit OTP code spoken by the customer.
        """
        if self.is_verified:
            return "Customer is already verified."
        if not self.generated_otp:
            return "No OTP has been sent yet. Please call send_verification_otp first."

        cleaned_otp = "".join(c for c in otp if c.isdigit())
        if cleaned_otp == self.generated_otp:
            self.is_verified = True
            self.generated_otp = None
            self.dtmf_buffer = ""
            if self.on_verify_success:
                try:
                    import asyncio
                    if asyncio.iscoroutinefunction(self.on_verify_success):
                        asyncio.create_task(self.on_verify_success())
                    else:
                        self.on_verify_success()
                except Exception as cb_err:
                    logger.error(f"Error executing on_verify_success callback: {cb_err}")
            return "Verification successful! The customer is now verified. You can now proceed to fulfill their original request."
        else:
            return f"Verification failed. The OTP '{otp}' is incorrect. Please ask the customer to check and provide the correct OTP."

    @llm.function_tool(description="Send a PDF of a specific Sales Order or Sales Invoice via WhatsApp. Caller verification is required first.")
    async def send_pdf_whatsapp(self, doctype: str, docname: str):
        """
        Args:
            doctype: The document type, either 'Sales Order' or 'Sales Invoice'.
            docname: The exact name/ID of the document (e.g. SAL-ORD-2026-01261 or LSA/26-27/0008).
        """
        if not self.is_verified:
            return "Verification required. Please send a verification OTP first by calling send_verification_otp."
        if doctype not in ["Sales Order", "Sales Invoice"]:
            return "Only 'Sales Order' or 'Sales Invoice' PDFs can be sent."
        if not docname:
            return "Please provide a valid document name."

        try:
            doc = self.client.get_resource(doctype, docname)
            if not doc:
                return f"{doctype} '{docname}' does not exist in the database."

            if self.customer_id and doc.get("customer") != self.customer_id:
                return f"{doctype} '{docname}' does not belong to the linked customer."

            recipient_phone = self.phone_number
            if not recipient_phone:
                cust = self.client.get_resource("Customer", doc.get("customer"))
                recipient_phone = cust.get("mobile_no") or cust.get("custom_primary_mobile_no") or cust.get("custom_alt_mobile_no")

            if not recipient_phone:
                return "Could not determine a WhatsApp phone number for the customer."

            cleaned_phone = "".join(c for c in recipient_phone if c.isdigit())
            if len(cleaned_phone) < 10:
                return f"Invalid phone number '{recipient_phone}' for customer. Cannot send PDF."
            last_10 = cleaned_phone[-10:]

            # Resolve print format over REST
            custom_formats_res = self.client.get_resource_list(
                "Print Format",
                filters=[["doc_type", "=", doctype], ["disabled", "=", 0]],
                fields=["name"]
            )
            custom_formats = [f.get("name") for f in custom_formats_res]
            
            if doctype == "Sales Order":
                print_format = "Sales Order Format"
                if "Sales Order Format" in custom_formats:
                    pe_list = self.client.get_resource_list(
                        "Payment Entry Reference",
                        filters=[["reference_doctype", "=", "Sales Order"], ["reference_name", "=", docname], ["docstatus", "=", 1]],
                        fields=["name"]
                    )
                    if pe_list and "Sales Order with payment details" in custom_formats:
                        print_format = "Sales Order with payment details"
                else:
                    print_format = custom_formats[0] if custom_formats else "Standard"
            else:
                print_format = custom_formats[0] if custom_formats else "Standard"

            pdf_link = f"{self.client.base_url}/api/method/frappe.utils.print_format.download_pdf?doctype={doctype.replace(' ', '%20')}&name={docname}&format={print_format.replace(' ', '%20')}&no_letterhead=0&letterhead=LSA&settings=%7B%7D&_lang=en/{docname}.pdf"
            message = f"Hello, here is your PDF copy of {doctype} {docname} from LSA Office."

            res = self.client.send_whatsapp_message_with_file(mobile_number=last_10, message=message, file_link=pdf_link)
            if res.get("status"):
                # Standard call completion doesn't block if log creation is skipped or not present
                try:
                    log_data = {
                        "doctype": "WhatsApp Message Log",
                        "details": [{
                            "type": doctype,
                            "document_id": docname,
                            "mobile_number": last_10,
                            "customer": doc.get("customer"),
                            "message_id": res.get("message_id"),
                            "sent_successfully": 1,
                        }]
                    }
                    # We can post to create a log entry if the remote server supports it
                    self.client._post("api/resource/WhatsApp Message Log", json_data=log_data)
                except Exception as log_err:
                    logger.debug(f"Could not create remote WhatsApp Message Log (probably vanilla site): {log_err}")

                return f"{doctype} PDF has been successfully sent to WhatsApp number {last_10}."
            else:
                return f"Failed to send PDF via WhatsApp: {res.get('msg')}."
        except Exception as e:
            logger.error(f"Error in send_pdf_whatsapp: {e}")
            return f"Error sending PDF: {str(e)}"
