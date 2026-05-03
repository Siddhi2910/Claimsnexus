"""
Debate Agent — orchestrates a two-round structured debate between specialist agents.

Round 1 — OPENING + CRITIQUE
  Each agent states its position with supporting evidence, then explicitly
  critiques the weakest point in each other agent's initial report.

Round 2 — COUNTER-ARGUMENT + DEFENSE
  Each agent defends itself against the specific critiques leveled at it,
  then mounts a targeted counter-argument to the strongest opposing point.
  Each agent must declare a position_shift verdict.

Post-debate — ANALYSIS
  One analysis call scores every argument for strength, maps all
  cross-agent contradictions, and produces debate analytics.
"""
import json
import uuid
import time
import structlog
from app.agents.base_agent import BaseAgent
from app.schemas.agent import (
    AgentReport, AgentVerdict,
    ArgumentStrength, Contradiction, Critique,
    DebateArgument, DebateRound, DebateTranscript, DebateAnalytics,
)
from app.schemas.risk import ConflictAnalysis
from app.utils.helpers import clamp

log = structlog.get_logger()

# ── System prompts ─────────────────────────────────────────────────────────────

ROUND_1_SYSTEM = """You are the ClaimsNexus Debate Moderator running Round 1 of a structured insurance claims debate.

THREE SPECIALIST AGENTS have already independently analyzed a healthcare claim and reached different verdicts.
Your job is to generate each agent's Round 1 contribution: an opening statement PLUS explicit critiques of the other two agents.

AGENT IDENTITIES AND MANDATES:
- fraud_agent: Forensic investigator. Adversarially skeptical. Mandate is to detect fraud.
  Bias: looks for anomalies, pattern mismatches, statistical outliers.
- medical_agent: Board-certified clinical reviewer. Patient advocate.
  Bias: evaluates clinical necessity; will defend medically sound treatment even if billing looks odd.
- policy_agent: Insurance contract specialist. Contract enforcer.
  Bias: applies policy terms precisely; will deny covered treatments if contractual requirements aren't met.

ROUND 1 RULES:
1. Each agent delivers an opening statement grounded in ITS OWN domain expertise
2. Each agent MUST then critique one specific claim made by EACH of the other two agents
3. Critiques must be substantive — cite specific evidence that undermines the other agent's position
4. No vague critiques ("their evidence is weak") — every critique must target a specific assertion
5. Arguments must be grounded in the claim data, not invented

OUTPUT FORMAT — a JSON object with one key per agent:
{
  "fraud_agent": {
    "argument_id": "R1_FRAUD",
    "position": "APPROVE" | "REJECT" | "PENDING" | "UNCERTAIN",
    "argument": "<opening statement — 2-4 sentences, domain-specific>",
    "evidence_cited": ["<specific data point 1>", "<data point 2>"],
    "critiques_of_others": [
      {
        "target_agent": "medical_agent",
        "target_claim": "<the specific claim from medical_agent's report being challenged>",
        "critique_argument": "<why that claim is wrong or insufficient>",
        "evidence_challenging": ["<counter-evidence>"],
        "severity": "LOW" | "MEDIUM" | "HIGH"
      },
      {
        "target_agent": "policy_agent",
        "target_claim": "<claim being challenged>",
        "critique_argument": "<challenge>",
        "evidence_challenging": ["<counter-evidence>"],
        "severity": "LOW" | "MEDIUM" | "HIGH"
      }
    ],
    "confidence": <float 0-1>
  },
  "medical_agent": { ... same structure ... },
  "policy_agent": { ... same structure ... }
}

QUALITY STANDARDS:
- Each opening statement must cite at least 2 specific data points from the claim
- Each critique must quote or paraphrase the specific claim being challenged
- Critiques from the fraud_agent should challenge clinical reasoning with statistical/forensic evidence
- Critiques from the medical_agent should challenge fraud/policy conclusions with clinical facts
- Critiques from the policy_agent should challenge clinical/fraud arguments with contractual analysis
- Agents with the same verdict may still disagree on the REASON — that is valid and encouraged
"""

