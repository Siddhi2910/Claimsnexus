"use client";

import Link from "next/link";
import { AlertTriangle, ShieldAlert } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/status-badge";
import { claimDecisionUtils, fetchDashboardStats } from "@/lib/api/claims";
import type { ClaimWithDecision } from "@/lib/api/claims";
import { Skeleton } from "@/components/ui/skeleton";

function queueReason(item: ClaimWithDecision) {
  const decision = item.decision;
  const scores = [
    ["Fraud", decision?.fraud_score],
    ["Medical", decision?.medical_risk_score],
    ["Policy", decision?.policy_risk_score]
  ] as const;
  const highAgent = scores.find(([, score]) => typeof score === "number" && score >= 0.55);

  if (decision?.human_required) return "Human review required by arbiter";
  if (highAgent) return `${highAgent[0]} risk is ${Number(highAgent[1]).toFixed(2)}`;
  if (item.claim.status === "PENDING_REVIEW") return "Claim status is pending review";
  return "Decision pending or review verdict";
}

export default function ReviewQueuePage() {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["review-queue"],
    queryFn: fetchDashboardStats,
    refetchInterval: 5000
  });

  const items = (data?.claims ?? []).filter(claimDecisionUtils.isPendingReview);

  return (
    <div className="page-shell">
      <div>
        <h2 className="page-title">Review Queue</h2>
        <p className="page-subtitle">
          Claims that need attention because of pending verdicts, human-review routing, or elevated agent risk.
        </p>
      </div>
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle>Human review candidates</CardTitle>
            <p className="mt-1 text-xs text-slate-500">Derived from live decision records and agent risk scores.</p>
          </div>
          <span className="rounded-full border border-cyan-300/25 bg-cyan-400/10 px-3 py-1 text-xs font-semibold text-cyan-100">
            {items.length} open
          </span>
        </div>
        {isLoading ? (
          <div className="mt-3 space-y-2">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : null}
        {isError ? (
          <div className="mt-3 flex items-center gap-2">
            <p className="text-sm text-rose-300">Could not load queue.</p>
            <button type="button" className="rounded-lg border border-white/20 px-3 py-1.5 text-xs hover:bg-white/10" onClick={() => refetch()}>
              Retry
            </button>
          </div>
        ) : null}
        <div className="mt-4 space-y-3">
          {!isLoading && !items.length ? (
            <div className="rounded-xl border border-dashed border-white/15 bg-white/5 p-5">
              <p className="text-sm text-slate-300">No risky or pending claims in the live review queue.</p>
              <p className="mt-1 text-xs text-slate-500">Submit a claim or wait for agent decisions to populate review candidates.</p>
            </div>
          ) : null}
          {items.map((item) => {
            const risk = claimDecisionUtils.riskScore(item.decision);
            const verdict = claimDecisionUtils.normalizeVerdict(item.decision, item.claim);
            return (
              <div
                key={item.claim.id}
                className="rounded-xl border border-white/10 bg-white/5 p-4 transition-all duration-200 hover:border-cyan-300/25 hover:bg-white/[0.07]"
              >
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      {risk != null && risk >= 0.67 ? <ShieldAlert className="h-4 w-4 text-rose-300" /> : <AlertTriangle className="h-4 w-4 text-amber-300" />}
                      <p className="font-medium">{item.claim.claim_number}</p>
                      <StatusBadge status={verdict} />
                    </div>
                    <p className="mt-1 text-sm text-slate-400">{item.claim.claimant_name || "Claimant not provided"}</p>
                    <p className="mt-2 text-sm text-slate-300">{queueReason(item)}</p>
                    <p className="mt-1 text-xs text-slate-500">
                      Composite risk {risk != null ? risk.toFixed(2) : "pending"} | Billed ${item.claim.billed_amount.toLocaleString()}
                    </p>
                  </div>
                  <Link
                    href={`/claims/${item.claim.id}`}
                    className="inline-flex h-9 items-center rounded-lg bg-cyan-500 px-3 text-xs font-medium text-slate-950 shadow-sm hover:bg-cyan-300"
                  >
                    Open pipeline view
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
        {isFetching && !isLoading ? <p className="mt-3 text-xs text-slate-500">Refreshing queue...</p> : null}
      </Card>
    </div>
  );
}
