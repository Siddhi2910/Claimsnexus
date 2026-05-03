"""
Simulation engine — runs the full pipeline in dry-run mode with parameter overrides.
No real payments, notifications, or external calls are ever made.
"""
import uuid
import copy
import time
from datetime import datetime
import structlog

from app.schemas.simulation import (
    SimulationRequest, SimulationResult, SimulationType,
    DecisionSnapshot, DeltaAnalysis, ImpactReport,
)
from app.workflow.pipeline import AdjudicationPipeline

log = structlog.get_logger()

pipeline = AdjudicationPipeline()


def _build_snapshot(decision: dict) -> DecisionSnapshot:
    return DecisionSnapshot(
        verdict=decision.get("verdict", "UNKNOWN"),
        confidence=float(decision.get("confidence", 0.0)),
        composite_risk_score=float(decision.get("composite_risk_score", 0.0)),
        fraud_score=float(decision.get("fraud_score", 0.0)),
        approved_amount=decision.get("approved_amount"),
        routing_decision=str(decision.get("routing_decision", "UNKNOWN")),
    )


def _build_delta(
    original: DecisionSnapshot | None,
    simulated: DecisionSnapshot,
    weight_overrides: dict,
) -> DeltaAnalysis:
    risk_delta = (
        simulated.composite_risk_score - original.composite_risk_score
        if original else 0.0
    )
    conf_delta = (
        simulated.confidence - original.confidence
        if original else 0.0
    )
    decision_changed = original is not None and original.verdict != simulated.verdict

    driver_parts = []
    if weight_overrides:
        driver_parts.append(f"weight overrides: {weight_overrides}")
    if abs(risk_delta) > 0.05:
        direction = "increase" if risk_delta > 0 else "decrease"
        driver_parts.append(f"risk score {direction} of {abs(risk_delta):.3f}")

    return DeltaAnalysis(
        decision_changed=decision_changed,
        original_verdict=original.verdict if original else None,
        simulated_verdict=simulated.verdict,
        risk_score_delta=round(risk_delta, 4),
        confidence_delta=round(conf_delta, 4),
        key_driver="; ".join(driver_parts) if driver_parts else "No significant driver identified",
    )


