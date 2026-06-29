# Outbound API Dial + Portable LiveKit Worker Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Refactor outbound calling so the Call API server owns SIP dialing, exact call status, dashboard/storage, and Hermes integration, while the LiveKit worker becomes a simple portable conversation worker deployable locally, on Dokploy, or on LiveKit Cloud.

**Architecture:** `call_api.py` becomes the outbound call control plane: it validates Hermes requests, creates the LiveKit room, dispatches the selected worker, creates the SIP participant with `wait_until_answered=True`, stores exact SIP outcomes, and serves status/dashboard data. `agent.py` becomes the media/conversation worker: in API-dialed outbound mode it waits for the already-created SIP participant, starts the realtime session, and later can post transcript/session-report data back to the API. Use distinct `LIVEKIT_AGENT_NAME` values for local/Dokploy/prod workers to avoid dispatch ambiguity.

**Tech Stack:** Python 3.11+, FastAPI, LiveKit Python SDK, LiveKit Agents SDK, SQLite via existing `call_status_store.py`, `unittest`, Hermes plugin under `integrations/hermes/livekit-caller/`.

---

## Current context / assumptions

- Inbound call flow is obsolete and should not drive this refactor.
- Hermes should continue to call only the authenticated Call API, not LiveKit directly.
- The Call API can remain local for testing and Dokploy-hosted for production control/dashboard.
- Worker deployment targets:
  - local testing: `LIVEKIT_AGENT_NAME=outbound-caller-local`
  - Dokploy testing: `LIVEKIT_AGENT_NAME=outbound-caller-dokploy`
  - production LiveKit Cloud: `LIVEKIT_AGENT_NAME=outbound-caller-prod`
- Do not run two workers with the same `LIVEKIT_AGENT_NAME` in the same LiveKit project.
- Keep SQLite/dashboard for now. Later, recording/transcript files should be stored outside SQLite; SQLite stores metadata/status/report pointers.
- Tests use `unittest`, not pytest.
- Do not commit during implementation unless the user explicitly asks; commit steps in this plan are intentionally replaced with `git diff` verification because repo guidance says not to commit unless asked.

---

## Proposed approach

1. Extract SIP outcome mapping from `agent.py` into a small shared module so both API and worker/tests use the same logic.
2. Add API-owned SIP dialing in `call_api.py` using `api.CreateSIPParticipantRequest(... wait_until_answered=True)`.
3. Change `/calls` so it creates the room, dispatches the selected worker, dials the SIP participant, records exact answered/failure state, and returns that status to Hermes.
4. Add dispatch metadata flag `outbound_dial_mode: "api"` plus deterministic `sip_participant_identity`.
5. Modify `agent.py` so API-dialed outbound calls do **not** call `_ensure_outbound_participant`; instead they wait for the SIP participant already created by the API.
6. Keep legacy worker-dial path temporarily as fallback for tests/manual rollback, but make API-dial mode the Call API default.
7. Update Hermes plugin docs/tests to understand statuses beyond `dispatched`.
8. Update docs with deployment matrix and future transcript/session-report plan.

---

## Desired final runtime flow

```text
Hermes make_phone_call
  → POST /calls on Call API
      → validate token + phone number + purpose
      → create call record: dispatching
      → create LiveKit room agent_call_<call_id>
      → dispatch worker selected by LIVEKIT_AGENT_NAME
      → update call record: dispatched
      → create SIP participant with wait_until_answered=True
          → if answered: status answered / active-ready
          → if TwirpError: map SIP code to failed_busy / failed_no_answer / failed_unreachable / failed_rejected / failed_trunk
      → return exact status to Hermes

LiveKit worker
  → receives dispatch with outbound_dial_mode=api
  → waits for sip_<phone> participant
  → starts AgentSession linked to that participant
  → does not auto-greet before callee speaks
  → later: can POST session report/transcript back to Call API
```

---

## Files likely to change

- Create: `call_outcomes.py`
- Modify: `call_api.py`
- Modify: `agent.py`
- Modify: `integrations/hermes/livekit-caller/__init__.py`
- Modify: `tests/test_call_api.py`
- Modify: `tests/test_agent_call_context.py`
- Modify: `tests/test_hermes_livekit_plugin.py`
- Modify: `docs/hermes-call-control.md`
- Modify: `docs/dokploy.md`
- Possibly modify: `README.md`
- Possibly later modify: `.env.example`

---

## Task 1: Add shared outbound SIP outcome mapping

