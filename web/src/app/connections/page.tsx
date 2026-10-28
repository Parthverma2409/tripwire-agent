"use client";

import clsx from "clsx";
import { BrainCircuit, CalendarDays, CarFront, ChevronDown, CircleCheck, Mail, Mic, Send, TriangleAlert, UtensilsCrossed } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, ErrorNote, PageHeader, Skeleton } from "@/components/ui";
import { post } from "@/lib/api";
import { useResource } from "@/lib/live";
import type { ConnectionState, Status } from "@/lib/types";

interface Step {
  text: React.ReactNode;
}

function Code({ children }: { children: React.ReactNode }) {
  return <code translate="no" className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[12px] text-ink break-all">{children}</code>;
}

function ConnectionCard({ icon, title, summary, state, actions, steps, optional = false, okLabel = "Connected" }: {
  icon: React.ReactNode;
  title: string;
  summary: string;
  state: ConnectionState | undefined;
  actions?: React.ReactNode;
  steps: Step[];
  optional?: boolean;
  /** What a working state is called; "Connected" overstates one with no sign-in. */
  okLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const ok = Boolean(state?.ok);
  useEffect(() => {
    if (state && !state.ok && !optional) setOpen(true);
  }, [state, optional]);
  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-start gap-4 p-4 sm:p-5">
        <span
          className={clsx("grid size-10 shrink-0 place-items-center rounded-xl", ok ? "bg-ok-soft text-ok" : "bg-surface-2 text-ink-3")}
          aria-hidden
        >
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
            {state ? (
              ok ? (
                <Badge tone="ok" icon={<CircleCheck className="size-3.5" aria-hidden />}>
                  {okLabel}
                </Badge>
              ) : optional ? (
                <Badge tone="neutral">Optional</Badge>
              ) : (
                <Badge tone="warn" icon={<TriangleAlert className="size-3.5" aria-hidden />}>
                  Needs setup
                </Badge>
              )
            ) : null}
          </div>
          <p className="mt-0.5 text-[13px] text-ink-3">{summary}</p>
          {state?.detail ? <p className="mt-2 text-[13px] text-ink-2 text-pretty">{state.detail}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
      </div>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between border-t border-line px-4 py-2.5 text-left text-[13px] font-medium text-ink-2 hover:bg-surface-2 sm:px-5"
      >
        How to set it up
        <ChevronDown className={clsx("size-4 transition-transform", open && "rotate-180")} aria-hidden />
      </button>
      {open ? (
        <ol className="space-y-2 border-t border-line bg-surface-2/50 px-4 py-4 text-[13px] text-ink-2 sm:px-5">
          {steps.map((step, i) => (
            <li key={i} className="flex gap-3">
              <span className="tabular grid size-5 shrink-0 place-items-center rounded-full bg-surface text-[11px] font-semibold text-ink-2 shadow-card">
                {i + 1}
              </span>
              <span className="min-w-0 pt-px text-pretty">{step.text}</span>
            </li>
          ))}
          <li className="pt-1 text-[12px] text-ink-3">
            Full walkthrough: <Code>docs/CONNECT.md</Code> in the project folder.
          </li>
        </ol>
      ) : null}
    </Card>
  );
}

export default function ConnectionsPage() {
  const toast = useToast();
  const { data: status, error, loading, reload } = useResource<Status>("/api/status", ["status", "scan"]);
  const [testing, setTesting] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("connected") === "google") setNotice({ tone: "ok", text: "Google connected. Tripwire is now using your real calendar and inbox." });
    if (params.get("connected") === "swiggy") setNotice({ tone: "ok", text: "Swiggy connected. Order updates will reach you, and you can book tables by asking on Today." });
    if (params.get("error")) setNotice({ tone: "error", text: params.get("error") as string });
    if (params.toString()) window.history.replaceState(null, "", window.location.pathname);
  }, []);

  async function test(name: string) {
    setTesting(name);
    try {
      const result = await post<{ ok: boolean; detail: string }>(`/api/connections/test/${name}`);
      toast(result.ok ? "ok" : "error", result.detail);
      reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setTesting(null);
    }
  }

  async function setMode(mode: "demo" | "live") {
    try {
      await post("/api/settings", { mode });
      toast("ok", mode === "demo" ? "Switched to demo mode." : "Switched to live mode.");
      reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  async function disconnectSwiggy() {
    try {
      await post("/auth/swiggy/disconnect");
      toast("ok", "Swiggy disconnected. Its sign-in was deleted from this computer.");
      reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  async function disconnect() {
    try {
      await post("/auth/google/disconnect");
      toast("ok", "Google disconnected and its access revoked.");
      reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (loading || !status) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-60" />
        <Skeleton className="h-32" />
        <Skeleton className="h-32" />
      </div>
    );
  }

  const s = status.settings;
  const c = status.connections;
  const live = status.mode === "live";
  const google = status.google;

  return (
    <div>
      <PageHeader
        title="Connections"
        description="Connect your calendar and inbox. Everything stays on this computer; Tripwire only ever asks for the access it needs."
      />

      {notice ? (
        <div
          role={notice.tone === "error" ? "alert" : "status"}
          className={clsx(
            "mb-5 rounded-xl border px-4 py-3 text-sm",
            notice.tone === "error" ? "border-danger/30 bg-danger-soft text-danger" : "border-transparent bg-ok-soft text-ok",
          )}
        >
          {notice.text}
        </div>
      ) : null}

      <Card className="mb-6 flex flex-wrap items-center justify-between gap-4 p-4 sm:p-5">
        <div>
          <p className="text-[15px] font-semibold text-ink">{live ? "Live mode" : "Demo mode"}</p>
          <p className="mt-0.5 text-[13px] text-ink-3">
            {live
              ? "Using your connected accounts."
              : "Using sample emails and a practice calendar, so you can try everything safely."}
          </p>
        </div>
        <div role="group" aria-label="Mode" className="flex rounded-xl border border-line bg-surface-2 p-0.5">
          {(["demo", "live"] as const).map((m) => (
            <button
              key={m}
              aria-pressed={status.mode === m}
              onClick={() => status.mode !== m && setMode(m)}
              className={clsx(
                "rounded-lg px-4 py-1.5 text-[13px] font-medium capitalize transition-colors",
                status.mode === m ? "bg-surface text-ink shadow-card" : "text-ink-3 hover:text-ink",
              )}
            >
              {m}
            </button>
          ))}
        </div>
      </Card>

      <div className="space-y-4">
        <ConnectionCard
          icon={<CalendarDays className="size-5" />}
          title="Google Calendar & Gmail"
          summary="Required for live mode. Read-only Gmail and calendar events only."
          state={live ? c.calendar : { ok: google.connected, detail: google.connected ? "Signed in. Switch to live mode to use it." : google.configured ? "Ready to connect." : "Add your Google OAuth client to .env first." }}
          actions={
            google.connected ? (
              <>
                {live ? (
                  <Button size="sm" busy={testing === "calendar"} onClick={() => test("calendar")}>
                    Test
                  </Button>
                ) : null}
                <Button size="sm" variant="ghost" onClick={disconnect}>
                  Disconnect
                </Button>
              </>
            ) : (
              <a
                href="/auth/google/start"
                aria-disabled={!google.configured}
                className={clsx(
                  "inline-flex h-8 items-center gap-1.5 rounded-lg bg-accent px-3 text-[13px] font-medium text-white shadow-card hover:bg-accent-hover dark:text-[#0b1530]",
                  !google.configured && "pointer-events-none opacity-50",
                )}
              >
                Connect Google
              </a>
            )
          }
          steps={[
            { text: <>Open Google Cloud Console and create a project (e.g. “Tripwire”).</> },
            { text: <>Under APIs &amp; Services → Library, enable <b>Google Calendar API</b> and <b>Gmail API</b>.</> },
            { text: <>OAuth consent screen: choose External, keep it in Testing, and add your own Gmail as a test user.</> },
            { text: <>Credentials → Create OAuth client ID → Web application. Add this redirect URI: <Code>{s.redirect_uri}</Code></> },
            { text: <>Copy the client ID and secret into <Code>.env</Code> as <Code>GOOGLE_CLIENT_ID</Code> and <Code>GOOGLE_CLIENT_SECRET</Code>, then restart Tripwire.</> },
            { text: <>Click <b>Connect Google</b> and approve. You’ll see two permissions: manage calendar events, and read email.</> },
          ]}
        />

        <ConnectionCard
          icon={<Mail className="size-5" />}
          title="Inbox"
          summary={status.mail_channel === "imap" ? "Reading over IMAP, read-only." : "Gmail is used automatically once Google is connected. Other providers use IMAP."}
          state={c.mail}
          actions={
            c.mail?.ok ? (
              <Button size="sm" busy={testing === "mail"} onClick={() => test("mail")}>
                Test
              </Button>
            ) : undefined
          }
          steps={[
            { text: <>Using Gmail? Nothing to do here: connecting Google above covers it.</> },
            { text: <>Yahoo, Zoho, iCloud or Fastmail: turn on two-step sign-in and create an <b>app password</b> for mail.</> },
            { text: <>Add <Code>IMAP_HOST</Code>, <Code>IMAP_USER</Code> and <Code>IMAP_PASSWORD</Code> to <Code>.env</Code> (e.g. <Code>imap.mail.yahoo.com</Code>), then restart.</> },
            { text: <>Personal Outlook.com no longer allows app passwords over IMAP. Set Outlook to forward booking emails to a Gmail account instead.</> },
            { text: <>Optional: in Gmail, add a filter that labels booking emails and set <Code>BSYNC_GMAIL_QUERY=label:bookings newer_than:30d</Code>.</> },
          ]}
        />

        <ConnectionCard
          icon={<Send className="size-5" />}
          title="Telegram"
          summary="“Needs your OK” questions with Approve / Decline buttons, plus a note on your phone whenever your calendar changes."
          state={c.telegram}
          optional
          actions={
            c.telegram?.ok ? (
              <Button size="sm" busy={testing === "telegram"} onClick={() => test("telegram")}>
                Send test message
              </Button>
            ) : undefined
          }
          steps={[
            { text: <>In Telegram, message <b>@BotFather</b>, send <Code>/newbot</Code>, and copy the token it gives you.</> },
            { text: <>Send any message to your new bot, then open <Code>https://api.telegram.org/bot&lt;TOKEN&gt;/getUpdates</Code> and copy <Code>chat.id</Code>.</> },
            { text: <>Add <Code>TELEGRAM_BOT_TOKEN</Code> and <Code>TELEGRAM_CHAT_ID</Code> to <Code>.env</Code>, restart, and press <b>Send test message</b>.</> },
            { text: <>Only that chat is listened to, and every button is signed, so nobody else can approve anything.</> },
          ]}
        />

        <ConnectionCard
          icon={<UtensilsCrossed className="size-5" />}
          title="Swiggy"
          summary="Food order updates as notifications, and Dineout tables booked by asking (always after your OK)."
          state={live ? c.swiggy : { ok: status.swiggy.connected, detail: status.swiggy.connected ? "Signed in. Switch to live mode to use it." : "Optional." }}
          optional
          actions={
            status.swiggy.connected ? (
              <>
                {live ? (
                  <Button size="sm" busy={testing === "swiggy"} onClick={() => test("swiggy")}>
                    Test
                  </Button>
                ) : null}
                <Button size="sm" variant="ghost" onClick={disconnectSwiggy}>
                  Disconnect
                </Button>
              </>
            ) : (
              <a
                href="/auth/swiggy/start"
                className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-[#fc8019] px-3 text-[13px] font-semibold text-[#2a1300] shadow-card hover:opacity-90"
              >
                Connect Swiggy
              </a>
            )
          }
          steps={[
            { text: <>Click <b>Connect Swiggy</b> and sign in with your phone number and the OTP Swiggy sends. The OTP goes to Swiggy, never to Tripwire.</> },
            { text: <>This uses Swiggy’s official connector for AI apps (the same one Claude and ChatGPT use). Swiggy’s sign-in is short (about 5 days). Tripwire renews it once a day while it is running; if it lapses anyway, this card asks you to connect again.</> },
            { text: <>For tables, set your <b>dining area and PIN code</b> in <Link href="/settings/" className="text-accent-ink underline underline-offset-2">Settings</Link>.</> },
            { text: <>Then on Today, say “book a table at Toit for 4 tomorrow at 8pm on Swiggy”. Tripwire finds a free slot and asks before booking. Paid deals stay in the Swiggy app.</> },
          ]}
        />

        <ConnectionCard
          icon={<CarFront className="size-5" />}
          title="Uber"
          summary="No sign-in needed. Uber Reserve emails become rides on your calendar, and every ride has a one-tap Book in Uber button."
          state={{ ok: true, detail: "Works through your inbox. Ride details open in Uber with the drop-off filled in." }}
          okLabel="No setup needed"
          steps={[
            { text: <>Uber doesn’t give personal apps access to rides or trip history, so Tripwire works from what Uber emails you: Reserve confirmations and their calendar invites are read automatically.</> },
            { text: <>Open any ride on the Bookings page and tap <b>Book this ride in Uber</b>. Uber opens with the drop-off already set; you confirm the ride there.</> },
            { text: <>Typed rides work too: “book a cab to the airport tomorrow at 6am” puts it on your calendar with the same button.</> },
          ]}
        />

        <ConnectionCard
          icon={<BrainCircuit className="size-5" />}
          title="Local AI (Ollama)"
          summary="Reads unusual booking emails the built-in rules can’t. Runs on your computer, nothing leaves it."
          state={c.ollama}
          optional
          actions={
            s.ollama_host ? (
              <Button size="sm" busy={testing === "ollama"} onClick={() => test("ollama")}>
                Check again
              </Button>
            ) : undefined
          }
          steps={[
            { text: <>Install Ollama from ollama.com and start it.</> },
            { text: <>In a terminal run <Code>ollama pull {s.ollama_model}</Code> (about 4.7 GB).</> },
            { text: <>Add <Code>OLLAMA_HOST=http://localhost:11434</Code> to <Code>.env</Code> and restart Tripwire.</> },
          ]}
        />

        <ConnectionCard
          icon={<Mic className="size-5" />}
          title="Voice"
          summary="Speak commands with the microphone button on Today."
          state={c.voice}
          optional
          steps={[
            { text: <>Works in Chrome and Edge with no setup. Allow microphone access when asked.</> },
            { text: <>Chrome and Edge send the audio to their own speech service to turn it into text. Tripwire only receives the text, and handles it exactly like a typed command.</> },
            { text: <>Prefer audio that never leaves your computer? Type instead, or see the Whisper section in <Code>docs/CONNECT.md</Code>.</> },
          ]}
        />
      </div>
    </div>
  );
}
