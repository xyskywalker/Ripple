from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._seq: dict[str, int] = defaultdict(int)

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers[job_id].append(q)
        return q

    async def publish(self, job_id: str, event_type: str, payload: dict) -> None:
        self._seq[job_id] += 1
        event = {
            "job_id": job_id,
            "seq": self._seq[job_id],
            "type": event_type,
            "ts": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        for q in self._subscribers[job_id]:
            await q.put(event)