ROUND_2_SYSTEM = """You are the ClaimsNexus Debate Moderator running Round 2 of a structured insurance claims debate.

Round 1 produced opening statements and critiques. Round 2 is for DEFENSE and COUNTER-ARGUMENT.

ROUND 2 RULES:
1. Each agent MUST respond to the critiques made against it in Round 1
2. Each agent MUST mount a targeted counter-argument to the STRONGEST opposing point
3. Each agent MUST declare its position_shift: MAINTAINED / STRENGTHENED / WEAKENED / REVERSED
4. Concessions are allowed and encouraged — acknowledging a valid point strengthens credibility
5. A REVERSED shift is only valid if the counter-evidence genuinely overturns the original basis
6. Arguments must advance the debate — no re-stating Round 1 content without new reasoning

OUTPUT FORMAT — a JSON object with one key per agent:
{
  "fraud_agent": {
    "argument_id": "R2_FRAUD",
    "position": "APPROVE" | "REJECT" | "PENDING" | "UNCERTAIN",
    "defense": "<response to the critiques made against fraud_agent in Round 1>",
    "counter_argument": "<targeted attack on the strongest point raised against fraud_agent>",
    "counter_target_agent": "<agent_id whose argument is being countered>",
    "evidence_cited": ["<new evidence not cited in Round 1>"],
    "position_shift": "MAINTAINED" | "STRENGTHENED" | "WEAKENED" | "REVERSED",
    "shift_reason": "<why the position changed or didn't>",
    "concession": "<any valid point conceded to opponents, or null>",
    "confidence": <float 0-1>
  },
  "medical_agent": { ... same structure ... },
  "policy_agent": { ... same structure ... }
}

QUALITY STANDARDS:
- A defense must directly address the specific critique (not dodge it)
- A counter-argument must target the strongest — not the easiest — opposing claim
- If an agent's evidence was weak, it should acknowledge this in concession
- Position shifts must be justified by what happened in Round 1, not re-stated priors
- An agent that MAINTAINS under heavy critique should explain why the critique failed
"""

ANALYSIS_SYSTEM = """You are the ClaimsNexus Debate Analyst. Your job is to objectively evaluate a completed two-round debate transcript.

You must:
1. Score EVERY argument's strength on four dimensions
2. Identify ALL direct contradictions between agents
3. Track position shifts across rounds
4. Identify resolution status of contradictions
5. Produce analytics

ARGUMENT STRENGTH DIMENSIONS (each 0-1):
- logical_coherence: Is the argument internally consistent? Does the conclusion follow from the premises?
- evidence_quality: Is the cited evidence specific, accurate, and directly relevant to the claim?
- specificity: Is the argument specific to this claim, or could it be copy-pasted onto any claim?
- rebuttal_power: Does it effectively neutralize the opposing view? (0 for Round 1 openings)
- overall: Weighted composite (coherence 30%, evidence 35%, specificity 20%, rebuttal 15%)

CONTRADICTION DETECTION:
A contradiction exists when Agent A asserts X and Agent B asserts not-X about the SAME specific aspect.
Examples:
- Fraud says "CPT 99215 is upcoded for this diagnosis" vs Medical says "99215 is appropriate for multi-system review"
- Medical says "prior auth was not required" vs Policy says "prior auth was mandatory for this CPT"
Severity: HIGH = factual dispute about claim data; MEDIUM = interpretation dispute; LOW = emphasis difference

RESOLUTION: A contradiction is RESOLVED if one agent conceded in Round 2.
PARTIALLY_RESOLVED if the agents narrowed the gap. UNRESOLVED if both maintained positions.

OUTPUT FORMAT:
{
  "argument_strengths": {
    "fraud_agent": {
      "R1_FRAUD": {
        "logical_coherence": <float>,
        "evidence_quality": <float>,
        "specificity": <float>,
        "rebuttal_power": <float>,
        "overall": <float>
      },
      "R2_FRAUD": { ... }
    },
    "medical_agent": { ... },
    "policy_agent": { ... }
  },
  "contradictions": [
    {
      "contradiction_id": "C1",
      "agent_a": "<agent_id>",
      "agent_b": "<agent_id>",
      "dimension": "<what aspect>",
      "agent_a_claim": "<what agent_a asserted>",
      "agent_b_claim": "<what agent_b asserted — must directly contradict agent_a>",
      "severity": "LOW" | "MEDIUM" | "HIGH",
      "resolution_status": "UNRESOLVED" | "PARTIALLY_RESOLVED" | "RESOLVED",
      "resolved_by": "<agent_id that conceded, or null>",
      "resolution_summary": "<how it was resolved, or null>"
    }
  ],
  "round_summaries": {
    "round_1": "<2-sentence summary of what Round 1 established>",
    "round_2": "<2-sentence summary of how Round 2 changed the landscape>"
  },
  "analytics": {
    "most_persuasive_agent": "<agent_id with highest average argument strength>",
    "most_consistent_agent": "<agent_id whose position shifted least but with good reasons>",
    "most_contested_dimension": "<the aspect argued over most intensely>",
    "total_contradictions": <int>,
    "resolved_contradictions": <int>,
    "unresolved_contradictions": <int>,
    "position_shifts": {
      "fraud_agent": "MAINTAINED" | "STRENGTHENED" | "WEAKENED" | "REVERSED",
      "medical_agent": "...",
      "policy_agent": "..."
    },
    "average_strength_by_agent": {
      "fraud_agent": <float>,
      "medical_agent": <float>,
      "policy_agent": <float>
    },
    "debate_outcome": "CONSENSUS" | "PARTIAL_CONSENSUS" | "DEADLOCK",
    "key_unresolved_issue": "<the single most important unresolved disagreement>"
  }
}
"""


