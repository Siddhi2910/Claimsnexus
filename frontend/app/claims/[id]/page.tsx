"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Card, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/status-badge";
import { fetchClaimDetails, fetchDecision } from "@/lib/api/claims";
import type { DecisionResponse } from "@/lib/api/claims";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";

type UiStage = "RECEIVED" | "PROCESSING" | "ANALYZING" | "DECISION";

const stageOrder: UiStage[] = ["RECEIVED", "PROCESSING", "ANALYZING", "DECISION"];

function getUiStage(claimStatus?: string, decision?: DecisionResponse | undefined): UiStage {
  const hasVerdict = Boolean(decision?.verdict && decision.status !== "PENDING");
  if (hasVerdict || claimStatus === "APPROVED" || claimStatus === "REJECTED") return "DECISION";
  if (claimStatus === "PENDING_REVIEW") return "ANALYZING";
  if (claimStatus === "RECEIVED") return "PROCESSING";
  return "RECEIVED";
}

function verdictFromReport(report: Record<string, unknown> | undefined): string {
  if (!report) return "";
  const v = report.verdict;
  if (typeof v === "string") return v;
  if (v && typeof v === "object" && v !== null && "value" in v) return String((v as { value: string }).value);
  return "";
}

function confidenceFromReport(report: Record<string, unknown> | undefined): number | null {
  if (!report) return null;
  const c = report.confidence;
  return typeof c === "number" ? c : null;
}

function stringField(report: Record<string, unknown> | undefined, keys: string[]): string {
  if (!report) return "";
  for (const key of keys) {
    const value = report[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  const chain = report.reasoning_chain as Array<{ inference?: string }> | undefined;
  return chain?.[0]?.inference ?? "";
}

function stringList(report: Record<string, unknown> | undefined, keys: string[]): string[] {
  if (!report) return [];
  for (const key of keys) {
    const value = report[key];
    if (Array.isArray(value)) {
      return value
        .map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object" && "label" in item) return String((item as { label: unknown }).label);
          if (item && typeof item === "object" && "description" in item) return String((item as { description: unknown }).description);
          if (item && typeof item === "object" && "inference" in item) return String((item as { inference: unknown }).inference);
          return "";
        })
        .filter(Boolean);
    }
  }
  return [];
}

function sourceLabel(report: Record<string, unknown> | undefined): string {
  const source = typeof report?.source === "string" ? report.source : "";
  const provider = typeof report?.provider === "string" ? report.provider : "";
  const model = typeof report?.model === "string" ? report.model : "";
  const flags = Array.isArray(report?.flags) ? report.flags : [];
  const raw = source || provider || model;
  if (raw.toLowerCase().includes("gemini")) return "Gemini";
  if (raw.toLowerCase().includes("openai")) return "OpenAI";
  if (raw.toLowerCase().includes("heuristic") || flags.includes("HEURISTIC_SAFETY_FALLBACK")) return "Heuristic";
  return raw || "Source pending";
}

