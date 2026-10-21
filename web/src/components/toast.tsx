"use client";

import clsx from "clsx";
import { CircleAlert, CircleCheck, X } from "lucide-react";
import { createContext, useCallback, useContext, useState } from "react";

interface Toast {
  id: number;
  tone: "ok" | "error" | "info";
  text: string;
}

const ToastContext = createContext<(tone: Toast["tone"], text: string) => void>(() => {});

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((tone: Toast["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((all) => [...all.slice(-2), { id, tone, text }]);
    setTimeout(() => setToasts((all) => all.filter((t) => t.id !== id)), tone === "error" ? 7000 : 4500);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-20 z-50 flex flex-col items-center gap-2 px-4 lg:bottom-6"
      >
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.tone === "error" ? "alert" : "status"}
            className={clsx(
              "animate-rise pointer-events-auto flex max-w-md items-start gap-2.5 rounded-xl border bg-surface px-4 py-3 text-sm shadow-pop",
              t.tone === "error" ? "border-danger/40" : "border-line",
            )}
          >
            {t.tone === "error" ? (
              <CircleAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden />
            ) : (
              <CircleCheck className="mt-0.5 size-4 shrink-0 text-ok" aria-hidden />
            )}
            <span className="min-w-0 text-ink text-pretty">{t.text}</span>
            <button
              onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))}
              className="-mr-1 ml-1 rounded p-0.5 text-ink-3 hover:text-ink"
              aria-label="Dismiss"
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