class DebateAgent(BaseAgent):
    agent_id = "debate_agent_v2"

    async def run_debate(
        self,
        claim_data: dict,
        fraud_report: AgentReport,
        medical_report: AgentReport,
        policy_report: AgentReport,
        conflict_analysis: ConflictAnalysis,
        context: dict,
    ) -> DebateTranscript:
        session_id = str(uuid.uuid4())
        claim_id = claim_data["id"]
        t0 = time.monotonic()
        llm_calls = 0

        log.info("debate_v2_starting", claim_id=claim_id,
                 conflict_type=conflict_analysis.conflict_type,
                 scope=conflict_analysis.debate_scope)

        # ── Build rich agent summaries from enhanced reports ──────────────────
        agent_summaries = _build_agent_summaries(fraud_report, medical_report, policy_report)
        claim_context = _sanitize_claim(claim_data)

        # ════════════════════════════════════════════════════════════════════
        # ROUND 1 — OPENING STATEMENTS + CRITIQUES
        # ════════════════════════════════════════════════════════════════════
        r1_user = _build_round1_prompt(claim_context, agent_summaries, conflict_analysis)
        r1_raw: dict = {}
        try:
            from app.services.llm_safety_wrapper import safe_llm_call
            llm_response = await safe_llm_call(prompt=r1_user, system=ROUND_1_SYSTEM, use_json=True, max_tokens=3000)
            if llm_response["status"] != "SUCCESS":
                raise RuntimeError(llm_response.get("reason", "LLM unavailable"))
            r1_raw = llm_response["result"] or {}
            llm_calls += 1
            log.info("debate_round1_complete", claim_id=claim_id, agents=list(r1_raw.keys()))
        except Exception as e:
            log.error("debate_round1_error", error=str(e))
            r1_raw = _fallback_round(1, fraud_report, medical_report, policy_report)

        round1 = _parse_round1(r1_raw, conflict_analysis)

        # ════════════════════════════════════════════════════════════════════
        # ROUND 2 — COUNTER-ARGUMENTS + DEFENSES
        # ════════════════════════════════════════════════════════════════════
        r2_user = _build_round2_prompt(claim_context, agent_summaries, r1_raw, conflict_analysis)
        r2_raw: dict = {}
        try:
            from app.services.llm_safety_wrapper import safe_llm_call
            llm_response = await safe_llm_call(prompt=r2_user, system=ROUND_2_SYSTEM, use_json=True, max_tokens=3000)
            if llm_response["status"] != "SUCCESS":
                raise RuntimeError(llm_response.get("reason", "LLM unavailable"))
            r2_raw = llm_response["result"] or {}
            llm_calls += 1
            log.info("debate_round2_complete", claim_id=claim_id, agents=list(r2_raw.keys()))
        except Exception as e:
            log.error("debate_round2_error", error=str(e))
            r2_raw = _fallback_round2(r1_raw)

        round2 = _parse_round2(r2_raw)

        # ════════════════════════════════════════════════════════════════════
        # POST-DEBATE ANALYSIS — scoring + contradictions + analytics
        # ════════════════════════════════════════════════════════════════════
        analysis_user = _build_analysis_prompt(claim_context, r1_raw, r2_raw, agent_summaries)
        analysis_raw: dict = {}
        try:
            from app.services.llm_safety_wrapper import safe_llm_call
            llm_response = await safe_llm_call(
                prompt=analysis_user,
                system=ANALYSIS_SYSTEM,
                use_json=True,
                max_tokens=3000,
            )
            if llm_response["status"] != "SUCCESS":
                raise RuntimeError(llm_response.get("reason", "LLM unavailable"))
            analysis_raw = llm_response["result"] or {}
            llm_calls += 1
            log.info("debate_analysis_complete", claim_id=claim_id,
                     contradictions=len(analysis_raw.get("contradictions", [])))
        except Exception as e:
            log.error("debate_analysis_error", error=str(e))
            analysis_raw = {}

        # ── Apply argument strength scores back to DebateArguments ────────────
        strengths = analysis_raw.get("argument_strengths", {})
        _apply_strengths(round1, strengths)
        _apply_strengths(round2, strengths)

        # ── Apply round summaries ─────────────────────────────────────────────
        summaries = analysis_raw.get("round_summaries", {})
        round1.round_summary = summaries.get("round_1", "")
        round2.round_summary = summaries.get("round_2", "")

        # ── Parse contradictions ──────────────────────────────────────────────
        contradictions = _parse_contradictions(analysis_raw.get("contradictions", []))

        # Attach active contradiction ids to each round
        round1.active_contradiction_ids = [c.contradiction_id for c in contradictions]
        resolved = {c.contradiction_id for c in contradictions
                    if c.resolution_status == "RESOLVED"}
        round2.active_contradiction_ids = [c.contradiction_id for c in contradictions
                                            if c.contradiction_id not in resolved]

        # ── Parse analytics ───────────────────────────────────────────────────
        analytics = _parse_analytics(analysis_raw.get("analytics"))

        # ── Derive final positions and consensus ──────────────────────────────
        final_positions = _extract_final_positions([round1, round2])
        consensus = _check_consensus(final_positions)
        if consensus:
            round2.consensus_reached = True

        duration_ms = int((time.monotonic() - t0) * 1000)
        log.info("debate_v2_complete",
                 claim_id=claim_id,
                 rounds=2,
                 llm_calls=llm_calls,
                 contradictions=len(contradictions),
                 consensus=consensus,
                 duration_ms=duration_ms)

        return DebateTranscript(
            session_id=session_id,
            claim_id=claim_id,
            triggered_by=conflict_analysis.conflict_type,
            scope=conflict_analysis.debate_scope,
            rounds=[round1, round2],
            contradictions=contradictions,
            analytics=analytics,
            consensus_reached=consensus,
            final_positions=final_positions,
            duration_ms=duration_ms,
            llm_calls_made=llm_calls,
        )

    async def run(self, claim_data: dict, context: dict) -> "AgentReport":  # type: ignore[override]
        raise NotImplementedError("Use run_debate() instead")


