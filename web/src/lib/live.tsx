"use client";

// One EventSource for the whole app. Components ask for data with
// useResource(path, topics); when the server announces a change on one of those
// topics, the resource refetches. No polling, no stale approval cards.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { get } from "./api";

type Listener = (topic: string) => void;

interface Live {
  connected: boolean;
  subscribe: (listener: Listener) => () => void;
}

const LiveContext = createContext<Live>({ connected: false, subscribe: () => () => {} });

const TOPICS = ["calendar", "confirmations", "inbox", "activity", "scan", "status", "rebuilt"];

export function LiveProvider({ children }: { children: React.ReactNode }) {
  const [connected, setConnected] = useState(false);
  const listeners = useRef(new Set<Listener>());

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let closed = false;

    const open = () => {
      source = new EventSource("/api/stream");
      source.addEventListener("hello", () => setConnected(true));
      for (const topic of TOPICS) {
        source.addEventListener(topic, () => listeners.current.forEach((fn) => fn(topic)));
      }
      source.onerror = () => {
        setConnected(false);
        source?.close();
        if (!closed) retry = setTimeout(open, 3000);
      };
    };
    open();
    return () => {
      closed = true;
      clearTimeout(retry);
      source?.close();
    };
  }, []);

  const subscribe = useCallback((listener: Listener) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);

  return <LiveContext.Provider value={{ connected, subscribe }}>{children}</LiveContext.Provider>;
}

export function useLive() {
  return useContext(LiveContext);
}

export function useResource<T>(path: string | null, topics: string[] = []) {
  const { subscribe } = useLive();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const topicKey = topics.join(",");

  const reload = useCallback(async () => {
    if (!path) return;
    try {
      const result = await get<T>(path);
      setData(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    if (!topicKey) return;
    const wanted = new Set([...topicKey.split(","), "rebuilt"]);
    return subscribe((topic) => {
      if (wanted.has(topic)) reload();
    });
  }, [subscribe, reload, topicKey]);

  return { data, error, loading, reload, setData };
}

/** The current time, client-side only. Null during prerender, so text that
 *  depends on "now" is never baked into the static HTML and then contradicted
 *  on hydration. Ticks every minute. */
export function useNow(): Date | null {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const timer = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

/** useState mirrored into the query string (replaceState, no history spam), so
 *  a filtered view survives a reload and can be bookmarked. */
export function useUrlState<T extends string>(key: string, fallback: T, allowed: readonly T[]): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(fallback);
  useEffect(() => {
    const raw = new URLSearchParams(window.location.search).get(key);
    if (raw && (allowed as readonly string[]).includes(raw)) setValue(raw as T);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const update = useCallback(
    (next: T) => {
      setValue(next);
      const params = new URLSearchParams(window.location.search);
      if (next === fallback) params.delete(key);
      else params.set(key, next);
      const query = params.toString();
      window.history.replaceState(null, "", window.location.pathname + (query ? `?${query}` : ""));
    },
    [key, fallback],
  );
  return [value, update];
}
