"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import { Card, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ClaimsNexusLogo, trustItems } from "@/components/brand-logo";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("admin@claimsnexus.ai");
  const [password, setPassword] = useState("password123");
  const [error, setError] = useState<string | null>(null);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (!email.trim() || !password.trim()) {
      setError("Email and password are required.");
      return;
    }

    const token = `cnx_${Date.now()}`;
    localStorage.setItem("claimsnexus_token", token);
    router.replace("/");
  }

  return (
    <motion.div className="w-full" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
      <Card className="w-full">
        <ClaimsNexusLogo />
        <div className="mt-6 rounded-xl border border-cyan-300/15 bg-cyan-400/10 p-4">
          <CardTitle>Autonomous Health Claims Intelligence</CardTitle>
          <p className="mt-2 text-sm leading-6 text-slate-300">
            Fraud, medical, policy, and arbiter agents route claims with explainable risk scoring.
          </p>
          <div className="mt-4 grid gap-2">
            {trustItems.map((item) => {
              const Icon = item.icon;
              return (
                <div key={item.title} className="flex items-center gap-2 text-xs text-slate-300">
                  <Icon className="h-4 w-4 text-cyan-200" />
                  {item.title}
                </div>
              );
            })}
          </div>
        </div>
        <CardTitle className="mt-6">Login</CardTitle>
        <p className="mt-1 text-sm text-slate-400">Secure access to the ClaimsNexus operations dashboard</p>
        <form className="mt-4 space-y-3" onSubmit={onSubmit}>
          <label className="block space-y-1">
            <span className="text-xs text-slate-400">Email</span>
            <input
              className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm outline-none transition-colors duration-200 hover:border-white/20 focus:border-violet-400"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
            />
          </label>
          <label className="block space-y-1">
            <span className="text-xs text-slate-400">Password</span>
            <input
              type="password"
              className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm outline-none transition-colors duration-200 hover:border-white/20 focus:border-violet-400"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="********"
            />
          </label>
          {error ? <p className="text-xs text-rose-300">{error}</p> : null}
          <Button type="submit" className="w-full">
            Sign In
          </Button>
        </form>
      </Card>
    </motion.div>
  );
}
