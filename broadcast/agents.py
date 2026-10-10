"""The crew's agents: Microsoft Agent Framework `Agent`s backed by models deployed in Microsoft Foundry.

Two models, chosen per job:
- reasoning model (gpt-5-mini) for the Analyst and Recap writer, where thinking pays off;
- fast model (gpt-4.1-mini) for the Commentator and Producer, where latency matters live.

Auth is keyless: locally your `az login`, in Azure the app's managed identity.
`FakeAgents` stands in for tests, so crew logic is tested without network or cost.
"""

from __future__ import annotations

import asyncio
import os
from typing import Protocol, TypeVar

from pydantic import BaseModel

from . import prompts
from .models import Commentary, Insight, PersonalizedLine, Recap

T = TypeVar("T", bound=BaseModel)

CALL_TIMEOUT_S = 30


class AgentPool(Protocol):
    async def run(self, role: str, prompt: str, schema: type[T], instructions: str | None = None) -> T: ...


class FoundryAgents:
    ROLES = {  # role -> (model kind, instructions, options)
        "analyst": ("reasoning", prompts.ANALYST, {"reasoning": {"effort": "minimal"}}),  # ~2x faster than "low", same quality in tests
        "commentator": ("fast", prompts.COMMENTATOR, {"temperature": 0.8}),
        "producer": ("fast", prompts.PRODUCER, {"temperature": 0.6}),
        "recap": ("reasoning", None, {"reasoning": {"effort": "low"}}),
    }

    def __init__(self, project_endpoint: str | None = None, fast_model: str | None = None,
                 reasoning_model: str | None = None, credential=None):
        from agent_framework import Agent
        from agent_framework.foundry import FoundryChatClient

        self._Agent = Agent
        self.credential = credential or _default_credential()
        endpoint = project_endpoint or os.environ["FOUNDRY_PROJECT_ENDPOINT"]
        self.clients = {
            "fast": FoundryChatClient(project_endpoint=endpoint, credential=self.credential,
                                      model=fast_model or os.getenv("MODEL_FAST", "gpt-4.1-mini")),
            "reasoning": FoundryChatClient(project_endpoint=endpoint, credential=self.credential,
                                           model=reasoning_model or os.getenv("MODEL_REASONING", "gpt-5-mini")),
        }
        self.agents = {
            role: Agent(self.clients[kind], instructions, name=role.title(), default_options=opts)
            for role, (kind, instructions, opts) in self.ROLES.items() if instructions
        }

    async def run(self, role: str, prompt: str, schema: type[T], instructions: str | None = None) -> T:
        if instructions is not None:  # per-call instructions (recap): a short-lived agent
            kind, _, opts = self.ROLES[role]
            agent = self._Agent(self.clients[kind], instructions, name=role.title(), default_options=opts)
        else:
            agent = self.agents[role]
        response = await asyncio.wait_for(agent.run(prompt, options={"response_format": schema}), CALL_TIMEOUT_S)
        if response.value is None:
            raise ValueError(f"{role} returned no structured output")
        return response.value


def _default_credential():
    if os.getenv("TOUCHLINE_AUTH") == "managed":  # running in Azure
        from azure.identity.aio import ManagedIdentityCredential
        return ManagedIdentityCredential(client_id=os.getenv("AZURE_CLIENT_ID"))
    from azure.identity.aio import AzureCliCredential
    return AzureCliCredential()


class FakeAgents:
    """Deterministic stand-in for tests. `bad_drafts` = how many drafts per role should contain
    an invented number (to exercise the fact-check retry loop); `fail` = roles that raise."""

    def __init__(self, bad_drafts: dict[str, int] | None = None, fail: set[str] | None = None):
        self.bad_left = dict(bad_drafts or {})
        self.fail = fail or set()
        self.calls: list[str] = []

    async def run(self, role: str, prompt: str, schema: type[T], instructions: str | None = None) -> T:
        self.calls.append(role)
        if role in self.fail:
            raise RuntimeError(f"{role} unavailable")
        bad = self.bad_left.get(role, 0) > 0
        if bad:
            self.bad_left[role] -= 1
        extra = " after 999 passes" if bad else ""
        if schema is Insight:
            return Insight(headline="A key moment", why_it_matters="It changes the game.", supporting_numbers=[],
                           angle="drama")
        if schema is Commentary:
            return Commentary(caption="Big moment", line=f"What a moment{extra}!")
        if schema is PersonalizedLine:
            return PersonalizedLine(caption="Big moment for you", line=f"Your kind of moment{extra}!")
        if schema is Recap:
            return Recap(headline="A match to remember", summary=[f"It was a story{extra}."], key_numbers=[],
                         player_of_the_match="Nobody", player_reason="Fake.")
        raise TypeError(schema)