**Objective:** Move status/reason mapping into a shared module that both `call_api.py` and `agent.py` can use.

**Files:**
- Create: `call_outcomes.py`
- Modify: `agent.py:286-318`
- Test: `tests/test_agent_call_context.py`
- Test: `tests/test_call_api.py`

**Step 1: Create failing tests for shared mapping**

Add to `tests/test_call_api.py` or a new focused test file `tests/test_call_outcomes.py`:

```python
# -*- coding: utf-8 -*-
import unittest

from call_outcomes import failure_status_for_reason, sip_failure_reason


class CallOutcomeTestCase(unittest.TestCase):
    def test_sip_failure_reason_maps_common_outcomes(self):
        self.assertEqual(sip_failure_reason("486", "Busy Here"), "busy")
        self.assertEqual(sip_failure_reason("603", "Decline"), "rejected")
        self.assertEqual(sip_failure_reason("408", "Request Timeout"), "no_answer")
        self.assertEqual(sip_failure_reason("480", "Temporarily Unavailable"), "unreachable")
        self.assertEqual(sip_failure_reason("503", "Service Unavailable"), "trunk")

    def test_failure_status_for_reason_maps_api_statuses(self):
        self.assertEqual(failure_status_for_reason("busy"), "failed_busy")
        self.assertEqual(failure_status_for_reason("rejected"), "failed_rejected")
        self.assertEqual(failure_status_for_reason("no_answer"), "failed_no_answer")
        self.assertEqual(failure_status_for_reason("unreachable"), "failed_unreachable")
        self.assertEqual(failure_status_for_reason("trunk"), "failed_trunk")
        self.assertEqual(failure_status_for_reason("other"), "failed")


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run test to verify failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_call_outcomes -v
```

Expected: FAIL because `call_outcomes.py` does not exist yet.

**Step 3: Create shared implementation**

Create `call_outcomes.py`:

```python
# -*- coding: utf-8 -*-
"""Shared outbound SIP call outcome mapping."""

from typing import Any


def sip_failure_reason(
    sip_status_code: Any = None,
    sip_status: Any = None,
    message: Any = None,
) -> str:
    """Map carrier/SIP failure details to stable API reasons."""
    try:
        code = int(str(sip_status_code)) if sip_status_code is not None else None
    except (TypeError, ValueError):
        code = None

    text = " ".join(str(item or "") for item in (sip_status, message)).lower()
    if code == 486 or "busy" in text:
        return "busy"
    if code == 408 or "timeout" in text or "timed out" in text:
        return "no_answer"
    if code == 603 or "decline" in text or "rejected" in text:
        return "rejected"
    if code in {480, 404, 410, 484, 604} or "unavailable" in text or "not found" in text:
        return "unreachable"
    if code in {401, 403, 500, 502, 503, 504} or "trunk" in text:
        return "trunk"
    return "sip_error"


def failure_status_for_reason(reason: str) -> str:
    """Convert stable failure reason to public call status."""
    return {
        "busy": "failed_busy",
        "no_answer": "failed_no_answer",
        "rejected": "failed_rejected",
        "unreachable": "failed_unreachable",
        "trunk": "failed_trunk",
    }.get(reason, "failed")
```

**Step 4: Update `agent.py` imports and wrappers**

Modify `agent.py`:

```python
from call_outcomes import failure_status_for_reason, sip_failure_reason
```

Then either:

- replace `_sip_failure_reason(...)` usages with `sip_failure_reason(...)`
- replace `_failure_status_for_reason(...)` usages with `failure_status_for_reason(...)`

For backward compatibility with existing tests, keep thin wrappers temporarily:

```python
def _sip_failure_reason(sip_status_code: Any = None, sip_status: Any = None, message: Any = None) -> str:
    return sip_failure_reason(sip_status_code, sip_status, message)


def _failure_status_for_reason(reason: str) -> str:
    return failure_status_for_reason(reason)
```

