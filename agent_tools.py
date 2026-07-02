import asyncio
import logging
import random
import re
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Optional, Callable, Any
from livekit.agents import llm
from call_status_store import update_call_record, get_call_record, list_call_records

logger = logging.getLogger("agent-tools")
END_CALL_SPEECH_START_TIMEOUT_SECONDS = 3.0
END_CALL_MAX_SPEECH_SECONDS = 8.0
CALLBACK_OFFICE_START = dt_time(10, 0)
CALLBACK_OFFICE_END = dt_time(18, 0)
CALLBACK_SLOT_MINUTES = 30
CALLBACK_IST = timezone(timedelta(hours=5, minutes=30))
CALLBACK_HOLIDAYS_2026 = {
    "2026-01-01": "New Year",
    "2026-01-04": "Sunday",
    "2026-01-11": "Sunday",
    "2026-01-15": "Makar Sankranthi",
    "2026-01-18": "Sunday",
    "2026-01-24": "Saturday Off",
    "2026-01-25": "Sunday",
    "2026-01-26": "Republic Day",
    "2026-02-01": "Sunday",
    "2026-02-08": "Sunday",
    "2026-02-15": "Sunday",
    "2026-02-22": "Sunday",
    "2026-02-28": "Saturday off",
    "2026-03-01": "Sunday",
    "2026-03-08": "Sunday",
    "2026-03-15": "Sunday",
    "2026-03-19": "Ugadi",
    "2026-03-22": "Sunday",
    "2026-03-28": "Saturday off",
    "2026-03-29": "Sunday",
    "2026-04-05": "Sunday",
    "2026-04-12": "Sunday",
    "2026-04-19": "Sunday",
    "2026-04-25": "Saturday off",
    "2026-04-26": "Sunday",
    "2026-05-01": "Labour Day",
    "2026-05-03": "Sunday",
    "2026-05-10": "Sunday",
    "2026-05-17": "Sunday",
    "2026-05-23": "Saturday off",
    "2026-05-24": "Sunday",
    "2026-05-31": "Sunday",
    "2026-06-07": "Sunday",
    "2026-06-14": "Sunday",
    "2026-06-21": "Sunday",
    "2026-06-27": "Saturday off",
    "2026-06-28": "Sunday",
    "2026-07-05": "Sunday",
    "2026-07-12": "Sunday",
    "2026-07-19": "Sunday",
    "2026-07-25": "Saturday off",
    "2026-07-26": "Sunday",
    "2026-08-02": "Sunday",
    "2026-08-09": "Sunday",
    "2026-08-15": "Independence Day",
    "2026-08-16": "Sunday",
    "2026-08-22": "Saturday off",
    "2026-08-23": "Sunday",
    "2026-08-30": "Sunday",
    "2026-09-06": "Sunday",
    "2026-09-13": "Sunday",
    "2026-09-20": "Sunday",
    "2026-09-26": "Saturday off",
    "2026-09-27": "Sunday",
    "2026-10-04": "Sunday",
    "2026-10-11": "Sunday",
    "2026-10-18": "Sunday",
    "2026-10-20": "Maha Navami / Vijayadashami",
    "2026-10-24": "Saturday off",
    "2026-10-25": "Sunday",
    "2026-11-01": "Sunday",
    "2026-11-08": "Sunday",
    "2026-11-09": "Diwali",
    "2026-11-15": "Sunday",
    "2026-11-22": "Sunday",
    "2026-11-28": "Saturday off",
    "2026-11-29": "Sunday",
    "2026-12-06": "Sunday",
    "2026-12-13": "Sunday",
    "2026-12-20": "Sunday",
    "2026-12-26": "Saturday off",
    "2026-12-27": "Sunday",
}


