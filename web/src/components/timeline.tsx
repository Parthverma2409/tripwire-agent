"use client";

import { BellRing, ChevronRight, Hash, MapPin } from "lucide-react";
import Link from "next/link";
import { dayKey, dayLabel, displayRef, time } from "@/lib/format";
import type { CalEvent } from "@/lib/types";
import { TypeIcon, typeLabel } from "./booking-bits";

export function groupByDay(events: CalEvent[]): [string, CalEvent[]][] {
  const groups = new Map<string, CalEvent[]>();
  for (const event of [...events].sort((a, b) => a.start.localeCompare(b.start))) {
    const key = dayKey(event.start);
    groups.set(key, [...(groups.get(key) ?? []), event]);
  }
  return [...groups.entries()];
}

export function bookingHref(id: string) {
  return `/bookings/view/?id=${encodeURIComponent(id)}`;
}

export function EventRow({ event }: { event: CalEvent }) {
  return (
    <li>
      <Link
        href={bookingHref(event.id)}
        className="group -mx-4 flex items-start gap-3 px-4 py-3 transition-colors hover:bg-surface-2/60 focus-visible:bg-surface-2/60 focus-visible:outline-none"
      >
      <div className="tabular w-16 shrink-0 pt-1.5 text-right text-[13px] font-medium text-ink-2">{time(event.start)}</div>
      <TypeIcon type={event.type} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-ink" title={event.title}>
          {event.title}
        </p>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[12.5px] text-ink-3">
          <span>{typeLabel(event.type)}</span>
          {event.location ? (
            <span className="inline-flex min-w-0 items-center gap-1">
              <MapPin className="size-3 shrink-0" aria-hidden />
              <span className="truncate">{event.location}</span>
            </span>
          ) : null}
          {displayRef(event.ref) ? (
            <span className="inline-flex items-center gap-1 font-mono text-[11.5px]">
              <Hash className="size-3" aria-hidden />
              <span className="sr-only">Reference </span>
              {displayRef(event.ref)}
            </span>
          ) : null}
          {event.alarm ? (
            <span className="inline-flex items-center gap-1" title={`Reminder at ${time(event.alarm)}`}>
              <BellRing className="size-3" aria-hidden />
              <span className="sr-only">Reminder at </span>
              {time(event.alarm)}
            </span>
          ) : null}
        </div>
      </div>
      <ChevronRight className="mt-2.5 size-4 shrink-0 text-ink-3 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" aria-hidden />
      </Link>
    </li>
  );
}

export function Timeline({ events, headingLevel = 3 }: { events: CalEvent[]; headingLevel?: 2 | 3 }) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <div className="space-y-5">
      {groupByDay(events).map(([key, items]) => (
        <section key={key} aria-label={dayLabel(key)}>
          <Heading className="sticky top-14 z-10 -mx-1 mb-1 bg-bg/90 px-1 py-1 text-[12px] font-semibold tracking-wide text-ink-3 uppercase backdrop-blur lg:top-0">
            {dayLabel(key)}
          </Heading>
          <ul className="divide-y divide-line rounded-2xl border border-line bg-surface px-4 shadow-card">
            {items.map((event) => (
              <EventRow key={event.id} event={event} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