**Step 5: Verify tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_call_outcomes -v
.venv/bin/python -m unittest tests.test_agent_call_context -v
```

Expected: PASS.

**Step 6: Inspect diff**

Run:

```bash
git diff -- call_outcomes.py agent.py tests/test_call_outcomes.py tests/test_agent_call_context.py
```

Expected: Only shared mapping and imports changed.

---

## Task 2: Add Call API helper for creating rooms and dispatching without dialing

**Objective:** Split room creation/agent dispatch into explicit helper(s), preparing `/calls` to dial from the API.

**Files:**
- Modify: `call_api.py:158-169`
- Test: `tests/test_call_api.py`

**Step 1: Add a focused test for dispatch metadata**

Update `tests/test_call_api.py` to expect metadata fields for API-dialed mode after `/calls` is refactored:

```python
self.assertEqual(captured["metadata"]["outbound_dial_mode"], "api")
self.assertEqual(captured["metadata"]["sip_participant_identity"], f"sip_{body['phone_number']}")
```

For now this may fail until Task 4.

**Step 2: Refactor helper naming**

Keep existing helper but make behavior clear:

```python
async def _create_room_and_dispatch_agent(room_name: str, dispatch_metadata: dict[str, Any]) -> Any:
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
```

Retain alias for tests during migration if needed:

```python
_dispatch_livekit_agent = _create_room_and_dispatch_agent
```

**Step 3: Verify existing API tests still pass**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -p test_call_api.py -v
```

Expected: PASS unless metadata expectations from Step 1 are already added; if added, expect failure until Task 4.

---

## Task 3: Add Call API SIP dialing helper with exact failure mapping

**Objective:** Add `_create_outbound_sip_participant()` in `call_api.py` so the API can dial and catch exact SIP failures.

**Files:**
- Modify: `call_api.py`
- Test: `tests/test_call_api.py`

**Step 1: Add failing unit tests for SIP helper**

Add fake API classes in `tests/test_call_api.py` or a new `tests/test_call_api_sip.py`.

Test successful helper behavior:

```python
async def test_create_outbound_sip_participant_uses_trunk_and_waits(self):
    captured = {}

    class FakeSip:
        async def create_sip_participant(self, request):
            captured["request"] = request
            return type("Info", (), {"sip_call_id": "sip-call-123"})()

    class FakeLiveKitAPI:
        def __init__(self):
            self.sip = FakeSip()
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            return None

    with patch.dict(os.environ, {"OUTBOUND_TRUNK_ID": "ST_TEST"}, clear=False), \
         patch("call_api.api.LiveKitAPI", FakeLiveKitAPI):
        info = await self.call_api._create_outbound_sip_participant(
            room_name="agent_call_call_123",
            phone_number="+919876543210",
            participant_identity="sip_+919876543210",
        )

    self.assertEqual(info.sip_call_id, "sip-call-123")
    self.assertEqual(captured["request"].kwargs["sip_trunk_id"], "ST_TEST")
    self.assertTrue(captured["request"].kwargs["wait_until_answered"])
```

Adjust request inspection to match actual LiveKit proto object behavior in tests. Existing fake request in `tests/test_agent_call_context.py` may be reusable.

**Step 2: Add missing-trunk test**

```python
def test_outbound_trunk_id_required_for_api_dial(self):
    with patch.dict(os.environ, {}, clear=True):
        self.assertIsNone(self.call_api._outbound_trunk_id({}))
```

**Step 3: Implement trunk helper in `call_api.py`**

```python
def _outbound_trunk_id(config_dict: Optional[dict[str, Any]] = None) -> Optional[str]:
    config_dict = config_dict or {}
    return (
        config_dict.get("outbound_trunk_id")
        or config_dict.get("sip_trunk_id")
        or os.environ.get("OUTBOUND_TRUNK_ID")
        or os.environ.get("LIVEKIT_OUTBOUND_TRUNK_ID")
        or os.environ.get("SIP_OUTBOUND_TRUNK_ID")
    )
```

**Step 4: Implement SIP helper in `call_api.py`**

```python
async def _create_outbound_sip_participant(
    *,
    room_name: str,
    phone_number: str,
    participant_identity: str,
    config_dict: Optional[dict[str, Any]] = None,
) -> Any:
    trunk_id = _outbound_trunk_id(config_dict)
    if not trunk_id:
        raise RuntimeError("Outbound SIP trunk not configured")

    async with api.LiveKitAPI() as lk:
        return await lk.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=room_name,
                sip_trunk_id=trunk_id,
                sip_call_to=phone_number,
                participant_identity=participant_identity,
                wait_until_answered=True,
            )
        )
```

