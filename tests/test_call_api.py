# -*- coding: utf-8 -*-
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class CallApiTestCase(unittest.TestCase):
    def setUp(self):
        import call_api

        self.call_api = call_api
        self.client = TestClient(call_api.app)
        self.call_api.CALL_RECORDS.clear()
        self.env = patch.dict(
            os.environ,
            {
                "CALL_API_TOKEN": "test-token",
                "CALL_API_ALLOWED_COUNTRY_PREFIXES": "+91",
                "CALL_API_DEFAULT_COUNTRY_CODE": "+91",
                "LIVEKIT_AGENT_NAME": "outbound-caller",
                "LIVEKIT_URL": "wss://test.livekit.cloud",
                "LIVEKIT_API_KEY": "test-key",
                "LIVEKIT_API_SECRET": "test-secret",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.call_api.CALL_RECORDS.clear()

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)

    def test_call_endpoint_requires_bearer_token(self):
        response = self.client.post(
            "/calls",
            json={"phone_number": "+919876543210", "purpose": "Follow up on enquiry"},
        )
        self.assertEqual(response.status_code, 401)

    def test_call_endpoint_rejects_wrong_bearer_token(self):
        response = self.client.post(
            "/calls",
            headers={"Authorization": "Bearer wrong-token"},
            json={"phone_number": "+919876543210", "purpose": "Follow up on enquiry"},
        )
        self.assertEqual(response.status_code, 403)

    def test_call_endpoint_rejects_disallowed_country_prefix(self):
        response = self.client.post(
            "/calls",
            headers={"Authorization": "Bearer test-token"},
            json={"phone_number": "+15551234567", "purpose": "Follow up on enquiry"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("not allowed", response.json()["detail"].lower())

    def test_call_endpoint_rejects_blank_purpose(self):
        response = self.client.post(
            "/calls",
            headers={"Authorization": "Bearer test-token"},
            json={"phone_number": "+919876543210", "purpose": "   "},
        )
        self.assertEqual(response.status_code, 422)

    def test_call_endpoint_dispatches_livekit_agent_with_outbound_metadata(self):
        captured = {}

        async def fake_dispatch(room_name, metadata):
            captured["room_name"] = room_name
            captured["metadata"] = metadata
            return object()

        with patch("call_api._dispatch_livekit_agent", new=fake_dispatch):
            response = self.client.post(
                "/calls",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "phone_number": "9876543210",
                    "purpose": "Follow up on ERPNext implementation enquiry",
                    "agent_type": "sales",
                    "customer_name": "Pankaj",
                    "requested_by": "hermes",
                    "metadata": {"source": "hermes-test"},
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["phone_number"], "+919876543210")
        self.assertTrue(body["room_name"].startswith("agent_call_call_"))
        self.assertEqual(captured["room_name"], body["room_name"])
        self.assertEqual(captured["metadata"]["call_direction"], "outbound")
        self.assertEqual(captured["metadata"]["phone_number"], "+919876543210")
        self.assertEqual(
            captured["metadata"]["call_purpose"],
            "Follow up on ERPNext implementation enquiry",
        )
        self.assertEqual(captured["metadata"]["agent_type"], "sales")
        self.assertEqual(captured["metadata"]["customer_name"], "Pankaj")
        self.assertEqual(captured["metadata"]["requested_by"], "hermes")
        self.assertEqual(captured["metadata"]["source"], "hermes-test")


if __name__ == "__main__":
    unittest.main()