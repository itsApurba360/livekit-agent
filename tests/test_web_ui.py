# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch, MagicMock
import os
import json
from web_ui_server import WebUITesterHandler

class TestWebUITesterHandler(unittest.TestCase):
    @patch("livekit.api.AccessToken")
    def test_token_endpoint(self, mock_access_token):
        # Mock AccessToken with chained methods
        mock_instance = MagicMock()
        mock_instance.with_grants.return_value = mock_instance
        mock_instance.with_identity.return_value = mock_instance
        mock_instance.with_name.return_value = mock_instance
        mock_instance.with_room_config.return_value = mock_instance
        mock_instance.to_jwt.return_value = "mock_jwt_token"
        mock_access_token.return_value = mock_instance

        # Set up test environment variables
        os.environ["LIVEKIT_API_KEY"] = "test_key"
        os.environ["LIVEKIT_API_SECRET"] = "test_secret"
        os.environ["LIVEKIT_URL"] = "wss://test.livekit.cloud"

        # Mock request handler parameters
        handler = MagicMock()
        handler.path = "/api/token"
        handler.headers = {"Content-Length": "100"}
        handler.rfile.read.return_value = b'{"room_name": "9062371141_web_test", "participant_identity": "web_9062371141", "participant_name": "Lokesh Associates"}'
        
        # Mock HTTP responses
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()
        
        # Call the POST handler method directly
        WebUITesterHandler.do_POST(handler)
        
        # Assertions
        handler.send_response.assert_called_with(200)
        handler.send_header.assert_called_with('Content-Type', 'application/json')
        
        # Verify output contains the mock JWT token
        written_bytes = handler.wfile.write.call_args[0][0]
        response_dict = json.loads(written_bytes.decode('utf-8'))
        self.assertEqual(response_dict["token"], "mock_jwt_token")
        self.assertEqual(response_dict["server_url"], "wss://test.livekit.cloud")
        self.assertEqual(response_dict["room_name"], "9062371141_web_test")

    @patch("livekit.api.AccessToken")
    def test_token_endpoint_can_dispatch_mock_outbound(self, mock_access_token):
        mock_instance = MagicMock()
        mock_instance.with_grants.return_value = mock_instance
        mock_instance.with_identity.return_value = mock_instance
        mock_instance.with_name.return_value = mock_instance
        mock_instance.with_room_config.return_value = mock_instance
        mock_instance.to_jwt.return_value = "mock_jwt_token"
        mock_access_token.return_value = mock_instance

        os.environ["LIVEKIT_API_KEY"] = "test_key"
        os.environ["LIVEKIT_API_SECRET"] = "test_secret"
        os.environ["LIVEKIT_URL"] = "wss://test.livekit.cloud"

        body = {
            "room_name": "9876543210_web_test",
            "phone_number": "9876543210",
            "participant_identity": "web_9876543210",
            "participant_name": "Mock Lead",
            "call_direction": "outbound",
            "outbound_dial_mode": "mock",
            "call_purpose": "Local mock outbound call test",
            "requested_by": "web_ui_mock",
            "agent_type": "sales",
        }
        encoded = json.dumps(body).encode("utf-8")

        handler = MagicMock()
        handler.path = "/api/token"
        handler.headers = {"Content-Length": str(len(encoded))}
        handler.rfile.read.return_value = encoded
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()

        WebUITesterHandler.do_POST(handler)

        room_config = mock_instance.with_room_config.call_args[0][0]
        metadata = json.loads(room_config.agents[0].metadata)
        self.assertEqual(metadata["call_direction"], "outbound")
        self.assertEqual(metadata["outbound_dial_mode"], "mock")
        self.assertEqual(metadata["call_purpose"], "Local mock outbound call test")
        self.assertEqual(metadata["agent_type"], "sales")

if __name__ == "__main__":
    unittest.main()
