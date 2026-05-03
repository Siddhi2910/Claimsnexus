"""
Tests for the enhanced agent output schemas.
These tests do NOT call the LLM — they verify schema parsing and validation logic.
"""
import pytest
from datetime import datetime
from app.schemas.agent import (
    AgentReport, AgentVerdict, VerdictProbability, EvidenceItem, RiskFactor,
    InternalReasoning, DissentingView, ReasoningStep,
)
from app.agents.base_agent import BaseAgent


# ── VerdictProbability ─────────────────────────────────────────────────────────

def test_verdict_probability_dominant():
    vp = VerdictProbability(APPROVE=0.1, REJECT=0.75, PENDING=0.15)
    assert vp.dominant() == "REJECT"


def test_verdict_probability_entropy_certain():
    vp = VerdictProbability(APPROVE=1.0, REJECT=0.0, PENDING=0.0)
    assert vp.entropy() == 0.0


def test_verdict_probability_entropy_uncertain():
    vp = VerdictProbability(APPROVE=0.333, REJECT=0.333, PENDING=0.334)
    assert vp.entropy() > 0.9


# ── EvidenceItem ───────────────────────────────────────────────────────────────

def test_evidence_item_construction():
    e = EvidenceItem(
        evidence_id="E1",
        type="SUPPORTING",
        description="Billed amount 5x benchmark",
        source="statistical_benchmark",
        strength=0.85,
        cited_value="$4250 billed vs $850 benchmark",
    )
    assert e.type == "SUPPORTING"
    assert 0 <= e.strength <= 1


def test_evidence_item_invalid_type():
    with pytest.raises(Exception):
        EvidenceItem(
            evidence_id="E1",
            type="INVALID_TYPE",
            description="test",
            source="claim_data",
            strength=0.5,
            cited_value="test",
        )


# ── RiskFactor ─────────────────────────────────────────────────────────────────

def test_risk_factor_construction():
    rf = RiskFactor(
        factor_id="RF1",
        category="UPCODING",
        description="Level 5 E&M for simple diagnosis",
        severity="HIGH",
        probability=0.72,
        impact="Overbilling of ~$200 per visit",
        mitigating_factors=["Provider notes may justify complexity"],
    )
    assert rf.severity == "HIGH"
    assert 0 <= rf.probability <= 1
    assert len(rf.mitigating_factors) == 1


# ── InternalReasoning ──────────────────────────────────────────────────────────

def test_internal_reasoning_all_phases():
    ir = InternalReasoning(
        phase_1_data_extraction="CPT 99215, ICD J18.9, billed $350",
        phase_2_hypothesis="Level 5 E&M for pneumonia — plausible if acute",
        phase_3_evidence_for=["Pneumonia is a moderately complex diagnosis"],
        phase_4_evidence_against=["99215 requires high MDM — pneumonia may only warrant 99214"],
        phase_5_weighing="Evidence roughly balanced — slight lean toward PENDING",
        phase_6_conclusion="PENDING — insufficient documentation to confirm Level 5 necessity",
    )
    assert len(ir.phase_3_evidence_for) == 1
    assert len(ir.phase_4_evidence_against) == 1


# ── DissentingView ─────────────────────────────────────────────────────────────

def test_dissenting_view():
    dv = DissentingView(
        alternative_verdict="APPROVE",
        probability=0.28,
        strongest_argument_for_alternative="Pneumonia in elderly patient may justify high complexity",
        what_would_change_my_verdict="Documentation showing multiple comorbidities managed in same visit",
    )
    assert 0 <= dv.probability <= 1
    assert dv.alternative_verdict == "APPROVE"


# ── BaseAgent output parser ────────────────────────────────────────────────────

class _ConcreteAgent(BaseAgent):
    """Minimal concrete subclass to test BaseAgent._parse_enhanced_output."""
    agent_id = "test_agent"
    async def run(self, claim_data, context): ...


def test_parse_enhanced_output_full():
    agent = _ConcreteAgent()
    raw = {
        "verdict_probability": {"APPROVE": 0.2, "REJECT": 0.6, "PENDING": 0.2},
        "internal_reasoning": {
            "phase_1_data_extraction": "test extraction",
            "phase_2_hypothesis": "test hypothesis",
            "phase_3_evidence_for": ["evidence A"],
            "phase_4_evidence_against": ["counter B"],
            "phase_5_weighing": "test weighing",
            "phase_6_conclusion": "REJECT",
        },
        "key_evidence": [
            {
                "evidence_id": "E1",
                "type": "SUPPORTING",
                "description": "test",
                "source": "claim_data",
                "strength": 0.8,
                "cited_value": "CPT 99215",
            }
        ],
        "risk_factors": [
            {
                "factor_id": "RF1",
                "category": "UPCODING",
                "description": "test risk",
                "severity": "HIGH",
                "probability": 0.7,
                "impact": "overbilling",
                "mitigating_factors": [],
            }
        ],
        "dissenting_view": {
            "alternative_verdict": "APPROVE",
            "probability": 0.2,
            "strongest_argument_for_alternative": "test",
            "what_would_change_my_verdict": "test",
        },
        "reasoning_chain": [
            {"step": 1, "observation": "obs", "evidence": ["e1"], "inference": "inf", "weight": 0.9}
        ],
    }
    parsed = agent._parse_enhanced_output(raw)

    assert "verdict_probability" in parsed
    assert abs(parsed["verdict_probability"].APPROVE + parsed["verdict_probability"].REJECT + parsed["verdict_probability"].PENDING - 1.0) < 0.01
    assert "internal_reasoning" in parsed
    assert len(parsed["key_evidence"]) == 1
    assert len(parsed["risk_factors"]) == 1
    assert "dissenting_view" in parsed
    assert len(parsed["reasoning_chain"]) == 1


def test_parse_enhanced_output_normalizes_probabilities():
    agent = _ConcreteAgent()
    raw = {
        "verdict_probability": {"APPROVE": 2.0, "REJECT": 6.0, "PENDING": 2.0},
    }
    parsed = agent._parse_enhanced_output(raw)
    vp = parsed["verdict_probability"]
    total = vp.APPROVE + vp.REJECT + vp.PENDING
    assert abs(total - 1.0) < 0.001
    assert abs(vp.REJECT - 0.6) < 0.001


def test_parse_enhanced_output_tolerates_missing_fields():
    agent = _ConcreteAgent()
    raw = {"reasoning_chain": []}
    parsed = agent._parse_enhanced_output(raw)
    assert parsed["reasoning_chain"] == []
    assert "verdict_probability" not in parsed
    assert "internal_reasoning" not in parsed


def test_parse_enhanced_output_skips_malformed_evidence():
    agent = _ConcreteAgent()
    raw = {
        "key_evidence": [
            {"evidence_id": "E1", "type": "INVALID", "description": "test", "source": "claim_data", "strength": 0.5, "cited_value": "x"},
            {"evidence_id": "E2", "type": "SUPPORTING", "description": "ok", "source": "claim_data", "strength": 0.8, "cited_value": "y"},
        ]
    }
    parsed = agent._parse_enhanced_output(raw)
    # Malformed type should be skipped, valid one kept
    assert len(parsed.get("key_evidence", [])) == 1
    assert parsed["key_evidence"][0].evidence_id == "E2"