**Step 5: Verify helper tests**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -p test_call_api.py -v
```

Expected: PASS for new helper tests after fake request inspection is aligned.

---

## Task 4: Change `/calls` to API-dial and return exact setup result

**Objective:** Make `POST /calls` create room, dispatch worker, dial SIP, store exact status, and return answered/failure immediately.

**Files:**
- Modify: `call_api.py:126-224`
- Test: `tests/test_call_api.py`

**Step 1: Extend response model**

Modify `CallResponse` in `call_api.py`:

```python
class CallResponse(BaseModel):
    ok: bool
    call_id: str
    room_name: str
    status: str
    phone_number: str
    reason: Optional[str] = None
    sip_status_code: Optional[str] = None
    sip_status: Optional[str] = None
    sip_call_id: Optional[str] = None
    error: Optional[str] = None
```

**Step 2: Add failing success test**

Update `test_call_endpoint_dispatches_livekit_agent_with_outbound_metadata` to also patch `_create_outbound_sip_participant`:

```python
async def fake_sip_dial(**kwargs):
    captured["sip_dial"] = kwargs
    return type("Info", (), {"sip_call_id": "sip-call-123"})()

with patch("call_api._create_room_and_dispatch_agent", new=fake_dispatch), \
     patch("call_api._create_outbound_sip_participant", new=fake_sip_dial):
    response = self.client.post(...)

self.assertEqual(body["status"], "answered")
self.assertEqual(body["sip_call_id"], "sip-call-123")
self.assertEqual(captured["sip_dial"]["room_name"], body["room_name"])
self.assertEqual(captured["sip_dial"]["phone_number"], body["phone_number"])
```

**Step 3: Add failing busy test**

```python
def test_call_endpoint_returns_busy_when_sip_reports_486(self):
    async def fake_dispatch(room_name, metadata):
        return object()

    async def fake_sip_dial(**kwargs):
        raise self.call_api.api.TwirpError(
            "callee busy",
            metadata={"sip_status_code": "486", "sip_status": "Busy Here"},
        )

    with patch("call_api._create_room_and_dispatch_agent", new=fake_dispatch), \
         patch("call_api._create_outbound_sip_participant", new=fake_sip_dial):
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
```

**Step 4: Implement dispatch metadata changes**

In `create_call`, add:

```python
participant_identity = f"sip_{normalized_phone}"
```

Add to `dispatch_metadata`:

```python
"outbound_dial_mode": "api",
"sip_participant_identity": participant_identity,
```

**Step 5: Implement new `/calls` sequence**

In `create_call`, sequence should be:

```python
create_call_record(... status="dispatching" ...)

try:
    await _create_room_and_dispatch_agent(room_name, dispatch_metadata)
except Exception as err:
    update_call_record(... status="dispatch_failed" ...)
    raise HTTPException(...)

update_call_record(call_id, status="dispatched", event_message="LiveKit agent dispatched")

try:
    sip_info = await _create_outbound_sip_participant(
        room_name=room_name,
        phone_number=normalized_phone,
        participant_identity=participant_identity,
        config_dict=dispatch_metadata,
    )
except api.TwirpError as err:
    metadata = getattr(err, "metadata", {}) or {}
    sip_status_code = metadata.get("sip_status_code")
    sip_status = metadata.get("sip_status")
    error_message = getattr(err, "message", str(err))
    reason = sip_failure_reason(sip_status_code, sip_status, error_message)
    status = failure_status_for_reason(reason)
    update_call_record(
        call_id,
        status=status,
        reason=reason,
        sip_status_code=sip_status_code,
        sip_status=sip_status,
        error=error_message,
        event_message="Outbound SIP call failed",
    )
    # Optional cleanup: delete room here, or defer to room timeout.
    return CallResponse(
        ok=False,
        call_id=call_id,
        room_name=room_name,
        status=status,
        phone_number=normalized_phone,
        reason=reason,
        sip_status_code=str(sip_status_code) if sip_status_code is not None else None,
        sip_status=sip_status,
        error=error_message,
    )
except Exception as err:
    update_call_record(... status="failed" reason="worker_error" ...)
    return CallResponse(ok=False, ...)

update_call_record(
    call_id,
    status="answered",
    sip_call_id=getattr(sip_info, "sip_call_id", None),
    event_message="Outbound SIP call answered",
)
return CallResponse(ok=True, status="answered", sip_call_id=...)
```

**Step 6: Decide cleanup behavior for failed calls**

Implementation should either:

- call `lk.room.delete_room(...)` on failure, or
- rely on room empty timeout.

Recommended: add helper `_delete_room_quietly(room_name)` and call it after SIP failure to avoid stranded rooms. Test only that failure returns correct status; cleanup can be best-effort and log-only.

**Step 7: Verify tests**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -p test_call_api.py -v
```

