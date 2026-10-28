"use client";

import clsx from "clsx";
import { BadgePercent, Clock, ExternalLink, Search, Star, Ticket, UtensilsCrossed } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/ui";
import { get } from "@/lib/api";
import { useResource } from "@/lib/live";
import type { Coupon, FoodIdeas, FoodRestaurant } from "@/lib/types";

const CHIPS = ["Biryani", "Pizza", "Burgers", "South Indian", "Chinese", "Healthy", "Desserts"];

function Offers({ restaurant }: { restaurant: FoodRestaurant }) {
  const [open, setOpen] = useState(false);
  const [coupons, setCoupons] = useState<Coupon[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || coupons || !restaurant.id) return;
    setBusy(true);
    try {
      setCoupons((await get<{ coupons: Coupon[] }>(`/api/swiggy/food/${encodeURIComponent(restaurant.id)}/coupons`)).coupons);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!restaurant.id) return null;
  return (
    <>
      <Button size="sm" variant="ghost" busy={busy} onClick={toggle} aria-expanded={open} icon={<Ticket className="size-3.5" aria-hidden />}>
        {open ? "Hide coupons" : "Coupons"}
      </Button>
      {open ? (
        <div className="basis-full">
          {error ? (
            <p className="text-[13px] text-danger">{error}</p>
          ) : coupons === null ? null : coupons.length ? (
            <ul className="space-y-2">
              {coupons.slice(0, 6).map((c, i) => (
                <li key={c.code ?? i} className={clsx("rounded-lg border border-dashed px-3 py-2 text-[13px]", c.applicable === false ? "border-line text-ink-3" : "border-ok/50 text-ink")}>
                  <p className="font-medium">{c.title}</p>
                  {c.subtitle || c.description ? <p className="mt-0.5 text-ink-3">{c.subtitle || c.description}</p> : null}
                  {c.applicable === false ? <p className="mt-0.5 text-[12px] text-ink-3">Not applicable to a cart from here yet</p> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[13px] text-ink-3">No coupons for this restaurant right now.</p>
          )}
        </div>
      ) : null}
    </>
  );
}

function RestaurantCard({ r }: { r: FoodRestaurant }) {
  return (
    <Card className={clsx("flex flex-col overflow-hidden", !r.open && "opacity-70")}>
      <div className="relative aspect-[16/9] bg-surface-2">
        {r.image ? (
          // eslint-disable-next-line @next/next/no-img-element -- remote Swiggy CDN image in a static export
          <img src={r.image} alt="" width={480} height={270} loading="lazy" className="size-full object-cover" />
        ) : (
          <div className="grid size-full place-items-center text-ink-3" aria-hidden>
            <UtensilsCrossed className="size-6" />
          </div>
        )}
        {r.offer ? (
          <span className="absolute bottom-2 left-2 inline-flex items-center gap-1 rounded-md bg-[#fc8019] px-2 py-0.5 text-[12px] font-semibold text-[#2a1300] shadow-card">
            <BadgePercent className="size-3.5" aria-hidden />
            {r.offer}
          </span>
        ) : null}
        {!r.open ? (
          <span className="absolute top-2 right-2 rounded-md bg-surface/95 px-2 py-0.5 text-[12px] font-medium text-ink">
            Closed{r.next_open ? ` · opens ${r.next_open}` : ""}
          </span>
        ) : null}
      </div>
      <div className="flex flex-1 flex-col p-4">
        <h3 className="truncate text-[15px] font-semibold text-ink" title={r.name}>
          {r.name}
        </h3>
        <p className="mt-0.5 truncate text-[13px] text-ink-3">{[r.cuisines.join(", "), r.area].filter(Boolean).join(" · ")}</p>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-ink-2">
          {r.rating ? (
            <span className="inline-flex items-center gap-1 font-medium">
              <Star className="size-3.5 fill-current text-ok" aria-hidden />
              <span className="sr-only">Rated </span>
              {r.rating}
              {r.ratings ? <span className="font-normal text-ink-3">({r.ratings})</span> : null}
            </span>
          ) : null}
          {r.eta ? (
            <span className="inline-flex items-center gap-1">
              <Clock className="size-3.5" aria-hidden />
              {r.eta}
            </span>
          ) : null}
          {r.cost ? <span>{r.cost}</span> : null}
        </div>
        <div className="mt-auto flex flex-wrap items-center gap-2 pt-3">
          <a
            href={r.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line-strong bg-surface px-3 text-[13px] font-medium text-ink shadow-card hover:bg-surface-2"
          >
            Open in Swiggy <ExternalLink className="size-3 opacity-60" aria-hidden />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
          <Offers restaurant={r} />
        </div>
      </div>
    </Card>
  );
}

export default function FoodPage() {
  const [query, setQuery] = useState<string | null>(null);
  const [text, setText] = useState("");

  useEffect(() => {
    const q = new URLSearchParams(window.location.search).get("q") ?? "";
    setText(q);
    setQuery(q);
  }, []);

  const { data, error, loading } = useResource<FoodIdeas>(
    query === null ? null : `/api/swiggy/food?q=${encodeURIComponent(query)}`,
    ["rebuilt"],
  );

  function search(next: string) {
    const q = next.trim();
    setText(q);
    setQuery(q);
    window.history.replaceState(null, "", window.location.pathname + (q ? `?q=${encodeURIComponent(q)}` : ""));
  }

  const notConnected = error?.includes("Connect Swiggy");
  const noArea = error?.includes("dining area");

  return (
    <div>
      <PageHeader
        title="Food"
        description={
          <>
            Ideas and offers from Swiggy{data?.area ? ` near ${data.area}` : ""}. Tripwire never places an order: open a restaurant in Swiggy
            to order.
          </>
        }
      />

      <form
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          search(text);
        }}
        className="flex gap-2"
      >
        <div className="relative min-w-0 flex-1 sm:max-w-md">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-3" aria-hidden />
          <label htmlFor="food-q" className="sr-only">
            Dish, cuisine or restaurant
          </label>
          <input
            id="food-q"
            name="q"
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Biryani, pizza, a restaurant…"
            autoComplete="off"
            className="h-10 w-full rounded-xl border border-line-strong bg-surface pr-3 pl-9 text-sm text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus-visible:ring-4 focus-visible:ring-accent/15"
          />
        </div>
        <Button type="submit" variant="primary">
          Search
        </Button>
      </form>
      <div className="-mx-1 mt-3 mb-6 flex gap-1.5 overflow-x-auto px-1 pb-1" role="group" aria-label="Quick searches">
        {CHIPS.map((c) => (
          <button
            key={c}
            onClick={() => search(c)}
            aria-pressed={query?.toLowerCase() === c.toLowerCase()}
            className={clsx(
              "shrink-0 rounded-full border px-3 py-1 text-[13px] font-medium transition-colors",
              query?.toLowerCase() === c.toLowerCase()
                ? "border-accent bg-accent-soft text-accent-ink"
                : "border-line bg-surface text-ink-2 hover:border-line-strong",
            )}
          >
            {c}
          </button>
        ))}
      </div>

      {notConnected ? (
        <EmptyState
          icon={<UtensilsCrossed className="size-5" />}
          title="Connect Swiggy to see food ideas"
          action={
            <Link href="/connections/" className="inline-flex h-9 items-center rounded-xl bg-[#fc8019] px-4 text-[13px] font-semibold text-[#2a1300] shadow-card hover:opacity-90">
              Go to Connections
            </Link>
          }
        >
          Suggestions, ratings, delivery times and offers come from your Swiggy account. Tripwire only looks; it never orders.
        </EmptyState>
      ) : noArea ? (
        <EmptyState
          icon={<UtensilsCrossed className="size-5" />}
          title="Where should Tripwire look?"
          action={
            <Link href="/settings/" className="inline-flex h-9 items-center rounded-xl bg-accent px-4 text-[13px] font-medium text-white shadow-card hover:bg-accent-hover dark:text-[#0b1530]">
              Set your area in Settings
            </Link>
          }
        >
          Add your area and PIN code once, and suggestions will be for places that deliver to you.
        </EmptyState>
      ) : error ? (
        <ErrorNote>{error}</ErrorNote>
      ) : loading || !data ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
      ) : (
        <div className="space-y-8">
          <section aria-labelledby="delivery">
            <h2 id="delivery" className="mb-3 text-[15px] font-semibold text-ink">
              Delivery{data.query && data.query !== "popular" ? ` · “${data.query}”` : ""}
            </h2>
            {data.restaurants.length ? (
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {data.restaurants.slice(0, 12).map((r, i) => (
                  <RestaurantCard key={r.id ?? `${r.name}-${i}`} r={r} />
                ))}
              </div>
            ) : (
              <p className="text-sm text-ink-3">Nothing on Swiggy matches that near you. Try a broader word, like a cuisine.</p>
            )}
          </section>

          {data.dishes.length ? (
            <section aria-labelledby="dishes">
              <h2 id="dishes" className="mb-3 text-[15px] font-semibold text-ink">
                Dishes
              </h2>
              <Card className="divide-y divide-line px-4">
                {data.dishes.map((d, i) => (
                  <div key={i} className="flex items-center gap-3 py-3 text-sm">
                    <span
                      className={clsx("size-3 shrink-0 rounded-sm border-2", d.veg ? "border-ok" : d.veg === false ? "border-danger" : "border-line-strong")}
                      role="img"
                      aria-label={d.veg ? "Vegetarian" : d.veg === false ? "Non-vegetarian" : "Veg status unknown"}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium text-ink">
                        {d.name}
                        {d.bestseller ? (
                          <Badge tone="warn" className="ml-2">
                            Bestseller
                          </Badge>
                        ) : null}
                      </p>
                      {d.restaurant ? <p className="truncate text-[13px] text-ink-3">{d.restaurant}</p> : null}
                    </div>
                    {d.price != null ? <span className="tabular shrink-0 font-medium text-ink-2">₹{d.price}</span> : null}
                  </div>
                ))}
              </Card>
            </section>
          ) : null}

          {data.dineout.length ? (
            <section aria-labelledby="dineout">
              <h2 id="dineout" className="text-[15px] font-semibold text-ink">
                Dine out offers
              </h2>
              <p className="mt-0.5 mb-3 text-[13px] text-ink-3">
                To reserve, say “book a table at … tomorrow at 8pm on Swiggy” on Today. Tripwire asks before it books.
              </p>
              <div className="grid gap-3 sm:grid-cols-2">
                {data.dineout.map((r, i) => (
                  <Card key={r.id ?? i} className="p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-ink">{r.name}</p>
                        <p className="truncate text-[13px] text-ink-3">{[r.area, r.cost].filter(Boolean).join(" · ")}</p>
                      </div>
                      {r.rating ? (
                        <span className="inline-flex shrink-0 items-center gap-1 text-[13px] font-medium text-ink-2">
                          <Star className="size-3.5 fill-current text-ok" aria-hidden />
                          <span className="sr-only">Rated </span>
                          {r.rating}
                        </span>
                      ) : null}
                    </div>
                    {[...r.offers, ...r.deals].length ? (
                      <ul className="mt-2 flex flex-wrap gap-1.5">
                        {[...r.offers, ...r.deals].map((o, j) => (
                          <li key={j}>
                            <Badge tone="accent" icon={<BadgePercent className="size-3.5" aria-hidden />}>
                              {o}
                            </Badge>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </Card>
                ))}
              </div>
            </section>
          ) : null}
        </div>
      )}
    </div>
  );
}
