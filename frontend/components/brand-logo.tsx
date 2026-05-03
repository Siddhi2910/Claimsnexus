import { Activity, Network, ShieldPlus } from "lucide-react";
import { cn } from "@/lib/utils";

export function ClaimsNexusLogo({ compact = false, className }: { compact?: boolean; className?: string }) {
  return (
    <div className={cn("flex items-center gap-3", className)}>
      <div className="relative flex h-11 w-11 items-center justify-center rounded-2xl border border-cyan-300/25 bg-cyan-400/10 shadow-glow">
        <svg viewBox="0 0 48 48" className="h-9 w-9" aria-hidden="true">
          <path
            d="M24 5 38 10v12c0 9.5-5.4 16.4-14 20-8.6-3.6-14-10.5-14-20V10l14-5Z"
            fill="rgba(34,211,238,0.16)"
            stroke="rgb(125, 211, 252)"
            strokeWidth="2"
          />
          <path d="M16 25h5l2-7 4 13 2-6h4" fill="none" stroke="rgb(167,139,250)" strokeLinecap="round" strokeWidth="2.4" />
          <circle cx="15" cy="16" r="2" fill="rgb(45,212,191)" />
          <circle cx="33" cy="16" r="2" fill="rgb(167,139,250)" />
          <circle cx="24" cy="35" r="2" fill="rgb(34,211,238)" />
        </svg>
      </div>
      {!compact ? (
        <div>
          <p className="text-lg font-semibold tracking-tight text-white">ClaimsNexus</p>
          <p className="text-[11px] uppercase tracking-[0.18em] text-cyan-200/75">AI-powered health claims adjudication</p>
        </div>
      ) : null}
    </div>
  );
}

export const trustItems = [
  { title: "Multi-agent adjudication", icon: Network },
  { title: "Explainable risk decisions", icon: Activity },
  { title: "Human-in-the-loop review", icon: ShieldPlus }
];
