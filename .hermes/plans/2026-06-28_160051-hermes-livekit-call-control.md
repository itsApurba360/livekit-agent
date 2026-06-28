# Hermes-Callable LiveKit Calls Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make this LiveKit voice agent callable from Hermes, and from other AI systems, through a small authenticated call-control API that dispatches the existing LiveKit worker into outbound SIP rooms.

**Architecture:** Add a separate FastAPI call-control service in this repo. Hermes calls the API through a native Hermes plugin/tool; the API creates a LiveKit room and explicit agent dispatch with outbound-call metadata; the existing `agent.py` worker reads that metadata and dials the callee through LiveKit SIP. The worker remains the realtime voice/audio system; Hermes only orchestrates calls.

**Tech Stack:** Python, FastAPI, uvicorn, LiveKit Python API (`api.LiveKitAPI`, `api.CreateRoomRequest`, `api.CreateAgentDispatchRequest`), existing `unittest` suite, optional Hermes plugin using stdlib HTTP.

---

## Current Context / Assumptions

- The repo is `/Users/pankajsankhla/code/livekit_agent`.
- The existing worker already supports outbound SIP dialing:
  - `agent.py:146-155` infers outbound calls from rooms named `agent_call_*`.
  - `agent.py:228-236` resolves `OUTBOUND_TRUNK_ID` / equivalent env vars.
  - `agent.py:239-294` calls `ctx.api.sip.create_sip_participant(...)`.
  - `agent.py:580-611` waits for callee speech before generating the first reply.
- Latest user instruction: do **not** deploy to Dokploy for the first verification. Implement locally, run the worker and call-control API locally, then place one intentional test call to `+919062371141`.
- The current Dokploy setup deploys only a worker and exposes no inbound ports. Keep Dokploy as a later deployment follow-up after the local test call proves the flow.
- Unit tests should not place real calls. All LiveKit API calls must be patched/faked in tests.
- Calls are financially and reputationally sensitive. The API must never be public without bearer-token auth, number validation, and basic policy checks.
- Do **not** commit during implementation unless the user explicitly asks. The “Commit” steps below are checkpoint suggestions only.

## Proposed Approach

1. Add a minimal FastAPI app, `call_api.py`, with:
   - `GET /health`
   - `POST /calls`
   - optional `GET /calls/{call_id}` for in-memory status visibility
2. Authenticate `POST /calls` with `Authorization: Bearer <CALL_API_TOKEN>`.
3. Validate phone numbers to safe E.164-ish format, initially allowing only configured country prefixes such as `+91`.
4. Dispatch the existing LiveKit agent by:
   - creating a room named `agent_call_<call_id>`
   - creating an explicit agent dispatch with metadata containing `call_direction: outbound`, `phone_number`, `call_purpose`, `requested_by`, and optional `agent_type`
5. Extend `agent.py` so the outbound agent prompt includes the API-provided purpose and requester, and optionally honors `agent_type` metadata.
6. Add a Hermes plugin template under `integrations/hermes/livekit-caller/` that registers a `make_phone_call` tool and POSTs to the API.
7. Document local run/testing first, including the one approved test call to `+919062371141`; keep Dokploy deployment notes as a later follow-up.

---

## Files Likely to Change

- Modify: `pyproject.toml`
- Create: `call_api.py`
- Create: `tests/test_call_api.py`
- Modify: `agent.py`
- Modify: `tests/test_agent_call_context.py`
- Create: `integrations/hermes/livekit-caller/plugin.yaml`
- Create: `integrations/hermes/livekit-caller/__init__.py`
- Create: `tests/test_hermes_livekit_plugin.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/dokploy.md`

---

## Environment Variables to Add

```bash
# Call Control API
CALL_API_TOKEN=replace-with-long-random-token
CALL_API_ALLOWED_COUNTRY_PREFIXES=+91
CALL_API_DEFAULT_COUNTRY_CODE=+91
CALL_API_MAX_PURPOSE_CHARS=300

# Hermes plugin / external clients
LIVEKIT_CALL_API_URL=http://127.0.0.1:8000
LIVEKIT_CALL_API_TOKEN=replace-with-same-token-as-call-api
```

Existing LiveKit vars still apply to both worker and API service:

```bash
LIVEKIT_URL=wss://...
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_AGENT_NAME=outbound-caller
OUTBOUND_TRUNK_ID=...
```

---

## Step-by-Step Plan

### Task 1: Add API Dependencies

**Objective:** Add the small HTTP API dependencies without changing worker behavior.

**Files:**
- Modify: `pyproject.toml:7-17`

**Step 1: Modify dependencies**

