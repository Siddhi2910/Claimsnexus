import uuid
from datetime import datetime
from typing import Any
import structlog
from app.schemas.stream import StreamEvent, EventTypes

log = structlog.get_logger()

# In-memory sequence counter per claim (production would use Redis INCR)
_sequence_counters: dict[str, int] = {}


def _next_seq(claim_id: str) -> int:
    _sequence_counters[claim_id] = _sequence_counters.get(claim_id, 0) + 1
    return _sequence_counters[claim_id]


# In-memory event buffer (production would use Redis pub/sub or Kafka)
_event_buffers: dict[str, list[StreamEvent]] = {}


def emit_event(
    claim_id: str,
    event_type: str,
    stage: str,
    payload: dict[str, Any],
) -> StreamEvent:
    event = StreamEvent(
        event_id=str(uuid.uuid4()),
        claim_id=claim_id,
        event_type=event_type,
        stage=stage,
        payload=payload,
        timestamp=datetime.utcnow(),
        sequence_number=_next_seq(claim_id),
    )
    if claim_id not in _event_buffers:
        _event_buffers[claim_id] = []
    _event_buffers[claim_id].append(event)
    log.info("stream_event", event_type=event_type, claim_id=claim_id, seq=event.sequence_number)
    return event


def get_events(claim_id: str) -> list[StreamEvent]:
    return _event_buffers.get(claim_id, [])


def clear_events(claim_id: str) -> None:
    _event_buffers.pop(claim_id, None)
    _sequence_counters.pop(claim_id, None)


def build_audit_entry(
    claim_id: str,
    agent_id: str,
    event_type: str,
    event_detail: str,
    input_snapshot: dict | None = None,
    output_snapshot: dict | None = None,
    duration_ms: int | None = None,
    is_human_action: bool = False,
) -> dict:
    return {
        "entry_id": str(uuid.uuid4()),
        "claim_id": claim_id,
        "agent_id": agent_id,
        "event_type": event_type,
        "event_detail": event_detail,
        "input_snapshot": input_snapshot,
        "output_snapshot": output_snapshot,
        "duration_ms": duration_ms,
        "is_human_action": is_human_action,
        "timestamp": datetime.utcnow().isoformat(),
    }
