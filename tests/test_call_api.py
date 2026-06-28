# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class CallApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "CALL_API_TOKEN": "test-token",
                "CALL_API_ALLOWED_COUNTRY_PREFIXES": "+91",
                "CALL_API_DEFAULT_COUNTRY_CODE": "+91",
                "CALL_API_DB_PATH": os.path.join(self.tmpdir.name, "calls.sqlite3"),
                "LIVEKIT_AGENT_NAME": "outbound-caller",
                "LIVEKIT_URL": "wss://test.livekit.cloud",
                "LIVEKIT_API_KEY": "test-key",
                "LIVEKIT_API_SECRET": "test-secret",
            },
            clear=False,
        )
        self.env.start()

        import call_api

        self.call_api = call_api
        self.client = TestClient(call_api.app)

    def tearDown(self):
        self.env.stop()
        self.tmpdir.cleanup()

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ok"], True)

    def test_dashboard_html_loads_without_call_data(self):
        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn("LiveKit Call Dashboard", response.text)
        self.assertIn("Call API token", response.text)
        self.assertIn("/dashboard/data", response.text)

    def test_dashboard_data_requires_bearer_token(self):
        response = self.client.get("/dashboard/data")

        self.assertEqual(response.status_code, 401)

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

        status_response = self.client.get(
            f"/calls/{body['call_id']}",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(status_response.status_code, 200)
        status_body = status_response.json()
        self.assertEqual(status_body["call_id"], body["call_id"])
        self.assertEqual(status_body["status"], "dispatched")
        self.assertEqual(status_body["phone_number"], body["phone_number"])
        self.assertEqual(status_body["metadata"]["source"], "hermes-test")
        self.assertGreaterEqual(len(status_body["events"]), 2)

        dashboard_response = self.client.get(
            "/dashboard/data?limit=10",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(dashboard_response.status_code, 200)
        dashboard_body = dashboard_response.json()
        self.assertTrue(dashboard_body["ok"])
        self.assertEqual(dashboard_body["summary"]["total"], 1)
        self.assertEqual(dashboard_body["summary"]["status_counts"]["dispatched"], 1)
        self.assertEqual(dashboard_body["calls"][0]["call_id"], body["call_id"])
        self.assertEqual(dashboard_body["calls"][0]["event_count"], len(status_body["events"]))

    def test_call_status_endpoint_returns_404_for_unknown_call(self):
        response = self.client.get(
            "/calls/call_missing",
            headers={"Authorization": "Bearer test-token"},
        )

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()