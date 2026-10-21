"use client";

import clsx from "clsx";
import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "warn";
type Size = "sm" | "md";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-white hover:bg-accent-hover shadow-card dark:text-[#0b1530]",
  secondary: "bg-surface text-ink border border-line-strong hover:bg-surface-2 shadow-card",
  ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
  danger: "bg-danger text-white hover:bg-danger-hover shadow-card dark:text-[#2a0b09]",
  warn: "bg-warn text-white hover:opacity-90 shadow-card dark:text-[#1a1203]",
};

const SIZES: Record<Size, string> = {
  sm: "h-8 px-3 text-[13px] gap-1.5 rounded-lg",
  md: "h-10 px-4 text-sm gap-2 rounded-xl",
};

export function Button({
  variant = "secondary",
  size = "md",
  busy = false,
  icon,
  className,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size; busy?: boolean; icon?: ReactNode }) {
  return (
    <button
      {...rest}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={clsx(
        "inline-flex shrink-0 items-center justify-center font-medium whitespace-nowrap transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-55",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
    >
      {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

export function Card({ className, children, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div {...rest} className={clsx("rounded-2xl border border-line bg-surface shadow-card", className)}>
      {children}
    </div>
  );
}

type Tone = "neutral" | "accent" | "ok" | "warn" | "danger";

const TONES: Record<Tone, string> = {
  neutral: "bg-surface-2 text-ink-2 border-line",
  accent: "bg-accent-soft text-accent-ink border-transparent",
  ok: "bg-ok-soft text-ok border-transparent",
  warn: "bg-warn-soft text-warn border-warn-line",
  danger: "bg-danger-soft text-danger border-transparent",
};

export function Badge({ tone = "neutral", icon, children, className, title }: {
  tone?: Tone;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[12px] leading-5 font-medium whitespace-nowrap",
        TONES[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}

export function SectionHeader({ title, description, action, id }: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  id?: string;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h2 id={id} className="text-[15px] font-semibold text-ink">
          {title}
        </h2>
        {description ? <p className="mt-0.5 text-[13px] text-ink-3">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function PageHeader({ title, description, action }: { title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-ink text-balance">{title}</h1>
        {description ? <p className="mt-1 max-w-2xl text-sm text-ink-2 text-pretty">{description}</p> : null}
      </div>
      {action}
    </header>
  );
}

export function EmptyState({ icon, title, children, action }: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-line-strong px-6 py-10 text-center">
      <div className="mb-3 grid size-10 place-items-center rounded-full bg-surface-2 text-ink-3" aria-hidden>
        {icon}
      </div>
      <p className="text-sm font-medium text-ink">{title}</p>
      {children ? <p className="mt-1 max-w-sm text-[13px] text-ink-3 text-pretty">{children}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded-lg bg-surface-2", className)} aria-hidden />;
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <div role="alert" className="rounded-xl border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">
      {children}
    </div>
  );
}

export function StatusDot({ ok, pulse = false, label }: { ok: boolean | null; pulse?: boolean; label?: string }) {
  return (
    <span className="relative inline-flex size-2.5" role={label ? "img" : undefined} aria-label={label}>
      {pulse && ok ? <span className="absolute inset-0 animate-ping rounded-full bg-ok opacity-50" /> : null}
      <span
        className={clsx(
          "relative inline-flex size-2.5 rounded-full",
          ok === null ? "bg-ink-3" : ok ? "bg-ok" : "bg-warn",
        )}
      />
    </span>
  );
}
