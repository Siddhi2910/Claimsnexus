import { cn } from "@/lib/utils";

const styles: Record<string, string> = {
  APPROVED: "bg-emerald-500/15 text-emerald-300 border-emerald-400/20",
  APPROVE: "bg-emerald-500/15 text-emerald-300 border-emerald-400/20",
  REJECTED: "bg-rose-500/15 text-rose-300 border-rose-400/20",
  REJECT: "bg-rose-500/15 text-rose-300 border-rose-400/20",
  PENDING_REVIEW: "bg-amber-500/15 text-amber-300 border-amber-400/20",
  REVIEW: "bg-amber-500/15 text-amber-300 border-amber-400/20",
  PENDING: "bg-violet-500/15 text-violet-200 border-violet-400/20",
  RECEIVED: "bg-blue-500/15 text-blue-300 border-blue-400/20",
  PROCESSING: "bg-cyan-500/15 text-cyan-200 border-cyan-400/20"
};

export function StatusBadge({ status }: { status: string }) {
  const label = status.replace("_", " ");
  return (
    <span className={cn("rounded-full border px-2.5 py-1 text-xs font-semibold", styles[status] ?? styles.PENDING)}>
      {label}
    </span>
  );
}
