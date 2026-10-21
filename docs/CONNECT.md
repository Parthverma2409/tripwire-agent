# Connecting Tripwire to your real accounts

Tripwire works out of the box in **demo mode**: sample emails, a practice calendar,
nothing real touched. This guide takes it to **live mode**, step by step.

Only step 1 is required. Everything after it is optional.

| Step | What you get | Time | Needed? |
|---|---|---|---|
| [0. Start the app](#0-start-the-app) | The app running at `http://127.0.0.1:8765` | 2 min | Yes |
| [1. Google Calendar + Gmail](#1-google-calendar--gmail) | Real calendar, real booking emails | 15 min | **Yes, for live mode** |
| [2. Narrow what Gmail reads](#2-optional-narrow-what-gmail-reads) | Tripwire reads booking mail only | 5 min | Recommended |
| [3. Another inbox (IMAP)](#3-optional-another-inbox-imap) | Yahoo / Zoho / iCloud / Fastmail instead of Gmail | 5 min | Only if not Gmail |
| [4. Telegram approvals](#4-optional-telegram-approvals) | Approve / Decline buttons on your phone | 5 min | Recommended |
| [5. Local AI (Ollama)](#5-optional-local-ai-ollama) | Reads unusual emails the rules miss | 10 min | Optional |
| [6. Local voice (Whisper)](#6-optional-local-voice-whisper) | Speech-to-text that never leaves your PC | 5 min | Optional |
| [7. Check everything](#7-check-everything) | Green ticks on the Connections page | 2 min | Yes |

All settings live in one file, **`.env`**, in the project folder. The launcher creates
it from `.env.example` on first run. `.env` is git-ignored: never commit it or paste it
anywhere.

---

## 0. Start the app

You need **Python 3.11+** and **Node.js 20+**.

**Windows:** double-click **`run_app.bat`**.
**macOS / Linux:** `./run_app.sh`

The first run creates `.venv`, installs everything, builds the web UI and opens
`http://127.0.0.1:8765`. Later runs start in seconds. Leave the terminal window open
while you use the app. **Ctrl+C** stops it.

> After any change to `.env`, **stop the app and start it again**. Settings are read at startup.

---

## 1. Google Calendar + Gmail

You will create your own Google "OAuth client". It's free, and it means Tripwire talks to
Google as an app *you* own. No third party sits in the middle.

### 1.1 Create a Google Cloud project

1. Go to <https://console.cloud.google.com/> and sign in with the Google account
   whose calendar you want to use.
2. Top bar → project picker → **New project**. Name it `Tripwire` → **Create**.
3. Make sure the new project is selected in the top bar.

### 1.2 Turn on the two APIs

1. Left menu → **APIs & Services** → **Library**.
2. Search **Google Calendar API** → open it → **Enable**.
3. Back to the Library, search **Gmail API** → **Enable**.

### 1.3 Set up the consent screen

Google renamed this area **Google Auth Platform**. Older guides call it
"OAuth consent screen". It's the same thing.

1. Left menu → **APIs & Services** → **OAuth consent screen** (or **Google Auth Platform**).
   Click **Get started** if asked.
2. **App information:** app name `Tripwire`, support email = your Gmail.
3. **Audience:** choose **External**.
4. **Contact information:** your Gmail again. Agree to the policy → **Create**.
5. Open **Audience** → under **Test users** click **Add users** and add **your own Gmail
   address**. Without this, Google blocks the sign-in with "Access blocked".

> **About the 7-day limit.** While the app's publishing status is **Testing**, Google
> expires the sign-in after **7 days** and you would have to click *Connect Google* again
> every week. To avoid that, publish the app. Google greys out **Publish app** until the
> **Branding** page has four things, so fill those in first:
>
> 1. Left menu → **Branding**.
> 2. **App name:** `Tripwire`. **User support email:** your Gmail.
> 3. **Application home page:** `https://github.com/Parthverma2409/tripwire-agent`
> 4. **Application privacy policy link:**
>    `https://github.com/Parthverma2409/tripwire-agent/blob/main/PRIVACY.md`
>    (the repo's privacy policy, which describes exactly what Tripwire does with your data)
> 5. Leave **Terms of service** and the logo empty. A logo would trigger brand review.
> 6. If Google asks for **Authorised domains**, add `github.com`.
> 7. **Developer contact information:** your Gmail → **Save**.
> 8. **Audience** → **Publish app** → **Confirm**. If Google offers to "prepare for
>    verification", skip it. Personal use (under 100 users) doesn't need it.
>
> When you connect, Google shows a "Google hasn't verified this app" screen. Click
> **Advanced** → **Go to Tripwire (unsafe)**: it's your own app talking to your own account.
> If Google won't accept the links, you can stay in Testing and click *Connect Google*
> once a week instead. Everything else works the same.

### 1.4 Create the OAuth client

1. **APIs & Services → Credentials** (or **Google Auth Platform → Clients**) →
   **Create credentials / Create client** → **OAuth client ID**.
2. **Application type:** **Web application**. Name: `Tripwire local`.
3. **Authorised redirect URIs** → **Add URI** → paste exactly:

   ```
   http://127.0.0.1:8765/auth/google/callback
   ```

   (Use `127.0.0.1`, not `localhost`. Google matches it character for character.)
4. **Create**. Copy the **Client ID** and **Client secret**.

### 1.5 Put them in `.env`

Open `.env` in the project folder (VS Code is fine) and fill in:

```ini
GOOGLE_CLIENT_ID=1234567890-abc...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...
```

Save, then **restart the app**.

### 1.6 Connect

1. In the app → **Connections** → **Connect Google**.
2. Pick your account, approve. You'll be asked for exactly two permissions:
   - **View and edit events on all your calendars** (`calendar.events`). Tripwire only
     ever creates, moves and deletes **events**, and deletes or moves only after you
     approve. It can't create or delete calendars, or change who they're shared with.
   - **View your email messages and settings** (`gmail.readonly`). Tripwire can't send,
     delete or label mail.

   Google may word these slightly differently over time. If you're asked for anything
   *beyond* events and read-only mail, stop: that isn't Tripwire's request.
3. You land back on Connections with **Connected** and the app switches to **live mode**.

**What happens to the sign-in:** Google's refresh token is saved to
`~/.booking-sync/google-token.json`, encrypted with AES-256-GCM. The encryption key is
generated once and kept in your OS keyring (Windows Credential Manager on Windows), so
the file alone is useless to anyone who copies it.

**To disconnect:** Connections → **Disconnect**. This deletes the local token and revokes
it with Google.

---

## 2. (Optional) Narrow what Gmail reads

By default Tripwire asks Gmail only for mail from the last 30 days, outside the
Promotions tab, with booking-shaped subjects (booking, confirmed, cancelled, PNR,
itinerary, ticket, reservation, ride, table…). Gmail filters on its servers, so the rest
of your inbox is never downloaded.

For an even tighter scope, label your booking mail and read only that label:

1. Gmail → search bar → **Show search options** (the sliders icon).
2. **From:** paste the senders you book with, for example:

   ```
   goindigo.in OR airindia.com OR akasaair.com OR irctc.co.in OR makemytrip.com OR goibibo.com OR booking.com OR agoda.com OR uber.com OR olacabs.com OR swiggy.in OR zomato.com OR practo.com
   ```
3. **Create filter** → tick **Apply the label** → **New label** `bookings` → also tick
   **Also apply filter to matching conversations** → **Create filter**.
4. In `.env`:

   ```ini
   BSYNC_GMAIL_QUERY=label:bookings newer_than:30d
   ```
5. Restart the app.

---

## 3. (Optional) Another inbox (IMAP)

Use this only if your bookings arrive somewhere other than Gmail. When Google is
connected, Gmail always takes priority.

| Provider | `IMAP_HOST` | Where to make an app password |
|---|---|---|
| Yahoo | `imap.mail.yahoo.com` | Account security → Generate app password |
| Zoho | `imap.zoho.in` (or `.com`) | Security → App passwords |
| iCloud | `imap.mail.me.com` | appleid.apple.com → App-specific passwords |
| Fastmail | `imap.fastmail.com` | Settings → Privacy & Security → App passwords |

> **Outlook.com / Hotmail:** Microsoft no longer accepts app passwords over IMAP for
> personal accounts. Set up an Outlook rule that **forwards** booking emails to a Gmail
> account, and connect that Gmail instead (step 1).

```ini
IMAP_HOST=imap.mail.yahoo.com
IMAP_PORT=993
IMAP_USER=you@yahoo.com
IMAP_PASSWORD=the-app-password
```

Tripwire opens the mailbox **read-only** (IMAP `EXAMINE`). It can't mark, move or
delete anything.

---

## 4. (Optional) Telegram approvals

When an email asks to cancel or move something, Tripwire needs your OK. With Telegram set
up, the question comes to your phone with **Approve** and **Decline** buttons. You can also
message the bot commands like *"what do I have tomorrow?"*.

1. In Telegram, open **@BotFather** → send `/newbot` → choose a name (e.g. *My Tripwire*)
   and a username ending in `bot`. BotFather replies with a **token** like
   `7123456789:AAH...`.
2. Open your new bot and send it any message (e.g. `hi`). Bots can't message you
   until you have messaged them.
3. In a browser, open (paste your token in place of `<TOKEN>`):

   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```

   Find `"chat":{"id":123456789,...` and copy that number.
4. In `.env`:

   ```ini
   TELEGRAM_BOT_TOKEN=7123456789:AAH...
   TELEGRAM_CHAT_ID=123456789
   ```
5. Restart the app → **Connections → Telegram → Send test message**.

**Security:** Tripwire listens to that one chat only. Every button carries a signature
made with a key only your app holds, so a forged button press can't approve anything.
The bot uses long polling, so nothing on your PC is exposed to the internet.

---

## 5. (Optional) Local AI (Ollama)

The built-in readers handle reservation markup, calendar invites and the vendors in
`data/vendors/vendors.yaml`. For anything else, an AI model on your own PC can take a
second look before an email is sent to *To review*.

1. Install Ollama from <https://ollama.com/download> and start it.
2. In a terminal:

   ```bash
   ollama pull qwen2.5:7b-instruct
   ```

   It's about 4.7 GB. 16 GB of RAM is comfortable; smaller machines can use
   `qwen2.5:3b-instruct` and set `OLLAMA_MODEL` to match.
3. In `.env`:

   ```ini
   OLLAMA_HOST=http://localhost:11434
   OLLAMA_MODEL=qwen2.5:7b-instruct
   ```
4. Restart → **Connections → Local AI → Check again**.

What the AI is allowed to do: it's only asked about emails the rules flagged for
review. Its answer is checked like any untrusted input; for example, a booking reference
it returns is thrown away unless it appears word for word in the email. Anything it finds
still goes through the same *Needs your OK* step.

---

## 6. (Optional) Local voice (Whisper)

By default the microphone button uses your browser's speech recognition (Chrome/Edge),
which sends audio to Google or Microsoft. To keep audio on your PC:

```bash
.venv\Scripts\python -m pip install faster-whisper      # Windows
.venv/bin/python -m pip install faster-whisper          # macOS / Linux
```

```ini
WHISPER_MODEL=base.en
```

Restart. The Connections page shows *Local Whisper*. The first recording downloads the
model (~150 MB), so it takes a little longer.

---

## 7. Check everything

Open **Connections**. Each card shows **Connected**, **Needs setup** or **Optional**, with
the exact reason underneath and a **Test** button.

Then try it for real:

1. **Inbox → Test a booking email.** In Gmail, open a real booking confirmation →
   **⋮ → Download message** → drop the `.eml` file on the page. You'll see exactly what
   Tripwire read, and how.
2. Or from a terminal, without touching any calendar:

   ```bash
   .venv\Scripts\python -m packages.nlp check-email path\to\booking.eml
   ```

   Keep real emails you test with in `data/private/`, which is git-ignored.
3. On **Today**, type *"what do I have tomorrow?"*.

If a vendor's real email isn't read correctly, its rules are in
`data/vendors/vendors.yaml` and can be adjusted without touching code. Those profiles
were written from public knowledge of each sender and **haven't been checked against
real mail from every vendor**, so testing your own emails this way is worth doing.

---

## Troubleshooting

| You see | Cause | Fix |
|---|---|---|
| `Error 400: redirect_uri_mismatch` | The redirect URI in Google doesn't match exactly | Step 1.4: it must be `http://127.0.0.1:8765/auth/google/callback`, no trailing slash, `127.0.0.1` not `localhost` |
| `Access blocked: Tripwire has not completed the Google verification process` | Your Gmail isn't a test user | Step 1.3, point 5. Or publish the app (1.3 note) |
| Works for a week, then *"Google rejected the sign-in"* / `invalid_grant` | App still in **Testing**: Google expires tokens after 7 days | Publish the app (1.3 note), then **Connect Google** once more |
| *"Google returned no refresh token"* | You had approved Tripwire before | <https://myaccount.google.com/permissions> → remove Tripwire → connect again |
| *"Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env"* after you did | App not restarted, or a typo in the variable name | Restart; check the names match `.env.example` |
| *"the OS keyring refused"* | No keyring available (headless Linux, some locked-down PCs) | Set `BSYNC_TOKEN_PASSPHRASE=` to a long random string in `.env` |
| Telegram test says *chat not found* | You never messaged the bot, or the chat ID is wrong | Step 4, points 2–3 |
| *"Ollama is not reachable"* | Ollama isn't running | Start the Ollama app; check <http://localhost:11434> opens |
| *"Model … not pulled"* | The model isn't downloaded | `ollama pull qwen2.5:7b-instruct` |
| The page says *"The web UI has not been built yet"* | Node.js missing on first run | Install Node 20+, then `run_app.bat --rebuild-ui` |
| Port 8765 already in use | Another copy is running | Close it, or start with `python -m packages.app --port 8766`. **Note:** the Google redirect URI must then use 8766 too |
| Times are off by hours | Wrong time zone | **Settings → Time zone** |

---

## What Tripwire stores, and where

| What | Where | Protected by |
|---|---|---|
| Google refresh token | `~/.booking-sync/google-token.json` | AES-256-GCM; key in your OS keyring |
| Inbox log, activity, scored trajectories, settings | `~/.booking-sync/app.db` (SQLite) | Your user account's file permissions |
| Telegram / IMAP / Google client secrets | `.env` in the project folder | Git-ignored; keep it private |
| Demo mode data | Memory only | Gone when the app stops |

The app listens on `127.0.0.1` only, so nothing else on your network can reach it. It
also refuses requests from other websites open in your browser.
