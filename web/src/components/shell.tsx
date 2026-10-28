"use client";

import clsx from "clsx";
import { Activity, CalendarDays, Inbox, Monitor, Moon, PlugZap, Settings, Sun, Sunrise, UtensilsCrossed } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useChangeNotifications, useLive, useResource } from "@/lib/live";
import { useToast } from "./toast";
import type { Confirmation, Status } from "@/lib/types";
import { StatusDot } from "./ui";

// `phone: false` keeps an item out of the six-slot bottom bar on small screens.
const NAV = [
  { href: "/", label: "Today", icon: Sunrise },
  { href: "/bookings/", label: "Bookings", icon: CalendarDays },
  { href: "/food/", label: "Food", icon: UtensilsCrossed },
  { href: "/inbox/", label: "Inbox", icon: Inbox },
  { href: "/activity/", label: "Activity", icon: Activity, phone: false },
  { href: "/connections/", label: "Connections", icon: PlugZap },
  { href: "/settings/", label: "Settings", icon: Settings },
];

function isActive(path: string, href: string) {
  return href === "/" ? path === "/" : path.startsWith(href.replace(/\/$/, ""));
}

export function Logo() {
  return (
    <span className="flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className="size-8" aria-hidden>
        <rect x="2" y="4" width="28" height="25" rx="7" className="fill-accent" />
        <rect x="2" y="4" width="28" height="8" rx="4" className="fill-[#1f45ad] dark:fill-[#4a72e8]" />
        <path d="M7 20h18" stroke="white" strokeWidth="2.2" strokeLinecap="round" strokeDasharray="0.1 4.4" />
        <circle cx="16" cy="20" r="3" fill="white" />
      </svg>
      <span translate="no" className="text-[17px] font-semibold tracking-tight text-ink">
        Tripwire
      </span>
    </span>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<"system" | "light" | "dark">("system");
  useEffect(() => {
    try {
      const saved = localStorage.getItem("theme");
      if (saved === "light" || saved === "dark") setTheme(saved);
    } catch {
      /* storage unavailable */
    }
  }, []);
  const apply = (next: typeof theme) => {
    setTheme(next);
    try {
      if (next === "system") {
        localStorage.removeItem("theme");
        document.documentElement.removeAttribute("data-theme");
      } else {
        localStorage.setItem("theme", next);
        document.documentElement.setAttribute("data-theme", next);
      }
    } catch {
      document.documentElement.setAttribute("data-theme", next);
    }
  };
  const options = [
    { value: "light", icon: Sun, label: "Light theme" },
    { value: "system", icon: Monitor, label: "Match system theme" },
    { value: "dark", icon: Moon, label: "Dark theme" },
  ] as const;
  return (
    <div role="group" aria-label="Theme" className="flex rounded-lg border border-line bg-surface-2 p-0.5">
      {options.map(({ value, icon: Icon, label }) => (
        <button
          key={value}
          aria-pressed={theme === value}
          aria-label={label}
          title={label}
          onClick={() => apply(value)}
          className={clsx(
            "grid size-7 place-items-center rounded-md transition-colors",
            theme === value ? "bg-surface text-ink shadow-card" : "text-ink-3 hover:text-ink",
          )}
        >
          <Icon className="size-3.5" aria-hidden />
        </button>
      ))}
    </div>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname() || "/";
  const { connected } = useLive();
  useChangeNotifications(useToast());
  const { data: status } = useResource<Status>("/api/status", ["status", "scan"]);
  const { data: pending } = useResource<{ confirmations: Confirmation[] }>("/api/confirmations", ["confirmations"]);
  const waiting = pending?.confirmations.length ?? 0;

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[240px_1fr]">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2"
      >
        Skip to content
      </a>

      <aside aria-label="Sidebar" className="sticky top-0 hidden h-dvh flex-col border-r border-line bg-surface px-3 py-5 lg:flex">
        <div className="px-2">
          <Logo />
        </div>
        <nav aria-label="Main navigation" className="mt-7 flex flex-col gap-0.5">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = isActive(path, href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium transition-colors",
                  active ? "bg-accent-soft text-accent-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                )}
              >
                <Icon className="size-[18px]" aria-hidden />
                <span className="flex-1">{label}</span>
                {href === "/" && waiting > 0 ? (
                  <span
                    className="tabular grid min-w-5 place-items-center rounded-full bg-warn px-1.5 text-[11px] font-semibold text-white dark:text-[#1a1203]"
                    aria-label={`${waiting} waiting for your OK`}
                  >
                    {waiting}
                  </span>
                ) : null}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto space-y-3 px-2">
          <div className="flex items-center gap-2 text-[12px] text-ink-3">
            <StatusDot ok={connected} pulse label={connected ? "Live" : "Reconnecting"} />
            <span>{connected ? "Live" : "Reconnecting…"}</span>
            {status ? (
              <span
                className={clsx(
                  "ml-auto rounded-md px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide",
                  status.mode === "demo" ? "bg-warn-soft text-warn" : "bg-ok-soft text-ok",
                )}
              >
                {status.mode}
              </span>
            ) : null}
          </div>
          <ThemeToggle />
        </div>
      </aside>

      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-surface/90 px-4 py-3 backdrop-blur lg:hidden">
          <Logo />
          <div className="flex items-center gap-2">
            {status ? (
              <span
                className={clsx(
                  "rounded-md px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide",
                  status.mode === "demo" ? "bg-warn-soft text-warn" : "bg-ok-soft text-ok",
                )}
              >
                {status.mode}
              </span>
            ) : null}
            <ThemeToggle />
          </div>
        </header>

        {status?.mode === "demo" ? (
          <section
            aria-label="Demo mode"
            className="border-b border-warn-line bg-warn-soft px-4 py-2 text-center text-[13px] text-warn lg:px-8"
          >
            Demo mode: sample emails and a practice calendar. Nothing here touches your real accounts.{" "}
            <Link href="/connections/" className="font-semibold underline underline-offset-2">
              Connect your accounts
            </Link>
          </section>
        ) : null}

        <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 pt-6 pb-28 lg:px-8 lg:pt-8 lg:pb-12">
          {children}
        </main>
      </div>

      <nav
        aria-label="Tab bar"
        className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-6 border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden"
      >
        {NAV.filter((item) => item.phone !== false).map(({ href, label, icon: Icon }) => {
          const active = isActive(path, href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={clsx(
                "relative flex flex-col items-center gap-0.5 py-2 text-[10.5px] font-medium",
                active ? "text-accent-ink" : "text-ink-3",
              )}
            >
              <Icon className="size-5" aria-hidden />
              <span className="truncate">{label}</span>
              {href === "/" && waiting > 0 ? (
                <span className="absolute top-1 right-[calc(50%-18px)] size-2 rounded-full bg-warn" aria-hidden />
              ) : null}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
