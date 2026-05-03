"use client";

import { motion } from "framer-motion";
import { useRouter } from "next/navigation";
import { ResponsiveContainer, AreaChart, Area, XAxis, Tooltip, CartesianGrid } from "recharts";
import { Activity, FileCheck2, HeartPulse, Hospital, ShieldCheck } from "lucide-react";
import { Card, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/status-badge";
import { useQuery } from "@tanstack/react-query";
import { claimDecisionUtils, fetchAnalytics, fetchDashboardStats } from "@/lib/api/claims";
import { Skeleton } from "@/components/ui/skeleton";
import { ClaimsNexusLogo, trustItems } from "@/components/brand-logo";

function formatCurrency(value: number | null | undefined) {
  return typeof value === "number"
    ? value.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 })
    : "--";
}

function formatPercent(value: number | null) {
  return typeof value === "number" ? `${value.toFixed(1)}%` : "--";
}

function riskLabel(score: number | null) {
  if (score == null) return "Pending";
  if (score >= 0.67) return "High";
  if (score >= 0.34) return "Medium";
  return "Low";
}

function RiskBadge({ score }: { score: number | null }) {
  const label = riskLabel(score);
  const styles =
    label === "High"
      ? "border-rose-400/30 bg-rose-500/15 text-rose-200"
      : label === "Medium"
        ? "border-amber-400/30 bg-amber-500/15 text-amber-200"
        : label === "Low"
          ? "border-emerald-400/30 bg-emerald-500/15 text-emerald-200"
          : "border-violet-400/30 bg-violet-500/15 text-violet-200";

  return <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${styles}`}>{label}</span>;
}

export default function DashboardPage() {
  const router = useRouter();
  const statsQuery = useQuery({
    queryKey: ["dashboard-stats"],
    queryFn: fetchDashboardStats,
    refetchInterval: 5000
  });
  const analyticsQuery = useQuery({
    queryKey: ["dashboard-analytics"],
    queryFn: fetchAnalytics,
    refetchInterval: 5000
  });

  const stats = statsQuery.data;
  const claims = stats?.claims ?? [];
  const trend = analyticsQuery.data?.claimVolumeByDay ?? [];

  return (
    <div className="page-shell">
      <section className="overflow-hidden rounded-2xl border border-cyan-300/15 bg-[linear-gradient(135deg,rgba(14,165,233,0.18),rgba(139,92,246,0.12)_48%,rgba(15,23,42,0.72))] p-5 shadow-glow md:p-7">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-3xl">
            <ClaimsNexusLogo className="mb-6" />
            <div className="flex flex-wrap gap-2 text-xs text-cyan-100/80">
              {[ShieldCheck, HeartPulse, Hospital, Activity, FileCheck2].map((Icon, idx) => (
                <span key={idx} className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-white/10 bg-white/[0.07]">
                  <Icon className="h-4 w-4" />
                </span>
              ))}
            </div>
            <h2 className="mt-5 max-w-2xl text-3xl font-semibold tracking-tight text-white md:text-4xl">
              Autonomous Health Claims Intelligence
            </h2>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-300">
              Fraud, Medical, Policy, and Arbiter agents evaluate every claim with explainable risk scoring and human-review routing.
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-3 lg:w-[520px]">
            {trustItems.map((item) => {
              const Icon = item.icon;
              return (
                <div key={item.title} className="rounded-xl border border-white/10 bg-black/20 p-4">
                  <Icon className="h-5 w-5 text-cyan-200" />
                  <p className="mt-3 text-sm font-medium text-white">{item.title}</p>
                </div>
              );
            })}
          </div>
        </div>
      </section>

      <div>
        <h2 className="page-title">Operations Overview</h2>
        <p className="page-subtitle">Live claims performance and adjudication health from the ClaimsNexus API.</p>
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        {statsQuery.isLoading
          ? Array.from({ length: 8 }).map((_, index) => (
              <Card key={index}>
                <Skeleton className="h-4 w-24" />
                <Skeleton className="mt-3 h-8 w-20" />
              </Card>
            ))
          : [
              { label: "Total Claims", value: String(stats?.totalClaims ?? 0) },
              { label: "Approved", value: String(stats?.approved ?? 0) },
              { label: "Pending Review", value: String(stats?.pendingReview ?? 0) },
              { label: "Rejected", value: String(stats?.rejected ?? 0) },
              { label: "Approval Rate", value: formatPercent(stats?.approvalRate ?? null) },
              { label: "Avg Risk Score", value: stats?.avgRiskScore != null ? stats.avgRiskScore.toFixed(2) : "--" },
              { label: "Total Billed", value: formatCurrency(stats?.totalBilledAmount) },
              { label: "Total Approved", value: formatCurrency(stats?.totalApprovedAmount) }
            ].map((s) => (
              <motion.div key={s.label} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
                <Card className="min-h-[108px]">
                  <p className="text-sm text-slate-400">{s.label}</p>
                  <p className="mt-2 text-2xl font-semibold">{s.value}</p>
                </Card>
              </motion.div>
            ))}
      </div>

      <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
        <Card>
          <CardTitle>Claim Volume by Day</CardTitle>
          {!analyticsQuery.isLoading && !trend.length ? (
            <p className="mt-4 rounded-xl border border-dashed border-white/15 bg-white/5 p-5 text-sm text-slate-400">
              No live claim data yet - submit a claim to populate analytics.
            </p>
          ) : (
            <div className="mt-4 h-56">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={trend}>
                  <defs>
                    <linearGradient id="dashboardClaimFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.55} />
                      <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0.04} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#27324a" />
                  <XAxis dataKey="date" stroke="#9ca3af" />
                  <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid rgba(255,255,255,.12)", borderRadius: 12 }} />
                  <Area type="monotone" dataKey="claims" stroke="#22d3ee" fill="url(#dashboardClaimFill)" isAnimationActive animationDuration={500} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
      </motion.div>

      <Card>
        <CardTitle>Recent Claims</CardTitle>
        <p className="mt-1 text-xs text-slate-500">Click a row to open the live AI adjudication view.</p>
        {statsQuery.isError ? (
          <div className="mt-3 flex items-center gap-3">
            <p className="text-sm text-rose-300">Failed to fetch claims or decision data.</p>
            <button onClick={() => statsQuery.refetch()} className="rounded-lg border border-white/20 px-2 py-1 text-xs hover:bg-white/10">
              Retry
            </button>
          </div>
        ) : null}
        <div className="mt-3 overflow-x-auto rounded-xl border border-white/10">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="bg-white/[0.02] text-left text-slate-400">
              <tr>
                <th className="px-3 py-2">Claim number</th>
                <th className="px-3">Claimant</th>
                <th className="px-3">Amount</th>
                <th className="px-3">Status / verdict</th>
                <th className="px-3">Risk</th>
                <th className="px-3">Created</th>
                <th className="px-3">Action</th>
              </tr>
            </thead>
            <tbody>
              {claims.slice(0, 8).map((row) => {
                const verdict = claimDecisionUtils.normalizeVerdict(row.decision, row.claim);
                const risk = claimDecisionUtils.riskScore(row.decision);
                return (
                  <tr
                    key={row.claim.id}
                    role="button"
                    tabIndex={0}
                    title={`Open pipeline detail (${row.claim.id})`}
                    onClick={() => router.push(`/claims/${row.claim.id}`)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") router.push(`/claims/${row.claim.id}`);
                    }}
                    className="cursor-pointer border-t border-white/10 transition-colors hover:bg-cyan-500/10"
                  >
                    <td className="px-3 py-3 font-medium">{row.claim.claim_number}</td>
                    <td className="px-3 text-slate-300">{row.claim.claimant_name || "--"}</td>
                    <td className="px-3">{formatCurrency(row.claim.billed_amount)}</td>
                    <td className="px-3"><StatusBadge status={verdict} /></td>
                    <td className="px-3"><RiskBadge score={risk} /></td>
                    <td className="px-3 text-slate-400">{new Date(row.claim.created_at).toLocaleDateString()}</td>
                    <td className="px-3 text-cyan-200">Open</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!statsQuery.isLoading && !claims.length ? (
            <p className="py-5 text-center text-sm text-slate-400">No live claim data yet - submit a claim to populate the dashboard.</p>
          ) : null}
        </div>
        {statsQuery.isFetching && !statsQuery.isLoading ? <p className="mt-2 text-xs text-slate-500">Refreshing live decisions...</p> : null}
      </Card>
    </div>
  );
}
