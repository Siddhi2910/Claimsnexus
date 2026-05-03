"use client";

import { motion } from "framer-motion";
import { useQuery } from "@tanstack/react-query";
import {
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Tooltip,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  AreaChart,
  Area
} from "recharts";
import { Card, CardTitle } from "@/components/ui/card";
import { fetchAnalytics } from "@/lib/api/claims";
import { Skeleton } from "@/components/ui/skeleton";

function formatCurrency(value: number) {
  return value.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

function EmptyState() {
  return (
    <p className="mt-4 rounded-xl border border-dashed border-white/15 bg-white/5 p-5 text-sm text-slate-400">
      No live claim data yet - submit a claim to populate analytics.
    </p>
  );
}

export default function AnalyticsPage() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["analytics"],
    queryFn: fetchAnalytics,
    refetchInterval: 6000
  });

  const hasClaims = Boolean(data?.totalClaims);
  const hasRisk = Boolean(data?.riskDistribution.some((item) => item.value > 0));

  return (
    <div className="page-shell">
      <div>
        <h2 className="page-title">Analytics</h2>
        <p className="page-subtitle">Live approval, risk, agent, and cost analytics computed from API claim and decision records.</p>
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        {isLoading
          ? Array.from({ length: 4 }).map((_, index) => (
              <Card key={index}>
                <Skeleton className="h-4 w-28" />
                <Skeleton className="mt-3 h-8 w-16" />
              </Card>
            ))
          : [
              { label: "Approval Rate", value: data?.approvalRate != null ? `${data.approvalRate.toFixed(1)}%` : "--" },
              { label: "Total Claims", value: data?.totalClaims ?? 0 },
              { label: "Approved", value: data?.approved ?? 0 },
              { label: "Pending Review", value: data?.pendingReview ?? 0 }
            ].map((item) => (
              <motion.div key={item.label} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}>
                <Card>
                  <p className="text-sm text-slate-400">{item.label}</p>
                  <p className="mt-2 text-2xl font-semibold">{item.value}</p>
                </Card>
              </motion.div>
            ))}
      </div>

      {isError ? (
        <Card>
          <p className="text-sm text-rose-300">Failed to load analytics data.</p>
          <button className="mt-2 rounded-lg border border-white/20 px-2 py-1 text-xs hover:bg-white/10" onClick={() => refetch()}>
            Retry
          </button>
        </Card>
      ) : null}

      {!isLoading && !hasClaims ? (
        <Card>
          <CardTitle>Live Analytics</CardTitle>
          <EmptyState />
        </Card>
      ) : null}

      {isLoading ? (
        <Card>
          <div className="space-y-3">
            <Skeleton className="h-5 w-48" />
            <Skeleton className="h-64 w-full" />
          </div>
        </Card>
      ) : null}

      {hasClaims ? (
        <>
          <div className="grid gap-5 lg:grid-cols-2">
            <Card>
              <CardTitle>Claim Volume by Day</CardTitle>
              <div className="mt-4 h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={data?.claimVolumeByDay ?? []}>
                    <defs>
                      <linearGradient id="claimVolumeFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.42} />
                        <stop offset="95%" stopColor="#22d3ee" stopOpacity={0.04} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#27324a" />
                    <XAxis dataKey="date" stroke="#9ca3af" />
                    <YAxis stroke="#9ca3af" allowDecimals={false} />
                    <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid rgba(255,255,255,.12)", borderRadius: 12 }} />
                    <Area type="monotone" dataKey="claims" stroke="#22d3ee" fill="url(#claimVolumeFill)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </Card>

            <Card>
              <CardTitle>Decision Distribution</CardTitle>
              <div className="mt-4 h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data?.decisionDistribution ?? []}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#27324a" />
                    <XAxis dataKey="label" stroke="#9ca3af" />
                    <YAxis stroke="#9ca3af" allowDecimals={false} />
                    <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid rgba(255,255,255,.12)", borderRadius: 12 }} />
                    <Bar dataKey="value" radius={[8, 8, 0, 0]}>
                      {(data?.decisionDistribution ?? []).map((entry) => (
                        <Cell key={entry.label} fill={entry.color} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <Card>
              <CardTitle>Billed vs Approved Amount</CardTitle>
              <div className="mt-4 h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={data?.billedVsApproved ?? []}>
                    <defs>
                      <linearGradient id="billedFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#f43f5e" stopOpacity={0.32} />
                        <stop offset="95%" stopColor="#f43f5e" stopOpacity={0.04} />
                      </linearGradient>
                      <linearGradient id="approvedFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#10b981" stopOpacity={0.34} />
                        <stop offset="95%" stopColor="#10b981" stopOpacity={0.04} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#27324a" />
                    <XAxis dataKey="date" stroke="#9ca3af" />
                    <YAxis stroke="#9ca3af" tickFormatter={(value) => `$${Number(value) / 1000}k`} />
                    <Tooltip
                      formatter={(value) => formatCurrency(Number(value))}
                      contentStyle={{ background: "#0f172a", border: "1px solid rgba(255,255,255,.12)", borderRadius: 12 }}
                    />
                    <Area type="monotone" dataKey="billed" stroke="#f43f5e" fill="url(#billedFill)" />
                    <Area type="monotone" dataKey="approved" stroke="#10b981" fill="url(#approvedFill)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </Card>

            <Card>
              <CardTitle>Risk Distribution</CardTitle>
              {!hasRisk ? (
                <EmptyState />
              ) : (
                <div className="mt-4 h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={data?.riskDistribution ?? []} dataKey="value" nameKey="label" innerRadius={68} outerRadius={98} paddingAngle={4}>
                        {(data?.riskDistribution ?? []).map((entry) => (
                          <Cell key={entry.label} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid rgba(255,255,255,.12)", borderRadius: 12 }} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
              )}
            </Card>
          </div>

          <Card>
            <CardTitle>Average Agent Risk Comparison</CardTitle>
            <div className="mt-4 h-64">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data?.agentRiskComparison ?? []}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#27324a" />
                  <XAxis dataKey="agent" stroke="#9ca3af" />
                  <YAxis stroke="#9ca3af" domain={[0, 1]} />
                  <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid rgba(255,255,255,.12)", borderRadius: 12 }} />
                  <Bar dataKey="risk" radius={[8, 8, 0, 0]} fill="#a78bfa" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </>
      ) : null}
    </div>
  );
}
