# Bookics status checks

Keeps https://bookics.statuspage.io/ truthful. Every 5 minutes GitHub Actions fetches:

| Check | URL | Component |
|---|---|---|
| Home page | https://bookics.com.au/ | Website and booking pages |
| Demo booking page | https://bookics.com.au/book/bookics-demo/ | Website and booking pages |
| Booking widget script | https://bookics.com.au/embed.js | Website and booking pages |
| Staff app sign-in | https://bookics.com.au/app/login | Staff app (iPhone, iPad and web) |

A check passes on HTTP 200 with the expected text in the body.

- **Two failed runs in a row** set the component to Major outage and email the alert address once.
- **The next passing run** sets it back to Operational, but only if this monitor took it down. A status set by a person (an incident, maintenance) is never overwritten.
- Recovery is not emailed. Nothing is emailed while all is well.
- "Booking emails and sign-in codes" and "Online payments" have no automatic check. Set them by hand in Statuspage when needed.

It runs on GitHub, not on the Bookics hosting, so it keeps working when the hosting is down.

## Secrets (Settings, Secrets and variables, Actions)

`STATUSPAGE_API_KEY`, `RESEND_API_KEY` and `ALERT_TO`. Where each comes from, and how to rotate it, is in the internal credentials register, not here. Set them by stdin from the password manager, never by pasting.

## State

Consecutive failure counts are carried between runs in the Actions cache (`state/state.json`). If the cache is lost, counting starts again from zero, which at worst delays an outage flag by one run.
