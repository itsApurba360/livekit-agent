# -*- coding: utf-8 -*-
import os
import json
import http.server
import socketserver
from dotenv import load_dotenv
from livekit import api

# Load dotenv to get LiveKit settings from environment
load_dotenv()

PORT = 8080
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_ui_static")
AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME") or os.environ.get("AGENT_NAME") or "outbound-caller"

class WebUITesterHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # Automatically serve static pages from the static directory
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def do_POST(self):
        if self.path == "/api/token":
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                body = json.loads(post_data.decode('utf-8'))
            except Exception:
                body = {}

            room_name = body.get('room_name') or 'agent-test-room'
            participant_identity = body.get('participant_identity') or 'web-client'
            participant_name = body.get('participant_name') or 'Web Tester'

            api_key = os.environ.get("LIVEKIT_API_KEY")
            api_secret = os.environ.get("LIVEKIT_API_SECRET")
            server_url = os.environ.get("LIVEKIT_URL")

            if not api_key or not api_secret:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": "LIVEKIT_API_KEY or LIVEKIT_API_SECRET not set in .env"}).encode('utf-8'))
                return

            try:
                # Initialize LiveKit access token
                token = api.AccessToken(api_key, api_secret)
                
                # Add grants to join and participate in the room
                grants = api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True
                )
                token = token.with_grants(grants)
                token = token.with_identity(participant_identity)
                token = token.with_name(participant_name)
                
                # Parse caller phone number from room name (formatted as <phone_number>_web_test)
                phone_number = room_name.split('_')[0] if '_' in room_name else None
                
                # Add room configuration to automatically dispatch the agent worker
                metadata = {
                    "phone_number": body.get("phone_number") or phone_number,
                    "call_direction": body.get("call_direction"),
                    "outbound_dial_mode": body.get("outbound_dial_mode"),
                    "call_purpose": body.get("call_purpose"),
                    "requested_by": body.get("requested_by"),
                    "agent_type": body.get("agent_type"),
                }
                metadata = {key: value for key, value in metadata.items() if value}
                dispatch_metadata = json.dumps(metadata)
                room_config = api.RoomConfiguration(
                    agents=[
                        api.RoomAgentDispatch(
                            agent_name=AGENT_NAME,
                            metadata=dispatch_metadata
                        )
                    ]
                )
                token = token.with_room_config(room_config)
                
                jwt_token = token.to_jwt()

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "server_url": server_url,
                    "token": jwt_token,
                    "room_name": room_name
                }).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

def run():
    if not os.path.exists(STATIC_DIR):
        os.makedirs(STATIC_DIR)
        
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), WebUITesterHandler) as httpd:
        print(f"\n==================================================")
        print(f"🚀 LiveKit Real-Time Agent Web Tester Sandbox")
        print(f"==================================================")
        print(f"Running at: http://localhost:{PORT}")
        print(f"Static files served from: {STATIC_DIR}")
        print(f"Using LiveKit URL: {os.environ.get('LIVEKIT_URL')}")
        print(f"\n💡 Instructions to test:")
        print(f"1. Make sure your local agent is running: uv run agent.py start")
        print(f"2. Open http://localhost:{PORT} in your browser")
        print(f"3. Select a profile (Customer or Lead) and click 'Connect'")
        print(f"==================================================\n")
        
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down Web UI server...")

if __name__ == "__main__":
    run()
