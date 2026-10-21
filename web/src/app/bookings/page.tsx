"use client";

import clsx from "clsx";
import { CalendarSearch, ChevronLeft, ChevronRight, List, Search, Table2 } from "lucide-react";
import { useMemo, useState } from "react";
import { TypeIcon } from "@/components/booking-bits";
import { EventRow, Timeline } from "@/components/timeline";
import { EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/ui";
import { dayKey, dayLabel, parseLocal, time } from "@/lib/format";
import { useResource, useUrlState } from "@/lib/live";
import type { BookingType, CalEvent } from "@/lib/types";

const FILTERS: { value: BookingType | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "flight", label: "Flights" },
  { value: "train", label: "Trains" },
  { value: "hotel", label: "Hotels" },
  { value: "ride", label: "Rides" },
  { value: "restaurant", label: "Tables" },
  { value: "appointment", label: "Appointments" },
];

function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T;
  options: { value: T; label: string; icon?: React.ReactNode }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="flex rounded-xl border border-line bg-surface-2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={clsx(
            "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors",
            value === o.value ? "bg-surface text-ink shadow-card" : "text-ink-3 hover:text-ink",
          )}
        >
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  );
}

function MonthGrid({ events, month, onMonth, selected, onSelect }: {
  events: CalEvent[];
  month: Date;
  onMonth: (d: Date) => void;
  selected: string | null;
  onSelect: (key: string | null) => void;
}) {
  const byDay = useMemo(() => {
    const map = new Map<string, CalEvent[]>();
    for (const e of events) map.set(dayKey(e.start), [...(map.get(dayKey(e.start)) ?? []), e]);
    return map;
  }, [events]);
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const offset = (first.getDay() + 6) % 7; // weeks start on Monday
  const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const cells = Array.from({ length: Math.ceil((offset + days) / 7) * 7 }, (_, i) => {
    const n = i - offset + 1;
    return n >= 1 && n <= days ? new Date(month.getFullYear(), month.getMonth(), n) : null;
  });
  const t = new Date();
  const localToday = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;

  return (
    <div className="rounded-2xl border border-line bg-surface p-3 shadow-card sm:p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[15px] font-semibold text-ink">
          {month.toLocaleDateString(undefined, { month: "long", year: "numeric" })}
        </h2>
        <div className="flex gap-1">
          <button
            onClick={() => onMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}
            className="grid size-8 place-items-center rounded-lg text-ink-2 hover:bg-surface-2"
            aria-label="Previous month"
          >
            <ChevronLeft className="size-4" aria-hidden />
          </button>
          <button
            onClick={() => onMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}
            className="grid size-8 place-items-center rounded-lg text-ink-2 hover:bg-surface-2"
            aria-label="Next month"
          >
            <ChevronRight className="size-4" aria-hidden />
          </button>
        </div>
      </div>
      <div className="grid grid-cols-7 gap-1 text-center text-[11px] font-semibold tracking-wide text-ink-3 uppercase" aria-hidden>
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
          <div key={d} className="py-1">
            {d}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {cells.map((date, i) => {
          if (!date) return <div key={i} />;
          const key = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
          const items = byDay.get(key) ?? [];
          const isSelected = selected === key;
          return (
            <button
              key={key}
              onClick={() => onSelect(isSelected ? null : key)}
              aria-pressed={isSelected}
              aria-label={`${dayLabel(key)}, ${items.length} booking${items.length === 1 ? "" : "s"}`}
              className={clsx(
                "flex min-h-16 flex-col items-stretch gap-1 rounded-xl border p-1.5 text-left transition-colors sm:min-h-24",
                isSelected ? "border-accent bg-accent-soft" : "border-transparent hover:bg-surface-2",
              )}
            >
              <span
                className={clsx(
                  "tabular grid size-6 place-items-center self-start rounded-full text-[12px] font-medium",
                  key === localToday ? "bg-accent text-white" : "text-ink-2",
                )}
              >
                {date.getDate()}
              </span>
              <span className="hidden flex-col gap-0.5 sm:flex">
                {items.slice(0, 2).map((e) => (
                  <span key={e.id} className="truncate rounded-md bg-surface-2 px-1.5 py-0.5 text-[11px] text-ink-2">
                    <span className="tabular font-medium">{time(e.start)}</span> {e.title}
                  </span>
                ))}
                {items.length > 2 ? <span className="px-1.5 text-[11px] text-ink-3">+{items.length - 2} more</span> : null}
              </span>
              {items.length ? (
                <span className="flex gap-0.5 sm:hidden" aria-hidden>
                  {items.slice(0, 3).map((e) => (
                    <span key={e.id} className="size-1.5 rounded-full bg-accent" />
                  ))}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function BookingsPage() {
  const { data, error, loading } = useResource<{ events: CalEvent[] }>("/api/events", ["calendar"]);
  const [filter, setFilter] = useUrlState<BookingType | "all">("type", "all", FILTERS.map((f) => f.value));
  const [query, setQuery] = useState("");
  const [view, setView] = useUrlState("view", "agenda", ["agenda", "month"] as const);
  const [when, setWhen] = useUrlState("when", "upcoming", ["upcoming", "past"] as const);
  const [month, setMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  const [selected, setSelected] = useState<string | null>(null);

  const now = new Date();
  const filtered = (data?.events ?? []).filter((e) => {
    if (filter !== "all" && e.type !== filter) return false;
    const q = query.trim().toLowerCase();
    if (q && ![e.title, e.location, e.ref].some((v) => v?.toLowerCase().includes(q))) return false;
    return true;
  });
  const agenda = filtered.filter((e) => (when === "upcoming" ? parseLocal(e.end ?? e.start) >= now : parseLocal(e.start) < now));
  if (when === "past") agenda.reverse();
  const dayItems = selected ? filtered.filter((e) => dayKey(e.start) === selected) : [];

  return (
    <div>
      <PageHeader title="Bookings" description="Everything Tripwire is tracking on your calendar." />

      <div className="mb-5 flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative w-full min-w-0 sm:w-auto sm:max-w-xs sm:flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-3" aria-hidden />
            <label htmlFor="search" className="sr-only">
              Search bookings
            </label>
            <input
              id="search"
              name="q"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search title, place or reference…"
              autoComplete="off"
              className="h-9 w-full rounded-xl border border-line-strong bg-surface pr-3 pl-9 text-sm text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus-visible:ring-4 focus-visible:ring-accent/15"
            />
          </div>
          <Segmented
            label="View"
            value={view}
            onChange={setView}
            options={[
              { value: "agenda", label: "Agenda", icon: <List className="size-3.5" aria-hidden /> },
              { value: "month", label: "Month", icon: <Table2 className="size-3.5" aria-hidden /> },
            ]}
          />
          {view === "agenda" ? (
            <Segmented
              label="Time range"
              value={when}
              onChange={setWhen}
              options={[
                { value: "upcoming", label: "Upcoming" },
                { value: "past", label: "Past" },
              ]}
            />
          ) : null}
        </div>
        <div className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1" role="group" aria-label="Filter by type">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              aria-pressed={filter === f.value}
              className={clsx(
                "flex shrink-0 items-center gap-1.5 rounded-full border px-3 py-1 text-[13px] font-medium transition-colors",
                filter === f.value
                  ? "border-accent bg-accent-soft text-accent-ink"
                  : "border-line bg-surface text-ink-2 hover:border-line-strong",
              )}
            >
              {f.value !== "all" ? <TypeIcon type={f.value} size="sm" /> : null}
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {error ? (
        <ErrorNote>{error}</ErrorNote>
      ) : loading ? (
        <div className="space-y-3">
          <Skeleton className="h-16" />
          <Skeleton className="h-16" />
        </div>
      ) : view === "month" ? (
        <div className="space-y-5">
          <MonthGrid events={filtered} month={month} onMonth={setMonth} selected={selected} onSelect={setSelected} />
          {selected ? (
            <section aria-label={dayLabel(selected)}>
              <h2 className="mb-1 text-[12px] font-semibold tracking-wide text-ink-3 uppercase">{dayLabel(selected)}</h2>
              {dayItems.length ? (
                <ul className="divide-y divide-line rounded-2xl border border-line bg-surface px-4 shadow-card">
                  {dayItems.map((e) => (
                    <EventRow key={e.id} event={e} />
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-ink-3">Nothing booked that day.</p>
              )}
            </section>
          ) : null}
        </div>
      ) : agenda.length ? (
        <Timeline events={agenda} headingLevel={2} />
      ) : (
        <EmptyState icon={<CalendarSearch className="size-5" />} title={query || filter !== "all" ? "No bookings match" : when === "past" ? "No past bookings" : "No upcoming bookings"}>
          {query || filter !== "all"
            ? "Try a different search or filter."
            : "Bookings show up here once their confirmation emails are read, or when you add one from Today."}
        </EmptyState>
      )}
    </div>
  );
}
