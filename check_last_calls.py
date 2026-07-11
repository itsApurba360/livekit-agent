import os
import sys
import logging
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.getcwd())

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("check-last-calls")

from call_status_store import list_call_records
from vobiz_client import find_recording_for_call

def check():
    logger.info("Fetching last 5 calls from PostgreSQL...")
    records = list_call_records(limit=5)
    
    if not records:
        logger.warning("No call records found in database.")
        return
        
    for r in records:
        call_id = r.get("call_id")
        phone = r.get("phone_number")
        status = r.get("status")
        sip_call_id = r.get("sip_call_id")
        vobiz_call_uuid = r.get("vobiz_call_uuid")
        rec_url = r.get("recording_url")
        created_at = r.get("created_at")
        
        print("-" * 50)
        print(f"Call ID: {call_id}")
        print(f"Created At: {created_at}")
        print(f"Phone: {phone}")
        print(f"Status: {status}")
        print(f"SIP Call ID: {sip_call_id}")
        print(f"Vobiz Call UUID: {vobiz_call_uuid}")
        print(f"Recording URL: {rec_url}")
        
        # If recording is missing, look it up on Vobiz right now
        if not rec_url and status == "completed":
            print("Checking Vobiz API for this call...")
            try:
                result = find_recording_for_call(r)
                if result:
                    print("Vobiz Search Result:")
                    for k, v in result.items():
                        if k != "raw_recording":
                            print(f"  {k}: {v}")
                else:
                    print("  No recording found on Vobiz.")
            except Exception as e:
                print(f"  Vobiz query error: {e}")

if __name__ == "__main__":
    check()
