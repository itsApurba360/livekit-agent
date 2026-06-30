# -*- coding: utf-8 -*-
import os
import re
import logging
import asyncio
import requests
from datetime import datetime, timezone
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# Load env
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sheet-automation")

STOP_FLAG = "agent_stop.flag"

def check_stop_requested() -> bool:
    if os.path.exists(STOP_FLAG):
        logger.info("Stop requested via flag file. Aborting loop.")
        return True
    return False

def get_google_sheets_client():
    creds_path = os.environ.get("GOOGLE_SHEETS_CREDS_PATH", ".google_sheets_creds.json")
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID")
    
    if not spreadsheet_id:
        raise ValueError("GOOGLE_SHEETS_SPREADSHEET_ID environment variable is missing")
    if not os.path.exists(creds_path):
        raise FileNotFoundError(f"Google credentials file not found at: {creds_path}")
        
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
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
        
    payload = {
        "phone_number": mobile,
        "purpose": str(row.get("Purpose/Prompt") or "GST filing documents collection"),
        "agent_type": "support",
        "customer_name": str(row.get("Owner name") or ""),
        "company_name": str(row.get("Company Name") or ""),
        "requested_by": "sheets_automation",
        "metadata": {
            "cid": str(row.get("CID")),
            "source": "sheets_automation"
        }
    }
    
    headers = {
        "Authorization": f"Bearer {call_api_token}",
        "Content-Type": "application/json"
    }
    
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{call_api_url}/calls", json=payload, headers=headers, timeout=30.0)
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

def sync_completed_calls_to_sheets(sheet):
    """Scan the call status store for ended, unsynced calls and append logs to Sheet 2."""
    from call_status_store import list_completed_call_records, update_call_record
    
    # 1. Fetch completed calls from the configured PostgreSQL status store
    try:
        calls = list_completed_call_records()
    except Exception as err:
        logger.error(f"Failed to read from call status store: {err}")
        return

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
    sheet2 = sheet.get_worksheet(1)
    
    # Pre-cache Sheet 1 records for looking up row index of matching CID
    sheet1_rows = sheet1.get_all_records()
    
    for c in unsynced_calls:
        if check_stop_requested():
            break
            
        cid = c["parsed_metadata"]["cid"]
        call_id = c["call_id"]
        
        # Read Human Callback metadata set by the schedule_human_callback tool
        next_action = c["parsed_metadata"].get("next_action", "")
        if next_action != "Human":
            next_action = "AI Call"
            
        next_action_date = c["parsed_metadata"].get("next_action_date", "")
        next_action_time = c["parsed_metadata"].get("next_action_time", "")
        client_comment = c["parsed_metadata"].get("client_comment", "")
        
        # Fallback text if call failed without speaking
        if not client_comment and c["status"].startswith("failed"):
            client_comment = f"Call failed: {c.get('reason') or 'unknown reason'}"
        elif not client_comment:
            client_comment = "Call completed."

        # Fetch recording and transcript from the call status store
        recording_url = c.get("recording_url") or ""
        if recording_url:
            api_url = os.environ.get("LIVEKIT_CALL_API_URL", "http://127.0.0.1:8000").rstrip("/")
            recording_url = f"{api_url}/calls/{call_id}/recording"
            
        transcript = c.get("transcript_text") or ""
        call_time = c.get("dispatched_at") or c.get("created_at") or ""
        
        # Format call_time to human readable
        try:
            parsed_time = datetime.fromisoformat(call_time.replace("Z", "+00:00"))
            formatted_call_time = parsed_time.strftime("%m/%d/%Y %H:%M:%S")
        except Exception:
            formatted_call_time = call_time

        # Sheet 2 columns: Client Comment,Next Action,Next Action Date (DD/MMYYYY),Next Action Time (IST),CID,Datetime,Recording,Trasncript
        new_row = [
            client_comment,       # Client Comment
            next_action,          # Next Action
            next_action_date,     # Next Action Date
            next_action_time,     # Next Action Time
            cid,                  # CID
            formatted_call_time,  # Datetime
            recording_url,        # Recording
            transcript            # Trasncript (with typo)
        ]
        
        try:
            # Append log to Sheet 2
            sheet2.append_row(new_row)
            logger.info(f"Appended call log to Sheet 2 for CID {cid}")
            
            # Find matching row in Sheet 1 to update Last Comment, Count, and Data Status
            matching_row_index = -1
            current_count = 0
            for idx, r in enumerate(sheet1_rows):
                if str(r.get("CID")) == str(cid):
                    matching_row_index = idx + 2
                    try:
                        current_count = int(r.get("Count") or 0)
                    except ValueError:
                        current_count = 0
                    break
            
            if matching_row_index != -1:
                sheet1.update_cell(matching_row_index, 8, client_comment) # Last Comment
                sheet1.update_cell(matching_row_index, 9, current_count + 1) # Count
                logger.info(f"Updated Sheet 1 row {matching_row_index} for CID {cid}")

            # Mark the persisted call as synced
            meta_dict = c["parsed_metadata"]
            meta_dict["synced"] = True
            update_call_record(call_id, metadata=meta_dict)
            logger.info(f"Marked call {call_id} as synced in the call status store")
            
        except Exception as e:
            logger.error(f"Failed to sync call {call_id} to Google Sheets: {e}")
            break

async def run_sheets_automation():
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

    # Second, scan Sheet 1 for new calls to place
    sheet1 = sheet.get_worksheet(0)
    sheet2 = sheet.get_worksheet(1)
    
    try:
        sheet1_rows = sheet1.get_all_records()
        sheet2_rows = sheet2.get_all_records()
    except Exception as e:
        logger.error(f"Failed to fetch sheets data: {e}")
        try:
            with open("agent_error.log", "w") as f:
                f.write(f"Google Sheets Data Fetch Error: {str(e)}")
        except Exception:
            pass
        return

    logger.info(f"Fetched {len(sheet1_rows)} clients and {len(sheet2_rows)} logs.")
    
    now = datetime.now()
    
    for row in sheet1_rows:
        if check_stop_requested():
            break
            
        cid = str(row.get("CID") or "").strip()
        status = str(row.get("Data Received Status") or "").strip().lower()
        
        if not cid:
            continue
            
        if status != "pending":
            continue
            
        # Find all call logs in Sheet 2 for this client CID
        client_logs = [log for log in sheet2_rows if str(log.get("CID")).strip() == cid]
        
        should_call = False
        
        if not client_logs:
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
                    if scheduled_time <= now:
                        logger.info(f"Client CID {cid} has scheduled AI Call due at {scheduled_time} (Current: {now}). Triggering call.")
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
            # Check active calls in call_api to avoid duplicate calls
            from call_api import get_active_calls
            active = get_active_calls()
            if any(str(c.get("phone_number")) == str(row.get("Mobile Number")).strip() or c.get("metadata", {}).get("cid") == cid for c in active):
                logger.info(f"Client CID {cid} already has an active call. Skipping duplicate dial.")
                continue
                
            success = await trigger_outbound_call(row)
            if success:
                await asyncio.sleep(3.0)

    logger.info("Google Sheets Automation cycle finished.")

if __name__ == "__main__":
    asyncio.run(run_sheets_automation())
