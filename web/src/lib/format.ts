// Times from the API are naive local wall-clock strings ("2026-10-25T06:00:00"),
// already in the user’s configured zone. They are parsed as local and never
// shifted -- the calendar shows what the ticket says.

const pad = (n: number) => String(n).padStart(2, "0");

export function parseLocal(iso: string): Date {
  const [d, t = "00:00:00"] = iso.split("T");
  const [y, m, day] = d.split("-").map(Number);
  const [hh, mm, ss = 0] = t.split(":").map((x) => Number.parseInt(x, 10));
  return new Date(y, m - 1, day, hh, mm, ss || 0);
}

export function dayKey(iso: string): string {
  const d = parseLocal(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function time(iso: string): string {
  const d = parseLocal(iso);
  return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

export function dateLong(iso: string): string {
  return parseLocal(iso).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

export function dateTime(iso: string): string {
  return `${dateLong(iso)}, ${time(iso)}`;
}

export function dayLabel(key: string, today = new Date()): string {
  const d = parseLocal(`${key}T00:00:00`);
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const diff = Math.round((d.getTime() - start.getTime()) / 86_400_000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  if (diff === -1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
}

export function relative(iso: string, now = new Date()): string {
  const seconds = Math.round((parseLocal(iso).getTime() - now.getTime()) / 1000);
  const abs = Math.abs(seconds);
  const fmt = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (abs < 60) return fmt.format(Math.round(seconds), "second");
  if (abs < 3600) return fmt.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return fmt.format(Math.round(seconds / 3600), "hour");
  return fmt.format(Math.round(seconds / 86400), "day");
}

export function countdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${pad(s % 60)}`;
}

export function senderName(sender: string | null | undefined): string {
  if (!sender) return "Unknown sender";
  const match = sender.match(/^\s*"?([^"<]+?)"?\s*</);
  return match ? match[1] : sender.replace(/[<>]/g, "");
}

const STAGES: Record<string, string> = {
  "schema.org": "Reservation data",
  ics: "Calendar invite",
  rules: "Text rules",
};

export function stageLabel(stage: string | null | undefined, vendorName?: string | null): string {
  if (!stage) return "Not read";
  if (STAGES[stage]) return STAGES[stage];
  if (stage.startsWith("vendor:")) return `${vendorName || stage.slice(7).replace(/^\w/, (c) => c.toUpperCase())} format`;
  if (stage.startsWith("llm:")) return "AI reader";
  return stage;
}

/** The agent’s messages carry machine reasons ("unparseable: missing
 *  date_time"). Same facts, in words a person would use. */
const REASONS: [RegExp, string][] = [
  [/\(unparseable: missing ([^)]+)\)/, "(couldn’t find the $1)"],
  [/\(promotional\)/, "(it’s a promotion)"],
  [/\(no booking status found\)/, "(it isn’t about a booking)"],
];

export function humanize(message: string | null | undefined): string {
  let out = message ?? "";
  for (const [pattern, replacement] of REASONS) out = out.replace(pattern, replacement);
  return out
    .replace(/date_time/g, "date and time")
    .replace(/, title/g, " or title")
    .replace(/ -- /g, " — ");
}

/** Calendar-invite UIDs ("reserve-8841@uber.com") are matching keys, not
 *  something a person would quote to a vendor. Only show real references. */
export function displayRef(ref: string | null | undefined): string | null {
  return ref && !ref.includes("@") ? ref : null;
}

export const TYPE_LABEL: Record<string, string> = {
  flight: "Flight",
  train: "Train",
  hotel: "Hotel",
  ride: "Ride",
  restaurant: "Table",
  appointment: "Appointment",
};
