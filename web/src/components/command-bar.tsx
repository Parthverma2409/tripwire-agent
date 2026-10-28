"use client";

import clsx from "clsx";
import { ArrowUp, CircleAlert, CircleCheck, Hourglass, Info, Mic, MicOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { post, upload } from "@/lib/api";
import { useResource } from "@/lib/live";
import Link from "next/link";
import type { CommandResult, Status } from "@/lib/types";

const SUGGESTIONS = [
  "What do I have tomorrow?",
  "Book a cab to the airport tomorrow at 6 am",
  "Cancel my Chennai flight",
];

// The Web Speech API is Chrome / Edge only and not in the TS DOM lib.
type Recognition = {
  lang: string;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string; confidence: number }>> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
};

function speechRecognition(): (new () => Recognition) | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function CommandBar() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState<CommandResult | { status: "error"; message: string } | null>(null);
  const [listening, setListening] = useState(false);
  const [voiceSupported, setVoiceSupported] = useState(false);
  const recognition = useRef<Recognition | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const { data: status } = useResource<Status>("/api/status", ["status"]);
  // Local Whisper on the server, when configured: audio never leaves the machine.
  const localVoice = status?.connections.voice.provider === "whisper";

  useEffect(() => {
    setVoiceSupported(localVoice ? typeof MediaRecorder !== "undefined" : Boolean(speechRecognition()));
  }, [localVoice]);

  async function recordLocally() {
    if (recorder.current) {
      recorder.current.stop();
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setReply({ status: "error", message: "Microphone access is blocked. Allow it in the address bar, or type instead." });
      return;
    }
    const chunks: Blob[] = [];
    const rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      recorder.current = null;
      setListening(false);
      setBusy(true);
      try {
        const file = new File(chunks, "command.webm", { type: rec.mimeType || "audio/webm" });
        const result = await upload<CommandResult & { transcript?: string }>("/api/voice", file);
        if (result.transcript) setText(result.status === "refused" ? result.transcript : "");
        setReply(result);
      } catch (e) {
        setReply({ status: "error", message: e instanceof Error ? e.message : String(e) });
      } finally {
        setBusy(false);
      }
    };
    recorder.current = rec;
    setListening(true);
    rec.start();
    // A command is a sentence; stop on its own if the user forgets to.
    setTimeout(() => recorder.current === rec && rec.state === "recording" && rec.stop(), 12_000);
  }

  async function send(value: string) {
    const command = value.trim();
    if (!command || busy) return;
    setBusy(true);
    try {
      setReply(await post<CommandResult>("/api/command", { text: command }));
      setText("");
    } catch (e) {
      setReply({ status: "error", message: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
      input.current?.focus();
    }
  }

  function toggleVoice() {
    if (localVoice) {
      recordLocally();
      return;
    }
    if (listening) {
      recognition.current?.stop();
      return;
    }
    const Ctor = speechRecognition();
    if (!Ctor) return;
    const r = new Ctor();
    r.lang = navigator.language || "en-IN";
    r.interimResults = false;
    r.onresult = (event) => {
      const best = event.results[0]?.[0];
      if (!best) return;
      setText(best.transcript);
      // A half-heard sentence is a request for clarification, not a command --
      // the same rule the server-side voice shell applies.
      if (best.confidence === 0 || best.confidence >= 0.5) send(best.transcript);
      else setReply({ status: "error", message: `I’m not sure I heard that right: “${best.transcript}”. Edit it and press Enter.` });
    };
    r.onerror = (event) => {
      if (event.error === "not-allowed") {
        setReply({ status: "error", message: "Microphone access is blocked. Allow it in the address bar, or type instead." });
      }
    };
    r.onend = () => setListening(false);
    recognition.current = r;
    setListening(true);
    r.start();
  }

  const tone =
    reply?.status === "success"
      ? { icon: CircleCheck, cls: "text-ok" }
      : reply?.status === "awaiting_confirmation"
        ? { icon: Hourglass, cls: "text-warn" }
        : reply?.status === "refused"
          ? { icon: Info, cls: "text-ink-3" }
          : { icon: CircleAlert, cls: "text-danger" };

  return (
    <section aria-label="Ask Tripwire">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
        className="flex items-center gap-2 rounded-2xl border border-line-strong bg-surface p-2 shadow-card focus-within:border-accent focus-within:ring-4 focus-within:ring-accent/15"
      >
        <label htmlFor="command" className="sr-only">
          Tell Tripwire what to do
        </label>
        <input
          ref={input}
          id="command"
          name="command"
          autoComplete="off"
          spellCheck={false}
          maxLength={500}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={listening ? "Listening…" : "Book, move, cancel, or ask about a booking…"}
          className="h-10 min-w-0 flex-1 bg-transparent px-2 text-[15px] text-ink placeholder:text-ink-3 focus:outline-none"
        />
        {voiceSupported ? (
          <button
            type="button"
            onClick={toggleVoice}
            aria-pressed={listening}
            aria-label={listening ? "Stop listening" : "Speak a command"}
            title={listening ? "Stop listening" : "Speak a command"}
            className={clsx(
              "grid size-10 shrink-0 place-items-center rounded-xl transition-colors",
              listening ? "bg-danger text-white" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
            )}
          >
            {listening ? <MicOff className="size-[18px]" aria-hidden /> : <Mic className="size-[18px]" aria-hidden />}
          </button>
        ) : null}
        <button
          type="submit"
          disabled={!text.trim() || busy}
          aria-label="Send"
          className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent text-white transition-colors hover:bg-accent-hover disabled:opacity-40 dark:text-[#0b1530]"
        >
          <ArrowUp className={clsx("size-[18px]", busy && "animate-pulse")} aria-hidden />
        </button>
      </form>

      {reply ? (
        <div aria-live="polite" className="animate-rise mt-3 flex items-start gap-2.5 rounded-xl bg-surface-2 px-4 py-3 text-sm">
          <tone.icon className={clsx("mt-0.5 size-4 shrink-0", tone.cls)} aria-hidden />
          <p className="min-w-0 text-ink text-pretty">
            {reply.message}
            {reply.status === "awaiting_confirmation" ? (
              <span className="text-ink-3"> It’s waiting for your OK below.</span>
            ) : null}
            {"meta" in reply && typeof reply.meta?.link === "string" ? (
              <>
                {" "}
                <Link href={reply.meta.link} className="font-medium whitespace-nowrap text-accent-ink underline underline-offset-2">
                  {String(reply.meta.link_label ?? "Open")} →
                </Link>
              </>
            ) : null}
          </p>
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => {
                setText(s);
                input.current?.focus();
              }}
              className="rounded-full border border-line bg-surface px-3 py-1 text-[13px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink"
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
