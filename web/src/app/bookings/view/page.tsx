"use client";

import clsx from "clsx";
import {
  ArrowLeft,
  BellRing,
  CalendarDays,
  CalendarPlus,
  CalendarX2,
  CarFront,
  ExternalLink,
  Mail,
  MoveRight,
  Sparkles,
  UtensilsCrossed,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { TrustBadge, TypeIcon, typeLabel } from "@/components/booking-bits";
import { Badge, Card, EmptyState, ErrorNote, Skeleton } from "@/components/ui";
import { dateLong, dateTime, displayRef, parseLocal, relative, senderName, stageLabel, time } from "@/lib/format";
import { useNow, useResource } from "@/lib/live";
import type { BookingHistory, BookingType, EventDetail, TrustLevel } from "@/lib/types";

const ORIGIN: Record<string, string> = {
  gmail: "Added by Gmail from a booking email",
  tripwire: "Added by Tripwire",
  calendar: "Already on your calendar",
};

const CHANNEL: Record<string, string> = {
  app: "in the app",
  telegram: "from Telegram",
  gmail: "from Gmail",
  imap: "from your inbox",
  upload: "from an uploaded email",
  demo: "from the demo inbox",
  swiggy: "from Swiggy",
  system: "automatically",
};

const CHANGE: Record<BookingHistory["type"], { label: string; icon: typeof CalendarPlus; tone: string }> = {
  CREATE: { label: "Added", icon: CalendarPlus, tone: "bg-ok-soft text-ok" },
  UPDATE: { label: "Moved", icon: MoveRight, tone: "bg-accent-soft text-accent-ink" },
  DELETE: { label: "Deleted", icon: CalendarX2, tone: "bg-danger-soft text-danger" },
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[8.5rem_1fr] gap-3 py-2.5 text-sm">
      <dt className="text-ink-3">{label}</dt>
      <dd className="min-w-0 break-words text-ink">{children}</dd>
    </div>
  );
}

