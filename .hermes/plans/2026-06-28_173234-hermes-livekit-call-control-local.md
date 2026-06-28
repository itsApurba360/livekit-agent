# Hermes-Callable LiveKit Calls (Local-First) Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Enable Hermes (and other AI systems) to trigger outbound phone calls through this LiveKit voice agent via a lightweight authenticated call-control API, with the first verification performed entirely locally against the approved test number +919062371141.

**Architecture:** Add a FastAPI call-control service (`call_api.py`) that creates LiveKit rooms and explicit agent dispatches with outbound metadata. The existing `agent.py` worker reads the metadata and dials via SIP. Hermes interacts only through the API (via plugin or direct HTTP). Everything runs locally for the initial test; Dokploy deployment is deferred.

**Tech Stack:** Python, FastAPI + uvicorn, LiveKit Python API (`api.LiveKitAPI`, `CreateRoomRequest`, `CreateAgentDispatchRequest`), existing `unittest` suite, urllib for the Hermes plugin.

---

## Current Context / Assumptions

- The repo is `/Users/pankajsankhla/code/livekit_agent`.
- The worker already supports outbound SIP (`agent.py:146-155`, `228-294`, `580-611`).
- First verification must be **local only** (no Dokploy). The approved test call target is `+919062371141`.
- No specific coding model (e.g. Grok Build) is required or mandated.
- Unit tests must not place real calls.
- The API must be secure (bearer token, prefix validation, non-empty purpose).

## Proposed Approach

1. Add FastAPI dependencies.
2. Implement `call_api.py` with auth, phone normalization, and LiveKit dispatch.
3. Extend `agent.py` to consume `call_id`, `call_purpose`, `requested_by`, and optional `agent_type`.
4. Add comprehensive unit tests (TDD).
5. Create Hermes plugin template (`integrations/hermes/livekit-caller/`).
6. Update docs and `.env.example` for local testing.
7. Verify with local worker + API + exactly one call to `+919062371141`.

---

## Step-by-Step Plan

### Task 1: Add FastAPI Dependencies

**Objective:** Add the minimal HTTP API dependencies.

**Files:**
- Modify: `pyproject.toml:7-25`

**Step 1: Update dependencies**

```toml
dependencies = [
    ...
    "fastapi",
    "uvicorn[standard]",
]

[dependency-groups]
dev = [
    "pytest",
    "httpx",
]
```

**Step 2: Sync**

Run: `uv sync`

Expected: Success.

**Step 3: Verify imports**

Run: `.venv/bin/python -c "import fastapi, uvicorn; print('ok')"`

Expected: `ok`

**Checkpoint:** Ready for next task.

---

### Task 2: Create Failing API Tests

**Objective:** Define security and validation behavior before writing `call_api.py`.

**Files:**
- Create: `tests/test_call_api.py`

**Step 1: Write the test file**

Create `tests/test_call_api.py` with the full test class from the plan (health, missing/wrong bearer, disallowed prefix, blank purpose, successful dispatch with patched `_dispatch_livekit_agent`).

**Step 2: Run to verify failure**

Run: `.venv/bin/python -m unittest tests.test_call_api`

Expected: Import / module errors (file does not exist yet).

---

### Task 3: Implement `call_api.py`

**Objective:** Build the call-control API with proper auth, validation, and LiveKit dispatch.

**Files:**
- Create: `call_api.py`

**Step 1: Implement the full `call_api.py`**

Use the complete, corrected version with:
- `Optional[str]` annotations (no `***` placeholders)
- `_require_auth(authorization: Optional[str] = None)`
- All endpoint logic, phone normalization, and `_dispatch_livekit_agent`

**Step 2: Verify syntax**

Run: `.venv/bin/python -m py_compile call_api.py`

Expected: No output (clean).

---

### Task 4: Make API Tests Pass + Add Dispatch Test

**Objective:** Achieve full test coverage for the API.

**Files:**
- Modify: `tests/test_call_api.py` (add the dispatch metadata test if not already present)

**Step 1: Run tests**

