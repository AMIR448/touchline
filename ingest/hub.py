"""The ingest stage on Azure Event Hubs.

    simulator ──publish──▶ Event Hubs ("match-events") ──consume──▶ app: matches ready to watch

Locally the app simulates matches in-process. When EVENTHUB_NAMESPACE is set (as in Azure), every
match goes through the hub instead: the simulator publishes its events and the app only knows a
match once it has read it back from the stream, exactly as it would with a real data provider's feed.

- Events are sent in ordered chunks (partition key = match id, so one match stays in order on one
  partition), which keeps a 1,800-event match well inside the Basic tier's throughput.
- The consumer starts from the oldest retained event, so after a restart the app rebuilds every
  match published in the retention window: the hub doubles as the app's short-term memory.
- Keyless: the app's managed identity (or `az login` locally) with Event Hubs data roles.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

log = logging.getLogger("touchline.ingest")

CHUNK = 50  # events per message


def chunks(match_key: str, events: list[dict]) -> list[dict[str, Any]]:
    total = (len(events) + CHUNK - 1) // CHUNK
    return [{"match_key": match_key, "chunk": i, "total": total, "events": events[i * CHUNK:(i + 1) * CHUNK]}
            for i in range(total)]


class Assembler:
    """Rebuilds whole matches from chunks, which may arrive interleaved with other matches or twice."""

    def __init__(self) -> None:
        self.parts: dict[str, dict[int, list[dict]]] = {}

    def add(self, msg: dict[str, Any]) -> tuple[str, list[dict]] | None:
        parts = self.parts.setdefault(msg["match_key"], {})
        parts[msg["chunk"]] = msg["events"]
        if len(parts) < msg["total"]:
            return None
        del self.parts[msg["match_key"]]
        return msg["match_key"], [ev for i in sorted(parts) for ev in parts[i]]


class EventHubIngest:
    def __init__(self, namespace: str, hub: str, credential, on_match: Callable[[str, list[dict]], None]):
        from azure.eventhub.aio import EventHubConsumerClient, EventHubProducerClient

        fqdn = namespace if "." in namespace else f"{namespace}.servicebus.windows.net"
        self.producer = EventHubProducerClient(fully_qualified_namespace=fqdn, eventhub_name=hub, credential=credential)
        self.consumer = EventHubConsumerClient(fully_qualified_namespace=fqdn, eventhub_name=hub,
                                               consumer_group="$Default", credential=credential)
        self.on_match = on_match
        self.assembler = Assembler()
        self.waiters: dict[str, asyncio.Future] = {}
        self.task: asyncio.Task | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(self._consume())

    async def _consume(self) -> None:
        async def on_batch(_partition, batch):
            for event in batch:
                done = self.assembler.add(json.loads(event.body_as_str()))
                if done:
                    key, events = done
                    self.on_match(key, events)
                    waiter = self.waiters.pop(key, None)
                    if waiter and not waiter.done():
                        waiter.set_result(key)
        async with self.consumer:
            await self.consumer.receive_batch(on_event_batch=on_batch, starting_position="-1", max_batch_size=200,
                                              max_wait_time=1)

    async def publish(self, match_key: str, events: list[dict], wait: float = 30.0) -> None:
        """Send a match into the stream and return once the consumer has read all of it back."""
        from azure.eventhub import EventData

        arrived = asyncio.get_running_loop().create_future()
        self.waiters[match_key] = arrived
        batch = await self.producer.create_batch(partition_key=match_key)
        for msg in chunks(match_key, events):
            data = EventData(json.dumps(msg, separators=(",", ":")))
            try:
                batch.add(data)
            except ValueError:  # batch full: send it and start the next one
                await self.producer.send_batch(batch)
                batch = await self.producer.create_batch(partition_key=match_key)
                batch.add(data)
        await self.producer.send_batch(batch)
        await asyncio.wait_for(arrived, wait)

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
        await self.producer.close()