Add FastAPI and uvicorn to `[project].dependencies`. Add httpx to dev dependencies for FastAPI `TestClient`.

```toml
[project]
dependencies = [
    "livekit",
    "livekit-agents",
    "livekit-plugins-openai",
    "livekit-plugins-google",
    "livekit-plugins-noise-cancellation",
    "livekit-plugins-silero",
    "requests",
    "python-dotenv",
    "certifi",
    "fastapi",
    "uvicorn[standard]",
]

[dependency-groups]
dev = [
    "pytest",
    "httpx",
]
```

**Step 2: Sync dependencies**

Run:

```bash
uv sync
```

Expected: dependencies install successfully; no source changes except lock/venv state if the project creates a lockfile.

**Step 3: Smoke-check imports**

Run:

```bash
.venv/bin/python - <<'PY'
import fastapi
import uvicorn
print('fastapi ok', fastapi.__version__)
print('uvicorn ok', uvicorn.__version__)
PY
```

Expected: both imports succeed.

**Checkpoint:** Do not commit unless user explicitly requested commits.

---

### Task 2: Create Failing Tests for API Auth and Phone Validation

**Objective:** Define the API security behavior before implementing `call_api.py`.

**Files:**
- Create: `tests/test_call_api.py`
- Test target: `call_api.py`

**Step 1: Create initial tests**

Create `tests/test_call_api.py` with this starting content:

```python
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


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_call_api
```

Expected: FAIL because `call_api.py` does not exist yet.

**Checkpoint:** Do not commit unless user explicitly requested commits.

---

### Task 3: Implement Minimal `call_api.py` App, Auth, and Validation

**Objective:** Add the API shell and validation helpers without dispatching real calls yet.

**Files:**
- Create: `call_api.py`
- Test: `tests/test_call_api.py`

**Step 1: Implement minimal API**

Create `call_api.py`:

```python
# -*- coding: utf-8 -*-
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from livekit import api

load_dotenv()

AGENT_NAME = os.environ.get("LIVEKIT_AGENT_NAME") or os.environ.get("AGENT_NAME") or "outbound-caller"

app = FastAPI(title="LiveKit Call Control API", version="0.1.0")
CALL_RECORDS: dict[str, dict[str, Any]] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _call_api_token() -> str:
    return os.environ.get("CALL_API_TOKEN", "").strip()


def _allowed_country_prefixes() -> list[str]:
    raw = os.environ.get("CALL_API_ALLOWED_COUNTRY_PREFIXES", "+91")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _default_country_code() -> str:
    return os.environ.get("CALL_API_DEFAULT_COUNTRY_CODE", "+91").strip()


def _max_purpose_chars() -> int:
    try:
        return int(os.environ.get("CALL_API_MAX_PURPOSE_CHARS", "300"))
    except ValueError:
        return 300


def _require_auth(authorization: str | None) -> None:
    expected_token = _call_api_token()
    if not expected_token:
        raise HTTPException(status_code=503, detail="CALL_API_TOKEN is not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    supplied_token = authorization.removeprefix("Bearer ").strip()
    if supplied_token != expected_token:
        raise HTTPException(status_code=403, detail="Invalid bearer token")


def normalize_phone_number(raw_phone: str) -> str:
    phone = (raw_phone or "").strip()
    if not phone:
        raise HTTPException(status_code=422, detail="phone_number is required")

    # Keep a leading plus and digits only. This accepts human-friendly separators.
    cleaned = re.sub(r"[^0-9+]", "", phone)
    if cleaned.count("+") > 1 or ("+" in cleaned and not cleaned.startswith("+")):
        raise HTTPException(status_code=422, detail="Invalid phone number format")

    if not cleaned.startswith("+"):
        cleaned = f"{_default_country_code()}{cleaned}"

    if not re.fullmatch(r"\+[1-9]\d{9,14}", cleaned):
        raise HTTPException(status_code=422, detail="Invalid E.164 phone number")

    allowed_prefixes = _allowed_country_prefixes()
    if allowed_prefixes and not any(cleaned.startswith(prefix) for prefix in allowed_prefixes):
        raise HTTPException(status_code=422, detail=f"Phone number prefix is not allowed: {cleaned}")

    return cleaned


class CallRequest(BaseModel):
    phone_number: str = Field(..., description="Destination phone number, preferably E.164")
    purpose: str = Field(..., description="Short reason the agent should give for the call")
    agent_type: Optional[str] = Field(default=None, description="Optional override: support or sales")
    customer_name: Optional[str] = None
    company_name: Optional[str] = None
    requested_by: str = "hermes"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValueError("purpose is required")
        if len(value) > _max_purpose_chars():
            raise ValueError("purpose is too long")
        return value

    @field_validator("agent_type")
    @classmethod
    def validate_agent_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in {"support", "sales"}:
            raise ValueError("agent_type must be support or sales")
        return normalized


class CallResponse(BaseModel):
    ok: bool
    call_id: str
    room_name: str
    status: str
    phone_number: str


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


async def _dispatch_livekit_agent(room_name: str, dispatch_metadata: dict[str, Any]) -> Any:
    """Create the LiveKit room and dispatch the configured worker into it."""
    metadata_json = json.dumps(dispatch_metadata, ensure_ascii=False)
    async with api.LiveKitAPI() as lk:
        await lk.room.create_room(api.CreateRoomRequest(name=room_name, metadata=metadata_json))
        return await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room_name,
                metadata=metadata_json,
            )
        )


@app.post("/calls", response_model=CallResponse)
async def create_call(request: CallRequest, authorization: str | None = Header(default=None)) -> CallResponse:
    _require_auth(authorization)
    normalized_phone = normalize_phone_number(request.phone_number)
    call_id = f"call_{uuid.uuid4().hex[:12]}"
    room_name = f"agent_call_{call_id}"
    dispatch_metadata = {
        **request.metadata,
        "call_id": call_id,
        "call_direction": "outbound",
        "phone_number": normalized_phone,
        "call_purpose": request.purpose,
        "requested_by": request.requested_by,
        "agent_type": request.agent_type,
        "customer_name": request.customer_name,
        "company_name": request.company_name,
        "source": request.metadata.get("source", request.requested_by),
    }
    dispatch_metadata = {key: value for key, value in dispatch_metadata.items() if value is not None}

    CALL_RECORDS[call_id] = {
        "call_id": call_id,
        "room_name": room_name,
        "phone_number": normalized_phone,
        "status": "dispatching",
        "metadata": dispatch_metadata,
        "created_at": _now_iso(),
    }

    try:
        await _dispatch_livekit_agent(room_name, dispatch_metadata)
    except Exception as err:
        CALL_RECORDS[call_id]["status"] = "dispatch_failed"
        CALL_RECORDS[call_id]["error"] = str(err)
        raise HTTPException(status_code=502, detail=f"Failed to dispatch LiveKit agent: {err}") from err

    CALL_RECORDS[call_id]["status"] = "dispatched"
    return CallResponse(
        ok=True,
        call_id=call_id,
        room_name=room_name,
        status="dispatched",
        phone_number=normalized_phone,
    )


@app.get("/calls/{call_id}")
def get_call(call_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _require_auth(authorization)
    record = CALL_RECORDS.get(call_id)
    if not record:
        raise HTTPException(status_code=404, detail="Call not found")
    return record
```

