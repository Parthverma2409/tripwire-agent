"use client";

import { CalendarCheck2, CalendarPlus, Inbox, RefreshCw, ShieldCheck, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { ApprovalCard } from "@/components/approval-card";
import { CommandBar } from "@/components/command-bar";
import { Timeline } from "@/components/timeline";
import { useToast } from "@/components/toast";
import { Button, Card, EmptyState, ErrorNote, SectionHeader, Skeleton } from "@/components/ui";
import { post } from "@/lib/api";
import { parseLocal, relative } from "@/lib/format";
import { useNow, useResource } from "@/lib/live";
import type { CalEvent, Confirmation, MailRow, Status } from "@/lib/types";

const HORIZON_DAYS = 14;

function greeting(now: Date) {
  const h = now.getHours();
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}

export default function TodayPage() {
  const toast = useToast();
  const clock = useNow();
  const now = clock ?? new Date(0);
  const events = useResource<{ events: CalEvent[] }>("/api/events", ["calendar"]);
  const questions = useResource<{ confirmations: Confirmation[] }>("/api/confirmations", ["confirmations", "calendar"]);
  const status = useResource<Status>("/api/status", ["scan", "status"]);
  const quarantine = useResource<{ mail: MailRow[] }>("/api/inbox?quarantine=true", ["inbox"]);
  const [scanning, setScanning] = useState(false);

  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const horizon = new Date(start.getTime() + HORIZON_DAYS * 86_400_000);
  const upcoming = (events.data?.events ?? []).filter((e) => {
    const t = parseLocal(e.end ?? e.start);
    return t >= now && parseLocal(e.start) < horizon;
  });
  const waiting = questions.data?.confirmations ?? [];
  const review = quarantine.data?.mail.length ?? 0;
  const ready = status.data?.ready ?? true;

  async function scan() {
    setScanning(true);
    try {
      const result = await post<{ handled: number }>("/api/scan");
      toast("ok", result.handled ? `Read ${result.handled} new email${result.handled === 1 ? "" : "s"}.` : "No new booking emails.");
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setScanning(false);
    }
  }

  return (
    <div className="space-y-8">
      <header>
        <p className="min-h-5 text-[13px] font-medium text-ink-3">
          {clock ? clock.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }) : ""}
        </p>
        <h1 className="mt-0.5 text-2xl font-semibold tracking-tight text-ink">{clock ? greeting(clock) : "Today"}</h1>
      </header>

      {!ready && status.data ? (
        <ErrorNote>
          Tripwire isn’t connected to a calendar yet.{" "}
          <Link href="/connections/" className="font-semibold underline underline-offset-2">
            Set up your connections
          </Link>{" "}
          or switch to demo mode in Settings.
        </ErrorNote>
      ) : null}

      <CommandBar />

      {waiting.length > 0 ? (
        <section aria-labelledby="needs-ok">
          <SectionHeader
            id="needs-ok"
            title={`Needs your OK (${waiting.length})`}
            description="Tripwire never deletes or moves an event until you say so."
          />
          <div className="grid items-start gap-4 xl:grid-cols-2">
            {waiting.map((item) => (
              <ApprovalCard key={item.request_id} item={item} onDone={() => questions.reload()} />
            ))}
          </div>
        </section>
      ) : null}

      <div className="grid gap-8 lg:grid-cols-[1fr_300px]">
        <section aria-labelledby="coming-up" className="min-w-0">
          <SectionHeader id="coming-up" title="Coming up" description={`Your next ${HORIZON_DAYS} days`} />
          {events.error ? (
            <ErrorNote>{events.error}</ErrorNote>
          ) : events.loading || !clock ? (
            <div className="space-y-3">
              <Skeleton className="h-16" />
              <Skeleton className="h-16" />
              <Skeleton className="h-16" />
            </div>
          ) : upcoming.length ? (
            <Timeline events={upcoming} />
          ) : (
            <EmptyState icon={<CalendarPlus className="size-5" />} title="Nothing booked for the next two weeks">
              New bookings appear here as their confirmation emails arrive. You can also add one above, like “book a
              train to Bangalore on Friday at 6 pm”.
            </EmptyState>
          )}
        </section>

        <aside className="space-y-4">
          <Card className="p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-ink">
              <Inbox className="size-4 text-ink-3" aria-hidden /> Inbox
            </div>
            <p className="mt-2 text-[13px] text-ink-2">
              {status.data?.last_scan
                ? `Checked ${relative(status.data.last_scan.at)}.`
                : "Not checked yet."}{" "}
              {status.data ? `Checks every ${status.data.settings.poll_minutes} min.` : ""}
            </p>
            <Button className="mt-3 w-full" size="sm" busy={scanning} onClick={scan} icon={<RefreshCw className="size-3.5" aria-hidden />}>
              Check inbox now
            </Button>
          </Card>

          {review > 0 ? (
            <Link href="/inbox/?tab=review" className="block">
              <Card className="flex items-start gap-3 border-warn-line p-4 transition-colors hover:bg-warn-soft/50">
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
                <div>
                  <p className="text-sm font-semibold text-ink">
                    {review} email{review === 1 ? "" : "s"} to review
                  </p>
                  <p className="mt-0.5 text-[13px] text-ink-2">
                    Looked like bookings, but Tripwire couldn’t read the details. Nothing was changed.
                  </p>
                </div>
              </Card>
            </Link>
          ) : null}

          <Card className="p-4">
            <div className="flex items-center gap-2 text-sm font-semibold text-ink">
              <ShieldCheck className="size-4 text-ok" aria-hidden /> How Tripwire keeps you safe
            </div>
            <ul className="mt-2 space-y-1.5 text-[13px] text-ink-2">
              <li>New bookings are added straight away.</li>
              <li>Deleting or moving an event always waits for your OK.</li>
              <li>Emails that fail the sender check are flagged before you decide.</li>
            </ul>
          </Card>

          {upcoming.length ? (
            <Link
              href="/bookings/"
              className="flex items-center gap-2 px-1 text-[13px] font-medium text-accent-ink hover:underline"
            >
              <CalendarCheck2 className="size-4" aria-hidden /> See all bookings
            </Link>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