async def run_simulation(request: SimulationRequest, base_claim_data: dict | None) -> SimulationResult:
    sim_id = str(uuid.uuid4())
    t0 = time.monotonic()

    log.info("simulation_start", sim_id=sim_id, sim_type=request.simulation_type)

    # Extract weight overrides from parameter_deltas
    weight_overrides: dict | None = None
    param_deltas = request.parameter_deltas or {}
    if any(k in param_deltas for k in ("fraud_weight", "medical_weight", "policy_weight")):
        weight_overrides = {
            k.replace("_weight", ""): v
            for k, v in param_deltas.items()
            if k.endswith("_weight")
        }

    original_snapshot: DecisionSnapshot | None = None

    if request.simulation_type == SimulationType.PARAMETER:
        # Re-run original claim with new weights
        if not base_claim_data:
            raise ValueError("base_claim_id required for parameter simulation")

        sim_claim = copy.deepcopy(base_claim_data)
        sim_claim["id"] = sim_id
        sim_claim["is_simulation"] = True

        # Get original decision snapshot from base claim
        original_result = await pipeline.run(base_claim_data, weight_overrides=None)
        original_snapshot = _build_snapshot(original_result)

        # Simulate with new weights
        sim_result = await pipeline.run(sim_claim, weight_overrides=weight_overrides)
        simulated_snapshot = _build_snapshot(sim_result)

    elif request.simulation_type == SimulationType.CLAIM:
        # Mutate claim fields and re-run
        if not base_claim_data:
            raise ValueError("base_claim_id required for claim simulation")

        original_result = await pipeline.run(base_claim_data)
        original_snapshot = _build_snapshot(original_result)

        sim_claim = copy.deepcopy(base_claim_data)
        sim_claim["id"] = sim_id
        sim_claim["is_simulation"] = True
        sim_claim.update(request.claim_field_overrides or {})

        sim_result = await pipeline.run(sim_claim, weight_overrides=weight_overrides)
        simulated_snapshot = _build_snapshot(sim_result)

    elif request.simulation_type == SimulationType.STRESS_TEST:
        # Generate N variants and aggregate
        if not base_claim_data:
            raise ValueError("base_claim_id required for stress test")

        count = min(request.stress_test_count, 50)  # cap at 50 in MVP
        results = []
        import asyncio, copy, random

        async def _run_variant(i: int) -> dict:
            variant = copy.deepcopy(base_claim_data)
            variant["id"] = str(uuid.uuid4())
            variant["is_simulation"] = True
            # Small random perturbation on billing amount
            variant["billed_amount"] = base_claim_data.get("billed_amount", 1000) * (0.8 + random.random() * 0.4)
            variant["requested_amount"] = variant["billed_amount"]
            return await pipeline.run(variant, weight_overrides=weight_overrides)

        batch = await asyncio.gather(*[_run_variant(i) for i in range(count)])
        results = list(batch)

        original_verdict = base_claim_data.get("verdict", "UNKNOWN")
        flipped = sum(1 for r in results if r.get("verdict") != original_verdict)
        a_to_r = sum(1 for r in results if original_verdict == "APPROVE" and r.get("verdict") == "REJECT")
        r_to_a = sum(1 for r in results if original_verdict == "REJECT" and r.get("verdict") == "APPROVE")
        avg_risk = sum(r.get("composite_risk_score", 0) for r in results) / len(results)
        avg_approved = sum(r.get("approved_amount") or 0 for r in results if r.get("verdict") == "APPROVE")

        impact = ImpactReport(
            total_claims_analyzed=count,
            decisions_flipped=flipped,
            flip_rate_pct=round(flipped / count * 100, 2),
            approve_to_reject=a_to_r,
            reject_to_approve=r_to_a,
            avg_risk_score_delta=round(avg_risk - (base_claim_data.get("composite_risk_score", avg_risk)), 4),
            projected_financial_impact=round(avg_approved, 2),
        )

        simulated_snapshot = _build_snapshot(results[0]) if results else DecisionSnapshot(
            verdict="UNKNOWN", confidence=0, composite_risk_score=0, fraud_score=0,
            approved_amount=None, routing_decision="UNKNOWN",
        )

        duration_ms = int((time.monotonic() - t0) * 1000)
        return SimulationResult(
            simulation_id=sim_id,
            simulation_type=request.simulation_type,
            description=request.description,
            original_decision=None,
            simulated_decision=simulated_snapshot,
            delta_analysis=_build_delta(None, simulated_snapshot, param_deltas),
            impact_report=impact,
            parameter_deltas_applied=param_deltas,
            generated_at=datetime.utcnow(),
            duration_ms=duration_ms,
        )

    else:
        # Type C (Policy) — simplified: re-run with note about policy change
        sim_claim = copy.deepcopy(base_claim_data) if base_claim_data else {"id": sim_id}
        sim_claim["id"] = sim_id
        sim_claim["is_simulation"] = True
        sim_result = await pipeline.run(sim_claim, weight_overrides=weight_overrides)
        simulated_snapshot = _build_snapshot(sim_result)

    delta = _build_delta(original_snapshot, simulated_snapshot, param_deltas)
    duration_ms = int((time.monotonic() - t0) * 1000)

    log.info("simulation_complete", sim_id=sim_id, duration_ms=duration_ms, changed=delta.decision_changed)

    return SimulationResult(
        simulation_id=sim_id,
        simulation_type=request.simulation_type,
        description=request.description,
        original_decision=original_snapshot,
        simulated_decision=simulated_snapshot,
        delta_analysis=delta,
        impact_report=None,
        parameter_deltas_applied=param_deltas,
        generated_at=datetime.utcnow(),
        duration_ms=duration_ms,
    )