**Important implementation note:** This first implementation always calls `create_room`. If LiveKit returns an “already exists” Twirp error in real deployments, add a tiny helper to ignore only that specific error. Do not swallow arbitrary LiveKit errors.

**Step 2: Run tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_call_api
```

Expected: validation/auth tests pass except any test that still expects dispatch behavior.

**Checkpoint:** Do not commit unless user explicitly requested commits.

---

### Task 4: Add Tests for Successful Dispatch Metadata

**Objective:** Prove `POST /calls` creates the right room name and LiveKit dispatch metadata without placing a real call.

**Files:**
- Modify: `tests/test_call_api.py`
- Modify: `call_api.py` only if needed

**Step 1: Add successful dispatch test**

Append this test method to `CallApiTestCase`:

```python
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
        self.assertEqual(captured["metadata"]["call_purpose"], "Follow up on ERPNext implementation enquiry")
        self.assertEqual(captured["metadata"]["agent_type"], "sales")
        self.assertEqual(captured["metadata"]["requested_by"], "hermes")
        self.assertEqual(captured["metadata"]["source"], "hermes-test")
```

**Step 2: Run test**

Run:

```bash
.venv/bin/python -m unittest tests.test_call_api.CallApiTestCase.test_call_endpoint_dispatches_livekit_agent_with_outbound_metadata
```

Expected: PASS after Task 3 implementation.

**Step 3: Run all API tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_call_api
```

Expected: PASS.

**Checkpoint:** Do not commit unless user explicitly requested commits.

---

### Task 5: Teach `agent.py` About Call Purpose and Requester Metadata

**Objective:** Ensure the voice agent knows why Hermes requested the call and can say the correct reason.

**Files:**
- Modify: `tests/test_agent_call_context.py:56-66,204-220`
- Modify: `agent.py:56-66,158-198,297-336`

**Step 1: Add failing tests**

