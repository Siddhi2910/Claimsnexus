"use client";

import Link from "next/link";
import { LayoutGrid, FilePlus2, ListFilter, BarChart3, LogOut } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { ClaimsNexusLogo } from "@/components/brand-logo";

const nav = [
  { href: "/", label: "Dashboard", icon: LayoutGrid },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/claims/submit", label: "Submit Claim", icon: FilePlus2 },
  { href: "/review-queue", label: "Review Queue", icon: ListFilter }
];

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [checkingAuth, setCheckingAuth] = useState(true);
  const isLogin = pathname === "/login";

  const hasToken = useMemo(() => {
    if (typeof window === "undefined") return false;
    return Boolean(localStorage.getItem("claimsnexus_token"));
  }, [pathname]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const token = localStorage.getItem("claimsnexus_token");
    if (!token && !isLogin) {
      router.replace("/login");
    } else if (token && isLogin) {
      router.replace("/");
    }
    setCheckingAuth(false);
  }, [isLogin, router, pathname]);

  if (checkingAuth) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-slate-300">Checking session...</p>
      </div>
    );
  }

  if (isLogin) {
    return <div className="mx-auto flex min-h-screen w-full max-w-md items-center px-4">{children}</div>;
  }

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-[1280px] gap-5 px-4 py-6 md:px-6 md:py-8">
      <aside className="glass hidden w-72 rounded-2xl p-4 md:block">
        <div className="mb-6 flex items-center justify-between">
          <ClaimsNexusLogo />
          {hasToken ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                localStorage.removeItem("claimsnexus_token");
                router.replace("/login");
              }}
            >
              <LogOut className="h-4 w-4" />
            </Button>
          ) : null}
        </div>
        <nav className="space-y-1.5">
          {nav.map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href));
            return (
              <Link
                key={item.href}
                href={item.href as any}
                className={cn(
                  "flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition-colors duration-200",
                  active ? "bg-violet-500/20 text-white" : "text-slate-300 hover:bg-white/[0.07]"
                )}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <p className="mt-6 border-t border-white/10 pt-4 text-xs leading-relaxed text-slate-500">
          Open any claim from <span className="text-slate-400">Dashboard</span> (click a row) to watch fraud, medical &amp; policy agents and the arbiter decision.
        </p>
      </aside>
      <main className="flex-1 pb-4">{children}</main>
    </div>
  );
}
