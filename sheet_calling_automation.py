# -*- coding: utf-8 -*-
import os
import re
import logging
import asyncio
import requests
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from gspread.utils import rowcol_to_a1

# Load env
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sheet-automation")

STOP_FLAG = "agent_stop.flag"
DEFAULT_MAX_AI_ATTEMPTS = 3

SHEET1_WORKFLOW_HEADERS = [
    "Campaign Type",
    "Workflow Status",
    "AI Enabled",
    "Assigned To",
    "Human Status",
    "Help Needed Notes",
    "Last Call Outcome",
    "Next AI Call Date",
    "Next AI Call Time",
    "AI Attempt Count",
    "Max AI Attempts",
]

SHEET2_EXTRA_HEADERS = [
    "Actor",
    "Call ID",
    "Call Outcome",
    "Help Needed Notes",
    "Assigned To",
    "WhatsApp Received",
    "Promised Date",
    "Delivery Mode",
    "Help/Issue",
    "Callback Time",
]

def check_stop_requested() -> bool:
    if os.path.exists(STOP_FLAG):
        logger.info("Stop requested via flag file. Aborting loop.")
        return True
    return False

def safe_get_all_records(worksheet) -> list[dict]:
    """Safely fetch all records from a worksheet even if there are duplicate or empty header columns."""
    try:
        all_values = worksheet.get_all_values()
    except Exception as e:
        logger.error(f"Error fetching worksheet values: {e}")
        return []

    if not all_values:
        return []

    raw_headers = [str(h).strip() for h in all_values[0]]
    headers = []
    seen = {}
    for idx, h in enumerate(raw_headers):
        header_name = h if h else f"col_{idx+1}"
        if header_name in seen:
            seen[header_name] += 1
            headers.append(f"{header_name}_{seen[header_name]}")
        else:
            seen[header_name] = 1
            headers.append(header_name)

    records = []
    for row in all_values[1:]:
        record = {}
        for idx, header_name in enumerate(headers):
            record[header_name] = row[idx] if idx < len(row) else ""
        records.append(record)
    return records


def _headers(sheet) -> list[str]:
    return [str(header).strip() for header in sheet.row_values(1)]

def _header_index(headers: list[str]) -> dict[str, int]:
    return {header: idx + 1 for idx, header in enumerate(headers) if header}

def _int_value(value, default: int = 0) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _update_row_by_header(sheet, row_index: int, updates: dict[str, object], headers: list[str] | None = None) -> None:
    cols = _header_index(headers or _headers(sheet))
    data = [
        {"range": rowcol_to_a1(row_index, cols[header]), "values": [[value]]}
        for header, value in updates.items()
        if header in cols
    ]
    if data:
        sheet.batch_update(data)

def _format_schedule(dt: datetime) -> tuple[str, str]:
    return dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M")

def _next_retry_datetime(reason: str | None, now: datetime) -> datetime:
    if reason in {"agent_ready_timeout", "dispatch_error"}:
        return now + timedelta(minutes=10)
    if reason == "busy":
        return now + timedelta(hours=1)
    if reason in {"no_answer", "unreachable"}:
        return now + timedelta(days=1)
    return now + timedelta(days=1)

def _is_human_handoff(next_action: str) -> bool:
    return next_action.strip().lower() == "human"

def _normalize_campaign_type(value) -> str | None:
    normalized = re.sub(r"[\s_-]+", " ", str(value or "gst").strip().lower())
    return {
        "gst": "gst",
        "income tax": "itr",
        "income tax return": "itr",
        "itr": "itr",
    }.get(normalized)

SPREADSHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
REQUIRED_SHEET1_HEADERS = ["CID", "Data Received Status"]


def parse_spreadsheet_id(url_or_id: str) -> str:
    """Extract a spreadsheet ID from a full Google Sheets URL, or pass through a raw ID."""
    value = (url_or_id or "").strip()
    match = SPREADSHEET_ID_RE.search(value)
    if match:
        return match.group(1)
    return value


