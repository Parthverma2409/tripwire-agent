"use client";

import clsx from "clsx";
import { ArrowDown, CalendarX2, MoveRight, ShieldAlert, TimerReset } from "lucide-react";
import { useEffect, useState } from "react";
import { post } from "@/lib/api";
import { countdown, dateTime, senderName, stageLabel } from "@/lib/format";
import type { CalEvent, CommandResult, Confirmation } from "@/lib/types";
import { TrustBadge, TypeIcon } from "./booking-bits";
import { useToast } from "./toast";
import { Badge, Button } from "./ui";

function EventLine({ event, muted = false, strike = false }: { event: CalEvent; muted?: boolean; strike?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <TypeIcon type={event.type} />
      <div className="min-w-0">
        <p
          className={clsx(
            "truncate text-sm font-medium",
            muted ? "text-ink-2" : "text-ink",
            strike && "line-through decoration-danger/60",
          )}
        >
          {event.title}
        </p>
        <p className="tabular text-[13px] text-ink-3">
          {dateTime(event.start)}
          {event.location ? ` · ${event.location}` : ""}
        </p>
      </div>
    </div>
  );
}

/** "Reservation data" mid-sentence is "reservation data"; a vendor name keeps its capital. */
function inSentence(label: string, stage: string) {
  return stage.startsWith("vendor:") ? label : label.charAt(0).toLowerCase() + label.slice(1);
}

export function ApprovalCard({ item, onDone }: { item: Confirmation; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<"yes" | "no" | null>(null);
  const [left, setLeft] = useState(item.seconds_left);
  const total = Math.max(1, (new Date(item.timeout_at).getTime() - new Date(item.created_at).getTime()) / 1000);

  useEffect(() => {
    setLeft(item.seconds_left);
    const timer = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(timer);
  }, [item.seconds_left]);

  const isDelete = item.action === "DELETE";
  const trust = item.meta.trust;
  const failed = trust?.level === "failed";
  const queued = item.status === "queued";
  const who = trust?.vendor_name || senderName(item.meta.sender);

  const apply = (
    <Button
      variant={failed ? "secondary" : isDelete ? "danger" : "primary"}
      busy={busy === "yes"}
      disabled={busy !== null}
      onClick={() => decide(true)}
    >
      {isDelete ? "Delete event" : "Move event"}
    </Button>
  );
  const keep = (
    <Button variant={failed ? "primary" : "secondary"} busy={busy === "no"} disabled={busy !== null} onClick={() => decide(false)}>
      {isDelete ? "Keep it" : "Keep current time"}
    </Button>
  );

  async function decide(approve: boolean) {
    setBusy(approve ? "yes" : "no");
    try {
      const result = await post<CommandResult>(`/api/confirmations/${encodeURIComponent(item.request_id)}`, { approve });
      toast(result.status === "success" ? "ok" : "info", result.message);
      onDone();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <article
      aria-labelledby={`q-${item.request_id}`}
      className={clsx(
        "animate-rise overflow-hidden rounded-2xl border bg-surface shadow-card",
        failed ? "border-danger/50" : "border-warn-line",
      )}
    >
      {failed ? (
        <div className="flex items-start gap-2.5 bg-danger-soft px-4 py-3 text-[13px] text-danger">
          <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          <p className="text-pretty">
            <strong className="font-semibold">This email failed the sender check.</strong> It may not really be from{" "}
            {who}. Only approve if you were expecting this change.
          </p>
        </div>
      ) : null}

      <div className="p-4 sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <span
              className={clsx(
                "grid size-8 place-items-center rounded-lg",
                isDelete ? "bg-danger-soft text-danger" : "bg-accent-soft text-accent-ink",
              )}
              aria-hidden
            >
              {isDelete ? <CalendarX2 className="size-4" /> : <MoveRight className="size-4" />}
            </span>
            <h3 id={`q-${item.request_id}`} className="text-[15px] font-semibold text-ink">
              {isDelete ? "Delete this event?" : "Move this event?"}
            </h3>
          </div>
          {queued ? (
            <Badge tone="neutral" icon={<TimerReset className="size-3.5" aria-hidden />}>
              Waiting for your active hours
            </Badge>
          ) : (
            <span className="tabular text-[12px] text-ink-3" aria-label={`Expires in ${countdown(left)}`}>
              Expires in <span className="font-semibold text-ink-2">{countdown(left)}</span>
            </span>
          )}
        </div>

        <div className="mt-4 space-y-2 rounded-xl bg-surface-2 p-3">
          {item.before ? <EventLine event={item.before} strike={isDelete} muted={!isDelete} /> : <p className="text-sm text-ink-2">{item.prompt}</p>}
          {!isDelete && item.after ? (
            <>
              <div className="flex items-center gap-2 pl-3 text-[12px] font-medium text-accent-ink">
                <ArrowDown className="size-3.5" aria-hidden /> New time
              </div>
              <EventLine event={item.after} />
            </>
          ) : null}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2 text-[13px] text-ink-3">
          <span>
            {item.source === "email" ? (
              <>
                From an email by <span className="font-medium text-ink-2">{who}</span>
                {item.meta.stage ? ` · read from ${inSentence(stageLabel(item.meta.stage, trust?.vendor_name), item.meta.stage)}` : ""}
              </>
            ) : (
              "From your request"
            )}
          </span>
          {item.source === "email" ? <TrustBadge trust={trust} /> : null}
        </div>

        {!queued ? (
          <div
            className="mt-4 h-1 overflow-hidden rounded-full bg-surface-2"
            role="progressbar"
            aria-label="Time left to answer"
            aria-valuemin={0}
            aria-valuemax={Math.round(total)}
            aria-valuenow={left}
          >
            <div
              className={clsx("h-full rounded-full transition-[width] duration-1000 ease-linear", left < 120 ? "bg-danger" : "bg-warn")}
              style={{ width: `${Math.min(100, (left / total) * 100)}%` }}
            />
          </div>
        ) : null}

        <div className="mt-4 flex flex-wrap gap-2">
          {/* For a failed sender check the safe answer comes first -- in the DOM,
              not just visually, so keyboard focus order matches what is seen. */}
          {failed ? (
            <>
              {keep}
              {apply}
            </>
          ) : (
            <>
              {apply}
              {keep}
            </>
          )}
        </div>
        <p className="mt-3 text-[12px] text-ink-3">If you don’t answer in time, nothing changes.</p>
      </div>
    </article>
  );
}
