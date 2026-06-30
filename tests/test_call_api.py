# -*- coding: utf-8 -*-
import asyncio
import copy
import os
import types
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class InMemoryCallStore:
    def __init__(self, now_func):
        self.now = now_func
        self.records = {}

    def _copy(self, record):
        return copy.deepcopy(record) if record is not None else None

    def _append_event(self, record, *, status, reason=None, sip_status_code=None, sip_status=None, message=None, details=None):
        record.setdefault("events", []).append(
            {
                "id": len(record.setdefault("events", [])) + 1,
                "status": status,
                "reason": reason,
                "sip_status_code": str(sip_status_code) if sip_status_code is not None else None,
                "sip_status": sip_status,
                "message": message,
                "details": details or {},
                "created_at": self.now(),
            }
        )

    def create_call_record(self, record):
        call_id = str(record["call_id"])
        created_at = record.get("created_at") or self.now()
        stored = {
            "call_id": call_id,
            "room_name": record.get("room_name") or "",
            "phone_number": record.get("phone_number") or "",
            "status": record.get("status") or "dispatching",
            "reason": record.get("reason"),
            "sip_status_code": str(record["sip_status_code"]) if record.get("sip_status_code") is not None else None,
            "sip_status": record.get("sip_status"),
            "sip_call_id": record.get("sip_call_id"),
            "participant_identity": record.get("participant_identity"),
            "participant_status": record.get("participant_status"),
            "transcript_source": record.get("transcript_source"),
            "transcript_text": record.get("transcript_text"),
            "session_report": record.get("session_report") or {},
            "vobiz_call_uuid": record.get("vobiz_call_uuid"),
            "vobiz_recording_id": record.get("vobiz_recording_id"),
            "recording_source": record.get("recording_source"),
            "recording_url": record.get("recording_url"),
            "recording_duration_ms": record.get("recording_duration_ms"),
            "recording_format": record.get("recording_format"),
            "recording_type": record.get("recording_type"),
            "error": record.get("error"),
            "metadata": copy.deepcopy(record.get("metadata") or {}),
            "created_at": created_at,
            "updated_at": created_at,
            "dispatched_at": record.get("dispatched_at"),
            "dialing_at": record.get("dialing_at"),
            "answered_at": record.get("answered_at"),
            "ended_at": record.get("ended_at"),
            "events": [],
        }
        self._append_event(
            stored,
            status=stored["status"],
            reason=stored.get("reason"),
            sip_status_code=stored.get("sip_status_code"),
            sip_status=stored.get("sip_status"),
            message=record.get("event_message") or "Call record created",
            details=record.get("event_details") or {"room_name": stored["room_name"]},
        )
        self.records[call_id] = stored
        return self._copy(stored)

    def update_call_record(self, call_id, **kwargs):
        record = self.records.get(call_id)
        if not record:
            return False
        record["updated_at"] = self.now()
        for key, value in kwargs.items():
            if key in {"event_message", "event_details"} or value is None:
                continue
            if key == "session_report":
                record["session_report"] = copy.deepcopy(value)
            elif key == "metadata":
                record["metadata"] = copy.deepcopy(value)
            elif key == "sip_status_code":
                record[key] = str(value)
            else:
                record[key] = value
        self._append_event(
            record,
            status=kwargs.get("status") or "updated",
            reason=kwargs.get("reason"),
            sip_status_code=kwargs.get("sip_status_code"),
            sip_status=kwargs.get("sip_status"),
            message=kwargs.get("event_message"),
            details=kwargs.get("event_details"),
        )
        return True

    def get_call_record(self, call_id):
        return self._copy(self.records.get(call_id))

    def get_call_record_by_vobiz_call_uuid(self, vobiz_call_uuid):
        for record in sorted(self.records.values(), key=lambda item: item.get("created_at") or "", reverse=True):
            if record.get("vobiz_call_uuid") == vobiz_call_uuid:
                return self._copy(record)
        return None

    def list_call_records(self, limit=100):
        records = sorted(self.records.values(), key=lambda item: item.get("created_at") or "", reverse=True)
        result = []
        for record in records[:limit]:
            item = self._copy(record)
            assert item is not None
            item["event_count"] = len(record.get("events") or [])
            result.append(item)
        return result

    def list_active_call_records(self):
        active = {"dispatching", "dispatched", "dialing", "answered", "active"}
        return [
            self._copy(record)
            for record in self.records.values()
            if record.get("status") in active
        ]