def validate_spreadsheet(spreadsheet_id: str) -> dict:
    """Open the sheet with the configured service-account creds and check it has the
    columns the automation loop needs. Returns {"ok", "errors", "warnings"}."""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        client, _ = get_google_sheets_client(spreadsheet_id_override=spreadsheet_id)
        sheet = client.open_by_key(spreadsheet_id)
    except Exception as e:
        return {"ok": False, "errors": [f"Could not open spreadsheet: {e}"], "warnings": []}

    worksheets = sheet.worksheets()
    if len(worksheets) < 2:
        errors.append("Spreadsheet must have at least two sheets (client list and call log).")
        return {"ok": False, "errors": errors, "warnings": warnings}

    sheet1_headers = _headers(worksheets[0])
    sheet2_headers = _headers(worksheets[1])

    missing_required = [h for h in REQUIRED_SHEET1_HEADERS if h not in sheet1_headers]
    if missing_required:
        errors.append(f"Sheet1 is missing required column(s): {', '.join(missing_required)}")

    missing_workflow = [h for h in SHEET1_WORKFLOW_HEADERS if h not in sheet1_headers]
    if missing_workflow:
        warnings.append(f"Sheet1 is missing workflow column(s): {', '.join(missing_workflow)}")

    missing_extra = [h for h in SHEET2_EXTRA_HEADERS if h not in sheet2_headers]
    if missing_extra:
        warnings.append(f"Sheet2 is missing column(s): {', '.join(missing_extra)}")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _within_calling_window(now: datetime) -> tuple[bool, str]:
    """Check the configured IST calling-time window. ``now`` must be a naive
    IST datetime. Returns (allowed, reason)."""
    from call_status_store import get_setting

    if not get_setting("calling_window_enabled", False):
        return True, "calling window not enabled"

    start_str = get_setting("calling_window_start") or "00:00"
    end_str = get_setting("calling_window_end") or "23:59"
    try:
        start_time = datetime.strptime(start_str, "%H:%M").time()
        end_time = datetime.strptime(end_str, "%H:%M").time()
    except ValueError:
        return True, "calling window misconfigured, ignoring"

    current_time = now.time()
    if start_time <= current_time <= end_time:
        return True, f"within window {start_str}-{end_str} IST"
    return False, f"outside window {start_str}-{end_str} IST (now {current_time.strftime('%H:%M')} IST)"


def parse_google_creds_info(raw_input: str) -> dict:
    """Parse Google Service Account JSON or Base64 credentials string safely into a dict."""
    if not raw_input or not isinstance(raw_input, str):
        raise ValueError("Credentials string is empty or invalid")

    import json
    import base64

    cleaned = raw_input.strip().strip("'\"")
    info = None

    # Strategy 1: Try base64 decode with padding, then json.loads(strict=False)
    try:
        padded = cleaned + '=' * (-len(cleaned) % 4)
        decoded = base64.b64decode(padded).decode('utf-8', errors='ignore')
        info = json.loads(decoded, strict=False)
    except Exception:
        pass

    # Strategy 2: Try direct json.loads(strict=False)
    if not info or not isinstance(info, dict):
        try:
            info = json.loads(cleaned, strict=False)
        except Exception:
            pass

    # Strategy 3: Try replacing escaped newlines then json.loads(strict=False)
    if not info or not isinstance(info, dict):
        try:
            info = json.loads(cleaned.replace('\\n', '\n'), strict=False)
        except Exception:
            pass

    if not info or not isinstance(info, dict):
        raise ValueError("Invalid JSON dict or Base64 encoding for Google Service Account credentials")

    if "private_key" in info and isinstance(info["private_key"], str):
        info["private_key"] = info["private_key"].replace("\\n", "\n")

    return info


def get_google_sheets_client(spreadsheet_id_override: str | None = None):
    from call_status_store import get_setting
    creds_path = os.environ.get("GOOGLE_SHEETS_CREDS_PATH", ".google_sheets_creds.json")
    creds_json = get_setting("google_sheets_creds_json") or os.environ.get("GOOGLE_SHEETS_CREDS_JSON")

    if spreadsheet_id_override:
        spreadsheet_id = spreadsheet_id_override
    else:
        spreadsheet_id = get_setting("spreadsheet_id") or os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID")

    if not spreadsheet_id:
        raise ValueError("No spreadsheet configured. Set it from the dashboard settings or GOOGLE_SHEETS_SPREADSHEET_ID.")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    
    if creds_json and creds_json.strip().strip("'\""):
        try:
            info = parse_google_creds_info(creds_json)
            creds = Credentials.from_service_account_info(info, scopes=scopes)
        except Exception as e:
            raise ValueError(f"Failed to create Google credentials from database or environment setting: {e}")
    else:
        if not os.path.exists(creds_path):
            raise FileNotFoundError(f"Google credentials not configured. Please save credentials in Dashboard Settings or set GOOGLE_SHEETS_CREDS_JSON / {creds_path}")
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        
    client = gspread.authorize(creds)
    return client, spreadsheet_id

