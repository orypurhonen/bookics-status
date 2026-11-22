#!/usr/bin/env python3
"""Bookics status checks.

Fetches the public Bookics pages, and after two consecutive failed runs sets the
matching Statuspage component to Major outage. When the checks pass again it sets
the component back to Operational, but only if this monitor was the one that took
it down (a status a person set by hand is never overwritten).

Emails ALERT_TO through Resend only when something goes down, or when this
monitor cannot do its job. Recovery is not announced.

State (consecutive failure counts) lives in state/state.json, carried between
runs by the Actions cache.

Exit code: 0 when the monitor ran (whatever it found), 1 when it could not run.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

PAGE_ID = os.environ["STATUSPAGE_PAGE_ID"]
API_KEY = os.environ["STATUSPAGE_API_KEY"]
RESEND_KEY = os.environ.get("RESEND_API_KEY", "")
ALERT_TO = os.environ.get("ALERT_TO", "")
ALERT_FROM = "Bookics status <noreply@bookics.com.au>"
STATE_FILE = "state/state.json"
FAILS_BEFORE_OUTAGE = 2
UA = "Mozilla/5.0 (compatible; bookics-status-check/1.0)"

COMPONENTS = {
    "website": os.environ["COMPONENT_WEBSITE"],
    "app": os.environ["COMPONENT_APP"],
}

# name, url, text that must appear in the body, component key
CHECKS = [
    ("Home page", "https://bookics.com.au/status-check-test-404/", "<title>Bookics", "website"),
    ("Demo booking page", "https://bookics.com.au/book/bookics-demo/", "<title>Book a table", "website"),
    ("Booking widget script", "https://bookics.com.au/embed.js", "currentScript", "website"),
    ("Staff app sign-in", "https://bookics.com.au/app/login", "<title>Sign in | Bookics", "app"),
]

COMPONENT_NAMES = {
    "website": "Website and booking pages",
    "app": "Staff app (iPhone, iPad and web)",
}


def check(url, marker):
    """Return (ok, detail). One retry inside the run for a dropped connection."""
    last = ""
    for attempt in range(2):
        req = urllib.request.Request(url + ("&" if "?" in url else "?") + "status_check=" + str(int(time.time())),
                                     headers={"User-Agent": UA, "Cache-Control": "no-cache"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read(400000).decode("utf-8", "replace")
                if r.status != 200:
                    last = "HTTP %d" % r.status
                elif marker not in body:
                    last = "HTTP 200 but the expected content was missing"
                else:
                    return True, "HTTP 200"
        except urllib.error.HTTPError as e:
            last = "HTTP %d" % e.code
        except Exception as e:  # timeout, DNS, TLS, reset
            last = type(e).__name__ + ": " + str(e)[:200]
        if attempt == 0:
            time.sleep(10)
    return False, last


def sp(method, path, body=None):
    url = "https://api.statuspage.io/v1/pages/%s%s" % (PAGE_ID, path)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "OAuth " + API_KEY,
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def email(subject, text):
    if not (RESEND_KEY and ALERT_TO):
        print("ALERT NOT SENT (RESEND_API_KEY or ALERT_TO missing): " + subject, file=sys.stderr)
        return False
    req = urllib.request.Request("https://api.resend.com/emails", method="POST",
                                 data=json.dumps({"from": ALERT_FROM, "to": [ALERT_TO],
                                                  "subject": subject, "text": text}).encode(),
                                 headers={"Authorization": "Bearer " + RESEND_KEY,
                                          "Content-Type": "application/json",
                                          "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("alert sent: %s (%s)" % (subject, json.loads(r.read().decode()).get("id")))
            return True
    except Exception as e:
        print("ALERT FAILED TO SEND: %s: %s" % (subject, e), file=sys.stderr)
        return False


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1)


def main():
    state = load_state()
    comps = state.setdefault("components", {})
    run_url = os.environ.get("RUN_URL", "")

    results = {k: [] for k in COMPONENTS}
    for name, url, marker, key in CHECKS:
        ok, detail = check(url, marker)
        print("%-4s %-24s %s  %s" % ("OK" if ok else "FAIL", name, url, detail))
        results[key].append((name, url, ok, detail))

    try:
        live = {c["id"]: c["status"] for c in sp("GET", "/components")}
    except Exception as e:
        save_state(state)
        today = time.strftime("%Y-%m-%d")
        if state.get("monitor_error_alerted") != today:
            state["monitor_error_alerted"] = today
            save_state(state)
            email("Bookics status monitor cannot reach Statuspage",
                  "The Bookics status checks ran but could not read the status page: %s\n\n"
                  "The page is not being kept up to date until this is fixed. Check the "
                  "Statuspage API key (see the credentials register) and the repo secret "
                  "STATUSPAGE_API_KEY.\n\n%s" % (e, run_url))
        print("ERROR: cannot read Statuspage components: %s" % e, file=sys.stderr)
        return 1

    for key, cid in COMPONENTS.items():
        rs = results[key]
        failed = [r for r in rs if not r[2]]
        st = comps.setdefault(cid, {"fails": 0, "set_by_monitor": False})
        current = live.get(cid)
        if current is None:
            print("ERROR: component %s (%s) not found on the page" % (key, cid), file=sys.stderr)
            return 1

        if failed:
            st["fails"] += 1
            print("%s: %d consecutive failed run(s), page shows %s" % (key, st["fails"], current))
            if st["fails"] >= FAILS_BEFORE_OUTAGE and current not in ("major_outage", "under_maintenance"):
                sp("PATCH", "/components/" + cid, {"component": {"status": "major_outage"}})
                st["set_by_monitor"] = True
                print("%s: set to major_outage" % key)
                lines = "\n".join("- %s (%s): %s" % (n, u, d) for n, u, ok, d in failed)
                email("Bookics is down: %s" % COMPONENT_NAMES[key],
                      "%s has failed %d checks in a row, so the status page now shows "
                      "Major outage for it.\n\n%s\n\nFix the site; the page goes back to "
                      "Operational by itself on the next passing check.\n"
                      "Status page: https://bookics.statuspage.io/\n%s"
                      % (COMPONENT_NAMES[key], st["fails"], lines, run_url))
        else:
            st["fails"] = 0
            if st.get("set_by_monitor"):
                if current == "major_outage":
                    sp("PATCH", "/components/" + cid, {"component": {"status": "operational"}})
                    print("%s: recovered, set back to operational" % key)
                else:
                    print("%s: recovered, but the page shows %s (set by a person), left alone" % (key, current))
                st["set_by_monitor"] = False

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
