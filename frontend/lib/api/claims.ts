import { api } from "@/lib/api/client";

export type ClaimStatus = "RECEIVED" | "PENDING_REVIEW" | "APPROVED" | "REJECTED";

export type ClaimStatusItem = {
  id: string;
  claim_number: string;
  status: ClaimStatus;
  claimant_name: string;
  billed_amount: number;
  approved_amount: number | null;
  created_at: string;
  updated_at: string;
};

export type ClaimListResponse = {
  total: number;
  items: ClaimStatusItem[];
};

export type SubmitClaimRequest = {
  claim_type: string;
  claimant_id: string;
  claimant_name: string;
  policy_number: string;
  plan_id: string;
  provider_id: string;
  provider_name: string;
  provider_npi?: string;
  facility_name?: string;
  service_date: string;
  icd_codes: string[];
  cpt_codes: string[];
  diagnosis_description: string;
  procedure_description: string;
  billed_amount: number;
  requested_amount: number;
  in_network: boolean;
  prior_auth_number?: string;
  raw_payload: Record<string, unknown>;
};

export type DecisionResponse = {
  status?: "PENDING" | "ERROR";
  message?: string;
  id?: string;
  claim_id?: string;
  verdict?: string;
  confidence?: number;
  approved_amount?: number | null;
  composite_risk_score?: number;
  risk_classification?: string;
  routing_decision?: string;
  fraud_score?: number;
  medical_risk_score?: number;
  policy_risk_score?: number;
  fraud_agent_report?: Record<string, unknown>;
  medical_agent_report?: Record<string, unknown>;
  policy_agent_report?: Record<string, unknown>;
  arbiter_report?: Record<string, unknown>;
  reasoning_tree?: Record<string, unknown>;
  debate_occurred?: boolean;
  debate_transcript?: Record<string, unknown> | null;
  conflict_analysis?: Record<string, unknown> | null;
  human_required?: boolean;
  human_override?: Record<string, unknown> | null;
  denial_reason?: string | null;
  appeals_pathway?: string | null;
  precedent_case_ids?: string[];
  created_at?: string;
  finalized_at?: string | null;
};

export type ClaimWithDecision = {
  claim: ClaimStatusItem;
  decision?: DecisionResponse;
  decisionError?: string;
};

export type DashboardStats = {
  claims: ClaimWithDecision[];
  totalClaims: number;
  approved: number;
  rejected: number;
  pendingReview: number;
  approvalRate: number | null;
  avgRiskScore: number | null;
  totalBilledAmount: number;
  totalApprovedAmount: number;
};

export type AnalyticsData = DashboardStats & {
  claimVolumeByDay: Array<{ date: string; claims: number }>;
  decisionDistribution: Array<{ label: string; value: number; color: string }>;
  riskDistribution: Array<{ label: string; value: number; color: string }>;
  billedVsApproved: Array<{ date: string; billed: number; approved: number }>;
  agentRiskComparison: Array<{ agent: string; risk: number }>;
};

function normalizeVerdict(decision?: DecisionResponse, claim?: ClaimStatusItem): string {
  const raw = decision?.verdict ?? decision?.status ?? claim?.status ?? "PENDING";
  if (raw === "APPROVED") return "APPROVE";
  if (raw === "REJECTED") return "REJECT";
  if (raw === "PENDING_REVIEW") return "REVIEW";
  return raw;
}

function riskScore(decision?: DecisionResponse): number | null {
  const scores = [
    decision?.composite_risk_score,
    decision?.fraud_score,
    decision?.medical_risk_score,
    decision?.policy_risk_score
  ].filter((score): score is number => typeof score === "number");

  if (!scores.length) return null;
  return typeof decision?.composite_risk_score === "number" ? decision.composite_risk_score : Math.max(...scores);
}

function isPendingReview(item: ClaimWithDecision): boolean {
  const verdict = normalizeVerdict(item.decision, item.claim);
  const scores = [item.decision?.fraud_score, item.decision?.medical_risk_score, item.decision?.policy_risk_score].filter(
    (score): score is number => typeof score === "number"
  );

  return (
    verdict === "PENDING" ||
    verdict === "REVIEW" ||
    item.claim.status === "PENDING_REVIEW" ||
    item.decision?.human_required === true ||
    scores.some((score) => score >= 0.55)
  );
}