def parse_schedule(date_str: str, time_str: str) -> datetime:
    """Parse date (DD/MM/YYYY) and time (HH:MM) safely, returning a local naive datetime."""
    date_str = str(date_str).strip()
    time_str = str(time_str).strip()
    
    # Try different date formats
    for date_fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            # Parse date part
            parsed_date = datetime.strptime(date_str, date_fmt)
            break
        except ValueError:
            continue
    else:
        # Fallback to epoch if parsing fails
        return datetime.min

    # Try different time formats
    for time_fmt in ("%H:%M", "%I:%M %p", "%H:%M:%S"):
        try:
            parsed_time = datetime.strptime(time_str, time_fmt).time()
            break
        except ValueError:
            continue
    else:
        # Default to start of day
        parsed_time = datetime.min.time()
        
    return datetime.combine(parsed_date.date(), parsed_time)

async def trigger_outbound_call(row: dict) -> bool:
    """Post call request to call_api.py."""
    call_api_url = os.environ.get("LIVEKIT_CALL_API_URL", "http://127.0.0.1:8000").rstrip("/")
    call_api_token = os.environ.get("CALL_API_TOKEN", "testsecret")
    
    mobile = str(row.get("Mobile Number") or "").strip()
    if not mobile:
        logger.warning(f"Row for CID {row.get('CID')} lacks a phone number.")
        return False

    campaign_type = _normalize_campaign_type(row.get("Campaign Type"))
    if not campaign_type:
        logger.error(
            "Row for CID %s has unsupported Campaign Type %r; use GST or ITR.",
            row.get("CID"),
            row.get("Campaign Type"),
        )
        return False

    default_purpose = (
        "Income Tax Return document collection"
        if campaign_type == "itr"
        else "GST filing documents collection"
    )
        
    payload = {
        "phone_number": mobile,
        "purpose": str(row.get("Purpose/Prompt") or default_purpose),
        "agent_type": "support",
        "customer_name": str(row.get("Owner name") or ""),
        "company_name": str(row.get("Company Name") or ""),
        "contact_person": str(row.get("Contact person") or ""),
        "gender": str(row.get("Gender") or ""),
        "requested_by": "sheets_automation",
        "metadata": {
            "cid": str(row.get("CID")),
            "source": "sheets_automation",
            "campaign_type": campaign_type,
        }
    }
    language = str(row.get("Language") or "").strip()
    if language:
        payload["metadata"]["preferred_language"] = language
    
    headers = {
        "Authorization": f"Bearer {call_api_token}",
        "Content-Type": "application/json"
    }
    
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{call_api_url}/calls", json=payload, headers=headers, timeout=90.0)
            if response.status_code == 200:
                res_data = response.json()
                logger.info(f"Successfully triggered outbound call for {mobile}. Call ID: {res_data.get('call_id')}")
                return True
            else:
                logger.error(f"Failed to trigger call. HTTP {response.status_code}: {response.text}")
                return False
    except Exception as e:
        logger.error(f"Connection error to Call API: {e}")
        return False

def _clean_api_url() -> str:
    url = (os.environ.get("LIVEKIT_CALL_API_URL") or "").strip().rstrip("/")
    if not url or "127.0.0.1" in url or "localhost" in url:
        return "https://livekit-agent-ur38zy-3ba7e4-173-212-216-156.sslip.io"
    return url