# ── Prompt builders ────────────────────────────────────────────────────────────

def _build_agent_summaries(
    fraud: AgentReport, medical: AgentReport, policy: AgentReport
) -> dict:
    """Extract the richest available context from each enhanced AgentReport."""
    def _summarise(r: AgentReport) -> dict:
        s: dict = {
            "verdict": r.verdict,
            "score": round(r.score, 3),
            "confidence": round(r.confidence, 3),
            "flags": r.flags,
        }
        # Verdict probability
        if r.verdict_probability:
            s["verdict_probability"] = {
                "APPROVE": round(r.verdict_probability.APPROVE, 3),
                "REJECT": round(r.verdict_probability.REJECT, 3),
                "PENDING": round(r.verdict_probability.PENDING, 3),
            }
        # Top reasoning steps (by weight)
        if r.reasoning_chain:
            top = sorted(r.reasoning_chain, key=lambda x: x.weight, reverse=True)[:3]
            s["top_reasoning"] = [
                {"inference": step.inference, "weight": round(step.weight, 2)}
                for step in top
            ]
        # Key evidence
        if r.key_evidence:
            s["key_evidence"] = [
                {
                    "id": e.evidence_id,
                    "type": e.type,
                    "description": e.description,
                    "strength": round(e.strength, 2),
                    "cited_value": e.cited_value,
                }
                for e in sorted(r.key_evidence, key=lambda x: x.strength, reverse=True)[:4]
            ]
        # Risk factors
        if r.risk_factors:
            s["risk_factors"] = [
                {
                    "id": rf.factor_id,
                    "category": rf.category,
                    "severity": rf.severity,
                    "probability": round(rf.probability, 2),
                    "description": rf.description,
                }
                for rf in sorted(r.risk_factors, key=lambda x: x.probability, reverse=True)[:3]
            ]
        # Dissenting view
        if r.dissenting_view:
            s["dissenting_view"] = {
                "alternative": r.dissenting_view.alternative_verdict,
                "probability": round(r.dissenting_view.probability, 2),
                "strongest_argument_for_alternative":
                    r.dissenting_view.strongest_argument_for_alternative,
            }
        # Internal reasoning conclusion
        if r.internal_reasoning:
            s["internal_conclusion"] = r.internal_reasoning.phase_6_conclusion
        return s

    return {
        "fraud_agent": _summarise(fraud),
        "medical_agent": _summarise(medical),
        "policy_agent": _summarise(policy),
    }