Expected: PASS.

---

## Task 5: Make worker skip dialing in API-dialed mode

**Objective:** Prevent `agent.py` from dialing a second SIP participant when `call_api.py` already created the SIP participant.

**Files:**
- Modify: `agent.py:376-489`
- Modify: `agent.py:782-790`
- Test: `tests/test_agent_call_context.py`

**Step 1: Add helper predicate**

In `agent.py`, add:

```python
def _dialed_by_api(config_dict: dict[str, Any]) -> bool:
    return str(config_dict.get("outbound_dial_mode") or "").strip().lower() == "api"
```

**Step 2: Add API-dial participant wait helper**

Add:

```python
async def _wait_for_api_dialed_outbound_participant(
    ctx: agents.JobContext,
    call_context: CallContext,
    config_dict: dict[str, Any],
) -> CallContext:
    if not call_context.is_outbound:
        return call_context

    participant_identity = (
        config_dict.get("sip_participant_identity")
        or call_context.participant_identity
        or (f"sip_{call_context.phone_number}" if call_context.phone_number else None)
    )
    participant = await _wait_for_sip_participant(
        ctx,
        participant_identity=participant_identity,
        use_job_wait=True,
    )
    if not participant:
        logger.error("API-dialed outbound call did not get SIP participant: %s", participant_identity)
        ctx.shutdown(reason="Outbound SIP participant did not join")
        call_context.ready = False
        return call_context
    return _build_call_context(ctx, config_dict, sip_participant=participant)
```

**Step 3: Add failing test that API mode does not dial**

In `tests/test_agent_call_context.py`:

```python
async def test_api_dialed_outbound_waits_for_existing_sip_participant_without_dialing(self):
    ctx = FakeContext(FakeRoom(name="agent_call_abc123"))
    config = {
        "phone_number": "+919876543210",
        "outbound_dial_mode": "api",
        "sip_participant_identity": "sip_+919876543210",
    }
    call_context = agent.CallContext(direction="outbound", phone_number="+919876543210")

    async def add_participant_after_wait(_delay):
        ctx.room.remote_participants["sip_+919876543210"] = FakeParticipant(
            identity="sip_+919876543210",
            attributes={"sip.phoneNumber": "+919876543210", "sip.callStatus": "active"},
        )

    with patch.object(agent.asyncio, "sleep", new=add_participant_after_wait):
        updated_context = await agent._prepare_outbound_participant(ctx, call_context, config)

    self.assertTrue(updated_context.ready)
    self.assertEqual(updated_context.sip_call_status, "active")
    self.assertEqual(ctx.api.sip.requests, [])
```

**Step 4: Implement unified preparation helper**

Add:

```python
async def _prepare_outbound_participant(
    ctx: agents.JobContext,
    call_context: CallContext,
    config_dict: dict[str, Any],
) -> CallContext:
    if call_context.is_outbound and _dialed_by_api(config_dict):
        return await _wait_for_api_dialed_outbound_participant(ctx, call_context, config_dict)
    return await _ensure_outbound_participant(ctx, call_context, config_dict)
```

**Step 5: Use helper in entrypoint**

Replace in `agent.py:784`:

```python
call_context = await _ensure_outbound_participant(ctx, call_context, config_dict)
```

with:

```python
call_context = await _prepare_outbound_participant(ctx, call_context, config_dict)
```

**Step 6: Verify worker tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_agent_call_context -v
```

Expected: PASS, including legacy worker-dial tests.

---

## Task 6: Ensure AgentSession links to the correct SIP participant

**Objective:** Make the worker session explicitly listen/respond to the SIP participant created by the API.

**Files:**
- Modify: `agent.py:792-805`
- Test: `tests/test_agent_call_context.py` if practical; otherwise targeted inspection plus existing tests.

**Step 1: Inspect current RoomOptions API compatibility**

Confirm installed LiveKit Agents version supports `RoomOptions(participant_identity=...)` or equivalent. If uncertain, inspect package docs or installed signatures with a read-only Python command:

```bash
.venv/bin/python - <<'PY'
import inspect
from livekit.agents.voice.room_io import RoomOptions
print(inspect.signature(RoomOptions))
PY
```

Expected: signature includes `participant_identity` or a related field. If not, skip explicit linking and rely on first SIP participant.

**Step 2: Update session start options if supported**

Preferred implementation:

```python
await session.start(
    room=ctx.room,
    agent=agent_instance,
    room_options=RoomOptions(
        participant_identity=call_context.participant_identity,
        audio_input=AudioInputOptions(
            noise_cancellation=nc_option,
        ),
        close_on_disconnect=True,
        delete_room_on_close=True,
    ),
)
```

If `participant_identity` is not supported in this SDK version, do not force it; keep current behavior and document that `AgentSession` links to the first participant after API-created SIP participant joins.

**Step 3: Verify smoke import**

Run:

```bash
.venv/bin/python -m py_compile agent.py
```

Expected: no syntax errors.

**Step 4: Verify targeted tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_agent_call_context -v
```