def sync_completed_calls_to_sheets(sheet):
    """Scan the call status store for ended, unsynced calls and append logs to Sheet 2."""
    from call_status_store import list_completed_call_records, update_call_record
    
    # 1. Fetch completed calls from the configured PostgreSQL status store
    try:
        calls = list_completed_call_records()
    except Exception as err:
        logger.error(f"Failed to read from call status store: {err}")
        return

    # Backfill recording links for calls already synced to Sheet 2 but missing recording URLs
    sheet2 = sheet.get_worksheet(1)
    try:
        sheet2_rows = safe_get_all_records(sheet2)
    except Exception as e:
        logger.error(f"Failed to read Sheet 2 for recording backfill: {e}")
        sheet2_rows = []

    if sheet2_rows:
        sheet2_headers = _headers(sheet2)
        header_to_idx = {h.strip(): idx for idx, h in enumerate(sheet2_headers)}
        call_id_col = header_to_idx.get("Call ID")
        rec_col = header_to_idx.get("Recording")
        transcript_col = header_to_idx.get("Trasncript") or header_to_idx.get("Transcript")
        
        if call_id_col is not None and rec_col is not None:
            sheet2_call_map = {}
            for idx, r in enumerate(sheet2_rows, start=2):
                c_id = str(r.get("Call ID") or "").strip()
                c_rec = str(r.get("Recording") or "").strip()
                c_transcript = str(r.get("Trasncript") or r.get("Transcript") or "").strip()
                if c_id and (not c_rec or "127.0.0.1" in c_rec or "localhost" in c_rec):
                    sheet2_call_map[c_id] = (idx, c_transcript)
            
            if sheet2_call_map:
                logger.info(f"Checking {len(sheet2_call_map)} logged calls in Sheet 2 for missing recordings...")
                for c in calls:
                    call_id = c["call_id"]
                    if call_id in sheet2_call_map:
                        row_idx, current_transcript = sheet2_call_map[call_id]
                        recording_url = c.get("recording_url") or ""
                        
                        # Dynamic Vobiz lookup fallback if DB has no recording URL for completed calls
                        if not recording_url and c.get("status") == "completed":
                            from vobiz_client import find_recording_for_call
                            try:
                                logger.info(f"Querying Vobiz API directly for missing recording for completed call {call_id}")
                                result = find_recording_for_call(c)
                                if result and result.get("recording_url"):
                                    recording_url = result["recording_url"]
                                    update_call_record(
                                        call_id,
                                        vobiz_call_uuid=result.get("vobiz_call_uuid") or None,
                                        vobiz_recording_id=result.get("vobiz_recording_id") or None,
                                        recording_source="vobiz",
                                        recording_url=recording_url,
                                        recording_duration_ms=result.get("recording_duration_ms"),
                                        recording_format=result.get("recording_format"),
                                        recording_type=result.get("recording_type"),
                                        event_message="Vobiz recording dynamically matched and backfilled during sync loop",
                                        event_details=result,
                                    )
                                    logger.info(f"Dynamically updated DB with recording for call {call_id}")
                            except Exception as ex:
                                logger.error(f"Failed to query Vobiz for recording backfill for {call_id}: {ex}")

                        if recording_url:
                            api_url = _clean_api_url()
                            public_rec_url = f"{api_url}/calls/{call_id}/recording"
                            logger.info(f"Backfilling recording URL for {call_id} in Sheet 2 row {row_idx}: {public_rec_url}")
                            try:
                                sheet2.update_cell(row_idx, rec_col + 1, public_rec_url)
                                db_transcript = c.get("transcript_text") or ""
                                if transcript_col is not None and not current_transcript and db_transcript:
                                    logger.info(f"Backfilling transcript for {call_id} in Sheet 2 row {row_idx}")
                                    sheet2.update_cell(row_idx, transcript_col + 1, db_transcript)
                            except Exception as ex:
                                logger.error(f"Failed to update Sheet 2 row {row_idx} for call {call_id}: {ex}")
    
    # Filter for unsynced calls initiated by sheets automation
    unsynced_calls = []
    for c in calls:
        meta_dict = c.get("metadata") or {}
            
        if meta_dict.get("source") == "sheets_automation" and meta_dict.get("cid") and not meta_dict.get("synced"):
            c["parsed_metadata"] = meta_dict
            unsynced_calls.append(c)

    if not unsynced_calls:
        logger.info("No new completed call records to sync to Google Sheets.")
        return

    logger.info(f"Found {len(unsynced_calls)} call records to sync back to sheets.")
    
    sheet1 = sheet.get_worksheet(0)
    
    # Pre-cache Sheet 1 records for looking up row index of matching CID
    sheet1_headers = _headers(sheet1)
    sheet1_rows = safe_get_all_records(sheet1)
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist).replace(tzinfo=None)
    
    for c in unsynced_calls:
        if check_stop_requested():
            break
            
        cid = c["parsed_metadata"]["cid"]
        call_id = c["call_id"]
        
        status = str(c.get("status") or "")
        reason = c.get("reason") or ""
        system_failure_no_attempt = status == "dispatch_failed" and reason in {
            "agent_ready_timeout",
            "dispatch_error",
        }
        matching_row_index = -1
        current_count = 0
        current_ai_attempt_count = 0
        max_attempts = DEFAULT_MAX_AI_ATTEMPTS
        current_assignee = ""
        matching_row = None
        for idx, r in enumerate(sheet1_rows):
            if str(r.get("CID")) == str(cid):
                matching_row_index = idx + 2
                matching_row = r
                current_count = _int_value(r.get("Count"), 0)
                current_ai_attempt_count = _int_value(r.get("AI Attempt Count"), current_count)
                max_attempts = _int_value(r.get("Max AI Attempts"), DEFAULT_MAX_AI_ATTEMPTS)
                current_assignee = str(r.get("Assigned To") or "")
                break

        ai_attempt_count = current_ai_attempt_count if system_failure_no_attempt else current_ai_attempt_count + 1
        next_action = str(c["parsed_metadata"].get("next_action") or "").strip()
        next_action_date = c["parsed_metadata"].get("next_action_date", "")
        next_action_time = c["parsed_metadata"].get("next_action_time", "")
        client_comment = c["parsed_metadata"].get("client_comment", "")
        help_needed_notes = c["parsed_metadata"].get("help_needed_notes", "")
        internal_error = str(c.get("error") or "").strip()
        
        # Fallback text if call failed without speaking
        if not client_comment and system_failure_no_attempt:
            detail = internal_error or reason or status
            client_comment = f"System error before dialing: {detail}. No customer call attempt counted."
        elif not client_comment and status.startswith("failed"):
            client_comment = f"Call failed: {reason or 'unknown reason'}"
        elif not client_comment:
            client_comment = "Call completed."

        retryable_failure = status in {"failed_busy", "failed_no_answer", "failed_unreachable"}
        if not next_action:
            if system_failure_no_attempt:
                next_action = "AI Call"
            elif retryable_failure and ai_attempt_count < max_attempts:
                next_action = "AI Call"
            elif status.startswith("failed") and ai_attempt_count >= max_attempts:
                next_action = "Human"
                help_needed_notes = help_needed_notes or f"Max AI attempts reached after {reason or status}."
            elif status.startswith("failed"):
                next_action = "Human"
                help_needed_notes = help_needed_notes or client_comment
            else:
                next_action = "AI Call"

        if next_action == "AI Call" and (not next_action_date or not next_action_time):
            retry_at = _next_retry_datetime(reason if status.startswith("failed") or system_failure_no_attempt else None, now)
            next_action_date, next_action_time = _format_schedule(retry_at)

        if _is_human_handoff(next_action):
            help_needed_notes = help_needed_notes or client_comment

        # Fetch recording and transcript from the call status store
        recording_url = c.get("recording_url") or ""
        if recording_url:
            api_url = _clean_api_url()
            recording_url = f"{api_url}/calls/{call_id}/recording"
            
        transcript = c.get("transcript_text") or ""
        call_time = c.get("dispatched_at") or c.get("created_at") or ""
        
        # Format call_time to human readable in IST
        try:
            parsed_time = datetime.fromisoformat(call_time.replace("Z", "+00:00"))
            ist = timezone(timedelta(hours=5, minutes=30))
            parsed_time = parsed_time.astimezone(ist)
            formatted_call_time = parsed_time.strftime("%m/%d/%Y %H:%M:%S")
        except Exception:
            formatted_call_time = call_time

        # Get contact person name from call metadata or Sheet 1 matching row
        contact_person = c["parsed_metadata"].get("contact_person") or ""
        if not contact_person and matching_row is not None:
            contact_person = matching_row.get("Contact person") or ""

        new_row = [
            client_comment,       # Client Comment
            next_action,          # Next Action
            next_action_date,     # Next Action Date
            next_action_time,     # Next Action Time
            cid,                  # CID
            contact_person,       # Contact person
            formatted_call_time,  # Datetime
            recording_url,        # Recording
            transcript,           # Trasncript (with typo)
            "AI",                 # Actor
            call_id,              # Call ID
            status,               # Call Outcome
            help_needed_notes,    # Help Needed Notes
            current_assignee,     # Assigned To
            c["parsed_metadata"].get("whatsapp_receipt_status", ""),  # WhatsApp Received
            c["parsed_metadata"].get("promised_date", ""),           # Promised Date
            c["parsed_metadata"].get("delivery_mode", ""),           # Delivery Mode
            c["parsed_metadata"].get("issue_help_required", ""),     # Help/Issue
            c["parsed_metadata"].get("callback_time", ""),           # Callback Time
        ]
        
        try:
            # Append log to Sheet 2
            sheet2.append_row(new_row)
            logger.info(f"Appended call log to Sheet 2 for CID {cid}")

            if matching_row_index != -1:
                updates = {
                    "Last Comment": client_comment,
                    "Count": current_count if system_failure_no_attempt else current_count + 1,
                    "AI Attempt Count": ai_attempt_count,
                    "Last Call Outcome": reason or status,
                    "Next AI Call Date": next_action_date if next_action == "AI Call" else "",
                    "Next AI Call Time": next_action_time if next_action == "AI Call" else "",
                }
                if _is_human_handoff(next_action):
                    updates.update({
                        "Workflow Status": "Human Help Needed",
                        "AI Enabled": "No",
                        "Human Status": "Open",
                        "Help Needed Notes": help_needed_notes,
                    })
                elif next_action == "AI Call":
                    updates.update({
                        "Workflow Status": "AI Scheduled",
                        "AI Enabled": "Yes",
                    })
                elif next_action in {"Documents Received", "Closed", "Close"}:
                    updates.update({
                        "Workflow Status": "Documents Received" if next_action == "Documents Received" else "Closed",
                        "AI Enabled": "No",
                    })
                _update_row_by_header(sheet1, matching_row_index, updates, sheet1_headers)
                if matching_row is not None:
                    matching_row.update(updates)
                logger.info(f"Updated Sheet 1 row {matching_row_index} for CID {cid}")

            # Mark the persisted call as synced
            meta_dict = c["parsed_metadata"]
            meta_dict["synced"] = True
            update_call_record(call_id, metadata=meta_dict)
            logger.info(f"Marked call {call_id} as synced in the call status store")
            
        except Exception as e:
            logger.error(f"Failed to sync call {call_id} to Google Sheets: {e}")
            break