def _sanitize_claim(claim_data: dict) -> dict:
    return {k: v for k, v in claim_data.items()
            if k not in {"raw_payload", "is_simulation"}}


def _build_round1_prompt(
    claim: dict, summaries: dict, conflict: ConflictAnalysis
) -> str:
    return f"""
Generate Round 1 debate contributions for this healthcare claims dispute.

═══════════════════════════════════════════
CLAIM UNDER DISPUTE
═══════════════════════════════════════════
{json.dumps(claim, indent=2, default=str)}

═══════════════════════════════════════════
AGENT REPORTS (what each agent concluded independently)
═══════════════════════════════════════════
{json.dumps(summaries, indent=2)}

═══════════════════════════════════════════
CONFLICT ANALYSIS
═══════════════════════════════════════════
Conflict type: {conflict.conflict_type}
Dispute dimensions: {json.dumps(conflict.debate_scope)}
Conflicting agents: {conflict.conflicting_agents}

═══════════════════════════════════════════
ROUND 1 TASK
═══════════════════════════════════════════
For each agent, generate:
1. An opening statement defending their verdict with their strongest domain-specific evidence
2. A critique of medical_agent (from fraud_agent and policy_agent perspectives)
3. A critique of fraud_agent (from medical_agent and policy_agent perspectives)
4. A critique of policy_agent (from fraud_agent and medical_agent perspectives)

Critiques must:
- Quote or paraphrase the specific claim from that agent's report being challenged
- Cite specific counter-evidence from the claim data
- Be grounded in the critiquing agent's domain expertise

Output the JSON object now.
"""


