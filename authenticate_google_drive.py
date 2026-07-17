# -*- coding: utf-8 -*-
"""Helper script to perform one-time Google Drive OAuth authentication."""

import os
import sys
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]
CLIENT_SECRETS_FILE = "client_secrets.json"
TOKEN_FILE = ".google_drive_token.json"

def main():
    if not os.path.exists(CLIENT_SECRETS_FILE):
        print(f"Error: '{CLIENT_SECRETS_FILE}' not found in the current directory.")
        print("\nPlease follow these steps to obtain it:")
        print("1. Go to Google Cloud Console (https://console.cloud.google.com/)")
        print("2. Select your project (e.g., 'fir-three-sixty')")
        print("3. Go to APIs & Services > Credentials")
        print("4. Click 'Create Credentials' > 'OAuth client ID'")
        print("5. Choose Application Type: 'Desktop app', name it (e.g., 'Drive Uploader'), and click 'Create'")
        print("6. Download the JSON file, rename it to 'client_secrets.json', and save it in this project folder.")
        sys.exit(1)

    print("Starting Google OAuth 2.0 flow...")
    print("If a browser window does not open automatically, copy and paste the link printed below.")
    
    try:
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
        # run_local_server will open a browser to authenticate
        creds = flow.run_local_server(port=0)
        
        # Save credentials to token file
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
            
        print("\nAuthentication successful!")
        print(f"Credentials successfully saved to '{TOKEN_FILE}'")
        print("You can now run the upload script to upload recordings using your personal user storage.")
        
    except Exception as e:
        print(f"\nAuthentication failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