In `tests/test_agent_call_context.py`, add tests near the existing outbound context tests:

```python
    def test_builds_outbound_context_with_call_purpose_metadata(self):
        ctx = FakeContext(
            FakeRoom(name="agent_call_abc123"),
            metadata=(
                '{"phone_number": "+919****3210", '
                '"call_purpose": "Follow up on ERPNext implementation enquiry", '
                '"requested_by": "hermes"}'
            ),
        )
        config = agent._load_json_dict(ctx.job.metadata)

        call_context = agent._build_call_context(ctx, config)

        self.assertEqual(call_context.direction, "outbound")
        self.assertEqual(call_context.call_purpose, "Follow up on ERPNext implementation enquiry")
        self.assertEqual(call_context.requested_by, "hermes")

    def test_outbound_prompt_includes_call_purpose_and_requester(self):
        call_context = agent.CallContext(
            direction="outbound",
            phone_number="+919****3210",
            call_purpose="Follow up on ERPNext implementation enquiry",
            requested_by="hermes",
        )

        prompt = agent._call_context_prompt(call_context)

        self.assertIn("Call purpose: Follow up on ERPNext implementation enquiry", prompt)
        self.assertIn("Requested by: hermes", prompt)
        self.assertIn("Use the call purpose", prompt)
```

**Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_agent_call_context.AgentCallContextTestCase.test_builds_outbound_context_with_call_purpose_metadata tests.test_agent_call_context.AgentCallContextTestCase.test_outbound_prompt_includes_call_purpose_and_requester
```

Expected: FAIL because `CallContext` does not yet have `call_purpose` / `requested_by`.

**Step 3: Implement minimal context fields**

In `agent.py`, extend the dataclass:

```python
@dataclass
class CallContext:
    direction: str = "web"
    phone_number: Optional[str] = None
    participant_identity: Optional[str] = None
    sip_call_status: Optional[str] = None
    sip_call_id: Optional[str] = None
    sip_rule_id: Optional[str] = None
    sip_trunk_id: Optional[str] = None
    source: str = "metadata"
    ready: bool = True
    call_id: Optional[str] = None
    call_purpose: Optional[str] = None
    requested_by: Optional[str] = None
```

In `_build_call_context(...)`, read these values from `config_dict`:

```python
    call_id = config_dict.get("call_id")
    call_purpose = config_dict.get("call_purpose") or config_dict.get("purpose")
    requested_by = config_dict.get("requested_by") or config_dict.get("source")
```

Pass the values in both `CallContext(...)` return sites:

```python
        return CallContext(
            direction=direction,
            phone_number=phone_number,
            participant_identity=getattr(participant, "identity", None),
            sip_call_status=attrs.get("sip.callStatus"),
            sip_call_id=attrs.get("sip.callIDFull") or attrs.get("sip.callID"),
            sip_rule_id=attrs.get("sip.ruleID"),
            sip_trunk_id=attrs.get("sip.trunkID"),
            source=source,
            call_id=call_id,
            call_purpose=call_purpose,
            requested_by=requested_by,
        )
```

and:

```python
    return CallContext(
        direction=direction,
        phone_number=phone_number,
        source=source,
        call_id=call_id,
        call_purpose=call_purpose,
        requested_by=requested_by,
    )
```

In `_call_context_prompt(...)`, include the extra context:

```python
    if call_context.call_id:
        lines.append(f"- Call id: {call_context.call_id}.")
    if call_context.call_purpose:
        lines.append(f"- Call purpose: {call_context.call_purpose}.")
    if call_context.requested_by:
        lines.append(f"- Requested by: {call_context.requested_by}.")
```

Then strengthen outbound instructions:

```python
    if call_context.is_outbound:
        lines.append(
            "- This is an outbound call placed by LSA Office. The customer or lead did not call us in this session. "
            "Do not speak before the callee answers or before they speak first. On your first response after they speak, "
            "briefly introduce yourself, LSA Office, and the reason for calling. Use the call purpose above as the reason "
            "when it is present; do not invent a different reason."
        )
```

**Step 4: Run targeted tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_agent_call_context
```

Expected: PASS.

**Checkpoint:** Do not commit unless user explicitly requested commits.

---

### Task 6: Optionally Honor `agent_type` Metadata

**Objective:** Let the call API request either the support or sales persona when needed, while preserving current automatic lookup behavior by default.

**Files:**
- Modify: `tests/test_agent_call_context.py`
- Modify: `agent.py:424-434`

**Step 1: Add failing helper tests**

Add these tests to `AgentCallContextTestCase`:

