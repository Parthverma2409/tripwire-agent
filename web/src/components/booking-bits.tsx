"use client";

import clsx from "clsx";
import {
  BedDouble,
  CalendarClock,
  CarFront,
  CircleCheck,
  CircleHelp,
  Plane,
  ShieldAlert,
  ShieldCheck,
  Stethoscope,
  TrainFront,
  UtensilsCrossed,
} from "lucide-react";
import type { BookingType, Outcome, Trust } from "@/lib/types";
import { TYPE_LABEL } from "@/lib/format";
import { Badge } from "./ui";

const ICONS: Record<BookingType, typeof Plane> = {
  flight: Plane,
  train: TrainFront,
  hotel: BedDouble,
  ride: CarFront,
  restaurant: UtensilsCrossed,
  appointment: Stethoscope,
};

const TINTS: Record<BookingType, string> = {
  flight: "bg-[#e7efff] text-[#2b59d9] dark:bg-[#1b2847] dark:text-[#9db7ff]",
  train: "bg-[#e9f6ee] text-[#17794a] dark:bg-[#12291f] dark:text-[#62d29b]",
  hotel: "bg-[#f3ecfd] text-[#7044c4] dark:bg-[#251a3d] dark:text-[#bfa2f5]",
  ride: "bg-[#fff1e3] text-[#b25a00] dark:bg-[#2e1f10] dark:text-[#f2b066]",
  restaurant: "bg-[#fdeaf0] text-[#b8325f] dark:bg-[#33141f] dark:text-[#f28cae]",
  appointment: "bg-[#e5f5f7] text-[#0f7383] dark:bg-[#0f2a2e] dark:text-[#5fcfdd]",
};

export function TypeIcon({ type, size = "md" }: { type: BookingType | null; size?: "sm" | "md" | "lg" }) {
  const Icon = type ? ICONS[type] : CalendarClock;
  const tint = type ? TINTS[type] : "bg-surface-2 text-ink-3";
  const box = size === "sm" ? "size-7 rounded-lg" : size === "lg" ? "size-11 rounded-xl" : "size-9 rounded-xl";
  const icon = size === "sm" ? "size-3.5" : size === "lg" ? "size-5" : "size-[18px]";
  return (
    <span className={clsx("grid shrink-0 place-items-center", box, tint)} aria-hidden>
      <Icon className={icon} strokeWidth={2} />
    </span>
  );
}

export function typeLabel(type: BookingType | null | undefined): string {
  return type ? TYPE_LABEL[type] ?? type : "Booking";
}

export function TrustBadge({ trust }: { trust: Trust | null | undefined }) {
  if (!trust) return null;
  if (trust.level === "verified")
    return (
      <Badge tone="ok" icon={<ShieldCheck className="size-3.5" aria-hidden />} title={trust.reason}>
        Verified sender
      </Badge>
    );
  if (trust.level === "failed")
    return (
      <Badge tone="danger" icon={<ShieldAlert className="size-3.5" aria-hidden />} title={trust.reason}>
        Failed sender check
      </Badge>
    );
  return (
    <Badge tone="neutral" icon={<CircleHelp className="size-3.5" aria-hidden />} title={trust.reason}>
      Can’t verify sender
    </Badge>
  );
}

const OUTCOME: Record<Outcome, { label: string; tone: "ok" | "warn" | "danger" | "neutral" | "accent" }> = {
  success: { label: "Done", tone: "ok" },
  awaiting_confirmation: { label: "Needs your OK", tone: "warn" },
  refused: { label: "No change", tone: "neutral" },
  failure: { label: "Needs review", tone: "danger" },
  error: { label: "Error", tone: "danger" },
};

export function OutcomeBadge({ status }: { status: Outcome }) {
  const o = OUTCOME[status] ?? OUTCOME.error;
  return (
    <Badge tone={o.tone} icon={status === "success" ? <CircleCheck className="size-3.5" aria-hidden /> : undefined}>
      {o.label}
    </Badge>
  );
}
