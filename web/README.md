# Tripwire web UI

Next.js (App Router, TypeScript, Tailwind v4). In production it is a static export
(`web/out`) served by the Python app on the same origin as the API, so there is one
process and one port: `http://127.0.0.1:8765`.

## Build (what the launchers do)

```bash
cd web
npm install
npm run build        # writes web/out; the Python app serves it at /
```

## Develop with hot reload

Two terminals:

```bash
# 1. the Python app, accepting writes from the dev server's origin
BSYNC_DEV_ORIGIN=http://localhost:3000 python -m packages.app --no-browser

# 2. the Next dev server, proxying /api and /auth to 127.0.0.1:8765
cd web && npm run dev
```

Open http://localhost:3000. Every write carries an `X-BSync: 1` header
(`src/lib/api.ts`); the server refuses writes without it.

## Layout

```
src/app/            one folder per screen: Today (/), bookings, inbox, activity, connections, settings
src/components/     shell, command bar, approval card, timeline, shared UI primitives
src/lib/            API client, types, formatting, live updates (SSE) and URL state
```

Design tokens (colours for light and dark) live at the top of `src/app/globals.css`;
components use them through Tailwind names like `bg-surface` and `text-ink-2`.