function ExternalButton({ href, icon, children, primary = false }: {
  href: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  primary?: boolean;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className={clsx(
        "inline-flex h-9 items-center gap-1.5 rounded-xl px-3.5 text-[13px] font-medium shadow-card transition-colors",
        primary
          ? "bg-accent text-white hover:bg-accent-hover dark:text-[#0b1530]"
          : "border border-line-strong bg-surface text-ink hover:bg-surface-2",
      )}
    >
      {icon}
      {children}
      <ExternalLink className="size-3 opacity-60" aria-hidden />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
}

function span(start: string, end: string | null) {
  if (!end) return dateTime(start);
  const sameDay = start.slice(0, 10) === end.slice(0, 10);
  return sameDay ? `${dateLong(start)}, ${time(start)} – ${time(end)}` : `${dateTime(start)} – ${dateTime(end)}`;
}

export default function BookingDetailPage() {
  const [id, setId] = useState<string | null | undefined>(undefined);
  useEffect(() => setId(new URLSearchParams(window.location.search).get("id")), []);
  const { data, error, loading } = useResource<EventDetail>(id ? `/api/events/${encodeURIComponent(id)}` : null, ["calendar"]);
  const now = useNow();

  const back = (
    <Link href="/bookings/" className="mb-5 inline-flex items-center gap-1.5 text-[13px] font-medium text-ink-2 hover:text-ink">
      <ArrowLeft className="size-4" aria-hidden /> Bookings
    </Link>
  );

  if (id === null) {
    return (
      <div>
        {back}
        <EmptyState icon={<CalendarDays className="size-5" />} title="No booking chosen">
          Open a booking from the Bookings or Today page.
        </EmptyState>
      </div>
    );
  }
  if (error) {
    return (
      <div>
        {back}
        <ErrorNote>{error.includes("No such") ? "This booking isn’t on your calendar any more, and Tripwire has no record of it." : error}</ErrorNote>
      </div>
    );
  }
  if (loading || !data || id === undefined) {
    return (
      <div className="space-y-4">
        {back}
        <Skeleton className="h-20" />
        <Skeleton className="h-40" />
      </div>
    );
  }

  const event = data.event;
  const b = data.booking;
  const type = (event?.type ?? b.booking_type ?? null) as BookingType | null;
  const title = event?.title ?? (b.title as string | undefined) ?? "Booking";
  const start = event?.start ?? (b.date_time as string | undefined) ?? null;
  const end = event?.end ?? (b.end_time as string | undefined) ?? null;
  const ref = displayRef(event?.ref ?? b.booking_ref);
  const origin = data.google.origin ?? (data.history.length ? "tripwire" : undefined);
  const past = start && now ? parseLocal(end ?? start) < now : false;
  const email = data.email;
  const description = (data.google.description ?? "").trim();

  const facts: [string, React.ReactNode][] = [];
  if (ref) facts.push(["Reference", <span key="r" translate="no" className="font-mono text-[13px]">{ref}</span>]);
  if (b.provider) facts.push(["Provider", String(b.provider)]);
  if (b.pickup) facts.push(["Pickup", String(b.pickup)]);
  if (b.dropoff) facts.push(["Drop-off", String(b.dropoff)]);
  if (b.venue) facts.push(["Restaurant", String(b.venue)]);
  if (b.party_size) facts.push(["Party size", `${b.party_size} ${Number(b.party_size) === 1 ? "person" : "people"}`]);
  if (event?.location ?? b.location) facts.push(["Where", String(event?.location ?? b.location)]);
  if (b.destination && b.destination !== (event?.location ?? b.location)) facts.push(["Destination", String(b.destination)]);
  if (event?.alarm) facts.push(["Reminder", <span key="a" className="inline-flex items-center gap-1.5"><BellRing className="size-3.5 text-ink-3" aria-hidden />{dateTime(event.alarm)}</span>]);

  return (
    <div className="max-w-3xl">
      {back}

      <header className="flex items-start gap-4">
        <TypeIcon type={type} size="lg" />
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-semibold tracking-tight text-ink text-balance">{title}</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[13px] text-ink-3">
            <span>{typeLabel(type)}</span>
            {event?.type_guessed ? (
              <Badge tone="neutral" icon={<Sparkles className="size-3.5" aria-hidden />} title="Tripwire didn’t create this event, so its type is read from the title.">
                Type guessed from title
              </Badge>
            ) : null}
            {data.gone ? <Badge tone="danger">Removed from calendar</Badge> : past ? <Badge tone="neutral">Past</Badge> : null}
          </div>
        </div>
      </header>

      {start ? (
        <Card className="mt-6 p-4 sm:p-5">
          <p className="tabular text-lg font-semibold text-ink">{span(start, end)}</p>
          {now ? <p className="mt-0.5 text-[13px] text-ink-3">{relative(start, now)}</p> : null}
          <div className="mt-4 flex flex-wrap gap-2">
            {data.links.uber ? (
              <ExternalButton href={data.links.uber} primary icon={<CarFront className="size-4" aria-hidden />}>
                Book this ride in Uber
              </ExternalButton>
            ) : null}
            {data.links.swiggy ? (
              <ExternalButton href={data.links.swiggy} icon={<UtensilsCrossed className="size-4" aria-hidden />}>
                Swiggy Dineout
              </ExternalButton>
            ) : null}
            {data.links.calendar ? (
              <ExternalButton href={data.links.calendar} icon={<CalendarDays className="size-4" aria-hidden />}>
                Google Calendar
              </ExternalButton>
            ) : null}
            {data.links.email ? (
              <ExternalButton href={data.links.email} icon={<Mail className="size-4" aria-hidden />}>
                Original email
              </ExternalButton>
            ) : null}
          </div>
          {data.links.uber ? (
            <p className="mt-3 text-[12px] text-ink-3">
              {data.links.uber.includes("dropoff")
                ? "Opens Uber with the drop-off filled in. "
                : "Opens Uber from where you are; add the drop-off there. "}
              Uber doesn’t let personal apps book rides directly, so you confirm the ride in Uber.
            </p>
          ) : null}
        </Card>
      ) : null}

      <div className="mt-6 grid gap-6 md:grid-cols-2">
        <section aria-labelledby="details">
          <h2 id="details" className="mb-2 text-[12px] font-semibold tracking-wide text-ink-3 uppercase">
            Details
          </h2>
          <Card className="px-4">
            {facts.length ? (
              <dl className="divide-y divide-line">
                {facts.map(([label, value]) => (
                  <Field key={label} label={label}>
                    {value}
                  </Field>
                ))}
              </dl>
            ) : (
              <p className="py-4 text-[13px] text-ink-3">No extra details. The calendar entry is all there is.</p>
            )}
          </Card>
        </section>

        <section aria-labelledby="source">
          <h2 id="source" className="mb-2 text-[12px] font-semibold tracking-wide text-ink-3 uppercase">
            Where it came from
          </h2>
          <Card className="space-y-3 p-4 text-sm">
            <p className="text-ink">{origin ? ORIGIN[origin] : "On your calendar"}</p>
            {email ? (
              <div className="rounded-xl bg-surface-2 p-3">
                <p className="truncate font-medium text-ink" title={email.subject}>
                  {email.subject || "(no subject)"}
                </p>
                <p className="mt-0.5 truncate text-[13px] text-ink-2">{senderName(email.sender)}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-ink-3">
                  {email.stage ? <span>Read from {stageLabel(email.stage).toLowerCase()}</span> : null}
                  {email.trust ? (
                    <TrustBadge trust={{ level: email.trust as TrustLevel, domain: "", vendor_id: null, vendor_name: null, reason: "" }} />
                  ) : null}
                </div>
              </div>
            ) : origin === "gmail" ? (
              <p className="text-[13px] text-ink-2">
                {data.links.email ? "Use Original email above to see the confirmation it came from." : "Gmail spotted the booking in your inbox."}
              </p>
            ) : origin !== "tripwire" ? (
              <p className="text-[13px] text-ink-3">Tripwire hasn’t changed this event. It watches it so a cancellation or new time can be matched to it.</p>
            ) : null}
          </Card>
        </section>
      </div>

      <section aria-labelledby="history" className="mt-6">
        <h2 id="history" className="mb-2 text-[12px] font-semibold tracking-wide text-ink-3 uppercase">
          History
        </h2>
        {data.history.length ? (
          <Card className="px-4">
            <ol className="divide-y divide-line">
              {data.history.map((h, i) => {
                const c = CHANGE[h.type];
                const Icon = c.icon;
                return (
                  <li key={i} className="flex items-start gap-3 py-3">
                    <span className={clsx("grid size-7 shrink-0 place-items-center rounded-lg", c.tone)} aria-hidden>
                      <Icon className="size-3.5" />
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm text-ink">
                        <span className="font-medium">{c.label}</span> {CHANNEL[h.channel] ?? `via ${h.channel}`}
                      </p>
                      <p className="mt-0.5 text-[13px] text-ink-2 text-pretty">{h.message}</p>
                      <p className="tabular mt-0.5 text-[12px] text-ink-3">{dateTime(h.at)}</p>
                    </div>
                  </li>
                );
              })}
            </ol>
          </Card>
        ) : (
          <p className="text-[13px] text-ink-3">
            {origin === "tripwire"
              ? "Added before Tripwire started keeping a history. Changes from now on will show here."
              : "Tripwire hasn’t added, moved or removed this event."}
          </p>
        )}
      </section>

      {description ? (
        <details className="group mt-6 rounded-2xl border border-line bg-surface shadow-card">
          <summary className="cursor-pointer list-none px-4 py-3 text-[13px] font-medium text-ink-2 hover:text-ink">
            Notes on the calendar entry
          </summary>
          <div className="max-h-80 overflow-auto border-t border-line px-4 py-3 text-[13px] whitespace-pre-wrap text-ink-2">
            {description.replace(/<[^>]+>/g, " ")}
          </div>
        </details>
      ) : null}
    </div>
  );
}
