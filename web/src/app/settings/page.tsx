"use client";

import { BellRing, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { useToast } from "@/components/toast";
import { Button, Card, ErrorNote, PageHeader, Skeleton } from "@/components/ui";
import { post } from "@/lib/api";
import { useResource } from "@/lib/live";
import type { Status } from "@/lib/types";

const ZONES = [
  "Asia/Kolkata",
  "Asia/Dubai",
  "Asia/Singapore",
  "Europe/London",
  "Europe/Berlin",
  "America/New_York",
  "America/Los_Angeles",
  "Australia/Sydney",
  "UTC",
];
const POLL = [1, 5, 10, 15, 30, 60];
const ALARM = [
  [0, "No reminder"],
  [15, "15 minutes before"],
  [30, "30 minutes before"],
  [60, "1 hour before"],
  [120, "2 hours before"],
  [180, "3 hours before"],
  [1440, "1 day before"],
] as const;

const field =
  "h-10 w-full rounded-xl border border-line-strong bg-surface px-3 text-sm text-ink focus:border-accent focus:outline-none focus-visible:ring-4 focus-visible:ring-accent/15";

function Row({ label, hint, htmlFor, children }: { label: string; hint?: string; htmlFor?: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-2 py-4 sm:grid-cols-[1fr_minmax(0,280px)] sm:items-center sm:gap-6">
      <div>
        <label htmlFor={htmlFor} className="text-sm font-medium text-ink">
          {label}
        </label>
        {hint ? <p className="mt-0.5 text-[13px] text-ink-3 text-pretty">{hint}</p> : null}
      </div>
      <div>{children}</div>
    </div>
  );
}

export default function SettingsPage() {
  const toast = useToast();
  const { data: status, error, loading, reload } = useResource<Status>("/api/status", ["status"]);
  const [zone, setZone] = useState("Asia/Kolkata");
  const [always, setAlways] = useState(true);
  const [from, setFrom] = useState("07:00");
  const [to, setTo] = useState("23:00");
  const [poll, setPoll] = useState(5);
  const [alarm, setAlarm] = useState(60);
  const [notify, setNotify] = useState(true);
  const [area, setArea] = useState("");
  const [pin, setPin] = useState("");
  const [permission, setPermission] = useState<NotificationPermission | "unsupported" | null>(null);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);

  useEffect(() => {
    if (!status) return;
    const s = status.settings;
    setZone(s.zone);
    setPoll(s.poll_minutes);
    setAlarm(s.alarm_minutes);
    setNotify(s.notify_changes);
    setArea(s.dining_area);
    setPin(s.dining_pin);
    if (s.active_window) {
      const [a, b] = s.active_window.split("-");
      setAlways(false);
      setFrom(a);
      setTo(b);
    } else {
      setAlways(true);
    }
  }, [status]);

  const dirty = Boolean(
    status &&
      (zone !== status.settings.zone ||
        poll !== status.settings.poll_minutes ||
        alarm !== status.settings.alarm_minutes ||
        notify !== status.settings.notify_changes ||
        area.trim() !== status.settings.dining_area ||
        pin.trim() !== status.settings.dining_pin ||
        (always ? "" : `${from}-${to}`) !== status.settings.active_window),
  );

  useEffect(() => {
    setPermission("Notification" in window ? Notification.permission : "unsupported");
  }, []);

  async function askPermission() {
    if (!("Notification" in window)) return;
    const result = await Notification.requestPermission();
    setPermission(result);
    if (result === "granted") new Notification("Tripwire", { body: "Desktop notifications are on.", icon: "/favicon.ico" });
  }

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await post("/api/settings", {
        zone,
        active_window: always ? "" : `${from}-${to}`,
        poll_minutes: poll,
        alarm_minutes: alarm,
        notify_changes: notify,
        dining_area: area.trim(),
        dining_pin: pin.trim(),
      });
      toast("ok", "Settings saved.");
      reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function resetDemo() {
    setResetting(true);
    try {
      await post("/api/demo/reset");
      toast("ok", "Demo data reset. The sample emails will be read again.");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : String(err));
    } finally {
      setResetting(false);
    }
  }

  if (error) return <ErrorNote>{error}</ErrorNote>;
  if (loading || !status) return <Skeleton className="h-80" />;

  return (
    <div>
      <PageHeader title="Settings" description="How Tripwire reads your times, when it may ask you things, and how often it checks mail." />

      <form onSubmit={save}>
        <Card className="divide-y divide-line px-4 sm:px-6">
          <Row label="Time zone" hint="Booking times are shown and saved in this zone." htmlFor="zone">
            <select id="zone" name="zone" value={zone} onChange={(e) => setZone(e.target.value)} className={field}>
              {[...new Set([zone, ...ZONES])].map((z) => (
                <option key={z} value={z}>
                  {z.replace("_", " ")}
                </option>
              ))}
            </select>
          </Row>

          <Row
            label="Active hours"
            hint="Outside these hours Tripwire still reads mail, but holds questions until you’re back. Nothing changes while it waits."
          >
            <div className="space-y-3">
              <label className="flex items-center gap-2 text-sm text-ink">
                <input
                  type="checkbox"
                  checked={always}
                  onChange={(e) => setAlways(e.target.checked)}
                  className="size-4 rounded accent-[var(--accent)]"
                />
                Always active
              </label>
              {!always ? (
                <div className="flex items-center gap-2">
                  <label htmlFor="from" className="sr-only">
                    From
                  </label>
                  <input id="from" name="from" type="time" value={from} onChange={(e) => setFrom(e.target.value)} className={field} />
                  <span className="text-ink-3" aria-hidden>
                    –
                  </span>
                  <label htmlFor="to" className="sr-only">
                    Until
                  </label>
                  <input id="to" name="to" type="time" value={to} onChange={(e) => setTo(e.target.value)} className={field} />
                </div>
              ) : null}
            </div>
          </Row>

          <Row label="Check inbox every" htmlFor="poll">
            <select id="poll" name="poll" value={poll} onChange={(e) => setPoll(Number(e.target.value))} className={field}>
              {POLL.map((m) => (
                <option key={m} value={m}>
                  {m === 1 ? "1 minute" : `${m} minutes`}
                </option>
              ))}
            </select>
          </Row>

          <Row label="Reminder for new events" hint="Applies to bookings added from now on." htmlFor="alarm">
            <select id="alarm" name="alarm" value={alarm} onChange={(e) => setAlarm(Number(e.target.value))} className={field}>
              {ALARM.map(([m, label]) => (
                <option key={m} value={m}>
                  {label}
                </option>
              ))}
            </select>
          </Row>
        </Card>

        <h2 className="mt-8 mb-2 text-[12px] font-semibold tracking-wide text-ink-3 uppercase">Notifications</h2>
        <Card className="divide-y divide-line px-4 sm:px-6">
          <Row
            label="Tell me about every change"
            hint="A message whenever Tripwire adds, moves or removes something on your calendar, and when a Swiggy order changes. Sent to Telegram if it’s connected."
          >
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                name="notify"
                checked={notify}
                onChange={(e) => setNotify(e.target.checked)}
                className="size-4 rounded accent-[var(--accent)]"
              />
              Notify me
            </label>
          </Row>
          <Row label="Desktop notifications" hint="Pop-ups on this computer while Tripwire is open in a background tab.">
            {permission === "granted" ? (
              <p className="text-sm text-ok">On for this browser</p>
            ) : permission === "denied" ? (
              <p className="text-[13px] text-ink-2">Blocked. Allow notifications for this site in your browser’s settings.</p>
            ) : permission === "unsupported" ? (
              <p className="text-[13px] text-ink-2">This browser doesn’t support them.</p>
            ) : (
              <Button type="button" size="sm" onClick={askPermission} icon={<BellRing className="size-3.5" aria-hidden />}>
                Allow desktop notifications
              </Button>
            )}
          </Row>
        </Card>

        <h2 className="mt-8 mb-2 text-[12px] font-semibold tracking-wide text-ink-3 uppercase">Swiggy Dineout</h2>
        <Card className="divide-y divide-line px-4 sm:px-6">
          <Row label="Dining area" hint="Where to look for restaurants, e.g. “Indiranagar, Bangalore”." htmlFor="area">
            <input
              id="area"
              name="area"
              value={area}
              onChange={(e) => setArea(e.target.value)}
              placeholder="Indiranagar, Bangalore…"
              autoComplete="address-level2"
              className={field}
            />
          </Row>
          <Row label="PIN code" hint="Swiggy needs it to place the area on the map." htmlFor="pin">
            <input
              id="pin"
              name="pin"
              value={pin}
              onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 6))}
              inputMode="numeric"
              placeholder="560038"
              autoComplete="postal-code"
              spellCheck={false}
              className={field}
            />
          </Row>
        </Card>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" busy={saving} disabled={!dirty && !saving}>
            {dirty ? "Save settings" : "Saved"}
          </Button>
          <p className="text-[13px] text-ink-3">Saving restarts the assistant. Any open questions are dropped, and nothing is changed.</p>
        </div>
      </form>

      {status.mode === "demo" ? (
        <Card className="mt-8 flex flex-wrap items-center justify-between gap-4 p-4 sm:p-6">
          <div>
            <p className="text-sm font-medium text-ink">Reset demo</p>
            <p className="mt-0.5 text-[13px] text-ink-3">Clears demo activity and reads the sample emails again from the start.</p>
          </div>
          <Button busy={resetting} onClick={resetDemo} icon={<RotateCcw className="size-4" aria-hidden />}>
            Reset demo data
          </Button>
        </Card>
      ) : null}

      <p className="mt-8 text-[12px] text-ink-3">
        Data folder: <span className="font-mono">{status.settings.home}</span>
      </p>
    </div>
  );
}
