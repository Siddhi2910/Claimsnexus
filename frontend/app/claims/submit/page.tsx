"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { useRouter } from "next/navigation";
import { Card, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useMutation } from "@tanstack/react-query";
import { submitClaim } from "@/lib/api/claims";

export default function SubmitClaimPage() {
  const router = useRouter();
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [form, setForm] = useState({
    claimant_name: "Sanyam Vats",
    policy_number: "POL-987654",
    provider_name: "AIIMS Delhi",
    requested_amount: "4500",
    diagnosis_description: "Fever and infection",
    procedure_description: "Blood test",
    in_network: "Yes",
    prior_auth_number: "AUTH123"
  });

  const mutation = useMutation({
    mutationFn: submitClaim,
    retry: 2,
    onSuccess: (data) => {
      setSubmitted(data.claim_number);
      router.push(`/claims/${data.id}`);
    }
  });

  return (
    <motion.div className="page-shell" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
      <div>
        <h2 className="page-title">Submit Claim</h2>
        <p className="page-subtitle">
          After submit you&apos;ll land on the claim&apos;s live page — fraud, medical &amp; policy agents run in the background, then the arbiter decides.
        </p>
      </div>
      <Card>
        <CardTitle>Claim Intake</CardTitle>
        <form
          className="mt-4 grid gap-4 md:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate({
              claim_type: "MEDICAL",
              claimant_id: "P12345",
              claimant_name: form.claimant_name,
              policy_number: form.policy_number,
              plan_id: "PLAN-A",
              provider_id: "HOSP001",
              provider_name: form.provider_name,
              provider_npi: "NPI123456",
              facility_name: "AIIMS",
              service_date: new Date().toISOString(),
              icd_codes: ["A01"],
              cpt_codes: ["12345"],
              diagnosis_description: form.diagnosis_description,
              procedure_description: form.procedure_description,
              billed_amount: Number(form.requested_amount),
              requested_amount: Number(form.requested_amount),
              in_network: form.in_network.toLowerCase() === "yes" || form.in_network.toLowerCase() === "true",
              prior_auth_number: form.prior_auth_number,
              raw_payload: {}
            });
          }}
        >
          {[
            ["Claimant Name", "claimant_name", "Sanyam Vats"],
            ["Policy Number", "policy_number", "POL-987654"],
            ["Provider Name", "provider_name", "AIIMS Delhi"],
            ["Claim Amount", "requested_amount", "4500"],
            ["In-Network (Yes/No)", "in_network", "Yes"],
            ["Prior Auth Number", "prior_auth_number", "AUTH123"]
          ].map(([label, key, placeholder]) => (
            <label key={label} className="space-y-1">
              <span className="text-xs text-slate-400">{label}</span>
              <input
                placeholder={placeholder}
                value={form[key as keyof typeof form]}
                onChange={(e) => setForm((prev) => ({ ...prev, [key]: e.target.value }))}
                className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm outline-none transition-colors duration-200 hover:border-white/20 focus:border-violet-400"
              />
            </label>
          ))}
          <label className="space-y-1 md:col-span-2">
            <span className="text-xs text-slate-400">Diagnosis Summary</span>
            <textarea
              value={form.diagnosis_description}
              onChange={(e) => setForm((prev) => ({ ...prev, diagnosis_description: e.target.value }))}
              className="h-24 w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm outline-none transition-colors duration-200 hover:border-white/20 focus:border-violet-400"
              placeholder="Describe diagnosis and treatment..."
            />
          </label>
          <div className="md:col-span-2 flex items-center gap-3">
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? "Submitting..." : "Submit Claim"}
            </Button>
            {submitted ? <span className="text-sm text-emerald-300">Submitted: {submitted}</span> : null}
            {mutation.isError ? <span className="text-sm text-rose-300">Submit failed. Please retry.</span> : null}
          </div>
        </form>
      </Card>
    </motion.div>
  );
}
