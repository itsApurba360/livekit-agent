# -*- coding: utf-8 -*-
import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _install_livekit_stubs():
    certifi_module = types.ModuleType("certifi")
    dotenv_module = types.ModuleType("dotenv")
    livekit_module = types.ModuleType("livekit")
    api_module = types.ModuleType("livekit.api")
    agents_module = types.ModuleType("livekit.agents")

    certifi_module.where = lambda: "/tmp/certifi-test.pem"
    dotenv_module.load_dotenv = lambda *_args, **_kwargs: None

    class CreateSIPParticipantRequest:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class TwirpError(Exception):
        def __init__(self, message="twirp error", metadata=None):
            super().__init__(message)
            self.message = message
            self.metadata = metadata or {}

    class Agent:
        def __init__(self, instructions=None, tools=None):
            self.instructions = instructions
            self.tools = tools or []

    class AgentSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class RoomInputOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class WorkerOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class ToolContext:
        def __init__(self, tools=None):
            self.tools = tools or []
            self.function_tools = {}

    def function_tool(*decorator_args, **decorator_kwargs):
        def decorate(fn):
            fn.tool_description = decorator_kwargs.get("description")
            return fn

        if decorator_args and callable(decorator_args[0]) and len(decorator_args) == 1:
            return decorator_args[0]
        return decorate

    agents_module.JobContext = object
    agents_module.Agent = Agent
    agents_module.AgentSession = AgentSession
    agents_module.RoomInputOptions = RoomInputOptions
    agents_module.WorkerOptions = WorkerOptions
    agents_module.cli = types.SimpleNamespace(run_app=lambda *_args, **_kwargs: None)
    agents_module.llm = types.SimpleNamespace(ToolContext=ToolContext, function_tool=function_tool)

    api_module.CreateSIPParticipantRequest = CreateSIPParticipantRequest
    api_module.TwirpError = TwirpError

    livekit_module.agents = agents_module
    livekit_module.api = api_module

    sys.modules["certifi"] = certifi_module
    sys.modules["dotenv"] = dotenv_module
    sys.modules["livekit"] = livekit_module
    sys.modules["livekit.api"] = api_module
    sys.modules["livekit.agents"] = agents_module

    for module_name in [
        "livekit.plugins",
        "livekit.plugins.openai",
        "livekit.plugins.google",
        "livekit.plugins.noise_cancellation",
    ]:
        sys.modules[module_name] = types.ModuleType(module_name)

    sys.modules["livekit.plugins.noise_cancellation"].BVCTelephony = lambda: object()
    sys.modules["livekit.plugins.openai"].realtime = types.SimpleNamespace(RealtimeModel=lambda **kwargs: kwargs)
    sys.modules["livekit.plugins.google"].realtime = types.SimpleNamespace(RealtimeModel=lambda **kwargs: kwargs)


_stubbed_module_names = [
    "certifi",
    "dotenv",
    "livekit",
    "livekit.api",
    "livekit.agents",
    "livekit.plugins",
    "livekit.plugins.openai",
    "livekit.plugins.google",
    "livekit.plugins.noise_cancellation",
    "agent",
]
_previous_modules = {name: sys.modules.get(name) for name in _stubbed_module_names}

try:
    for name in _stubbed_module_names:
        sys.modules.pop(name, None)
    _install_livekit_stubs()
    agent = importlib.import_module("agent")
finally:
    for name, module in _previous_modules.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class FakeParticipant:
    def __init__(self, identity="sip_+919876543210", attributes=None, kind="SIP"):
        self.identity = identity
        self.attributes = attributes or {}
        self.kind = kind


class FakeRoom:
    def __init__(self, name="support_room", metadata="", participants=None):
        self.name = name
        self.metadata = metadata
        self.remote_participants = {
            participant.identity: participant
            for participant in (participants or [])
        }


class FakeSipClient:
    def __init__(self):
        self.requests = []

    async def create_sip_participant(self, request):
        self.requests.append(request)
        return types.SimpleNamespace(sip_call_id="SIP-CALL-001")