```python
    def test_agent_type_from_metadata_accepts_sales_and_support(self):
        self.assertEqual(agent._agent_type_from_metadata("sales"), "Sales")
        self.assertEqual(agent._agent_type_from_metadata("support"), "Support")
        self.assertEqual(agent._agent_type_from_metadata("unknown"), None)

    def test_select_agent_type_honors_metadata_override(self):
        self.assertEqual(agent._select_agent_type({"agent_type": "sales"}, "Customer"), "Sales")
        self.assertEqual(agent._select_agent_type({"agent_type": "support"}, "Unknown"), "Support")
        self.assertEqual(agent._select_agent_type({}, "Customer"), "Support")
        self.assertEqual(agent._select_agent_type({}, "Lead"), "Sales")
```

**Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_agent_call_context.AgentCallContextTestCase.test_agent_type_from_metadata_accepts_sales_and_support tests.test_agent_call_context.AgentCallContextTestCase.test_select_agent_type_honors_metadata_override
```

Expected: FAIL because helpers do not exist yet.

**Step 3: Implement helpers in `agent.py`**

Place these near other small metadata helpers:

```python
def _agent_type_from_metadata(value: Any) -> Optional[str]:
    if not value:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"support", "customer", "kavya"}:
        return "Support"
    if normalized in {"sales", "lead", "nandini"}:
        return "Sales"
    return None


def _select_agent_type(config_dict: dict[str, Any], caller_status: Any) -> str:
    metadata_agent_type = _agent_type_from_metadata(config_dict.get("agent_type"))
    if metadata_agent_type:
        return metadata_agent_type
    return "Support" if caller_status == "Customer" else "Sales"
```

Then replace the current selection in `entrypoint(...)`:

```python
    agent_type = _select_agent_type(config_dict, caller_status)
```

**Step 4: Run tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_agent_call_context
```

Expected: PASS.

**Checkpoint:** Do not commit unless user explicitly requested commits.

---

### Task 7: Add Hermes Plugin Template for `make_phone_call`

**Objective:** Give Hermes a clean tool that calls the deployed API without exposing LiveKit credentials to Hermes.

**Files:**
- Create: `integrations/hermes/livekit-caller/plugin.yaml`
- Create: `integrations/hermes/livekit-caller/__init__.py`
- Create: `tests/test_hermes_livekit_plugin.py`

**Step 1: Create failing tests for plugin handler**

Create `tests/test_hermes_livekit_plugin.py`:

```python
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
                "status": "dispatched",
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


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run test to verify failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_hermes_livekit_plugin
```

Expected: FAIL because plugin files do not exist.

**Step 3: Create plugin manifest**

Create `integrations/hermes/livekit-caller/plugin.yaml`:

```yaml
name: livekit-caller
version: "0.1.0"
description: Call phone numbers through the deployed LiveKit voice agent call-control API
provides_tools:
  - make_phone_call
requires_env:
  - LIVEKIT_CALL_API_URL
  - LIVEKIT_CALL_API_TOKEN
```

**Step 4: Create plugin implementation**

Create `integrations/hermes/livekit-caller/__init__.py`:

```python
# -*- coding: utf-8 -*-
"""Hermes plugin for triggering outbound calls through the LiveKit call-control API."""

import json
import os
import urllib.error
import urllib.request


MAKE_PHONE_CALL_SCHEMA = {
    "name": "make_phone_call",
    "description": (
        "Place an outbound phone call through the deployed LiveKit voice agent. "
        "Use this only when the user explicitly asks to call someone or after they confirm a call. "
        "Requires a phone number and a short purpose/reason for the call."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "phone_number": {
                "type": "string",
                "description": "Destination phone number, preferably E.164 such as +919876543210",
            },
            "purpose": {
                "type": "string",
                "description": "Short reason the voice agent should give for the call",
            },
            "agent_type": {
                "type": "string",
                "enum": ["sales", "support"],
                "description": "Optional persona override",
            },
            "customer_name": {
                "type": "string",
                "description": "Optional name to pass as context",
            },
            "company_name": {
                "type": "string",
                "description": "Optional company name to pass as context",
            },
        },
        "required": ["phone_number", "purpose"],
    },
}


def _call_api_url() -> str:
    return os.environ.get("LIVEKIT_CALL_API_URL", "").rstrip("/")


def _call_api_token() -> str:
    return os.environ.get("LIVEKIT_CALL_API_TOKEN", "")


def make_phone_call(args: dict, **kwargs) -> str:
    del kwargs
    base_url = _call_api_url()
    token = _call_api_token()
    if not base_url or not token:
        return json.dumps(
            {
                "ok": False,
                "error": "LIVEKIT_CALL_API_URL and LIVEKIT_CALL_API_TOKEN must be configured in Hermes environment",
            }
        )

    payload = {
        "phone_number": args.get("phone_number"),
        "purpose": args.get("purpose"),
        "agent_type": args.get("agent_type"),
        "customer_name": args.get("customer_name"),
        "company_name": args.get("company_name"),
        "requested_by": "hermes",
        "metadata": {"source": "hermes-plugin"},
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        f"{base_url}/calls",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        return json.dumps({"ok": False, "status": err.code, "error": body})
    except Exception as err:
        return json.dumps({"ok": False, "error": str(err)})


def register(ctx):
    ctx.register_tool(
        name="make_phone_call",
        toolset="livekit_caller",
        schema=MAKE_PHONE_CALL_SCHEMA,
        handler=make_phone_call,
        description="Place an outbound phone call through the deployed LiveKit voice agent",
    )
```