Expected: PASS.

---

## Task 7: Update Hermes plugin expectations for immediate statuses

**Objective:** Ensure Hermes plugin can pass through and document immediate `answered`/failure responses from `/calls`.

**Files:**
- Modify: `integrations/hermes/livekit-caller/__init__.py:11-63`
- Modify: `tests/test_hermes_livekit_plugin.py`

**Step 1: Add test for failure response pass-through**

In `tests/test_hermes_livekit_plugin.py`, add or update a test where the fake API returns:

```json
{
  "ok": false,
  "call_id": "call_123",
  "room_name": "agent_call_call_123",
  "status": "failed_busy",
  "reason": "busy",
  "sip_status_code": "486",
  "sip_status": "Busy Here",
  "phone_number": "+919876543210"
}
```

Expected plugin returns this JSON unchanged enough for Hermes to summarize.

**Step 2: Update tool description**

Modify `MAKE_PHONE_CALL_SCHEMA["description"]` to mention the tool returns immediate setup result:

```python
"Place an outbound phone call through the deployed LiveKit call-control API. "
"The API returns the immediate dial outcome, such as answered, failed_busy, "
"failed_no_answer, failed_unreachable, failed_rejected, or failed_trunk. "
"Use this only when the user explicitly asks to call someone or after they confirm a call."
```

**Step 3: Consider `get_phone_call_status` semantics**

Keep `get_phone_call_status` for dashboard/SQLite record lookup for now. If `/calls` becomes enough, later deprecate it; do not remove in this refactor.

**Step 4: Verify plugin tests**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -p test_hermes_livekit_plugin.py -v
```

Expected: PASS.

---

## Task 8: Update dashboard/status records for API-owned dialing

**Objective:** Make stored call records and dashboard reflect the new status flow accurately.

**Files:**
- Modify: `call_api.py`
- Possibly modify: `call_dashboard.py`
- Test: `tests/test_call_api.py`

**Step 1: Verify existing statuses display generically**

Read `call_dashboard.py` before changing. If it already displays arbitrary `status`, `reason`, `sip_status_code`, and events, no UI change is needed.

**Step 2: Add test expectations for event history**

In successful `/calls` test, expect records:

```python
self.assertEqual(status_body["status"], "answered")
self.assertEqual(status_body["sip_call_id"], "sip-call-123")
self.assertGreaterEqual(len(status_body["events"]), 3)
```

In busy test, fetch `/calls/{call_id}` and expect:

```python
self.assertEqual(status_body["status"], "failed_busy")
self.assertEqual(status_body["reason"], "busy")
self.assertEqual(status_body["sip_status_code"], "486")
```

**Step 3: Verify dashboard data summary counts**

Existing `_dashboard_summary` already counts statuses starting with `failed` as failures. Add assertions:

```python
self.assertEqual(dashboard_body["summary"]["failures"], 1)
self.assertEqual(dashboard_body["summary"]["busy"], 1)
```

**Step 4: Verify tests**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -p test_call_api.py -v
```

Expected: PASS.

---

## Task 9: Add future transcript/session-report callback scaffold without storing transcripts yet

**Objective:** Prepare the architecture for future transcripts without implementing full recording storage now.

**Files:**
- Modify: `agent.py`
- Modify: `docs/hermes-call-control.md`
- Possibly create later: `transcript_client.py`
- Test: skip or add minimal unit test if helper is pure.

**Step 1: Add metadata pass-through only**

Ensure dispatch metadata includes `call_id` and `room_name`; current flow already includes `call_id`. Add no new transcript code unless needed.

**Step 2: Document future endpoint**

In docs, describe planned endpoint:

```text
POST /internal/calls/{call_id}/session-report
Authorization: Bearer <CALL_API_INTERNAL_TOKEN>
```

Payload will eventually include:

