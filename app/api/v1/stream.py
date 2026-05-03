import asyncio
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.utils.audit_logger import get_events

router = APIRouter(prefix="/stream", tags=["Stream"])


async def _event_generator(claim_id: str):
    """SSE generator — streams all buffered events then polls for new ones."""
    sent_seq = set()
    poll_count = 0
    max_polls = 120  # 120 * 0.5s = 60s timeout

    while poll_count < max_polls:
        events = get_events(claim_id)
        for event in events:
            if event.sequence_number not in sent_seq:
                sent_seq.add(event.sequence_number)
                data = json.dumps(event.model_dump(), default=str)
                yield f"data: {data}\n\n"

                # Close stream once execution completes or terminal events appear
                if event.event_type in ("execution.completed", "simulation.completed", "system.error"):
                    yield "data: {\"event_type\": \"stream.closed\"}\n\n"
                    return

        await asyncio.sleep(0.5)
        poll_count += 1

    yield "data: {\"event_type\": \"stream.timeout\"}\n\n"


@router.get("/claims/{claim_id}/events")
async def stream_claim_events(claim_id: str) -> StreamingResponse:
    return StreamingResponse(
        _event_generator(claim_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/claims/{claim_id}/events/snapshot")
async def get_event_snapshot(claim_id: str) -> list:
    """Non-streaming: return all buffered events as JSON array."""
    events = get_events(claim_id)
    return [e.model_dump() for e in events]