**Step 5: Run plugin test**

Run:

```bash
.venv/bin/python -m unittest tests.test_hermes_livekit_plugin
```

Expected: PASS.

**Step 6: Document installation path**

Do not write to `~/.hermes/plugins/` automatically from implementation. Document manual installation/enablement instead:

```bash
mkdir -p ~/.hermes/plugins/livekit-caller
cp integrations/hermes/livekit-caller/* ~/.hermes/plugins/livekit-caller/
hermes plugins enable livekit-caller
```

Then restart Hermes or start a new session so plugin/tool changes load.

**Checkpoint:** Do not commit unless user explicitly requested commits.

---

### Task 8: Update `.env.example`

**Objective:** Make required call-control settings discoverable without leaking secrets.

**Files:**
- Modify: `.env.example:15-18`

**Step 1: Add env var examples**

Append to `.env.example`:

```bash
# Call Control API Settings
# Generate with: openssl rand -hex 32
CALL_API_TOKEN=your-long-random-call-api-token
CALL_API_ALLOWED_COUNTRY_PREFIXES=+91
CALL_API_DEFAULT_COUNTRY_CODE=+91
CALL_API_MAX_PURPOSE_CHARS=300

# Hermes plugin / external AI client settings
LIVEKIT_CALL_API_URL=https://your-call-api-domain.example.com
LIVEKIT_CALL_API_TOKEN=your-long-random-call-api-token
```

**Step 2: Verify no real secret is present**

Run:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
text = Path('.env.example').read_text()
assert 'your-long-random-call-api-token' in text
assert 'test-token' not in text
print('env example ok')
PY
```

Expected: `env example ok`.

**Checkpoint:** Do not commit unless user explicitly requested commits.

---

### Task 9: Update Local Testing Documentation

**Objective:** Document the local-first testing flow requested by the user, and keep Dokploy deployment as a later follow-up.

**Files:**
- Modify: `README.md:105-130`
- Modify: `docs/dokploy.md:63-89`

**Step 1: Update README with local run workflow**

Add a subsection under deployment/testing:

```markdown
### Local Call Control Test for Hermes / External AI

Before deploying to Dokploy, verify the call-control flow locally with two local processes:

1. Start the LiveKit worker:

```bash
uv run agent.py start
```

2. In another terminal, start the call-control API using the same `.env` LiveKit credentials:

```bash
CALL_API_TOKEN=local-test-token \
CALL_API_ALLOWED_COUNTRY_PREFIXES=+91 \
CALL_API_DEFAULT_COUNTRY_CODE=+91 \
uv run uvicorn call_api:app --host 127.0.0.1 --port 8000
```

3. Health-check the API:

```bash
curl -s http://127.0.0.1:8000/health
```

Expected:

```json
{"ok":true}
```

4. After the worker and API are both running, trigger the approved local test call:

```bash
curl -X POST "http://127.0.0.1:8000/calls" \
  -H "Authorization: Bearer local-test-token" \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+919062371141",
    "purpose": "Local integration test call from Hermes setup",
    "agent_type": "sales",
    "requested_by": "manual-local-test"
  }'
```

Only run this call when the number is safe to call and the LiveKit SIP outbound trunk is configured.
```

**Step 2: Update Dokploy reference to mark deployment as later**

In `docs/dokploy.md`, add a short note before the deployment section:

```markdown
## Call Control API Rollout Status

The call-control API should be verified locally before creating a Dokploy API app. Local verification uses:

- Worker: `uv run agent.py start`
- API: `uv run uvicorn call_api:app --host 127.0.0.1 --port 8000`
- Test call target approved by the user: `+919062371141`

After local verification passes, create a second Dokploy application for the API with command:

```bash
uv run uvicorn call_api:app --host 0.0.0.0 --port 8000
```

