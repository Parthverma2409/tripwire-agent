# Tripwire privacy policy

Tripwire is a personal, self-hosted app. Each person runs their own copy on their own
computer with their own Google OAuth client. There is no Tripwire server and no
Tripwire company. The developer never receives your data.

## What Tripwire accesses

| Google permission | What it is used for |
|---|---|
| `gmail.readonly` | Reading booking emails (tickets, hotel stays, cab and table reservations, appointments) to find new, changed or cancelled bookings. Tripwire never sends, deletes or modifies email. |
| `calendar.events` | Adding, moving and (only after you approve) deleting the matching events on the calendar you choose. |

## Where your data goes

- **Stays on your computer.** Extracted booking details, the activity log and the list of
  emails already read are kept in a local SQLite database in `~/.booking-sync/`. Your
  Google sign-in token is kept there too, encrypted with AES-256-GCM. The encryption key
  lives in your operating system's keyring.
- **Google.** Tripwire talks directly to Google's Gmail and Calendar APIs.
- **Telegram (only if you turn it on).** Approval questions, including the booking they are
  about, are sent to your own Telegram bot and pass through Telegram's servers.
- **Local AI (only if you turn it on).** Ollama runs on your own machine; nothing leaves it.
- **Browser voice input (only if you use it).** Your browser's speech recognition may send
  audio to Google or Microsoft. Local Whisper avoids this.

Tripwire has no analytics, no ads and no tracking, and does not sell, share or transfer
your data to anyone.

## Google API Services User Data Policy

Tripwire's use and transfer of information received from Google APIs adheres to the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements. Google user data is used only to provide the
calendar-sync features described above and is never used for advertising or to train AI
models.

## Deleting your data

1. In Tripwire, open **Connections** → Google → **Disconnect**. This deletes the stored token.
2. Delete the `~/.booking-sync/` folder to remove everything else.
3. Optionally revoke access at <https://myaccount.google.com/permissions>.

## Contact

Open an issue at <https://github.com/Parthverma2409/tripwire-agent/issues>.