export default function ClaimDetailPage({ params }: { params: { id: string } }) {
  const claimQuery = useQuery({
    queryKey: ["claim", params.id],
    queryFn: () => fetchClaimDetails(params.id),
    refetchInterval: 2500
  });
  const decisionQuery = useQuery({
    queryKey: ["decision", params.id],
    queryFn: () => fetchDecision(params.id),
    refetchInterval: 2500
  });

  const claim = claimQuery.data;
  const decision = decisionQuery.data;
  const decisionPending = Boolean(
    decision &&
      (decision.status === "PENDING" ||
        (!decision.verdict && decision.status !== "ERROR" && typeof decision.composite_risk_score !== "number"))
  );
  const hasFinalDecision = Boolean(decision?.verdict && decision.verdict !== "PENDING");

  const uiStage = getUiStage(claim?.status, decision);
  const stageIndex = stageOrder.indexOf(uiStage);

  const fraudReport = decision?.fraud_agent_report as Record<string, unknown> | undefined;
  const medicalReport = decision?.medical_agent_report as Record<string, unknown> | undefined;
  const policyReport = decision?.policy_agent_report as Record<string, unknown> | undefined;
  const arbiterReport = decision?.arbiter_report as Record<string, unknown> | undefined;

  const tree = (decision?.reasoning_tree ?? {}) as {
    root_reason?: string;
    branches?: Array<{ agent?: string; verdict?: string; key_factors?: string[] }>;
  };

  const debateRounds = (decision?.debate_transcript as { rounds?: unknown[] } | null)?.rounds?.length ?? 0;

  const agentCards = [
    {
      key: "fraud",
      label: "Fraud Agent",
      score: decision?.fraud_score,
      verdict: verdictFromReport(fraudReport),
      agentConfidence: confidenceFromReport(fraudReport),
      report: fraudReport
    },
    {
      key: "medical",
      label: "Medical Agent",
      score: decision?.medical_risk_score,
      verdict: verdictFromReport(medicalReport),
      agentConfidence: confidenceFromReport(medicalReport),
      report: medicalReport
    },
    {
      key: "policy",
      label: "Policy Agent",
      score: decision?.policy_risk_score,
      verdict: verdictFromReport(policyReport),
      agentConfidence: confidenceFromReport(policyReport),
      report: policyReport
    }
  ];

  const confidenceBars = [
    { label: "Fraud risk (score)", value: decision?.fraud_score ?? 0 },
    { label: "Medical risk (score)", value: decision?.medical_risk_score ?? 0 },
    { label: "Policy risk (score)", value: decision?.policy_risk_score ?? 0 },
    { label: "Arbiter confidence", value: decision?.confidence ?? 0 }
  ];

  const titleSuffix = claim?.claim_number ?? params.id;

  return (
    <div className="page-shell">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="page-title">Claim {titleSuffix}</h2>
          <p className="page-subtitle">
            Internal ID <span className="font-mono text-xs text-slate-400">{params.id}</span> — multi-agent adjudication &
            arbiter.
          </p>
        </div>
        <StatusBadge status={claim?.status ?? "PENDING_REVIEW"} />
      </div>

      {claimQuery.isLoading ? (
        <Card>
          <div className="space-y-2">
            <Skeleton className="h-4 w-52" />
            <Skeleton className="h-4 w-full" />
          </div>
        </Card>
      ) : null}

      {claimQuery.isError ? (
        <Card>
          <p className="text-sm text-rose-300">
            Claim not found. URLs must use the internal UUID from the API (click a row on the Dashboard — not CLM-XXXX
            alone).
          </p>
          <button
            type="button"
            className="mt-2 rounded-lg border border-white/20 px-2 py-1 text-xs hover:bg-white/10"
            onClick={() => claimQuery.refetch()}
          >
            Retry
          </button>
        </Card>
      ) : null}

      {!claimQuery.isError && decisionQuery.isError ? (
        <Card>
          <p className="text-sm text-amber-200">Decision API unreachable — check backend on port 8000.</p>
          <button
            type="button"
            className="mt-2 rounded-lg border border-white/20 px-2 py-1 text-xs hover:bg-white/10"
            onClick={() => decisionQuery.refetch()}
          >
            Retry decision
          </button>
        </Card>
      ) : null}

      {!claimQuery.isError && decisionPending && !hasFinalDecision ? (
        <Card>
          <CardTitle className="text-sky-200">Pipeline active</CardTitle>
          <p className="mt-2 text-sm text-slate-300">
            Fraud, Medical, and Policy agents run in parallel on the API. When they disagree at higher risk, the debate
            agent may run; the Arbiter issues the final verdict. This page polls every few seconds — keep it open.
          </p>
        </Card>
      ) : null}

      <Card>
        <CardTitle>Processing stages</CardTitle>
        <div className="mt-4 grid gap-3 md:grid-cols-4">
          {stageOrder.map((stage, idx) => {
            const active = idx <= stageIndex;
            const current = idx === stageIndex;
            return (
              <motion.div
                key={stage}
                initial={{ opacity: 0.5, y: 6 }}
                animate={{ opacity: active ? 1 : 0.5, y: 0 }}
                transition={{ duration: 0.25 }}
                className={cn(
                  "rounded-xl border px-3 py-2 text-sm",
                  active ? "border-violet-400/40 bg-violet-500/15" : "border-white/10 bg-white/5"
                )}
              >
                <div className="flex items-center justify-between">
                  <span>{stage}</span>
                  {current ? <span className="h-2 w-2 animate-pulse rounded-full bg-violet-300" /> : null}
                </div>
              </motion.div>
            );
          })}
        </div>
      </Card>

      <Card>
        <CardTitle>Claim info</CardTitle>
        <div className="mt-3 grid gap-3 text-sm md:grid-cols-2">
          <p>Claimant: {claim?.claimant_name ?? "—"}</p>
          <p>Claim number: {claim?.claim_number ?? "—"}</p>
          <p>Billed amount: {claim?.billed_amount != null ? `$${claim.billed_amount.toLocaleString()}` : "—"}</p>
          <p>Approved amount: {claim?.approved_amount != null ? `$${claim.approved_amount.toLocaleString()}` : "Pending"}</p>
        </div>
      </Card>

      <div className="grid gap-4 md:grid-cols-3">
        {agentCards.map((agent, idx) => {
          const reason = stringField(agent.report, ["reason", "reasoning", "explanation", "summary"]);
          const evidence = stringList(agent.report, ["evidence", "key_evidence", "evidence_items", "supporting_evidence"]);
          const signals = stringList(agent.report, ["extracted_signals", "signals", "key_signals"]);
          return (
          <motion.div key={agent.key} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
            <Card className="h-full">
              <div className="flex items-start justify-between gap-3">
                <CardTitle>{agent.label}</CardTitle>
                <span className="rounded-full border border-cyan-300/20 bg-cyan-400/10 px-2.5 py-1 text-[10px] font-semibold text-cyan-100">
                  {sourceLabel(agent.report)}
                </span>
              </div>
              <p className="mt-2 text-sm text-slate-200">
                {agent.verdict ? (
                  <>
                    Verdict: <span className="text-violet-200">{agent.verdict}</span>
                  </>
                ) : decisionPending ? (
                  <span className="text-slate-400">Running specialist analysis…</span>
                ) : (
                  <span className="text-slate-500">—</span>
                )}
              </p>
              {agent.agentConfidence != null ? (
                <p className="mt-1 text-xs text-slate-400">Agent confidence: {agent.agentConfidence.toFixed(2)}</p>
              ) : null}
              <p className="mt-1 text-xs text-violet-300">
                {typeof agent.score === "number" ? `Risk score (used in routing): ${agent.score.toFixed(2)}` : "Risk score: —"}
              </p>
              <div className="mt-4 space-y-3 border-t border-white/10 pt-3">
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Reason</p>
                  <p className="mt-1 text-xs leading-relaxed text-slate-300">
                    {reason || (decisionPending ? "Generating..." : "Reason not provided by backend")}
                  </p>
                </div>
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Evidence</p>
                  {evidence.length ? (
                    <ul className="mt-1 space-y-1 text-xs text-slate-300">
                      {evidence.slice(0, 3).map((item, evidenceIdx) => (
                        <li key={`${agent.key}-evidence-${evidenceIdx}`}>{item}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-1 text-xs text-slate-500">Evidence not provided by backend</p>
                  )}
                </div>
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-500">Extracted signals</p>
                  {signals.length ? (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {signals.slice(0, 4).map((item, signalIdx) => (
                        <span key={`${agent.key}-signal-${signalIdx}`} className="rounded-full border border-white/10 bg-white/5 px-2 py-1 text-[11px] text-slate-300">
                          {item}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="mt-1 text-xs text-slate-500">Signals not provided by backend</p>
                  )}
                </div>
              </div>
              {!agent.verdict && decisionPending && idx === 0 ? (
                <p className="mt-2 text-[11px] leading-snug text-slate-500">
                  Negotiation between agents happens in the debate phase when risk/conflicts trigger it — see below when
                  debate_occurred is true.
                </p>
              ) : null}
            </Card>
          </motion.div>
          );
        })}
      </div>

      {decision?.debate_occurred && debateRounds > 0 ? (
        <Card>
          <CardTitle>Multi-agent debate</CardTitle>
          <p className="mt-2 text-sm text-slate-300">
            {debateRounds} debate round(s) recorded — specialists challenged each other before the Arbiter.
          </p>
          <p className="mt-1 text-xs text-slate-500">Expand reasoning sections below for structured outputs.</p>
        </Card>
      ) : null}

      <Card>
        <CardTitle>Arbiter result</CardTitle>
        <p className="mt-2 text-sm text-slate-300">
          Verdict:{" "}
          <span className="font-medium text-violet-200">{decision?.verdict ?? decision?.status ?? "PENDING"}</span>
          {" · "}
          Confidence: {typeof decision?.confidence === "number" ? decision.confidence.toFixed(2) : "—"}
        </p>
        {typeof arbiterReport?.conflict_summary === "string" ? (
          <p className="mt-2 text-xs text-slate-400">{arbiterReport.conflict_summary as string}</p>
        ) : null}
      </Card>

      <Card>
        <CardTitle>Timeline</CardTitle>
        <div className="mt-4 space-y-3">
          {[
            "Claim received and validated",
            "Planner + parallel specialist agents",
            "Risk scoring & optional debate",
            "Arbiter final decision"
          ].map((item, idx) => (
            <motion.div
              key={item}
              initial={{ opacity: 0.4, x: -6 }}
              animate={{ opacity: idx <= stageIndex ? 1 : 0.45, x: 0 }}
              transition={{ duration: 0.25, delay: idx * 0.05 }}
              className="flex items-center gap-3"
            >
              <span
                className={cn("h-2.5 w-2.5 rounded-full", idx <= stageIndex ? "bg-violet-300 shadow-glow" : "bg-slate-600")}
              />
              <p className="text-sm text-slate-300">{item}</p>
            </motion.div>
          ))}
        </div>
      </Card>

      <Card>
        <CardTitle>Explainability</CardTitle>
        <div className="mt-4 space-y-3">
          <details className="rounded-xl border border-white/10 bg-white/5 p-3" open>
            <summary className="cursor-pointer text-sm font-medium">Reasoning tree</summary>
            <div className="mt-3 space-y-2 text-sm text-slate-300">
              <p className="text-slate-200">Root: {tree.root_reason ?? "Waiting for arbiter output…"}</p>
              <div className="ml-4 border-l border-white/15 pl-4">
                {(tree.branches ?? []).length ? (
                  tree.branches?.map((branch, idx) => (
                    <div key={`${branch.agent}-${idx}`} className="mb-2">
                      <p className="font-medium text-slate-200">
                        {branch.agent ?? "Agent"} → {branch.verdict ?? "—"}
                      </p>
                      <p className="text-xs text-slate-400">
                        {(branch.key_factors ?? []).slice(0, 4).join(" · ") || ""}
                      </p>
                    </div>
                  ))
                ) : (
                  <p className="text-xs text-slate-400">Branches appear when the decision record is written.</p>
                )}
              </div>
            </div>
          </details>

          <details className="rounded-xl border border-white/10 bg-white/5 p-3" open>
            <summary className="cursor-pointer text-sm font-medium">Agent explanations (first reasoning step)</summary>
            <div className="mt-3 grid gap-2 md:grid-cols-3">
              {[
                { title: "Fraud Agent", data: fraudReport },
                { title: "Medical Agent", data: medicalReport },
                { title: "Policy Agent", data: policyReport }
              ].map((item) => {
                const chain = item.data?.reasoning_chain as Array<{ inference?: string }> | undefined;
                return (
                  <div key={item.title} className="rounded-lg border border-white/10 bg-black/20 p-3">
                    <p className="text-sm font-medium">{item.title}</p>
                    <p className="mt-2 text-xs text-slate-300">
                      {chain?.[0]?.inference ?? (decisionPending ? "Generating…" : "No chain stored.")}
                    </p>
                  </div>
                );
              })}
            </div>
          </details>

          <details className="rounded-xl border border-white/10 bg-white/5 p-3" open>
            <summary className="cursor-pointer text-sm font-medium">Risk & arbiter confidence bars</summary>
            <div className="mt-3 space-y-3">
              {confidenceBars.map((bar) => {
                const pct = Math.max(0, Math.min(100, Math.round(bar.value * 100)));
                return (
                  <div key={bar.label}>
                    <div className="mb-1 flex items-center justify-between text-xs text-slate-300">
                      <span>{bar.label}</span>
                      <span>{pct}%</span>
                    </div>
                    <div className="h-2 rounded-full bg-white/10">
                      <motion.div
                        initial={{ width: 0 }}
                        animate={{ width: `${pct}%` }}
                        transition={{ duration: 0.45 }}
                        className="h-2 rounded-full bg-violet-400"
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </details>

          <details className="rounded-xl border border-white/10 bg-white/5 p-3" open>
            <summary className="cursor-pointer text-sm font-medium">Arbiter key factors</summary>
            <div className="mt-3 flex flex-wrap gap-2">
              {Array.isArray(arbiterReport?.key_deciding_factors) && (arbiterReport.key_deciding_factors as string[]).length ? (
                (arbiterReport.key_deciding_factors as string[]).map((factor, idx) => (
                  <span
                    key={`${factor}-${idx}`}
                    className="rounded-full border border-violet-400/30 bg-violet-500/15 px-2.5 py-1 text-xs text-violet-200"
                  >
                    {factor}
                  </span>
                ))
              ) : (
                <p className="text-xs text-slate-400">Factors appear when the arbiter JSON is persisted.</p>
              )}
            </div>
          </details>
        </div>
      </Card>
    </div>
  );
}
