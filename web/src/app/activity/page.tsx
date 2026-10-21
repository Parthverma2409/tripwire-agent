"use client";

import clsx from "clsx";
import { Bot, ChevronDown, Clock, ExternalLink, Mail, MessageSquare, Send, Upload, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { OutcomeBadge } from "@/components/booking-bits";
import { EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/ui";
import { get } from "@/lib/api";
import { dateTime, humanize, relative } from "@/lib/format";
import { useResource } from "@/lib/live";
import type { ActivityRow, Score, Trajectory } from "@/lib/types";

const CHANNEL: Record<string, { icon: typeof Mail; label: string }> = {
  app: { icon: UserRound, label: "You, in the app" },
  telegram: { icon: Send, label: "You, on Telegram" },
  gmail: { icon: Mail, label: "Gmail" },
  imap: { icon: Mail, label: "Email (IMAP)" },
  demo: { icon: Mail, label: "Demo inbox" },
  upload: { icon: Upload, label: "Uploaded email" },
  system: { icon: Clock, label: "Tripwire" },
};

const AXES: Record<string, string> = {
  task_success: "Task success",
  tool_use_correctness: "Tool use",
  safety_compliance: "Safety (asked before changing)",
  reasoning_faithfulness: "Reasoning matches actions",
  unnecessary_action: "No unnecessary changes",
};

function ScoreBar({ name, score }: { name: string; score: Score }) {
  if (!score.applicable || score.value === null) {
    return (
      <div className="flex items-center justify-between gap-3 text-[13px]">
        <span className="text-ink-2">{AXES[name] ?? name}</span>
        <span className="text-ink-3">Not measured</span>
      </div>
    );
  }
  const max = score.scale === "1-5" ? 5 : 1;
  const ratio = score.scale === "1-5" ? (score.value - 1) / 4 : score.value;
  const good = ratio >= 0.75;
  return (
    <div title={score.rationale}>
      <div className="flex items-center justify-between gap-3 text-[13px]">
        <span className="text-ink-2">{AXES[name] ?? name}</span>
        <span className={clsx("tabular font-semibold", good ? "text-ok" : "text-warn")}>
          {score.scale === "binary" ? (score.value ? "Pass" : "Fail") : `${score.value}/${max}`}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface" aria-hidden>
        <div className={clsx("h-full rounded-full", good ? "bg-ok" : "bg-warn")} style={{ width: `${Math.max(6, ratio * 100)}%` }} />
      </div>
    </div>
  );
}

function Details({ id }: { id: string }) {
  const [trajectory, setTrajectory] = useState<Trajectory | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    get<Trajectory>(`/api/trajectories/${encodeURIComponent(id)}`).then(
      (t) => live && setTrajectory(t),
      (e) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
  }, [id]);
  if (error) return <p className="text-[13px] text-danger">{error}</p>;
  if (!trajectory) return <Skeleton className="h-24" />;
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div>
        <h4 className="mb-2 text-[11px] font-semibold tracking-wide text-ink-3 uppercase">How it decided</h4>
        <ol className="space-y-1.5 text-[13px] text-ink-2">
          {(trajectory.reasoning ?? []).map((step) => (
            <li key={step.step} className="flex gap-2">
              <span className="tabular w-4 shrink-0 text-ink-3">{step.step}.</span>
              <span className="min-w-0 break-words">{step.text}</span>
            </li>
          ))}
        </ol>
        {trajectory.tool_calls.length ? (
          <>
            <h4 className="mt-4 mb-2 text-[11px] font-semibold tracking-wide text-ink-3 uppercase">Steps taken</h4>
            <ul className="flex flex-wrap gap-1.5">
              {trajectory.tool_calls.map((call, i) => (
                <li key={i} className="rounded-md bg-surface px-2 py-0.5 font-mono text-[11.5px] text-ink-2">
                  {call.name}
                </li>
              ))}
            </ul>
          </>
        ) : null}
        <p className="mt-4 text-[12px] text-ink-3">Read by {trajectory.agent.model}</p>
      </div>
      <div>
        <h4 className="mb-2 text-[11px] font-semibold tracking-wide text-ink-3 uppercase">Behaviour checks</h4>
        <div className="space-y-2.5">
          {Object.entries(trajectory.scores ?? {}).map(([name, score]) => (
            <ScoreBar key={name} name={name} score={score} />
          ))}
        </div>
      </div>
    </div>
  );
}

function Item({ row }: { row: ActivityRow }) {
  const [open, setOpen] = useState(false);
  const channel = CHANNEL[row.channel] ?? { icon: MessageSquare, label: row.channel };
  const Icon = channel.icon;
  return (
    <li className="py-3 [contain-intrinsic-size:auto_96px] [content-visibility:auto]">
      <div className="flex items-start gap-3">
        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-surface-2 text-ink-3" aria-hidden>
          <Icon className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-ink-3">
            <span className="font-medium text-ink-2">{channel.label}</span>
            <span aria-hidden>·</span>
            <time dateTime={row.ts} title={dateTime(row.ts)}>
              {relative(row.ts)}
            </time>
            <OutcomeBadge status={row.status} />
          </div>
          {row.input && row.kind !== "decision" ? (
            <p className="mt-1 truncate text-[13px] text-ink-3" title={row.input}>
              “{row.input}”
            </p>
          ) : null}
          <p className="mt-1 text-sm text-ink text-pretty">{humanize(row.message)}</p>
        </div>
        {row.trajectory_id ? (
          <button
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-label={open ? "Hide reasoning" : "Show reasoning and checks"}
            className="grid size-8 shrink-0 place-items-center rounded-lg text-ink-3 hover:bg-surface-2 hover:text-ink"
          >
            <ChevronDown className={clsx("size-4 transition-transform", open && "rotate-180")} aria-hidden />
          </button>
        ) : null}
      </div>
      {open && row.trajectory_id ? (
        <div className="animate-rise mt-3 ml-11 rounded-xl bg-surface-2 p-4">
          <Details id={row.trajectory_id} />
        </div>
      ) : null}
    </li>
  );
}

export default function ActivityPage() {
  const { data, error, loading } = useResource<{ activity: ActivityRow[] }>("/api/activity?limit=200", ["activity"]);
  return (
    <div>
      <PageHeader
        title="Activity"
        description="Everything Tripwire did and said, with its reasoning and an automatic check of each decision."
        action={
          <a
            href="/research/"
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-10 items-center gap-2 rounded-xl border border-line-strong bg-surface px-4 text-sm font-medium text-ink shadow-card hover:bg-surface-2"
          >
            Research dashboard <ExternalLink className="size-3.5" aria-hidden />
          </a>
        }
      />
      {error ? (
        <ErrorNote>{error}</ErrorNote>
      ) : loading ? (
        <div className="space-y-3">
          <Skeleton className="h-16" />
          <Skeleton className="h-16" />
        </div>
      ) : data?.activity.length ? (
        <ul className="divide-y divide-line rounded-2xl border border-line bg-surface px-4 shadow-card">
          {data.activity.map((row) => (
            <Item key={row.id} row={row} />
          ))}
        </ul>
      ) : (
        <EmptyState icon={<Bot className="size-5" />} title="No activity yet">
          When Tripwire reads an email or you give it a command, it shows up here with the reasoning behind it.
        </EmptyState>
      )}
    </div>
  );
}