async def run_sheets_automation(ignore_schedule: bool = False):
    """Main execution loop for Google Sheets automated dialing & log syncing."""
    logger.info("Starting Google Sheets Automation cycle...")
    
    try:
        if os.path.exists("agent_error.log"):
            os.remove("agent_error.log")
    except Exception:
        pass

    if check_stop_requested():
        return
        
    try:
        client, spreadsheet_id = get_google_sheets_client()
        sheet = client.open_by_key(spreadsheet_id)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Failed to connect to Google Sheets: {e}\n{tb}")
        try:
            with open("agent_error.log", "w") as f:
                f.write(f"Google Sheets Connection Error: {str(e)}")
        except Exception:
            pass
        return

    # First, run the post-call synchronization to sync completed calls back to sheets
    sync_completed_calls_to_sheets(sheet)
    
    if check_stop_requested():
        return

    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist).replace(tzinfo=None)
    window_ok, window_reason = _within_calling_window(now_ist)
    if not window_ok:
        logger.info(f"Skipping new AI call placement: {window_reason}")
        return

    # Second, scan Sheet 1 for new calls to place
    sheet1 = sheet.get_worksheet(0)
    sheet2 = sheet.get_worksheet(1)
    
    try:
        sheet1_rows = safe_get_all_records(sheet1)
        sheet2_rows = safe_get_all_records(sheet2)
    except Exception as e:
        logger.error(f"Failed to fetch sheets data: {e}")
        try:
            with open("agent_error.log", "w") as f:
                f.write(f"Google Sheets Data Fetch Error: {str(e)}")
        except Exception:
            pass
        return

    logger.info(f"Fetched {len(sheet1_rows)} clients and {len(sheet2_rows)} logs.")

    now = now_ist

    for row in sheet1_rows:
        if check_stop_requested():
            break
            
        cid = str(row.get("CID") or "").strip()
        status = str(row.get("Data Received Status") or "").strip().lower()
        workflow_status = str(row.get("Workflow Status") or "").strip().lower()
        ai_enabled = str(row.get("AI Enabled") or "Yes").strip().lower()
        ai_attempt_count = _int_value(row.get("AI Attempt Count"), _int_value(row.get("Count"), 0))
        max_attempts = _int_value(row.get("Max AI Attempts"), DEFAULT_MAX_AI_ATTEMPTS)
        
        if not cid:
            continue
            
        if status != "pending":
            continue

        if ai_enabled in {"no", "false", "0"}:
            logger.info(f"Client CID {cid} has AI disabled. Skipping.")
            continue

        if workflow_status in {"human help needed", "documents received", "closed", "do not call"}:
            logger.info(f"Client CID {cid} workflow status is '{workflow_status}'. Skipping AI call.")
            continue

        if ai_attempt_count >= max_attempts:
            logger.info(f"Client CID {cid} reached max AI attempts ({max_attempts}). Skipping.")
            continue
            
        # Find all call logs in Sheet 2 for this client CID
        client_logs = [log for log in sheet2_rows if str(log.get("CID")).strip() == cid]
        
        should_call = False
        row_next_date = row.get("Next AI Call Date")
        row_next_time = row.get("Next AI Call Time")
        
        if row_next_date and row_next_time:
            scheduled_time = parse_schedule(row_next_date, row_next_time)
            if ignore_schedule or scheduled_time <= now:
                logger.info(f"Client CID {cid} has Sheet 1 AI call due at {scheduled_time} (or ignore_schedule=True). Triggering call.")
                should_call = True
            else:
                logger.info(f"Client CID {cid} has Sheet 1 AI call in future at {scheduled_time}. Skipping.")
        elif not client_logs:
            logger.info(f"Client CID {cid} has never been called. Triggering initial call.")
            should_call = True
        else:
            latest_log = client_logs[-1]
            next_action = str(latest_log.get("Next Action") or "").strip()
            
            if next_action == "AI Call":
                next_action_date = latest_log.get("Next Action Date (DD/MMYYYY)")
                next_action_time = latest_log.get("Next Action Time (IST)")
                
                if next_action_date and next_action_time:
                    scheduled_time = parse_schedule(next_action_date, next_action_time)
                    if ignore_schedule or scheduled_time <= now:
                        logger.info(f"Client CID {cid} has scheduled AI Call due at {scheduled_time} (or ignore_schedule=True). Triggering call.")
                        should_call = True
                    else:
                        logger.info(f"Client CID {cid} has scheduled AI Call in future at {scheduled_time}. Skipping.")
                else:
                    logger.info(f"Client CID {cid} has scheduled AI Call but missing date/time. Triggering call.")
                    should_call = True
            elif next_action == "Human":
                logger.info(f"Client CID {cid} is scheduled for a Human callback. Skipping AI Call.")
            else:
                logger.info(f"Client CID {cid} latest action is '{next_action}'. Skipping.")
                
        if should_call:
            # Limit to single active call globally
            from call_api import get_active_calls
            active = get_active_calls()
            if len(active) > 0:
                logger.info("An active call is already in progress. Skipping further dials this cycle.")
                break
                
            success = await trigger_outbound_call(row)
            if success:
                await asyncio.sleep(3.0)

    logger.info("Google Sheets Automation cycle finished.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Google Sheets Calling Automation")
    parser.add_argument("--ignore-schedule", action="store_true", help="Bypass date/time scheduling checks and place pending calls immediately.")
    args = parser.parse_args()
    
    asyncio.run(run_sheets_automation(ignore_schedule=args.ignore_schedule))