def _build_round2_prompt(
    claim: dict, summaries: dict, r1: dict, conflict: ConflictAnalysis
) -> str:
    return f"""
Generate Round 2 debate contributions responding to Round 1 critiques.

═══════════════════════════════════════════
CLAIM UNDER DISPUTE
═══════════════════════════════════════════
{json.dumps(claim, indent=2, default=str)}

═══════════════════════════════════════════
ORIGINAL AGENT VERDICTS
═══════════════════════════════════════════
{json.dumps({k: {"verdict": v["verdict"], "confidence": v["confidence"]} for k, v in summaries.items()}, indent=2)}

═══════════════════════════════════════════
ROUND 1 TRANSCRIPT
═══════════════════════════════════════════
{json.dumps(r1, indent=2)}

═══════════════════════════════════════════
ROUND 2 TASK
═══════════════════════════════════════════
For each agent:
1. DEFEND against the specific critiques made about you in Round 1 (address each one)
2. COUNTER the single strongest argument made against your position (identify who made it)
3. DECLARE your position_shift (MAINTAINED/STRENGTHENED/WEAKENED/REVERSED) and justify it
4. CONCEDE any valid point your opponents made (be honest — concession strengthens credibility)

Each Round 2 argument must advance the debate — no re-stating Round 1 content without new reasoning.
Do not use the same evidence already cited in Round 1.

Output the JSON object now.
"""


def _build_analysis_prompt(
    claim: dict, r1: dict, r2: dict, summaries: dict
) -> str:
    return f"""
Analyze the completed two-round debate transcript for this healthcare claim.

═══════════════════════════════════════════
CLAIM SUMMARY
═══════════════════════════════════════════
Claim ID: {claim.get("id")}
ICD codes: {claim.get("icd_codes")}
CPT codes: {claim.get("cpt_codes")}
Billed: ${claim.get("billed_amount")}

═══════════════════════════════════════════
ORIGINAL VERDICTS
═══════════════════════════════════════════
{json.dumps({k: {"verdict": v["verdict"], "confidence": v["confidence"]} for k, v in summaries.items()}, indent=2)}

═══════════════════════════════════════════
ROUND 1 TRANSCRIPT
═══════════════════════════════════════════
{json.dumps(r1, indent=2)}

═══════════════════════════════════════════
ROUND 2 TRANSCRIPT
═══════════════════════════════════════════
{json.dumps(r2, indent=2)}

═══════════════════════════════════════════
ANALYSIS TASK
═══════════════════════════════════════════
1. Score every argument (R1_FRAUD, R1_MEDICAL, R1_POLICY, R2_FRAUD, R2_MEDICAL, R2_POLICY)
   on all four strength dimensions
2. Identify all direct contradictions between agents
3. Assess resolution status of each contradiction based on Round 2 concessions
4. Compute analytics: who was most persuasive, what was most contested, final outcome

Be objective — do not favor any agent. Score based on evidence quality and logical rigor.
Output the analysis JSON now.
"""


# ── Output parsers ─────────────────────────────────────────────────────────────

def _parse_round1(raw: dict, conflict: ConflictAnalysis) -> DebateRound:
    arguments: list[DebateArgument] = []
    agent_keys = ["fraud_agent", "medical_agent", "policy_agent"]

    for agent_id in agent_keys:
        data = raw.get(agent_id, {})
        if not data:
            continue

        critiques = []
        for c in data.get("critiques_of_others", []):
            try:
                critiques.append(Critique(
                    target_agent=c.get("target_agent", ""),
                    target_claim=c.get("target_claim", ""),
                    critique_argument=c.get("critique_argument", ""),
                    evidence_challenging=c.get("evidence_challenging", []),
                    severity=c.get("severity", "MEDIUM"),
                ))
            except Exception:
                continue

        try:
            arg = DebateArgument(
                argument_id=data.get("argument_id", f"R1_{agent_id.upper()[:5]}"),
                agent_id=agent_id,
                round_number=1,
                argument_type="OPENING" if not critiques else "CRITIQUE",
                position=AgentVerdict(data.get("position", "UNCERTAIN")),
                argument=data.get("argument", ""),
                evidence_cited=data.get("evidence_cited", []),
                critiques_of_others=critiques,
                confidence=clamp(float(data.get("confidence", 0.5))),
                is_duplicate=False,
            )
            arguments.append(arg)
        except Exception as e:
            log.warning("round1_parse_error", agent=agent_id, error=str(e))

    return DebateRound(
        round_number=1,
        round_type="OPENING",
        arguments=arguments,
        consensus_reached=_check_consensus(_pos_map(arguments)),
    )


