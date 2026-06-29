# -*- coding: utf-8 -*-
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


PLUGIN_PATH = Path(__file__).resolve().parents[1] / "integrations" / "hermes" / "livekit-caller" / "__init__.py"


def load_plugin_module():
    spec = importlib.util.spec_from_file_location("livekit_caller_plugin", PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class HermesLiveKitPluginTestCase(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "LIVEKIT_CALL_API_URL": "https://calls.example.com",
                "LIVEKIT_CALL_API_TOKEN": "test-token",
            },
            clear=False,
        )
        self.env.start()
        self.plugin = load_plugin_module()

    def tearDown(self):
        self.env.stop()

    def test_make_phone_call_posts_to_call_api(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {
                "ok": True,
                "call_id": "call_123",
                "room_name": "agent_call_call_123",
                "status": "answered",
                "sip_call_id": "sip-call-123",
                "phone_number": "+919876543210",
            }
        ).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=response) as mock_urlopen:
            result = json.loads(
                self.plugin.make_phone_call(
                    {
                        "phone_number": "+919876543210",
                        "purpose": "Follow up on ERPNext implementation enquiry",
                        "agent_type": "sales",
                    }
                )
            )

        self.assertTrue(result["ok"])
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://calls.example.com/calls")
        self.assertEqual(request.headers["Authorization"], "Bearer test-token")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["phone_number"], "+919876543210")
        self.assertEqual(payload["purpose"], "Follow up on ERPNext implementation enquiry")
        self.assertEqual(payload["agent_type"], "sales")
        self.assertEqual(payload["requested_by"], "hermes")
        self.assertEqual(payload["metadata"]["source"], "hermes-plugin")

    def test_make_phone_call_passes_through_immediate_failure_response(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {
                "ok": False,
                "call_id": "call_123",
                "room_name": "agent_call_call_123",
                "status": "failed_busy",
                "reason": "busy",
                "sip_status_code": "486",
                "sip_status": "Busy Here",
                "phone_number": "+919876543210",
            }
        ).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=response):
            result = json.loads(
                self.plugin.make_phone_call(
                    {
                        "phone_number": "+919876543210",
                        "purpose": "Follow up on ERPNext implementation enquiry",
                    }
                )
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed_busy")
        self.assertEqual(result["reason"], "busy")
        self.assertEqual(result["sip_status_code"], "486")
        self.assertEqual(result["sip_status"], "Busy Here")

    def test_get_phone_call_status_fetches_call_record(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {
                "call_id": "call_123",
                "status": "failed_busy",
                "reason": "busy",
                "sip_status_code": "486",
                "sip_status": "Busy Here",
            }
        ).encode("utf-8")

        with patch("urllib.request.urlopen", return_value=response) as mock_urlopen:
            result = json.loads(self.plugin.get_phone_call_status({"call_id": "call_123"}))

        self.assertEqual(result["status"], "failed_busy")
        self.assertEqual(result["reason"], "busy")
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://calls.example.com/calls/call_123")
        self.assertEqual(request.headers["Authorization"], "Bearer test-token")

    def test_make_phone_call_reports_missing_config(self):
        with patch.dict(os.environ, {}, clear=True):
            result = json.loads(self.plugin.make_phone_call({"phone_number": "+919876543210", "purpose": "test"}))

        self.assertFalse(result["ok"])
        self.assertIn("LIVEKIT_CALL_API_URL", result["error"])


if __name__ == "__main__":
    unittest.main()
