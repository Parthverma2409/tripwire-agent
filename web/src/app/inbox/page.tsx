"use client";

import clsx from "clsx";
import { ChevronDown, FileUp, Inbox, MailCheck, RefreshCw, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { OutcomeBadge, TrustBadge, TypeIcon, typeLabel } from "@/components/booking-bits";
import { useToast } from "@/components/toast";
import { Badge, Button, EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/ui";
import { post, upload } from "@/lib/api";
import { dateTime, displayRef, humanize, relative, senderName, stageLabel } from "@/lib/format";
import { useResource, useUrlState } from "@/lib/live";
import type { CommandResult, MailRow, Status } from "@/lib/types";

function Field({ label, value, mono = false }: { label: string; value: string | null | undefined; mono?: boolean }) {
  if (!value) return null;
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-semibold tracking-wide text-ink-3 uppercase">{label}</dt>
      <dd className={clsx("mt-0.5 truncate text-[13px] text-ink", mono && "font-mono")} title={value}>
        {value}
      </dd>
    </div>
  );
}

function MailItem({ row, onDismiss }: { row: MailRow; onDismiss?: (id: number) => void }) {
  const [open, setOpen] = useState(false);
  const b = row.booking;
  return (
    <li className="py-3 [contain-intrinsic-size:auto_96px] [content-visibility:auto]">
      <div className="flex items-start gap-3">
        <TypeIcon type={b?.type ?? null} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <p className="min-w-0 truncate text-sm font-medium text-ink" title={row.subject ?? ""}>
              {row.subject || "(no subject)"}
            </p>
          </div>
          <p className="mt-0.5 truncate text-[12.5px] text-ink-3">
            {senderName(row.sender)} · {relative(row.processed_at)}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <OutcomeBadge status={row.status} />
            {row.stage ? (
              <Badge tone="accent" title="How Tripwire read this email">
                {stageLabel(row.stage, row.trust?.vendor_name)}
              </Badge>
            ) : null}
            <TrustBadge trust={row.trust} />
          </div>
          {row.message ? <p className="mt-2 text-[13px] text-ink-2 text-pretty">{humanize(row.message)}</p> : null}
        </div>
        <button
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={open ? "Hide details" : "Show what was read"}
          className="grid size-8 shrink-0 place-items-center rounded-lg text-ink-3 hover:bg-surface-2 hover:text-ink"
        >
          <ChevronDown className={clsx("size-4 transition-transform", open && "rotate-180")} aria-hidden />
        </button>
      </div>
      {open ? (
        <div className="animate-rise mt-3 ml-12 rounded-xl bg-surface-2 p-3">
          {b ? (
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <Field label="Booking" value={b.title} />
              <Field label="Type" value={b.type ? typeLabel(b.type) : null} />
              <Field label="Status" value={b.status} />
              <Field label="Starts" value={b.start ? dateTime(b.start) : null} />
              <Field label="Ends" value={b.end ? dateTime(b.end) : null} />
              <Field label="Reference" value={displayRef(b.ref)} mono />
              <Field label="Where" value={b.location} />
              <Field label="Why not used" value={b.reason ? humanize(`(${b.reason})`).slice(1, -1) : null} />
            </dl>
          ) : (
            <p className="text-[13px] text-ink-3">Nothing was extracted from this email.</p>
          )}
          <p className="mt-3 text-[12px] text-ink-3">
            {row.trust?.reason ? `Sender check: ${row.trust.reason}. ` : ""}Arrived via {row.channel}.
          </p>
          {onDismiss ? (
            <Button className="mt-3" size="sm" onClick={() => onDismiss(row.id)}>
              Mark as reviewed
            </Button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

export default function InboxPage() {
  const toast = useToast();
  const [tab, setTab] = useUrlState("tab", "all", ["all", "review"] as const);
  const all = useResource<{ mail: MailRow[] }>("/api/inbox", ["inbox"]);
  const review = useResource<{ mail: MailRow[] }>("/api/inbox?quarantine=true", ["inbox"]);
  const status = useResource<Status>("/api/status", ["status"]);
  const query = status.data?.mail_channel === "gmail" ? status.data.settings.gmail_query : "";
  const [scanning, setScanning] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const current = tab === "all" ? all : review;
  const reviewCount = review.data?.mail.length ?? 0;

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

  async function sendFiles(files: FileList | null) {
    if (!files?.length) return;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        if (!file.name.toLowerCase().endsWith(".eml")) {
          toast("error", `${file.name} isn’t an .eml file. In Gmail, open the email, then ⋮ → Download message.`);
          continue;
        }
        const { results } = await upload<{ results: CommandResult[] }>("/api/emails/upload", file);
        toast(results[0]?.status === "error" ? "error" : "ok", results.map((r) => r.message).join(" "));
      }
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function dismiss(id: number) {
    try {
      await post(`/api/inbox/${id}/dismiss`);
      review.reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div>
      <PageHeader
        title="Inbox"
        description="Every booking email Tripwire has read, what it understood, and how sure it is of the sender."
        action={
          <Button variant="primary" busy={scanning} onClick={scan} icon={<RefreshCw className="size-4" aria-hidden />}>
            Check inbox now
          </Button>
        }
      />

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          sendFiles(e.dataTransfer.files);
        }}
        className={clsx(
          "mb-6 flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed px-6 py-6 text-center transition-colors sm:flex-row sm:text-left",
          dragging ? "border-accent bg-accent-soft" : "border-line-strong bg-surface",
        )}
      >
        <span className="grid size-10 shrink-0 place-items-center rounded-full bg-surface-2 text-ink-3" aria-hidden>
          <FileUp className="size-5" />
        </span>
        <div className="flex-1">
          <p className="text-sm font-medium text-ink">Test a booking email</p>
          <p className="text-[13px] text-ink-3">
            Drop an .eml file here to see what Tripwire reads from it. In Gmail: open the email, ⋮ → Download message.
          </p>
        </div>
        <input
          ref={fileInput}
          type="file"
          accept=".eml,message/rfc822"
          multiple
          className="sr-only"
          id="eml"
          aria-label="Choose .eml files to test"
          onChange={(e) => sendFiles(e.target.files)}
        />
        <Button busy={uploading} onClick={() => fileInput.current?.click()} icon={<Upload className="size-4" aria-hidden />}>
          Choose file
        </Button>
      </div>

      <div role="tablist" aria-label="Inbox views" className="mb-4 flex gap-1 border-b border-line">
        {(
          [
            ["all", "All mail", null],
            ["review", "To review", reviewCount],
          ] as const
        ).map(([value, label, count]) => (
          <button
            key={value}
            role="tab"
            aria-selected={tab === value}
            onClick={() => setTab(value)}
            className={clsx(
              "-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium transition-colors",
              tab === value ? "border-accent text-ink" : "border-transparent text-ink-3 hover:text-ink",
            )}
          >
            {label}
            {count ? (
              <span className="tabular rounded-full bg-warn-soft px-1.5 text-[11px] font-semibold text-warn">{count}</span>
            ) : null}
          </button>
        ))}
      </div>

      <div role="tabpanel">
        {current.error ? (
          <ErrorNote>{current.error}</ErrorNote>
        ) : current.loading ? (
          <div className="space-y-3">
            <Skeleton className="h-20" />
            <Skeleton className="h-20" />
          </div>
        ) : current.data?.mail.length ? (
          <ul className="divide-y divide-line rounded-2xl border border-line bg-surface px-4 shadow-card">
            {current.data.mail.map((row) => (
              <MailItem key={row.id} row={row} onDismiss={tab === "review" ? dismiss : undefined} />
            ))}
          </ul>
        ) : tab === "review" ? (
          <EmptyState icon={<MailCheck className="size-5" />} title="Nothing to review">
            Emails that look like bookings but can’t be read land here, so nothing slips by unnoticed.
          </EmptyState>
        ) : (
          <EmptyState
            icon={<Inbox className="size-5" />}
            title="No booking emails yet"
            action={
              <Button busy={scanning} onClick={scan} icon={<RefreshCw className="size-4" aria-hidden />}>
                Check inbox now
              </Button>
            }
          >
            {query ? (
              <>
                Tripwire only reads Gmail matching <code translate="no" className="rounded bg-surface-2 px-1 font-mono text-[12px] text-ink">{query}</code>.
                If that label has no emails, nothing shows up here. Remove <code translate="no" className="font-mono text-[12px]">BSYNC_GMAIL_QUERY</code> from
                .env to use Tripwire’s built-in search for airline, hotel, train, cab and restaurant senders.
              </>
            ) : (
              "Tripwire reads new booking emails every few minutes. Check now, or drop an .eml file above to try one."
            )}
          </EmptyState>
        )}
      </div>
    </div>
  );
}