Run: `.venv/bin/python -m unittest tests.test_call_api`

Expected: All 7 tests pass.

---

### Task 5: Extend `CallContext` and Prompt Logic in `agent.py`

**Objective:** Make the voice agent aware of call purpose and requester.

**Files:**
- Modify: `agent.py:56-66,146-198,297-336,424-434`

**Step 1: Add fields to dataclass**

Add `call_id`, `call_purpose`, `requested_by` to `CallContext`.

**Step 2: Add helper functions**

Add `_agent_type_from_metadata` and `_select_agent_type`.

**Step 3: Update `_build_call_context` and `_call_context_prompt`**

Read the new metadata fields and include them in the prompt (especially for outbound calls).

**Step 4: Update agent selection**

Replace the ternary with `_select_agent_type(config_dict, caller_status)`.

**Step 5: Add tests in `tests/test_agent_call_context.py`**

Add the four new test methods for call purpose metadata, prompt inclusion, and agent type helpers.

**Step 6: Run tests**

Run: `.venv/bin/python -m unittest tests.test_agent_call_context`

Expected: All tests pass.

---

### Task 6: Create Hermes Plugin

**Objective:** Give Hermes a `make_phone_call` tool.

**Files:**
- Create: `integrations/hermes/livekit-caller/plugin.yaml`
- Create: `integrations/hermes/livekit-caller/__init__.py`
- Create: `tests/test_hermes_livekit_plugin.py`

**Step 1: Create the three files**

Use the exact plugin code and test file from the plan (with the `assert spec is not None` fix).

**Step 2: Run plugin tests**

Run: `.venv/bin/python -m unittest tests.test_hermes_livekit_plugin`

Expected: 2 tests pass.

---

### Task 7: Update Documentation and Environment Examples

**Objective:** Make local testing discoverable.

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/dokploy.md`

**Step 1: Update `.env.example`**

Add the CALL_API_* and LIVEKIT_CALL_API_* variables (with `http://127.0.0.1:8000`).

**Step 2: Update README.md**

Add the "Local Call Control Test" section with the exact local commands and the approved test number `+919062371141`.

**Step 3: Update docs/dokploy.md**

Add the "Call Control API Rollout Status" section marking local verification first.

**Step 4: Verify references**

Run the verification Python snippet from the plan.

Expected: All checks pass.

---

### Task 8: Final Local Test Run (No Code Changes)

**Objective:** Execute the approved test call.

**Prerequisites:**
- Real credentials in `.env`
- Worker running: `uv run agent.py start`
- API running on 127.0.0.1:8000 with `CALL_API_TOKEN=...`

**Step 1: Health check**

Run: `curl -s http://127.0.0.1:8000/health`

**Step 2: Trigger exactly one test call**

Use the curl command targeting `+919062371141` with a clear purpose.

Expected: 200 response + worker logs showing the outbound SIP call.

**Step 3: Stop processes**

`Ctrl+C` on both terminals.

---

## Files Likely to Change

- `pyproject.toml`
- `call_api.py` (new)
- `tests/test_call_api.py` (new)
- `agent.py`
- `tests/test_agent_call_context.py`
- `integrations/hermes/livekit-caller/` (new directory + 2 files)
- `tests/test_hermes_livekit_plugin.py` (new)
- `.env.example`
- `README.md`
- `docs/dokploy.md`

---

## Risks, Tradeoffs, and Open Questions

**Risks**
- Real phone call cost and reputation — limited to one approved test call.
- LiveKit "room already exists" error — handled by ignoring only that specific Twirp error if it occurs.
- Frappe connectivity in logs — expected in local test if no Frappe is running.

**Tradeoffs**
- Local-first vs immediate Dokploy deployment — prioritizes safety and quick feedback.
- No mandated coding model — keeps the plan model-agnostic.

**Open Questions**
- After the single test call, should a allowlist of numbers be added for future calls?

---

**Plan complete and saved.** Ready to execute using subagent-driven-development (no Grok Build requirement). Shall I proceed?