def _parse_round2(raw: dict) -> DebateRound:
    arguments: list[DebateArgument] = []
    agent_keys = ["fraud_agent", "medical_agent", "policy_agent"]

    for agent_id in agent_keys:
        data = raw.get(agent_id, {})
        if not data:
            continue

        try:
            # Merge defense + counter_argument into single argument text
            defense = data.get("defense", "")
            counter = data.get("counter_argument", "")
            combined = ""
            if defense:
                combined += f"[DEFENSE] {defense}"
            if counter:
                combined += f"\n[COUNTER] {counter}"

            shift_raw = data.get("position_shift", "MAINTAINED").upper()
            valid_shifts = {"MAINTAINED", "STRENGTHENED", "WEAKENED", "REVERSED"}
            shift = shift_raw if shift_raw in valid_shifts else "MAINTAINED"

            arg = DebateArgument(
                argument_id=data.get("argument_id", f"R2_{agent_id.upper()[:5]}"),
                agent_id=agent_id,
                round_number=2,
                argument_type="DEFENSE" if not data.get("counter_argument") else "COUNTER_ARGUMENT",
                position=AgentVerdict(data.get("position", "UNCERTAIN")),
                argument=combined or data.get("argument", ""),
                evidence_cited=data.get("evidence_cited", []),
                responding_to=data.get("counter_target_agent"),
                defense_of=f"R1_{agent_id.upper()[:5]}",
                concession=data.get("concession") or None,
                position_shift=shift,
                confidence=clamp(float(data.get("confidence", 0.5))),
                is_duplicate=False,
            )
            arguments.append(arg)
        except Exception as e:
            log.warning("round2_parse_error", agent=agent_id, error=str(e))

    return DebateRound(
        round_number=2,
        round_type="COUNTER_ARGUMENT",
        arguments=arguments,
        consensus_reached=_check_consensus(_pos_map(arguments)),
    )


def _apply_strengths(round_obj: DebateRound, strengths: dict) -> None:
    """Back-fill ArgumentStrength scores onto DebateArguments from analysis output."""
    for arg in round_obj.arguments:
        agent_strengths = strengths.get(arg.agent_id, {})
        score_data = agent_strengths.get(arg.argument_id)
        if not score_data and isinstance(agent_strengths, dict):
            # Try matching by round prefix
            prefix = f"R{arg.round_number}_"
            score_data = next(
                (v for k, v in agent_strengths.items() if k.startswith(prefix)),
                None,
            )
        if isinstance(score_data, dict):
            try:
                arg.argument_strength = ArgumentStrength(
                    logical_coherence=clamp(float(score_data.get("logical_coherence", 0.5))),
                    evidence_quality=clamp(float(score_data.get("evidence_quality", 0.5))),
                    specificity=clamp(float(score_data.get("specificity", 0.5))),
                    rebuttal_power=clamp(float(score_data.get("rebuttal_power", 0.0))),
                    overall=clamp(float(score_data.get("overall", 0.5))),
                )
            except Exception:
                pass