```json
{
  "room_name": "agent_call_call_xxx",
  "report": "ctx.make_session_report().to_dict()"
}
```

**Step 3: Do not implement storage yet**

Reason: avoid mixing call-flow refactor with transcript persistence. Keep this as future work after outbound flow is stable.

---

## Task 10: Update deployment docs for local/Dokploy/LiveKit Cloud worker selection

**Objective:** Document how to run the same worker locally, on Dokploy, and on LiveKit Cloud using distinct `LIVEKIT_AGENT_NAME` values.

**Files:**
- Modify: `docs/hermes-call-control.md`
- Modify: `docs/dokploy.md`
- Modify: `README.md`
- Possibly modify: `.env.example`

**Step 1: Update architecture diagram**

In `docs/hermes-call-control.md`, replace old flow:

```text
Hermes → POST /calls → call_api.py → LiveKit room + agent dispatch → agent.py outbound SIP
```

with:

```text
Hermes → POST /calls → call_api.py
  → create LiveKit room
  → dispatch selected worker
  → create SIP participant with wait_until_answered=True
  → return exact dial status
  → worker handles answered conversation
```

**Step 2: Document worker names**

Add:

```text
Recommended worker dispatch names:
- outbound-caller-local: local testing
- outbound-caller-dokploy: Dokploy testing
- outbound-caller-prod: LiveKit Cloud production
```

**Step 3: Document env examples**

Local API + local worker:

```bash
LIVEKIT_AGENT_NAME=outbound-caller-local uv run agent.py start
LIVEKIT_AGENT_NAME=outbound-caller-local uv run uvicorn call_api:app --host 127.0.0.1 --port 8000
```

Dokploy API dispatching LiveKit Cloud worker:

```env
LIVEKIT_AGENT_NAME=outbound-caller-prod
CALL_API_TOKEN=...
OUTBOUND_TRUNK_ID=ST_...
CALL_API_ALLOWED_COUNTRY_PREFIXES=+91
```

LiveKit Cloud worker secrets:

```env
LIVEKIT_AGENT_NAME=outbound-caller-prod
FRAPPE_SITE_URL=...
FRAPPE_API_KEY=...
FRAPPE_API_SECRET=...
OPENAI_API_KEY=...
GOOGLE_API_KEY=...
```

Note: LiveKit Cloud injects `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` for the worker deployment; do not manually set those as LiveKit Cloud secrets.

**Step 4: Update status examples**

Document immediate `/calls` statuses:

```text
answered
failed_busy
failed_no_answer
failed_unreachable
failed_rejected
failed_trunk
failed
```

**Step 5: Verify docs contain no secrets**

Run:

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
for path in [Path('docs/hermes-call-control.md'), Path('docs/dokploy.md'), Path('README.md')]:
    text = path.read_text()
    assert 'LIVEKIT_API_SECRET=' not in text
    assert 'FRAPPE_API_SECRET=' not in text
print('docs secret placeholder check passed')
PY
```

Expected: `docs secret placeholder check passed`.

---

## Task 11: End-to-end local verification without placing a real call

**Objective:** Verify imports, tests, and API behavior with mocked LiveKit calls before any real phone call.

**Files:**
- No code changes expected.

**Step 1: Run targeted unit tests**

```bash
.venv/bin/python -m unittest tests.test_call_outcomes -v
.venv/bin/python -m unittest discover -s tests -p test_call_api.py -v
.venv/bin/python -m unittest tests.test_agent_call_context -v
.venv/bin/python -m unittest discover -s tests -p test_hermes_livekit_plugin.py -v
```

Expected: all pass.

**Step 2: Run broader test suite**

```bash
.venv/bin/python -m unittest discover -s tests
```

Expected: pass, or report any unrelated/pre-existing failures explicitly.

**Step 3: Run syntax checks**

```bash
.venv/bin/python -m py_compile call_api.py agent.py call_outcomes.py integrations/hermes/livekit-caller/__init__.py
```

Expected: no syntax errors.

**Step 4: Inspect git diff**

```bash
git diff --stat
git diff -- call_api.py agent.py call_outcomes.py tests/test_call_api.py tests/test_agent_call_context.py integrations/hermes/livekit-caller/__init__.py docs/hermes-call-control.md docs/dokploy.md README.md
```

Expected: changes match this plan; no `.env`, secrets, generated DB files, or unrelated refactors.

---

## Task 12: Optional real local test call gate

**Objective:** Define safe manual steps for a future approved real call after tests pass.

**Files:**
- No code changes expected.

**Precondition:** Ask the user before placing any real phone call. Use the approved test callee only if user confirms.

**Step 1: Start local worker**

```bash
set -a && source .env && set +a
LIVEKIT_AGENT_NAME=outbound-caller-local uv run agent.py start
```

Expected: worker registers with LiveKit as `outbound-caller-local`.

**Step 2: Start local API**

```bash
set -a && source .env && set +a
LIVEKIT_AGENT_NAME=outbound-caller-local uv run uvicorn call_api:app --host 127.0.0.1 --port 8000
```

Expected: API starts on port 8000.

**Step 3: Health check**

```bash
curl -s http://127.0.0.1:8000/health
```

Expected:

```json
{"ok":true}
```

**Step 4: Confirm trunk shape before dialing**

```bash
python - <<'PY'
import os
trunk = os.getenv('OUTBOUND_TRUNK_ID', '')
assert trunk.startswith('ST_'), f'OUTBOUND_TRUNK_ID must be ST_..., got {trunk!r}'
print('trunk id shape ok')
PY
```

Expected: `trunk id shape ok`.

**Step 5: Place one approved test call only after explicit user approval**

```bash
curl -X POST "http://127.0.0.1:8000/calls" \
  -H "Authorization: Bearer $CALL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "phone_number": "+91_APPROVED_TEST_NUMBER",
    "purpose": "Local outbound API-dial integration test",
    "agent_type": "sales",
    "requested_by": "manual-local-test"
  }'