function dayKey(dateValue?: string): string {
  const date = dateValue ? new Date(dateValue) : new Date();
  if (Number.isNaN(date.getTime())) return "Unknown";
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function average(values: number[]): number | null {
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export async function submitClaim(payload: SubmitClaimRequest): Promise<ClaimStatusItem> {
  const { data } = await api.post<ClaimStatusItem>("/claims/submit", payload);
  return data;
}

export async function fetchClaims(status?: string): Promise<ClaimListResponse> {
  return fetchClaimsList(status);
}

export async function fetchClaimsList(status?: string): Promise<ClaimListResponse> {
  const { data } = await api.get<ClaimListResponse>("/claims", {
    params: status ? { status } : undefined
  });
  return data;
}

export async function fetchClaimDetails(claimId: string): Promise<ClaimStatusItem> {
  // Use hardened endpoint (returns 200 w/ PENDING message if not ready)
  const { data } = await api.get<any>(`/claims/${claimId}`);
  if (data?.status === "OK") {
    return {
      id: data.id,
      claim_number: data.claim_number,
      status: data.status_value,
      claimant_name: data.claimant_name,
      billed_amount: data.billed_amount,
      approved_amount: data.approved_amount ?? null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString()
    } as ClaimStatusItem;
  }
  // fallback shape so UI can keep polling
  return {
    id: claimId,
    claim_number: data?.claim_number ?? "",
    status: "PENDING_REVIEW",
    claimant_name: "",
    billed_amount: 0,
    approved_amount: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString()
  } as ClaimStatusItem;
}

export async function fetchDecision(claimId: string): Promise<DecisionResponse> {
  const { data } = await api.get<DecisionResponse>(`/decisions/${claimId}`);
  return data;
}

export async function fetchClaimsWithDecisions(): Promise<ClaimWithDecision[]> {
  const claimsResponse = await fetchClaims();
  const decisions = await Promise.allSettled(claimsResponse.items.map((claim) => fetchDecision(claim.id)));

  return claimsResponse.items.map((claim, index) => {
    const result = decisions[index];
    if (result.status === "fulfilled") {
      return { claim, decision: result.value };
    }
    return {
      claim,
      decisionError: result.reason instanceof Error ? result.reason.message : "Decision unavailable"
    };
  });
}

export async function fetchDashboardStats(): Promise<DashboardStats> {
  const claims = await fetchClaimsWithDecisions();
  const approved = claims.filter((item) => normalizeVerdict(item.decision, item.claim) === "APPROVE").length;
  const rejected = claims.filter((item) => normalizeVerdict(item.decision, item.claim) === "REJECT").length;
  const pendingReview = claims.filter(isPendingReview).length;
  const riskScores = claims.map((item) => riskScore(item.decision)).filter((score): score is number => score != null);
  const totalBilledAmount = claims.reduce((sum, item) => sum + (item.claim.billed_amount ?? 0), 0);
  const totalApprovedAmount = claims.reduce(
    (sum, item) => sum + (item.decision?.approved_amount ?? item.claim.approved_amount ?? 0),
    0
  );

  return {
    claims,
    totalClaims: claims.length,
    approved,
    rejected,
    pendingReview,
    approvalRate: claims.length ? (approved / claims.length) * 100 : null,
    avgRiskScore: average(riskScores),
    totalBilledAmount,
    totalApprovedAmount
  };
}

export async function fetchAnalytics(): Promise<AnalyticsData> {
  const stats = await fetchDashboardStats();
  const byDay = new Map<string, { date: string; claims: number; billed: number; approved: number }>();

  stats.claims.forEach((item) => {
    const date = dayKey(item.claim.created_at);
    const current = byDay.get(date) ?? { date, claims: 0, billed: 0, approved: 0 };
    current.claims += 1;
    current.billed += item.claim.billed_amount ?? 0;
    current.approved += item.decision?.approved_amount ?? item.claim.approved_amount ?? 0;
    byDay.set(date, current);
  });

  const risks = stats.claims.map((item) => riskScore(item.decision)).filter((score): score is number => score != null);
  const low = risks.filter((score) => score < 0.34).length;
  const medium = risks.filter((score) => score >= 0.34 && score < 0.67).length;
  const high = risks.filter((score) => score >= 0.67).length;

  const fraudScores = stats.claims.map((item) => item.decision?.fraud_score).filter((score): score is number => typeof score === "number");
  const medicalScores = stats.claims
    .map((item) => item.decision?.medical_risk_score)
    .filter((score): score is number => typeof score === "number");
  const policyScores = stats.claims
    .map((item) => item.decision?.policy_risk_score)
    .filter((score): score is number => typeof score === "number");

  return {
    ...stats,
    claimVolumeByDay: Array.from(byDay.values()).map(({ date, claims }) => ({ date, claims })),
    decisionDistribution: [
      { label: "APPROVE", value: stats.approved, color: "#10b981" },
      { label: "PENDING", value: stats.claims.filter((item) => normalizeVerdict(item.decision, item.claim) === "PENDING").length, color: "#a78bfa" },
      { label: "REVIEW", value: stats.pendingReview, color: "#f59e0b" },
      { label: "REJECT", value: stats.rejected, color: "#f43f5e" }
    ],
    riskDistribution: [
      { label: "Low", value: low, color: "#10b981" },
      { label: "Medium", value: medium, color: "#f59e0b" },
      { label: "High", value: high, color: "#ef4444" }
    ],
    billedVsApproved: Array.from(byDay.values()).map(({ date, billed, approved }) => ({ date, billed, approved })),
    agentRiskComparison: [
      { agent: "Fraud", risk: average(fraudScores) ?? 0 },
      { agent: "Medical", risk: average(medicalScores) ?? 0 },
      { agent: "Policy", risk: average(policyScores) ?? 0 }
    ]
  };
}

export const claimDecisionUtils = {
  normalizeVerdict,
  riskScore,
  isPendingReview
};