class CallApiTestCase(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "CALL_API_TOKEN": "test-token",
                "CALL_API_ALLOWED_COUNTRY_PREFIXES": "+91",
                "CALL_API_DEFAULT_COUNTRY_CODE": "+91",
                "CALL_API_DATABASE_URL": "postgresql://test_user:test_password@localhost:5432/test_db",
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
        self.store = InMemoryCallStore(call_api.now_iso)
        self.store_patchers = [
            patch.object(call_api, "create_call_record", self.store.create_call_record),
            patch.object(call_api, "update_call_record", self.store.update_call_record),
            patch.object(call_api, "get_call_record", self.store.get_call_record),
            patch.object(call_api, "get_call_record_by_vobiz_call_uuid", self.store.get_call_record_by_vobiz_call_uuid),
            patch.object(call_api, "list_call_records", self.store.list_call_records),
            patch.object(call_api, "list_active_call_records", self.store.list_active_call_records),
        ]
        for patcher in self.store_patchers:
            patcher.start()
        self.client = TestClient(call_api.app)

    def tearDown(self):
        for patcher in reversed(self.store_patchers):
            patcher.stop()
        self.env.stop()

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

    def test_outbound_trunk_id_reads_documented_env_name(self):
        with patch.dict(os.environ, {"OUTBOUND_TRUNK_ID": "ST_TEST"}, clear=False):
            self.assertEqual(self.call_api._outbound_trunk_id({}), "ST_TEST")

    def test_outbound_trunk_id_required_for_api_dial(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(self.call_api._outbound_trunk_id({}))

    def test_create_outbound_sip_participant_uses_trunk_and_waits(self):
        captured = {}

        class FakeSip:
            async def create_sip_participant(self, request):
                captured["request"] = request
                return types.SimpleNamespace(sip_call_id="sip-call-123")

        class FakeLiveKitAPI:
            def __init__(self):
                self.sip = FakeSip()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

        with patch.dict(os.environ, {"OUTBOUND_TRUNK_ID": "ST_TEST"}, clear=False), \
             patch("call_api.api.LiveKitAPI", FakeLiveKitAPI):
            info = asyncio.run(
                self.call_api._create_outbound_sip_participant(
                    room_name="agent_call_call_123",
                    phone_number="+919876543210",
                    participant_identity="sip_+919876543210",
                )
            )

        self.assertEqual(info.sip_call_id, "sip-call-123")
        self.assertEqual(captured["request"].sip_trunk_id, "ST_TEST")
        self.assertEqual(captured["request"].room_name, "agent_call_call_123")
        self.assertEqual(captured["request"].sip_call_to, "+919876543210")
        self.assertEqual(captured["request"].participant_identity, "sip_+919876543210")
        self.assertTrue(captured["request"].wait_until_answered)

    def test_call_endpoint_dispatches_livekit_agent_with_outbound_metadata(self):
        captured = {}

        async def fake_dispatch(room_name, metadata):
            captured["room_name"] = room_name
            captured["metadata"] = metadata
            return object()

        async def fake_sip_dial(**kwargs):
            captured["sip_dial"] = kwargs
            return types.SimpleNamespace(sip_call_id="sip-call-123")

        with patch("call_api._create_room_and_dispatch_agent", new=fake_dispatch), \
             patch("call_api._create_outbound_sip_participant", new=fake_sip_dial):
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
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["sip_call_id"], "sip-call-123")
        self.assertEqual(body["phone_number"], "+919876543210")
        self.assertTrue(body["room_name"].startswith("agent_call_call_"))
        self.assertEqual(captured["room_name"], body["room_name"])
        self.assertEqual(captured["metadata"]["call_direction"], "outbound")
        self.assertEqual(captured["metadata"]["outbound_dial_mode"], "api")
        self.assertEqual(captured["metadata"]["phone_number"], "+919876543210")
        self.assertEqual(captured["metadata"]["sip_participant_identity"], "sip_+919876543210")
        self.assertEqual(
            captured["metadata"]["call_purpose"],
            "Follow up on ERPNext implementation enquiry",
        )
        self.assertEqual(captured["metadata"]["agent_type"], "sales")
        self.assertEqual(captured["metadata"]["customer_name"], "Pankaj")
        self.assertEqual(captured["metadata"]["requested_by"], "hermes")
        self.assertEqual(captured["metadata"]["source"], "hermes-test")
        self.assertEqual(captured["sip_dial"]["room_name"], body["room_name"])
        self.assertEqual(captured["sip_dial"]["phone_number"], body["phone_number"])
        self.assertEqual(captured["sip_dial"]["participant_identity"], "sip_+919876543210")

        status_response = self.client.get(
            f"/calls/{body['call_id']}",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(status_response.status_code, 200)
        status_body = status_response.json()
        self.assertEqual(status_body["call_id"], body["call_id"])
        self.assertEqual(status_body["status"], "answered")
        self.assertEqual(status_body["sip_call_id"], "sip-call-123")
        self.assertEqual(status_body["phone_number"], body["phone_number"])
        self.assertEqual(status_body["metadata"]["source"], "hermes-test")
        self.assertGreaterEqual(len(status_body["events"]), 3)

        dashboard_response = self.client.get(
            "/dashboard/data?limit=10",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(dashboard_response.status_code, 200)
        dashboard_body = dashboard_response.json()
        self.assertTrue(dashboard_body["ok"])
        self.assertEqual(dashboard_body["summary"]["total"], 1)
        self.assertEqual(dashboard_body["summary"]["status_counts"]["answered"], 1)
        self.assertEqual(dashboard_body["calls"][0]["call_id"], body["call_id"])
        self.assertEqual(dashboard_body["calls"][0]["event_count"], len(status_body["events"]))

    def test_call_endpoint_returns_busy_when_sip_reports_486(self):
        async def fake_dispatch(room_name, metadata):
            return object()

        async def fake_sip_dial(**kwargs):
            raise self.call_api.api.TwirpError(
                "unavailable",
                "callee busy",
                status=500,
                metadata={"sip_status_code": "486", "sip_status": "Busy Here"},
            )

        async def fake_delete_room(room_name):
            return None

        with patch("call_api._create_room_and_dispatch_agent", new=fake_dispatch), \
             patch("call_api._create_outbound_sip_participant", new=fake_sip_dial), \
             patch("call_api._delete_room_quietly", new=fake_delete_room):
            response = self.client.post(
                "/calls",
                headers={"Authorization": "Bearer test-token"},
                json={"phone_number": "9876543210", "purpose": "Follow up"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["status"], "failed_busy")
        self.assertEqual(body["reason"], "busy")
        self.assertEqual(body["sip_status_code"], "486")
        self.assertEqual(body["sip_status"], "Busy Here")

        status_response = self.client.get(
            f"/calls/{body['call_id']}",
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(status_response.status_code, 200)
        status_body = status_response.json()
        self.assertEqual(status_body["status"], "failed_busy")
        self.assertEqual(status_body["reason"], "busy")
        self.assertEqual(status_body["sip_status_code"], "486")

        dashboard_response = self.client.get(
            "/dashboard/data?limit=10",
            headers={"Authorization": "Bearer test-token"},
        )
        dashboard_body = dashboard_response.json()
        self.assertEqual(dashboard_body["summary"]["failures"], 1)
        self.assertEqual(dashboard_body["summary"]["busy"], 1)

    def test_call_status_endpoint_returns_404_for_unknown_call(self):
        response = self.client.get(
            "/calls/call_missing",
            headers={"Authorization": "Bearer test-token"},
        )

        self.assertEqual(response.status_code, 404)

    def _create_answered_call(self):
        async def fake_dispatch(room_name, metadata):
            return object()

        async def fake_sip_dial(**kwargs):
            return types.SimpleNamespace(sip_call_id="sip-call-123")

        with patch("call_api._create_room_and_dispatch_agent", new=fake_dispatch), \
             patch("call_api._create_outbound_sip_participant", new=fake_sip_dial):
            response = self.client.post(
                "/calls",
                headers={"Authorization": "Bearer test-token"},
                json={"phone_number": "9876543210", "purpose": "Follow up"},
            )
        self.assertEqual(response.status_code, 200)
        return response.json()["call_id"]

    def test_internal_session_report_stores_livekit_transcript_and_refreshes_vobiz_recording(self):
        call_id = self._create_answered_call()

        with patch(
            "call_api.find_recording_for_call",
            return_value={
                "vobiz_call_uuid": "vobiz-call-123",
                "vobiz_recording_id": "rec-123",
                "recording_source": "vobiz",
                "recording_url": "https://media.vobiz.ai/rec-123.mp3",
                "recording_duration_ms": "12000.00000",
                "recording_format": "mp3",
                "recording_type": "call",
            },
        ):
            response = self.client.post(
                f"/internal/calls/{call_id}/session-report",
                headers={"Authorization": "Bearer test-token"},
                json={
                    "transcript_source": "livekit",
                    "report": {"items": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "namaste"}]},
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        call = body["call"]
        self.assertEqual(call["transcript_source"], "livekit")
        self.assertIn("user: hello", call["transcript_text"])
        self.assertEqual(call["recording_source"], "vobiz")
        self.assertEqual(call["vobiz_call_uuid"], "vobiz-call-123")
        self.assertEqual(call["vobiz_recording_id"], "rec-123")
        self.assertEqual(call["recording_url"], "https://media.vobiz.ai/rec-123.mp3")

    def test_refresh_recording_endpoint_stores_vobiz_recording_url(self):
        call_id = self._create_answered_call()

        with patch(
            "call_api.find_recording_for_call",
            return_value={
                "vobiz_call_uuid": "vobiz-call-456",
                "vobiz_recording_id": "rec-456",
                "recording_source": "vobiz",
                "recording_url": "https://media.vobiz.ai/rec-456.mp3",
            },
        ):
            response = self.client.post(
                f"/calls/{call_id}/refresh-recording",
                headers={"Authorization": "Bearer test-token"},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["call"]["recording_url"], "https://media.vobiz.ai/rec-456.mp3")
        self.assertEqual(body["call"]["recording_source"], "vobiz")

    def test_vobiz_recording_callback_updates_linked_call(self):
        call_id = self._create_answered_call()
        self.call_api.update_call_record(call_id, vobiz_call_uuid="vobiz-call-789")

        response = self.client.post(
            "/internal/vobiz/recording-callback?token=test-token&call_id={}".format(call_id),
            data={
                "call_uuid": "vobiz-call-789",
                "recording_id": "rec-789",
                "record_url": "https://media.vobiz.ai/rec-789.mp3",
                "recording_duration_ms": "9000",
            },
        )

        self.assertEqual(response.status_code, 200)
        status_response = self.client.get(
            f"/calls/{call_id}",
            headers={"Authorization": "Bearer test-token"},
        )
        call = status_response.json()
        self.assertEqual(call["recording_source"], "vobiz")
        self.assertEqual(call["vobiz_recording_id"], "rec-789")
        self.assertEqual(call["recording_url"], "https://media.vobiz.ai/rec-789.mp3")


if __name__ == "__main__":
    unittest.main()