```

Expected: response is one of:

```json
{"ok":true,"status":"answered",...}
```

or a structured failure:

```json
{"ok":false,"status":"failed_busy","sip_status_code":"486",...}
```

---

## Tests / validation summary

Run before considering implementation complete:

```bash
.venv/bin/python -m unittest tests.test_call_outcomes -v
.venv/bin/python -m unittest discover -s tests -p test_call_api.py -v
.venv/bin/python -m unittest tests.test_agent_call_context -v
.venv/bin/python -m unittest discover -s tests -p test_hermes_livekit_plugin.py -v
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m py_compile call_api.py agent.py call_outcomes.py integrations/hermes/livekit-caller/__init__.py
```

Expected: all pass, except any unrelated pre-existing failures must be documented with exact output.

---

## Risks, tradeoffs, and open questions

### Risk: Dispatch-before-dial vs dial-before-dispatch

Recommended order is dispatch first, then dial, so the worker is ready when the callee answers. If dispatch succeeds but dial fails, the API should best-effort delete the room to avoid an idle worker job.

Alternative order is dial first, then dispatch. That gives clean failure handling but risks the callee waiting in silence while the worker starts.

### Risk: `POST /calls` becomes blocking

Because `wait_until_answered=True`, `/calls` can block until answer or failure. This is desirable for exact Hermes feedback, but API client timeout must be long enough. Hermes plugin currently uses `timeout=30`; check whether this is enough for real ringing/no-answer timeout. If not, increase plugin timeout to 60-90 seconds or make timeout configurable.

### Risk: duplicate worker names

If local/Dokploy/LiveKit Cloud workers share the same `LIVEKIT_AGENT_NAME`, LiveKit dispatch can route to the wrong worker. Use distinct names per environment.

### Risk: LiveKit Cloud worker env

LiveKit Cloud injects LiveKit credentials for the worker. The Dokploy Call API still needs LiveKit credentials because it creates rooms, dispatches agents, and creates SIP participants.

### Risk: transcript completeness with realtime models

Current realtime model path can have delayed/incomplete `session.history` transcripts compared to explicit STT pipeline. Future transcript work should evaluate whether LiveKit Agent Insights/session reports are sufficient, or whether to move to STT + LLM + TTS for stronger transcript control.

### Open question: keep legacy worker-dial path forever?

Recommendation: keep it during migration only. After API-dial flow is proven in production, remove `_ensure_outbound_participant` and related SQLite update calls from the worker.

### Open question: storage backend for production

SQLite is acceptable for local/Dokploy small-scale status and dashboard. If production traffic increases or multiple API replicas are used, migrate `call_status_store.py` to Postgres/Supabase/Neon.

---

## Implementation handoff notes

- Implement in small tasks and run targeted tests after each task.
- Do not delete dashboard/SQLite in the first implementation pass; keep them useful for status visibility.
- Do not place real calls until all unit tests pass and the user explicitly approves the target number.
- Do not commit unless the user explicitly asks.