def _parse_contradictions(raw_list: list) -> list[Contradiction]:
    result = []
    for i, c in enumerate(raw_list, 1):
        if not isinstance(c, dict):
            continue
        try:
            result.append(Contradiction(
                contradiction_id=c.get("contradiction_id", f"C{i}"),
                agent_a=c.get("agent_a", ""),
                agent_b=c.get("agent_b", ""),
                dimension=c.get("dimension", ""),
                agent_a_claim=c.get("agent_a_claim", ""),
                agent_b_claim=c.get("agent_b_claim", ""),
                severity=c.get("severity", "MEDIUM"),
                resolution_status=c.get("resolution_status", "UNRESOLVED"),
                resolved_by=c.get("resolved_by"),
                resolution_summary=c.get("resolution_summary"),
            ))
        except Exception as e:
            log.warning("contradiction_parse_error", index=i, error=str(e))
    return result


def _parse_analytics(raw: dict | None) -> DebateAnalytics | None:
    if not raw or not isinstance(raw, dict):
        return None
    try:
        return DebateAnalytics(
            most_persuasive_agent=raw.get("most_persuasive_agent", ""),
            most_consistent_agent=raw.get("most_consistent_agent", ""),
            most_contested_dimension=raw.get("most_contested_dimension", ""),
            total_contradictions=int(raw.get("total_contradictions", 0)),
            resolved_contradictions=int(raw.get("resolved_contradictions", 0)),
            unresolved_contradictions=int(raw.get("unresolved_contradictions", 0)),
            position_shifts=raw.get("position_shifts", {}),
            average_strength_by_agent=raw.get("average_strength_by_agent", {}),
            debate_outcome=raw.get("debate_outcome", "DEADLOCK"),
            key_unresolved_issue=raw.get("key_unresolved_issue", ""),
        )
    except Exception as e:
        log.warning("analytics_parse_error", error=str(e))
        return None


# ── Consensus and position helpers ────────────────────────────────────────────

def _pos_map(arguments: list[DebateArgument]) -> dict[str, str]:
    return {a.agent_id: a.position for a in arguments if not a.is_duplicate}


def _check_consensus(positions: dict[str, str]) -> bool:
    vals = list(positions.values())
    return len(vals) >= 2 and len(set(vals)) == 1


def _extract_final_positions(rounds: list[DebateRound]) -> dict[str, str]:
    positions: dict[str, str] = {}
    for rnd in reversed(rounds):
        for arg in rnd.arguments:
            if arg.agent_id not in positions and not arg.is_duplicate:
                positions[arg.agent_id] = arg.position
        if len(positions) == 3:
            break
    return positions


# ── Fallback generators ────────────────────────────────────────────────────────

def _fallback_round(
    round_num: int,
    fraud: AgentReport,
    medical: AgentReport,
    policy: AgentReport,
) -> dict:
    return {
        "fraud_agent": {
            "argument_id": f"R{round_num}_FRAUD",
            "position": fraud.verdict,
            "argument": "LLM unavailable — using pre-computed verdict from initial analysis.",
            "evidence_cited": fraud.flags,
            "critiques_of_others": [],
            "confidence": fraud.confidence,
        },
        "medical_agent": {
            "argument_id": f"R{round_num}_MEDICAL",
            "position": medical.verdict,
            "argument": "LLM unavailable — using pre-computed verdict from initial analysis.",
            "evidence_cited": medical.flags,
            "critiques_of_others": [],
            "confidence": medical.confidence,
        },
        "policy_agent": {
            "argument_id": f"R{round_num}_POLICY",
            "position": policy.verdict,
            "argument": "LLM unavailable — using pre-computed verdict from initial analysis.",
            "evidence_cited": policy.flags,
            "critiques_of_others": [],
            "confidence": policy.confidence,
        },
    }


def _fallback_round2(r1: dict) -> dict:
    result = {}
    for agent_id, data in r1.items():
        result[agent_id] = {
            "argument_id": f"R2_{agent_id.split('_')[0].upper()}",
            "position": data.get("position", "UNCERTAIN"),
            "defense": "LLM unavailable — maintaining Round 1 position.",
            "counter_argument": "",
            "counter_target_agent": None,
            "evidence_cited": [],
            "position_shift": "MAINTAINED",
            "shift_reason": "System error — no Round 2 analysis available.",
            "concession": None,
            "confidence": data.get("confidence", 0.5),
        }
    return result
