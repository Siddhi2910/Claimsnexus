from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

import structlog


log = structlog.get_logger()

_claims: dict[str, dict[str, Any]] = {}
_decisions: dict[str, dict[str, Any]] = {}
memory_store: dict[str, dict[str, dict[str, Any]]] = {
    "claims": _claims,
    "decisions": _decisions,
}


def create_claim(claim_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    now = datetime.utcnow()
    data = deepcopy(payload)
    data["id"] = claim_id
    data["created_at"] = now
    data["updated_at"] = now
    _claims[claim_id] = data
    log.info("in_memory_claim_created", claim_id=claim_id)
    return deepcopy(data)


def update_claim(claim_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    claim = _claims.get(claim_id)
    if not claim:
        return None
    claim.update(updates)
    claim["updated_at"] = datetime.utcnow()
    _claims[claim_id] = claim
    return deepcopy(claim)


def get_claim(claim_id: str) -> dict[str, Any] | None:
    claim = _claims.get(claim_id)
    return deepcopy(claim) if claim else None


def _status_value(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return str(getattr(raw, "value", raw))


def list_claims(status: str | None = None, skip: int = 0, limit: int = 20) -> tuple[int, list[dict[str, Any]]]:
    items = list(_claims.values())
    items.sort(key=lambda c: c.get("created_at", datetime.min), reverse=True)
    if status:
        items = [c for c in items if _status_value(c.get("status")) == status]
    total = len(items)
    sliced = items[skip : skip + limit]
    return total, deepcopy(sliced)


def store_decision(decision_data: dict[str, Any]) -> None:
    _decisions[decision_data["id"]] = deepcopy(decision_data)
    log.info("in_memory_decision_stored", decision_id=decision_data["id"], claim_id=decision_data["claim_id"])


def get_decision_by_claim_id(claim_id: str) -> dict[str, Any] | None:
    for decision in _decisions.values():
        if decision.get("claim_id") == claim_id:
            return deepcopy(decision)
    return None