class FakeContext:
    def __init__(self, room, metadata=""):
        self.room = room
        self.job = types.SimpleNamespace(metadata=metadata)
        self.api = types.SimpleNamespace(sip=FakeSipClient())
        self.shutdown_reason = None

    def shutdown(self, reason=None):
        self.shutdown_reason = reason


class AgentCallContextTestCase(unittest.IsolatedAsyncioTestCase):
    def test_builds_inbound_context_from_sip_attributes(self):
        participant = FakeParticipant(
            identity="caller-1",
            attributes={
                "sip.phoneNumber": "+919876543210",
                "sip.callStatus": "active",
                "sip.ruleID": "SDR_123",
                "sip.callIDFull": "carrier-call-id",
                "sip.trunkID": "ST_INBOUND",
            },
        )
        ctx = FakeContext(FakeRoom(participants=[participant]))

        call_context = agent._build_call_context(ctx, {})

        self.assertEqual(call_context.direction, "inbound")
        self.assertEqual(call_context.phone_number, "+919876543210")
        self.assertEqual(call_context.participant_identity, "caller-1")
        self.assertEqual(call_context.sip_call_status, "active")
        self.assertEqual(call_context.sip_call_id, "carrier-call-id")
        self.assertEqual(call_context.sip_rule_id, "SDR_123")

    def test_builds_outbound_context_from_agent_call_room(self):
        ctx = FakeContext(FakeRoom(name="agent_call_abc123"), metadata='{"phone_number": "+919876543210"}')
        config = agent._load_json_dict(ctx.job.metadata)

        call_context = agent._build_call_context(ctx, config)

        self.assertEqual(call_context.direction, "outbound")
        self.assertEqual(call_context.phone_number, "+919876543210")

    def test_outbound_prompt_waits_for_callee_first(self):
        call_context = agent.CallContext(direction="outbound", phone_number="+919876543210")

        prompt = agent._call_context_prompt(call_context)

        self.assertIn("Direction: outbound", prompt)
        self.assertIn("Do not speak before the callee answers or before they speak first", prompt)

    async def test_ensure_outbound_participant_dials_with_trunk_and_waits(self):
        ctx = FakeContext(FakeRoom(name="agent_call_abc123"))
        config = {"phone_number": "+919876543210", "outbound_trunk_id": "ST_OUTBOUND"}
        call_context = agent.CallContext(direction="outbound", phone_number="+919876543210")

        async def add_participant_after_dial(_delay):
            ctx.room.remote_participants["sip_+919876543210"] = FakeParticipant(
                identity="sip_+919876543210",
                attributes={"sip.phoneNumber": "+919876543210", "sip.callStatus": "active"},
            )

        with patch.object(agent.asyncio, "sleep", new=add_participant_after_dial):
            updated_context = await agent._ensure_outbound_participant(ctx, call_context, config)

        self.assertIsNone(ctx.shutdown_reason)
        self.assertTrue(updated_context.ready)
        self.assertEqual(updated_context.direction, "outbound")
        self.assertEqual(updated_context.sip_call_status, "active")
        self.assertEqual(len(ctx.api.sip.requests), 1)
        self.assertEqual(ctx.api.sip.requests[0].kwargs["sip_trunk_id"], "ST_OUTBOUND")
        self.assertTrue(ctx.api.sip.requests[0].kwargs["wait_until_answered"])

    async def test_ensure_outbound_participant_shuts_down_without_trunk(self):
        ctx = FakeContext(FakeRoom(name="agent_call_abc123"))
        call_context = agent.CallContext(direction="outbound", phone_number="+919876543210")

        updated_context = await agent._ensure_outbound_participant(ctx, call_context, {})

        self.assertFalse(updated_context.ready)
        self.assertEqual(ctx.shutdown_reason, "Outbound SIP trunk not configured")
        self.assertEqual(ctx.api.sip.requests, [])


if __name__ == "__main__":
    unittest.main()
