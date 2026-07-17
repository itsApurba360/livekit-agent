# -*- coding: utf-8 -*-
"""Script to upload Vobiz recordings to Google Drive using OAuth 2.0 user credentials."""

import os
import sys
import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import psycopg
from psycopg.rows import dict_row
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Import the existing Vobiz client
sys.path.append("/Users/pankajsankhla/code/livekit_agent")
from vobiz_client import VobizRestClient

# Configuration
DB_URL = "postgresql://user:PMw5MQBYUV3wLIQT6n0i@173.212.216.156:5432/live"
FOLDER_ID = "1MyndfHZv17lu3dItpqiZORAyNF8rFsrw"
TOKEN_FILE = ".google_drive_token.json"

def get_drive_token():
    token_json = os.environ.get("GOOGLE_DRIVE_TOKEN_JSON")
    if token_json:
        try:
            import json
            info = json.loads(token_json)
            creds = Credentials.from_authorized_user_info(info)
            if creds.expired and creds.refresh_token:
                print("Refreshing Google Drive access token from environment info...")
                creds.refresh(Request())
            return creds.token
        except Exception as e:
            print(f"Error loading credentials from GOOGLE_DRIVE_TOKEN_JSON environment variable: {e}")
            sys.exit(1)
            
    if not os.path.exists(TOKEN_FILE):
        print(f"Error: Token file '{TOKEN_FILE}' not found and GOOGLE_DRIVE_TOKEN_JSON env var is not set.")
        print("Please run 'python authenticate_google_drive.py' first to authenticate.")
        sys.exit(1)
        
    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE)
        if creds.expired and creds.refresh_token:
            print("Refreshing Google Drive access token from local file...")
            creds.refresh(Request())
            # Save the refreshed token
            with open(TOKEN_FILE, "w") as token_file:
                token_file.write(creds.to_json())
        return creds.token
    except Exception as e:
        print(f"Error loading credentials from token file: {e}")
        sys.exit(1)


def list_existing_drive_files(token):
    print("Listing existing files in Google Drive folder...")
    url = "https://www.googleapis.com/drive/v3/files"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "q": f"'{FOLDER_ID}' in parents and trashed = false",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
        "fields": "files(id,name,size)",
        "pageSize": 1000
    }
    existing = {}
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        files = response.json().get("files", [])
        for f in files:
            name = f.get("name")
            if name:
                size = f.get("size")
                size_int = int(size) if size is not None else 0
                existing[name] = {
                    "id": f["id"],
                    "size": size_int
                }
    else:
        print(f"Warning: Failed to list existing files from Drive: {response.text}")
    return existing

def process_call(call, token, vobiz_client, existing_files):
    call_id = call["call_id"]
    recording_url = call["recording_url"]
    phone_number = call["phone_number"]
    created_at_raw = call["created_at"]
    
    # Try parsing datetime to format pretty name
    try:
        if isinstance(created_at_raw, str):
            dt = datetime.fromisoformat(created_at_raw)
        else:
            dt = created_at_raw
        dt_str = dt.strftime("%Y%m%d_%H%M%S")
    except Exception:
        dt_str = "unknown_time"
        
    ext = "wav"
    if recording_url.endswith(".mp3"):
        ext = "mp3"
        
    filename = f"{dt_str}_{phone_number}_{call_id}.{ext}"
    
    if filename in existing_files:
        return call_id, "skipped_already_exists"
            
    # Check if google_drive_file_id is in metadata
    meta = call.get("metadata") or {}
    if meta.get("google_drive_file_id"):
        return call_id, "skipped_meta_exists"
        
    # Download from Vobiz
    try:
        content, content_type = vobiz_client.download_recording(recording_url)
    except Exception as e:
        return call_id, f"error_downloading: {e}"
        
    # Upload to Google Drive
    try:
        # Step 1: Create metadata
        create_url = "https://www.googleapis.com/drive/v3/files"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        body = {
            "name": filename,
            "parents": [FOLDER_ID]
        }
        create_res = requests.post(create_url, headers=headers, json=body)
        if create_res.status_code != 200:
            return call_id, f"error_creating_metadata: {create_res.text}"
            
        file_id = create_res.json()["id"]
        
        # Step 2: Upload media
        upload_url = f"https://www.googleapis.com/upload/drive/v3/files/{file_id}?uploadType=media"
        upload_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type
        }
        upload_res = requests.patch(upload_url, headers=upload_headers, data=content)
        if upload_res.status_code != 200:
            return call_id, f"error_uploading_media: {upload_res.text}"
            
        # Update database with google_drive_file_id in metadata
        from call_status_store import update_call_record
        meta["google_drive_file_id"] = file_id
        update_call_record(call_id, metadata=meta)
        
        return call_id, "success"
        
    except Exception as e:
        return call_id, f"error_upload_flow: {e}"