Expose only the API app over HTTPS. Keep the worker app private/no inbound port.
```

**Step 3: Verify markdown references**

Run:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
checks = {
    'README.md': ['Local Call Control Test', '+919062371141', '127.0.0.1:8000'],
    'docs/dokploy.md': ['Call Control API Rollout Status', '+919062371141', 'uvicorn call_api:app'],
}
for path, needles in checks.items():
    text = Path(path).read_text()
    for needle in needles:
        assert needle in text, f'{needle!r} missing from {path}'
print('local testing docs ok')
PY
```

Expected: `local testing docs ok`.

**Checkpoint:** Do not commit unless user explicitly requested commits.

---

### Task 10: Run Focused Test Suite

**Objective:** Verify the call-control changes do not regress existing call context, tools, or web UI behavior.

**Files:**
- Test-only task

**Step 1: Run new tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_call_api tests.test_hermes_livekit_plugin
```

Expected: PASS.

**Step 2: Run call context tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_agent_call_context
```

Expected: PASS.

**Step 3: Run existing lightweight tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_agent_tools tests.test_web_ui
```

Expected: PASS.

**Step 4: Run all unit tests**

Run:

```bash
.venv/bin/python -m unittest discover -s tests
```

Expected: PASS, excluding integration tests that require real Frappe/LiveKit services.

**Checkpoint:** Do not commit unless user explicitly requested commits.

---

### Task 11: Manual Local API Smoke Test Before Real Call

**Objective:** Verify the local API starts and answers health checks before triggering the approved real call.

**Files:**
- Test-only task

**Step 1: Confirm local `.env` has real credentials**

Confirm `.env` contains real values for:

```bash
LIVEKIT_URL
LIVEKIT_API_KEY
LIVEKIT_API_SECRET
OUTBOUND_TRUNK_ID
OPENAI_API_KEY or GOOGLE_API_KEY
```

Do not print these values in logs or chat. Only confirm they are present.

**Step 2: Start API locally**

Run in one terminal:

```bash
CALL_API_TOKEN=test-token \
CALL_API_ALLOWED_COUNTRY_PREFIXES=+91 \
CALL_API_DEFAULT_COUNTRY_CODE=+91 \
uv run uvicorn call_api:app --host 127.0.0.1 --port 8000
```

Expected: uvicorn starts on `http://127.0.0.1:8000` and loads the real LiveKit settings from `.env`.

**Step 3: Health check**

Run in another terminal:

```bash
curl -s http://127.0.0.1:8000/health
```

Expected:

```json
{"ok":true}
```

**Step 4: Stop the API if health check fails**

If `/health` fails, stop here and fix the API startup problem before moving to the real call task.

**Checkpoint:** Do not commit unless user explicitly requested commits.

---

### Task 12: Manual Local LiveKit Integration Test to `+919062371141`

**Objective:** Prove the local API dispatches the local worker and the worker dials the user-approved test number through SIP.

**Files:**
- No code changes unless this test reveals a bug

**Prerequisites:**

- Real `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` in `.env`
- Real `OUTBOUND_TRUNK_ID` in `.env` for the worker
- Real model key in `.env` (`OPENAI_API_KEY` or `GOOGLE_API_KEY`) matching `agent_config.json`
- Worker running locally with `uv run agent.py start`
- Call API running locally with the same `.env` LiveKit credentials
- User-approved test number: `+919062371141`

**Step 1: Start worker locally**

Run:

```bash
uv run agent.py start
```

Expected: worker connects to LiveKit and registers as `LIVEKIT_AGENT_NAME` / `AGENT_NAME`. Keep this terminal open and watch logs.

**Step 2: Start API locally**

Run:

```bash
CALL_API_TOKEN=test-token \
CALL_API_ALLOWED_COUNTRY_PREFIXES=+91 \
CALL_API_DEFAULT_COUNTRY_CODE=+91 \
uv run uvicorn call_api:app --host 127.0.0.1 --port 8000
```

Expected: API starts.

**Step 3: Trigger exactly one test call**

Run the call request once:

```bash
curl -X POST "http://127.0.0.1:8000/calls" \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+919062371141",
    "purpose": "Local integration test call from Hermes setup",
    "agent_type": "sales",
    "requested_by": "manual-local-test"
  }'
```

Expected API response:

```json
{
  "ok": true,
  "call_id": "call_...",
  "room_name": "agent_call_call_...",
  "status": "dispatched",
  "phone_number": "+919062371141"
}
```

Expected worker logs:

- room name starts with `agent_call_call_`
- resolved context is `direction=outbound`
- `Initiating outbound SIP call...`
- after answer, agent waits for callee speech before replying