async def _wait_for_end_call_speech(session: Optional[Any]) -> None:
    if not session or not hasattr(session, "on") or not hasattr(session, "off"):
        await asyncio.sleep(END_CALL_SPEECH_START_TIMEOUT_SECONDS)
        return

    started = asyncio.Event()
    finished = asyncio.Event()

    def on_agent_state_changed(ev):
        state = getattr(ev, "new_state", None)
        if state == "speaking":
            started.set()
        elif started.is_set():
            finished.set()

    session.on("agent_state_changed", on_agent_state_changed)
    if getattr(session, "agent_state", None) == "speaking":
        started.set()

    try:
        try:
            await asyncio.wait_for(started.wait(), END_CALL_SPEECH_START_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return

        if getattr(session, "agent_state", None) != "speaking":
            return

        try:
            await asyncio.wait_for(finished.wait(), END_CALL_MAX_SPEECH_SECONDS)
        except asyncio.TimeoutError:
            pass
    finally:
        session.off("agent_state_changed", on_agent_state_changed)


def _now_ist() -> datetime:
    return datetime.now(CALLBACK_IST).replace(tzinfo=None)


def _relative_callback_minutes(value: str) -> Optional[int]:
    text = (value or "").strip().lower()
    match = re.fullmatch(r"\+(\d{1,4})", text) or re.fullmatch(
        r"(?:\+|in\s+|after\s+)?(\d{1,4})\s*(?:m|min|mins|minute|minutes)(?:\s+later)?",
        text,
    )
    if not match:
        return None
    minutes = int(match.group(1))
    return minutes if 1 <= minutes <= 1440 else None


def _parse_callback_schedule(date_str: str, time_str: str) -> Optional[datetime]:
    relative_minutes = _relative_callback_minutes(time_str) or _relative_callback_minutes(date_str)
    if relative_minutes is not None:
        return (_now_ist() + timedelta(minutes=relative_minutes)).replace(second=0, microsecond=0)

    parsed_date = None
    for date_fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            parsed_date = datetime.strptime((date_str or "").strip(), date_fmt).date()
            break
        except ValueError:
            pass
    if not parsed_date:
        return None

    for time_fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed_time = datetime.strptime((time_str or "").strip(), time_fmt).time()
            return datetime.combine(parsed_date, parsed_time.replace(second=0, microsecond=0))
        except ValueError:
            pass
    return None


def _format_callback_schedule(schedule: datetime) -> tuple[str, str]:
    return schedule.strftime("%d/%m/%Y"), schedule.strftime("%H:%M")


def _is_callback_office_time(schedule: datetime) -> bool:
    return CALLBACK_OFFICE_START <= schedule.time() <= CALLBACK_OFFICE_END


def _callback_holiday_name(schedule: datetime) -> Optional[str]:
    return CALLBACK_HOLIDAYS_2026.get(schedule.date().isoformat())


def _round_up_callback_slot(schedule: datetime) -> datetime:
    minute = schedule.minute
    extra = minute % CALLBACK_SLOT_MINUTES
    if extra:
        schedule += timedelta(minutes=CALLBACK_SLOT_MINUTES - extra)
    return schedule.replace(second=0, microsecond=0)


def _scheduled_callback_times(exclude_call_id: Optional[str]) -> dict[str, set[str]]:
    occupied: dict[str, set[str]] = {}
    # ponytail: recent 500 records are enough for the current campaign volume; add a date-indexed query if this grows.
    for record in list_call_records(limit=500):
        if record.get("call_id") == exclude_call_id:
            continue
        metadata = record.get("metadata") or {}
        schedule = _parse_callback_schedule(
            str(metadata.get("next_action_date") or ""),
            str(metadata.get("next_action_time") or ""),
        )
        if not schedule:
            continue
        date_key, time_key = _format_callback_schedule(schedule)
        occupied.setdefault(date_key, set()).add(time_key)
    return occupied


def _first_available_callback_slot(
    requested: datetime,
    occupied: dict[str, set[str]],
    *,
    prefer_same_day: bool = False,
) -> Optional[datetime]:
    if prefer_same_day and requested.time() > CALLBACK_OFFICE_END:
        if not _callback_holiday_name(requested):
            same_day = datetime.combine(requested.date(), CALLBACK_OFFICE_END)
            while same_day.time() >= CALLBACK_OFFICE_START:
                date_key, time_key = _format_callback_schedule(same_day)
                if time_key not in occupied.get(date_key, set()):
                    return same_day
                same_day -= timedelta(minutes=CALLBACK_SLOT_MINUTES)
        candidate = datetime.combine(requested.date() + timedelta(days=1), CALLBACK_OFFICE_START)
    elif prefer_same_day or requested.time() < CALLBACK_OFFICE_START:
        candidate = datetime.combine(requested.date(), CALLBACK_OFFICE_START)
    elif requested.time() > CALLBACK_OFFICE_END:
        candidate = datetime.combine(requested.date() + timedelta(days=1), CALLBACK_OFFICE_START)
    else:
        candidate = _round_up_callback_slot(requested + timedelta(minutes=1))

    for day_offset in range(8):
        day = candidate.date() + timedelta(days=day_offset)
        if day.isoformat() in CALLBACK_HOLIDAYS_2026:
            continue
        start = candidate if day_offset == 0 else datetime.combine(day, CALLBACK_OFFICE_START)
        if start.time() < CALLBACK_OFFICE_START:
            start = datetime.combine(day, CALLBACK_OFFICE_START)
        start = _round_up_callback_slot(start)

        while start.time() <= CALLBACK_OFFICE_END:
            date_key, time_key = _format_callback_schedule(start)
            if time_key not in occupied.get(date_key, set()):
                return start
            start += timedelta(minutes=CALLBACK_SLOT_MINUTES)
    return None


def _validate_callback_slot(date_str: str, time_str: str, call_id: Optional[str]) -> tuple[bool, str, str, str]:
    requested = _parse_callback_schedule(date_str, time_str)
    if not requested:
        return False, "", "", "I could not understand that callback date or time. Please ask for a DD/MM/YYYY date and HH:MM IST time."

    occupied = _scheduled_callback_times(call_id)
    requested_date, requested_time = _format_callback_schedule(requested)
    reason = None
    holiday_name = _callback_holiday_name(requested)
    if holiday_name:
        reason = f"an LSA holiday ({holiday_name})"
    elif not _is_callback_office_time(requested):
        reason = "outside our office hours of 10:00 to 18:00 IST"
    elif requested_time in occupied.get(requested_date, set()):
        reason = "already scheduled for another customer"
    else:
        return True, requested_date, requested_time, ""

    suggestion = _first_available_callback_slot(
        requested,
        occupied,
        prefer_same_day=not _is_callback_office_time(requested),
    )
    if not suggestion:
        return False, "", "", f"That callback time is {reason}. Please ask the customer for another time between 10:00 and 18:00 IST."

    suggestion_date, suggestion_time = _format_callback_schedule(suggestion)
    return (
        False,
        "",
        "",
        f"That callback time is {reason}. Politely suggest {suggestion_date} at {suggestion_time} IST instead and ask if that works. Do not schedule it until the customer confirms.",
    )

class CustomerQueryTools(llm.ToolContext):
    """
    LLM ToolContext for outbound campaign actions.

    Legacy Frappe/WhatsApp methods remain callable in Python but are not exposed
    to the model while outbound-call management is the active scope.
    """
    ACTIVE_TOOL_NAMES = {"schedule_human_callback", "schedule_ai_followup", "end_call"}

    def __init__(self, client: Optional[Any] = None, customer_id: Optional[str] = None, phone_number: Optional[str] = None, on_verify_success: Optional[Callable] = None, session: Optional[Any] = None, ctx: Optional[Any] = None, call_id: Optional[str] = None):
        super().__init__(tools=[])
        tool_map = getattr(self, "_fnc_tools_map", None)
        if tool_map is None:
            tool_map = getattr(self, "function_tools", None)
        for tool_name in list(tool_map or {}):
            if tool_name not in self.ACTIVE_TOOL_NAMES:
                tool_map.pop(tool_name, None)
        self.client = client
        self.customer_id = customer_id
        self.phone_number = phone_number
        self.is_verified = False
        self.generated_otp = None
        self.dtmf_buffer = ""
        self.on_verify_success = on_verify_success
        self.session = session
        self.ctx = ctx
        self.call_id = call_id

    @llm.function_tool(description="Get the list and status of sales orders for the current customer.")
    async def get_customer_sales_orders(self, customer_id: Optional[str] = None):
        """
        Args:
            customer_id: Optional customer ID. If not provided, the customer linked to this call will be used.
        """
        target_customer = customer_id or self.customer_id
        if not target_customer:
            return "No customer is linked or provided. Please search for the customer first using search_customer."
        try:
            orders = await asyncio.to_thread(
                self.client.get_resource_list,
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
        target_customer = customer_id or self.customer_id
        if not target_customer:
            return "No customer is linked or provided. Outstanding balance cannot be calculated."
        try:
            invoices = await asyncio.to_thread(
                self.client.get_resource_list,
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
        target_customer = customer_id or self.customer_id
        if not target_customer:
            return "No customer is linked or provided. Please search for the customer first using search_customer."
        try:
            cust = await asyncio.to_thread(self.client.get_resource, "Customer", target_customer)
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
                    contact = await asyncio.to_thread(self.client.get_resource, "Contact", contact_card)
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
        if not sales_order_id:
            return "Please provide a valid Sales Order ID."
        try:
            so = await asyncio.to_thread(self.client.get_resource, "Sales Order", sales_order_id)
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
        if not invoice_id:
            return "Please provide a valid Sales Invoice ID."
        try:
            si = await asyncio.to_thread(self.client.get_resource, "Sales Invoice", invoice_id)
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
            customers = await asyncio.to_thread(
                self.client.get_resource_list,
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

    @llm.function_tool(description="Send a 4-digit verification OTP to the customer's WhatsApp. Call this when the customer asks to receive information (text details or PDF documents) via WhatsApp.")
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
                cust = await asyncio.to_thread(self.client.get_resource, "Customer", target_customer)
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
            res = await asyncio.to_thread(self.client.send_whatsapp_message, mobile_number=last_10, message=message)
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

    @llm.function_tool(description="Send a PDF of a specific Sales Order or Sales Invoice via WhatsApp. WhatsApp OTP verification is required first.")
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
            doc = await asyncio.to_thread(self.client.get_resource, doctype, docname)
            if not doc:
                return f"{doctype} '{docname}' does not exist in the database."

            if self.customer_id and doc.get("customer") != self.customer_id:
                return f"{doctype} '{docname}' does not belong to the linked customer."

            recipient_phone = self.phone_number
            if not recipient_phone:
                cust = await asyncio.to_thread(self.client.get_resource, "Customer", doc.get("customer"))
                recipient_phone = cust.get("mobile_no") or cust.get("custom_primary_mobile_no") or cust.get("custom_alt_mobile_no")

            if not recipient_phone:
                return "Could not determine a WhatsApp phone number for the customer."

            cleaned_phone = "".join(c for c in recipient_phone if c.isdigit())
            if len(cleaned_phone) < 10:
                return f"Invalid phone number '{recipient_phone}' for customer. Cannot send PDF."
            last_10 = cleaned_phone[-10:]

            # Resolve print format over REST
            custom_formats_res = await asyncio.to_thread(
                self.client.get_resource_list,
                "Print Format",
                filters=[["doc_type", "=", doctype], ["disabled", "=", 0]],
                fields=["name"]
            )
            custom_formats = [f.get("name") for f in custom_formats_res]
            
            if doctype == "Sales Order":
                print_format = "Sales Order Format"
                if "Sales Order Format" in custom_formats:
                    pe_list = await asyncio.to_thread(
                        self.client.get_resource_list,
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

            res = await asyncio.to_thread(self.client.send_whatsapp_message_with_file, mobile_number=last_10, message=message, file_link=pdf_link)
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
                    await asyncio.to_thread(self.client._post, "api/resource/WhatsApp Message Log", json_data=log_data)
                except Exception as log_err:
                    logger.debug(f"Could not create remote WhatsApp Message Log (probably vanilla site): {log_err}")

                return f"{doctype} PDF has been successfully sent to WhatsApp number {last_10}."
            else:
                return f"Failed to send PDF via WhatsApp: {res.get('msg')}."
        except Exception as e:
            logger.error(f"Error in send_pdf_whatsapp: {e}")
            return f"Error sending PDF: {str(e)}"

    @llm.function_tool(description="Send a text message with requested customer details (such as order ID, invoice amount, customer name, order status, etc.) to the customer's WhatsApp. Use this when the customer explicitly asks to receive information via WhatsApp instead of hearing it on the call. Look up the details using the appropriate tools first, then pass a clear formatted message. WhatsApp OTP verification is required first.")
    async def send_text_whatsapp(self, message: str, customer_id: Optional[str] = None):
        """
        Args:
            message: The text content to send on WhatsApp. Should be a clear, readable summary of the requested details.
            customer_id: Optional customer ID to resolve the WhatsApp number. If not provided, the customer linked to this call will be used.
        """
        if not self.is_verified:
            return "Verification required. Please send a verification OTP first by calling send_verification_otp."
        if not message or not message.strip():
            return "Please provide the text message to send on WhatsApp."

        target_customer = customer_id or self.customer_id
        recipient_phone = self.phone_number
        if not recipient_phone and target_customer:
            try:
                cust = await asyncio.to_thread(self.client.get_resource, "Customer", target_customer)
                recipient_phone = cust.get("mobile_no") or cust.get("custom_primary_mobile_no") or cust.get("custom_alt_mobile_no")
            except Exception as e:
                logger.error(f"Error fetching customer phone number over REST: {e}")

        if not recipient_phone:
            return "Could not determine a WhatsApp phone number for the customer."

        cleaned_phone = "".join(c for c in recipient_phone if c.isdigit())
        if len(cleaned_phone) < 10:
            return f"Invalid phone number '{recipient_phone}' for customer. Cannot send WhatsApp message."
        last_10 = cleaned_phone[-10:]

        try:
            res = await asyncio.to_thread(self.client.send_whatsapp_message, mobile_number=last_10, message=message.strip())
            if res.get("status"):
                logger.info(f"WhatsApp text message successfully sent to {last_10}.")
                return f"The message has been successfully sent to WhatsApp number {last_10}."
            return f"Failed to send WhatsApp message: {res.get('msg')}."
        except Exception as e:
            logger.error(f"Error in send_text_whatsapp: {e}")
            return f"Error sending WhatsApp message: {str(e)}"

    @llm.function_tool(description="Ends the current call immediately. Call this when the conversation is finished or the user wants to end the call.")
    async def end_call(self):
        """
        """
        logger.info("Custom end_call tool executed.")
        ctx = getattr(self, "ctx", None)
        session = getattr(self, "session", None)
        if ctx:
            async def perform_shutdown():
                await _wait_for_end_call_speech(session)
                try:
                    logger.info("Custom end_call: Deleting room and shutting down job...")
                    await ctx.delete_room()
                    ctx.shutdown(reason="Agent ended call")
                except Exception as e:
                    logger.warning(f"Error during custom end_call shutdown: {e}")
            
            asyncio.create_task(perform_shutdown())
            return "Call is ending. Politely say goodbye to the user now in natural, simple spoken language (e.g., 'Thank you, bye' or 'Theek hai, thank you')."
        elif session:
            async def perform_session_shutdown():
                await _wait_for_end_call_speech(session)
                try:
                    logger.info("Custom end_call: Shutting down agent session...")
                    session.shutdown()
                except Exception as e:
                    logger.warning(f"Error during session shutdown: {e}")
            
            asyncio.create_task(perform_session_shutdown())
            return "Call is ending. Politely say goodbye to the user now in natural, simple spoken language (e.g., 'Thank you, bye' or 'Theek hai, thank you')."
            
        return "Failed to end call: context not available."

    @llm.function_tool(description="Schedule a follow-up callback with a human representative. Use DD/MM/YYYY and HH:MM IST, or time_str like '+5 minutes' for requests such as 'call me after 5 minutes'; relative minutes are resolved in IST. Call once with confirmed=false to check the slot and ask the customer to confirm; only call with confirmed=true after the customer clearly agrees. Office hours are 10:00 to 18:00 IST.")
    async def schedule_human_callback(self, date_str: str, time_str: str, client_notes: Optional[str] = None, confirmed: bool = False):
        """
        Args:
            date_str: Date for the callback in DD/MM/YYYY format, or 'today' when time_str is a relative offset.
            time_str: Time in 24-hour IST format, or relative IST offset like '+5 minutes'.
            client_notes: Optional notes or context about why the callback is scheduled.
            confirmed: Set true only after the customer has confirmed the exact date and time.
        """
        logger.info(f"Scheduling human callback on {date_str} at {time_str}. Notes: {client_notes}")
        if self.call_id:
            try:
                allowed, date_str, time_str, message = _validate_callback_slot(date_str, time_str, self.call_id)
                if not allowed:
                    return message
                if not confirmed:
                    return f"Please confirm with the customer: callback on {date_str} at {time_str} IST. Do not schedule it until they clearly agree."
                record = get_call_record(self.call_id)
                metadata = record.get("metadata") or {} if record else {}
                metadata["next_action"] = "Human"
                metadata["next_action_date"] = date_str
                metadata["next_action_time"] = time_str
                if client_notes:
                    metadata["client_comment"] = client_notes
                    metadata["help_needed_notes"] = client_notes
                update_call_record(
                    self.call_id,
                    metadata=metadata,
                    event_message=f"Scheduled human callback on {date_str} at {time_str}"
                )
                logger.info(f"Successfully saved human callback in PostgreSQL metadata for {self.call_id}")
                return f"Human callback successfully scheduled for {date_str} at {time_str}."
            except Exception as e:
                logger.error(f"Failed to update callback in PostgreSQL for {self.call_id}: {e}")
                return f"Error scheduling callback in database: {str(e)}."
        return "Failed to schedule callback: Call ID not available."

    @llm.function_tool(description="Schedule the next AI follow-up call when the customer is not ready yet and does not need human help. Use DD/MM/YYYY and HH:MM IST, or time_str like '+5 minutes' for requests such as 'call me after 5 minutes'; relative minutes are resolved in IST. Call once with confirmed=false to check the slot and ask the customer to confirm; only call with confirmed=true after the customer clearly agrees. Office hours are 10:00 to 18:00 IST.")
    async def schedule_ai_followup(self, date_str: str, time_str: str, client_notes: Optional[str] = None, confirmed: bool = False):
        """
        Args:
            date_str: Date for the next AI call in DD/MM/YYYY format, or 'today' when time_str is a relative offset.
            time_str: Time in 24-hour IST format, or relative IST offset like '+5 minutes'.
            client_notes: Optional notes or context about why AI should call again later.
            confirmed: Set true only after the customer has confirmed the exact date and time.
        """
        logger.info(f"Scheduling AI follow-up on {date_str} at {time_str}. Notes: {client_notes}")
        if self.call_id:
            try:
                allowed, date_str, time_str, message = _validate_callback_slot(date_str, time_str, self.call_id)
                if not allowed:
                    return message
                if not confirmed:
                    return f"Please confirm with the customer: AI follow-up on {date_str} at {time_str} IST. Do not schedule it until they clearly agree."
                record = get_call_record(self.call_id)
                metadata = record.get("metadata") or {} if record else {}
                metadata["next_action"] = "AI Call"
                metadata["next_action_date"] = date_str
                metadata["next_action_time"] = time_str
                if client_notes:
                    metadata["client_comment"] = client_notes
                update_call_record(
                    self.call_id,
                    metadata=metadata,
                    event_message=f"Scheduled AI follow-up on {date_str} at {time_str}"
                )
                logger.info(f"Successfully saved AI follow-up in PostgreSQL metadata for {self.call_id}")
                return f"AI follow-up successfully scheduled for {date_str} at {time_str}."
            except Exception as e:
                logger.error(f"Failed to update AI follow-up in PostgreSQL for {self.call_id}: {e}")
                return f"Error scheduling AI follow-up in database: {str(e)}."
        return "Failed to schedule AI follow-up: Call ID not available."