def delete_invalid_files(token, existing_files):
    to_delete = [f for f in existing_files if existing_files[f]["size"] <= 100]
    if not to_delete:
        return
    print(f"Found {len(to_delete)} invalid (0-byte placeholder) files. Deleting them...")
    
    headers = {"Authorization": f"Bearer {token}"}
    
    def delete_single(filename):
        file_id = existing_files[filename]["id"]
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
        params = {"supportsAllDrives": "true"}
        res = requests.delete(url, headers=headers, params=params)
        if res.status_code in (200, 204):
            return filename, True
        else:
            print(f"Failed to delete {filename}: {res.text}")
            return filename, False

    deleted_count = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(delete_single, fname): fname for fname in to_delete}
        for future in as_completed(futures):
            fname, success = future.result()
            if success:
                deleted_count += 1
                existing_files.pop(fname, None)
                
    print(f"Successfully deleted {deleted_count} invalid files from Google Drive.")

def main():
    # Expose variables in env for module import
    os.environ["CALL_API_DATABASE_URL"] = DB_URL
    
    token = get_drive_token()
    existing_files = list_existing_drive_files(token)
    print(f"Found {len(existing_files)} existing files in Drive recordings folder.")
    
    # Delete invalid 0-byte placeholder files first
    delete_invalid_files(token, existing_files)
    
    vobiz_client = VobizRestClient()
    if not vobiz_client.configured:
        print("Error: Vobiz API client credentials not configured in environment.")
        sys.exit(1)
        
    # Fetch calls from database
    print("Fetching calls with recording_url from database...")
    db_url = os.environ.get("CALL_API_DATABASE_URL") or DB_URL
    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT call_id, phone_number, recording_url, metadata_json, created_at
                FROM calls
                WHERE recording_url IS NOT NULL AND recording_url != ''
            """)
            rows = cur.fetchall()
            
    calls = []
    for r in rows:
        metadata_json = r.get("metadata_json") or "{}"
        try:
            metadata = json.loads(metadata_json)
        except Exception:
            metadata = {}
        calls.append({
            "call_id": r["call_id"],
            "phone_number": r["phone_number"],
            "recording_url": r["recording_url"],
            "metadata": metadata,
            "created_at": r["created_at"]
        })
        
    total_calls = len(calls)
    print(f"Total calls with recordings in DB: {total_calls}")
    
    # Process calls using ThreadPoolExecutor
    success_count = 0
    skipped_count = 0
    error_count = 0
    
    print("\nStarting upload processing (using up to 5 parallel threads)...")
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(process_call, call, token, vobiz_client, existing_files): call
            for call in calls
        }
        
        for future in as_completed(futures):
            call_id, status = future.result()
            call = futures[future]
            if status == "success":
                success_count += 1
                print(f"[SUCCESS] Call {call_id} uploaded successfully.")
            elif status.startswith("skipped"):
                skipped_count += 1
            else:
                error_count += 1
                print(f"[ERROR] Call {call_id} failed: {status}")
                
    print("\n--- Upload Process Summary ---")
    print(f"Total processed: {total_calls}")
    print(f"Successfully uploaded: {success_count}")
    print(f"Skipped (already exists): {skipped_count}")
    print(f"Failed: {error_count}")

if __name__ == "__main__":
    main()