Expected real-world result:

- `+919062371141` receives one phone call.
- The agent should not speak until the callee answers/speaks.
- The first agent response should reference the local integration test purpose, not a generic enquiry unless metadata was missing.

**Step 4: Stop local processes after the test**

After the call completes or fails, stop both local terminals with `Ctrl+C`:

```bash
# Terminal 1: stop uv run agent.py start
# Terminal 2: stop uv run uvicorn call_api:app ...
```

**Step 5: If LiveKit says room already exists**

Patch `_dispatch_livekit_agent(...)` to ignore only the specific “already exists” / already-created room error and still create dispatch. Re-run unit and manual test.

**Checkpoint:** Do not commit unless user explicitly requested commits.

---

## Optional Phase 2: MCP Server Instead of Hermes Plugin

The REST API is enough for “Hermes or any other AI” if the other AI can call HTTP tools. If you want first-class MCP compatibility, add a small MCP server later that exposes `make_call(phone_number, purpose, agent_type)` and internally POSTs to `/calls`.

Recommended after Phase 1 is stable:

- Create: `integrations/mcp/livekit_calls_server.py`
- Add dependency only if needed: official `mcp` Python package
- Expose a tool named `make_call`
- Configure Hermes:

```yaml
mcp_servers:
  livekit_calls:
    command: "uv"
    args: ["run", "python", "integrations/mcp/livekit_calls_server.py"]
    env:
      LIVEKIT_CALL_API_URL: "https://your-call-api-domain.example.com"
      LIVEKIT_CALL_API_TOKEN: "${LIVEKIT_CALL_API_TOKEN}"
```

Do not do this before Phase 1 unless another AI platform specifically requires MCP.

---

## Risks, Tradeoffs, and Open Questions

### Risks

1. **Abuse / accidental calls:** The API can place real calls. Mitigate with bearer auth, HTTPS, prefix restrictions, rate limits, logs, and explicit Hermes tool descriptions.
2. **Cost exposure:** Outbound SIP calls may cost money. Start with `CALL_API_ALLOWED_COUNTRY_PREFIXES=+91` and a small allowlist if needed.
3. **Room already exists errors:** The first implementation may need a small LiveKit Twirp error handler after manual testing.
4. **Agent chooses wrong persona:** Metadata `agent_type` override reduces this risk, but defaults should remain current behavior.
5. **Prompt mismatch:** If `call_purpose` is missing, the agent will fall back to generic outbound behavior. API should require purpose.
6. **Local real-call risk:** The first manual integration test intentionally calls `+919062371141`; run the curl command exactly once and stop local processes afterward.
7. **Later Dokploy routing:** When deployment happens later, the worker app should remain private; only the API app should get an HTTPS route.

### Tradeoffs

- **REST API first vs MCP first:** REST is simpler, universal, easier to secure/deploy, and can be consumed by Hermes through a plugin. MCP can be added later for more agent-native integrations.
- **Separate API service vs same container:** Separate app is cleaner and easier to scale/secure. Same container needs a supervisor and mixes worker/API lifecycles.
- **Simple regex phone validation vs `phonenumbers`:** Regex avoids another dependency and is good enough for initial E.164 enforcement. Add `phonenumbers` later if formatting rules become complex.

### Open Questions

1. After the first local test call to `+919062371141`, should calls be restricted to a configured allowlist of numbers, not just country prefixes?
2. Should Hermes be allowed to call without explicit human confirmation, or should the plugin description require confirmation before placing calls?
3. Should the API persist call records to a database/Redis, or is in-memory status enough for v1?
4. Should `agent_type` override always win, or should customers always go to Support regardless of API request?
5. Do we need a `POST /calls/{call_id}/hangup` endpoint in v1, or can LiveKit room cleanup remain worker-managed?

---

## Final Verification Checklist

- [ ] `uv sync` succeeds.
- [ ] `call_api.py` imports successfully.
- [ ] `POST /calls` rejects missing/wrong auth.
- [ ] `POST /calls` rejects disallowed numbers.
- [ ] `POST /calls` requires non-empty purpose.
- [ ] `POST /calls` dispatches metadata with `call_direction=outbound` and `phone_number`.
- [ ] `agent.py` prompt includes `call_purpose` and `requested_by`.
- [ ] Agent still passes existing inbound/outbound context tests.
- [ ] Hermes plugin test confirms it posts to `/calls` with bearer auth.
- [ ] Docs explain the local-first worker/API test flow and mark Dokploy deployment as later.
- [ ] Manual local integration test places exactly one intentional call to `+919062371141`.
