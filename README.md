# Qualys Tenant Version Tracker

Monitors **your specific Qualys tenant** via the Qualys Version API and
emails you whenever Qualys upgrades, changes, or otherwise reports a
new version for any module in your subscription -- and, for every such
change, correlates it against Qualys's own official public release
notes so the email also answers *what that version actually gives you*
and *whether Qualys has already announced something newer that hasn't
reached this tenant yet*.

This is a standalone project. It does not depend on, import, or scrape
any third-party Qualys release-tracking repository, blog, or
aggregator -- only `qualys.com`/`docs.qualys.com` are ever contacted.
It is built around three distinct facts, kept clearly separate end to
end (see [Public release-notes correlation](#public-release-notes-correlation)):

1. **What Qualys has publicly announced** -- the official release notes.
2. **What your tenant is actually running** -- `/qps/rest/portal/version`.
3. **What capabilities you have available right now** -- the release
   notes that correspond to your tenant's *current* version, not the
   newest one that exists in the world.

> **What version is currently installed/reported by my Qualys tenant,
> did it change, what does that version give me, and has Qualys
> already announced something newer that hasn't reached this tenant
> yet?**

## Table of contents

- [Architecture](#architecture)
- [Qualys API endpoint](#qualys-api-endpoint)
- [Required Qualys account permissions](#required-qualys-account-permissions)
- [Configuration](#configuration)
- [How email works](#how-email-works)
- [How the baseline works](#how-the-baseline-works)
- [How change detection works](#how-change-detection-works)
- [Public release-notes correlation](#public-release-notes-correlation)
- [Running locally](#running-locally)
- [Running manually in GitHub Actions](#running-manually-in-github-actions)
- [Resetting the baseline](#resetting-the-baseline)
- [Interpreting notifications](#interpreting-notifications)
- [Staleness monitoring](#staleness-monitoring)
- [Failure behavior](#failure-behavior)
- [DST / timezone handling](#dst--timezone-handling)
- [Troubleshooting](#troubleshooting)
- [Security considerations](#security-considerations)
- [Project structure](#project-structure)
- [Future extensibility](#future-extensibility)

## Architecture

```text
                 ┌─────────────────────────┐
                 │     Qualys Tenant       │
                 │ /qps/rest/portal/version│
                 └────────────┬────────────┘
                              │
                              ▼
                 ┌─────────────────────────┐
                 │     API Collector       │   src/qualys_tracker/api.py
                 │ retry / timeout / auth  │   3-5 retries, exp. backoff,
                 └────────────┬────────────┘   30s timeout, no retry on 401/403
                              │
                              ▼
                 ┌─────────────────────────┐
                 │   Response Validator    │   src/qualys_tracker/parser.py
                 │  (responseCode, data,   │   Bad/empty response -> ParserError,
                 │   Portal-Version, ...)  │   never treated as "0 modules"
                 └────────────┬────────────┘
                              │
                              ▼
                 ┌─────────────────────────┐
                 │   Version Normalizer    │   parser.py: "<X>-VERSION" -> module "<X>"
                 └────────────┬────────────┘   every module handled dynamically
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
        ┌─────────────────┐       ┌─────────────────┐
        │ Current State   │       │ Version History │
        │ snapshot.json   │       │ history.json    │   src/qualys_tracker/state.py
        └────────┬────────┘       └─────────────────┘   src/qualys_tracker/history.py
                 │                         ▲             atomic writes, corrupted
                 ▼                         │             snapshot never overwritten
        ┌─────────────────┐                │
        │ Change Detector │────────────────┘             src/qualys_tracker/comparison.py
        └────────┬────────┘                              old != new (string compare)
                 │
          changes detected?
             /        \
           no          yes
           │            │
           │            ▼
           │   ┌────────────────────────┐
           │   │  Release Notes Engine    │   release_notes.py (fetch+parse;
           │   │ tenant capabilities +    │   qualys.com/docs.qualys.com only)
           │   │ latest public version    │   release_intelligence.py orchestrates,
           │   └──────────────┬───────────┘   never lets a lookup failure block
           │                │                 tenant tracking above
           │                ▼
           │     release_notes_cache.json
           │                │
           ▼                ▼
        no email    Enhanced HTML email                  src/qualys_tracker/notifier.py

  Every run, regardless of outcome, is also recorded in run_log.json
  (src/qualys_tracker/run_log.py) and checked for staleness
  (src/qualys_tracker/monitoring.py).
```

## Qualys API endpoint

```
GET <QUALYS_API_URL>/qps/rest/portal/version
Accept: application/json
```

This returns the Portal version plus every `<Module>-VERSION` field
currently visible to your subscription, e.g.:

```json
{
  "ServiceResponse": {
    "data": [{ "Portal-Version": { "FIM-VERSION": "1.5.1", "WAS-VERSION": "6.0.0.0" } }],
    "responseCode": "SUCCESS",
    "count": 1
  }
}
```

The parser (`src/qualys_tracker/parser.py`) never hardcodes a module
list -- it dynamically processes every key ending in `-VERSION`, so a
module Qualys adds in the future is picked up automatically the next
run, without a code change.

`QUALYS_API_URL` is configurable specifically so this works against
any Qualys platform (US, EU, India, private cloud, etc.) -- just point
it at your platform's API gateway host, e.g.
`https://qualysapi.qg1.apps.qualys.com` or
`https://qualysapi.qg2.apps.qualys.com`.

## Required Qualys account permissions

Use a dedicated Qualys API/service account rather than a personal one.
The Version API endpoint only requires:

- **Portal access** with API access enabled ("API access" permission
  in the Qualys user's role).
- No module-specific write permissions are needed -- this is a
  read-only GET.

Rotate this account's password periodically and store it only in
GitHub Secrets (see below) -- never in code or in the repo.

## Configuration

All configuration is via environment variables / GitHub
Secrets & Variables. Nothing has a hardcoded default that includes a
real hostname or credential.

### Secrets (sensitive -- set under **Settings > Secrets and variables > Actions > Secrets**)

| Secret | Required | Purpose |
| --- | --- | --- |
| `QUALYS_API_URL` | yes | Base URL of your Qualys platform, e.g. `https://qualysapi.qg1.apps.qualys.com` |
| `QUALYS_USERNAME` | yes | Qualys API service account username |
| `QUALYS_PASSWORD` | yes | Qualys API service account password |
| `SMTP_HOST` | yes | SMTP server (or transactional email provider's SMTP relay) |
| `SMTP_PORT` | no (default 587) | SMTP port |
| `SMTP_USERNAME` | no | SMTP auth username |
| `SMTP_PASSWORD` | no | SMTP auth password |
| `EMAIL_FROM` | yes | From address |
| `EMAIL_TO` | yes | Comma-separated list of recipients |

### Variables (non-sensitive -- set under **... > Variables**)

| Variable | Default | Purpose |
| --- | --- | --- |
| `TENANT_IDENTIFIER` | derived from `QUALYS_API_URL` host | Friendly label shown in emails |
| `INITIAL_RUN_NOTIFY` | `false` | Send an email on the very first (baseline) run |
| `STALE_AFTER_DAYS` | `3` | Days without a successful check before alerting |
| `STALE_ALERT_SUPPRESSION_DAYS` | `3` | Minimum gap between repeated staleness alerts |
| `TRACKER_TIMEZONE` | `Europe/Amsterdam` | IANA timezone used for the schedule guard |
| `TRACKER_LOCAL_RUN_TIMES` | `08:45,16:45` | Local times the tracker is expected to run |
| `RUN_LOG_MAX_ENTRIES` | `500` | Retention cap for `run_log.json` |
| `QUALYS_RELEASE_NOTES_URL` | `https://www.qualys.com/documentation/release-notes` | Official Qualys release-notes index to correlate against |
| `CHECK_PUBLIC_RELEASES` | `true` | Also scan unchanged modules for newly-announced public versions (spec section 21) |
| `RELEASE_NOTES_CACHE_TTL_DAYS` | `1` | How long a "latest public version" lookup is trusted before re-checking |
| `PUBLIC_RELEASE_NOTIFICATION` | `true` | Allow the standalone scan above to actually send an email (vs. silently caching) |

Everything is read in `src/qualys_tracker/config.py`; nothing here is
ever printed to the console or logged (see [Security
considerations](#security-considerations)).

## How email works

`src/qualys_tracker/notifier.py` sends HTML email over SMTP
(`smtplib`, STARTTLS by default) so it works with any transactional
SMTP provider (SendGrid, Mailgun, Amazon SES, Office 365, Gmail app
passwords, etc.) -- just point `SMTP_HOST`/`SMTP_PORT` at that
provider's relay and use its credentials.

Five distinct email types exist, and they never get confused with each
other because each has a distinct subject and an explicit label in the
body:

1. **Change notification** -- one or more versions actually changed.
   When a changed/new module's release notes could be correlated, this
   email is enhanced with capability and rollout-status sections -- see
   [Public release-notes correlation](#public-release-notes-correlation).
2. **Public release announcement** -- Qualys announced a newer version
   for a module whose *tenant* version did not change this run.
3. **Initial baseline created** -- only sent if `INITIAL_RUN_NOTIFY=true`.
4. **Manual / forced notification** -- `force_notify`, full inventory,
   clearly marked so it's never mistaken for a real upgrade.
5. **Staleness alert** -- the tracker itself has been failing to reach
   Qualys for too long.

A rendered example lives at
[`docs/sample-email-change-notification.html`](docs/sample-email-change-notification.html).
The tables use plain inline styles (no external CSS, no flexbox/grid)
specifically so they render correctly in Outlook.

**Email failures are never silent.** If SMTP delivery fails, the run
is still considered successful for state purposes (the snapshot and
history you already computed are saved -- see [Failure
behavior](#failure-behavior)), but the run log records
`"email_sent": false`, the console output prints `Notification: FAILED`,
and the GitHub Actions job itself is marked failed so it surfaces in
your notifications/inbox for the repo.

## How the baseline works

On the very first run (no `tenant_snapshot.json` exists yet):

1. The current API response is treated as the baseline, not as "every
   module just changed."
2. The snapshot and an initial history entry per module are saved.
3. By default (`INITIAL_RUN_NOTIFY=false`), **no email is sent** --
   you will not get an email listing 30 "new modules" on day one.
4. Set `INITIAL_RUN_NOTIFY=true` if you do want a one-time "baseline
   created" email listing the starting inventory.

## How change detection works

`src/qualys_tracker/comparison.py` compares the previous snapshot's
`modules` map against the freshly parsed API response, per module
name, and classifies each into exactly one bucket:

- **NEW_MODULE** -- present now, absent from the stored snapshot.
- **VERSION_CHANGED** -- present in both, but `old_version != new_version`.
- **MODULE_REMOVED** -- present in the stored snapshot, absent now.
- unchanged -- present in both with the same version string.

Comparison is a **plain string inequality**, deliberately. Qualys
version strings don't follow one consistent scheme --
`4.9.4`, `2.12.0-12-101`, `1.44.5-9`, `2.33.0.0-SNAPSHOT-1` all show up
in the wild -- so no numeric ordering is attempted. The goal is
reliable *change* detection, not "is this newer or older."

## Public release-notes correlation

Whenever a module is `VERSION_CHANGED` or `NEW_MODULE` (never for
`MODULE_REMOVED` -- there is no current version to look up), the
tracker additionally asks the **official** Qualys release notes at
`QUALYS_RELEASE_NOTES_URL` (default
`https://www.qualys.com/documentation/release-notes`; only that host
and `docs.qualys.com` are ever contacted -- no blogs, no aggregators,
no third-party mirrors) two questions:

1. What does the tenant's *current* version actually give you?
2. Has Qualys already announced something newer that hasn't reached
   this tenant yet?

**Matching strategy** (`src/qualys_tracker/release_notes.py`): the
index page is fetched and parsed for real `<module, version, url>`
entries -- no URL pattern is ever guessed. A short module code (e.g.
`FIM`) is matched to its full product name via a small, documented,
*best-effort* hint table (`MODULE_NAME_HINTS`); an unmapped module
falls back to matching its own code as a substring, and if nothing
matches, the result is honestly "release notes: not found" rather than
a guess. An exact version match is required before a release page is
fetched and its title/date/features parsed.

**Ordering is never guessed either.** `src/qualys_tracker/version_compare.py`
tokenizes version strings and only claims an ordering when every
token pair is confidently comparable (numeric-vs-numeric, or
zero-padding on a purely-numeric trailing segment); anything else
(`2.33.0.0` vs `2.33.0.0-SNAPSHOT-1`, differing letter suffixes like
`RC` vs `BETA`) is reported as "unable to safely determine ordering"
-- `UpgradeStatus.VERSION_ORDER_UNKNOWN` -- instead of a guess.

**Status wording is deliberately careful.** The tracker never says
"your tenant is behind." Per the spec this was built against: Qualys
may perform phased rollouts, so a newer public version simply means
*"Qualys has publicly announced version X, but X has not yet been
detected on this tenant"* -- not that anything is wrong or overdue.

**A lookup failure never blocks tenant tracking.** If the release-notes
site is unreachable, the module's snapshot/history update is
unaffected; the enhanced email section for that module just says
"Temporarily unavailable," and `run_log.json` records
`"release_notes_lookup_success": false` -- distinct from
`"api_success"`, which reflects the Qualys tenant API only.

**Caching** (`release_notes_cache.json`, via
`src/qualys_tracker/release_cache.py`): a specific module+version's
release notes never change once published, so those entries are
cached indefinitely. The "latest public version" per module *does*
need periodic re-checking, so that entry expires after
`RELEASE_NOTES_CACHE_TTL_DAYS` (default 1 day). Within a single run,
the (large) release-notes index page itself is only ever fetched once
and shared across every module being checked.

**Standalone announcements without a tenant change** (`CHECK_PUBLIC_RELEASES`,
default `true`): once per run, every module whose version did *not*
change this run is also compared against the latest public release. If
Qualys has announced something newer, a separate "Public release
announcement" email is sent -- but only once per distinct newly-found
version (deduplicated via `public_release_notifications.json`), so an
outstanding announcement doesn't re-email on every run until either
the tenant catches up or Qualys announces something even newer. Set
`PUBLIC_RELEASE_NOTIFICATION=false` to keep the scan (and its cache
warming) running without ever emailing about it.

## Running locally

You do not need real Qualys credentials to develop or test this
project -- a sanitized fixture is included at
`tests/fixtures/valid_response.json`.

```bash
python -m venv .venv
source .venv/bin/activate        # or .venv\Scripts\activate on Windows
pip install -r requirements-dev.txt
pytest -q
```

To dry-run the full pipeline against the fixture (no network, no real
SMTP server) see the snippet used to validate this repo, or simply run
the test suite -- `tests/test_main_integration.py` exercises the exact
same `main.run()` entry point end to end using a faked API client and a
faked SMTP transport, covering: first-run baseline, no-change run,
change detected -> email + history, idempotent reprocessing,
`--force-notify`, `--reset-baseline` confirmation gating, and a
malformed response that must NOT overwrite the snapshot.

To run against a **real** tenant locally:

```bash
export QUALYS_API_URL="https://qualysapi.qg1.apps.qualys.com"
export QUALYS_USERNAME="svc-account"
export QUALYS_PASSWORD="********"
export SMTP_HOST="smtp.yourprovider.com"
export EMAIL_FROM="tracker@yourdomain.com"
export EMAIL_TO="you@yourdomain.com"
pip install .
python -m qualys_tracker.main
```

## Running manually in GitHub Actions

Open **Actions > Qualys Tenant Version Tracker > Run workflow** and
choose:

- `force_notify: true` -- sends the current inventory even with no
  changes. Useful for confirming email delivery works end-to-end
  against your real SMTP provider without waiting for a real Qualys
  change.
- `send_test_email: true` -- sends a minimal test email (no tenant
  data at all) and exits immediately. The fastest way to check SMTP
  credentials.

## Resetting the baseline

`reset_baseline` is deliberately hard to trigger by accident: the
input is a free-text field, and the run aborts with exit code `2`
unless you type the literal string `CONFIRM` (exact case). There is no
checkbox.

When confirmed, the existing `tenant_snapshot.json` is renamed to
`tenant_snapshot.json.backup-<timestamp>` (never deleted) before the
run continues as a fresh baseline. `version_history.json` and
`run_log.json` are untouched, so your history of past changes survives
a baseline reset.

## Interpreting notifications

| Email subject pattern | Meaning |
| --- | --- |
| `[Qualys Tenant] <Module> upgraded X → Y` | Exactly one module changed |
| `[Qualys Tenant] N module versions changed` | Multiple modules changed (new/changed/removed combined) |
| `[Qualys Tenant] Initial baseline created (...)` | First run, `INITIAL_RUN_NOTIFY=true` |
| `[Qualys Tenant] Manual / forced notification (...)` | You (or a teammate) ran `force_notify` -- **not** a real change |
| `[Qualys Tenant] <Module> <version> publicly announced (not yet on this tenant)` | Tenant version unchanged; Qualys announced something newer (see [Public release-notes correlation](#public-release-notes-correlation)) |
| `[Qualys Tenant] Tracker has not succeeded in over N day(s)` | The tracker itself is unhealthy -- Qualys API has been failing, not that your tenant is fine |

Within a change-notification email, look for the badge next to each
module's "Latest publicly announced version" section:

| Badge | Meaning |
| --- | --- |
| `CURRENT` | The tenant is already on the latest publicly announced version |
| `PUBLICLY ANNOUNCED — NOT YET DETECTED ON THIS TENANT` | Qualys has announced a newer version; this tenant hasn't received it (phased rollouts are normal -- this is not a fault) |
| `NOT FOUND` | The module couldn't be matched against the release-notes site at all (see `MODULE_NAME_HINTS`) |
| `ORDERING UNKNOWN` | Multiple public versions were found but couldn't be safely ordered -- reported honestly rather than guessed |
| `TEMPORARILY UNAVAILABLE` | The release-notes site couldn't be reached this run; tenant tracking above was unaffected |

## Staleness monitoring

If the tracker has not had a **successful** API check in more than
`STALE_AFTER_DAYS` (default 3), it sends a staleness alert on the next
run:

```text
Qualys Tenant Version Tracker has not successfully retrieved tenant
version information for 3 days.

Last successful check: 2026-09-07T08:45:03Z
Last error: HTTP 503
```

To avoid spamming you every run while an outage persists, another
staleness alert is only sent after `STALE_ALERT_SUPPRESSION_DAYS`
(default 3) have passed since the last one. This is computed entirely
from `run_log.json` (`src/qualys_tracker/monitoring.py`) -- no extra
state file is needed.

## Failure behavior

| Scenario | Snapshot | History | Change email | Job status |
| --- | --- | --- | --- | --- |
| Qualys API unreachable/5xx after retries | untouched | untouched | none | **failed** (+ staleness alert if past threshold) |
| 401/403 | untouched | untouched | none | **failed** immediately, no retries |
| Response fails validation (missing fields, empty `Portal-Version`, wrong `responseCode`) | untouched | untouched | none | **failed** |
| Existing `tenant_snapshot.json` is corrupted on disk | untouched | untouched | none | **failed** (won't guess a fresh baseline) |
| API succeeds, versions changed, email send fails | **updated** | **updated** | attempted, failed | **failed** (loudly, but state is not lost) |
| API succeeds, versions changed, release-notes site unreachable | **updated** | **updated** | sent (correlation section says "Temporarily unavailable") | success |
| API succeeds, no changes | updated (`last_checked` only) | untouched | none | success |

The one rule underlying all of this: **a bad read must never cause a
bad write, and a failed notification must never cause a lost change.**
Since the snapshot/history are already updated when only the email
fails, simply re-running with `force_notify: true` will resend the
current inventory without fabricating a duplicate "change".

## DST / timezone handling

GitHub Actions cron is UTC-only, but the desired schedule is expressed
in local time (`TRACKER_TIMEZONE`, default `Europe/Amsterdam`,
08:45/16:45). Instead of editing the cron expressions twice a year,
[`tracker.yml`](.github/workflows/tracker.yml) registers **both** UTC
times each local slot can map to (CET and CEST), and
`src/qualys_tracker/scheduling.py` uses the real IANA tzdata (via
`zoneinfo`) to compute the actual local time at execution and no-ops
the trigger that doesn't currently match (within a configurable
tolerance, default 90 minutes). `workflow_dispatch` runs always bypass
this guard, so manual runs are never silently skipped.

This is more robust than hardcoding UTC offsets because it re-derives
the correct offset from the timezone database on every run, and it
survives Qualys/GitHub Actions cron drift without any manual
intervention around the DST transition dates.

## Troubleshooting

- **"Configuration error: QUALYS_API_URL is required"** -- a required
  secret is missing. Check **Settings > Secrets and variables >
  Actions**.
- **Job succeeded but I got no email on a real change** -- check the
  step output for `Notification: FAILED` and the reason; also check
  `run_log.json`'s latest entry for `"email_sent": false`.
- **Getting a staleness alert but Qualys looks fine in the UI** -- this
  alert is about the *tracker's* ability to reach the API (network,
  auth, or transient Qualys errors), not about your tenant's health.
  Check the `error` field of the most recent failed `run_log.json`
  entry.
- **The scheduled run seems to "do nothing"** -- check whether it was
  outside the configured local run window (see [DST / timezone
  handling](#dst--timezone-handling)); this is expected for the
  "off-season" cron trigger and is not a failure.
- **I want to see the exact current inventory without waiting for a
  change** -- run the workflow manually with `force_notify: true`, or
  read `tenant_report.md` / `tenant_report.json` in the repo, updated
  every successful run.

## Security considerations

- Credentials (`QUALYS_USERNAME`, `QUALYS_PASSWORD`, `SMTP_PASSWORD`,
  etc.) are read only from environment variables populated by GitHub
  Secrets. They are never written to any JSON state file, never
  included in exception messages, and never printed to console output
  (`src/qualys_tracker/api.py` and `notifier.py` only ever log status
  codes/exception types, not headers or credentials).
- Full API response bodies are never logged -- only structured,
  already-normalized summaries (module/version pairs) appear in
  console output and in `tenant_snapshot.json`.
- Use a dedicated Qualys API service account with only the permissions
  described [above](#required-qualys-account-permissions).
- `tenant_snapshot.json`, `version_history.json`, and `run_log.json`
  are committed to the repository by the workflow so state survives
  between ephemeral runners. If your Qualys module inventory itself is
  sensitive, keep this repository private.
- The release-notes client (`src/qualys_tracker/release_notes.py`)
  only ever contacts `qualys.com`/`docs.qualys.com` (enforced by an
  explicit host allowlist, not just by convention) and only performs
  read-only `GET` requests. Its content is Qualys's own public
  documentation, so caching it in `release_notes_cache.json` and
  committing that file is safe -- unlike the tenant files above, it
  contains no tenant-specific data at all, only public release text.

## Project structure

```text
.
├── .github/workflows/
│   ├── tracker.yml          # Scheduled + manual execution
│   └── ci.yml                # Runs pytest on push/PR
├── src/qualys_tracker/
│   ├── api.py                 # HTTP client: retry, backoff, timeout, auth handling
│   ├── parser.py               # Validates + dynamically normalizes *-VERSION fields
│   ├── models.py                # Shared dataclasses/enums
│   ├── comparison.py             # old != new change detection
│   ├── state.py                    # Snapshot load/build/atomic save
│   ├── history.py                   # Append-only version_history.json
│   ├── run_log.py                    # Bounded run_log.json
│   ├── monitoring.py                  # Staleness detection + suppression
│   ├── notifier.py                     # HTML email construction + SMTP send
│   ├── report.py                        # tenant_report.json / .md
│   ├── scheduling.py                     # DST-robust local-time guard
│   ├── version_compare.py                 # Safe, best-effort version ordering
│   ├── release_notes.py                    # Qualys release-notes HTTP client + HTML parsing
│   ├── release_cache.py                     # release_notes_cache.json (atomic, TTL for "latest public")
│   ├── release_intelligence.py               # Orchestrates release_notes.py + release_cache.py
│   ├── config.py                              # All env-var configuration
│   └── main.py                                 # Orchestration + console output
├── tests/                                        # pytest suite (see below)
├── docs/sample-email-change-notification.html
├── tenant_snapshot.json      # created by the tracker (git-committed by CI)
├── version_history.json      # created by the tracker (git-committed by CI)
├── run_log.json               # created by the tracker (git-committed by CI)
├── tenant_report.{json,md}     # created by the tracker (git-committed by CI)
├── release_notes_cache.json    # created by the tracker (git-committed by CI; public data only)
├── public_release_notifications.json  # dedup state for standalone announcements
├── requirements.txt / requirements-dev.txt
├── pyproject.toml
└── pytest.ini
```

Test coverage (`pytest -q`, 120+ tests):

- `test_parser.py` -- valid response, missing `ServiceResponse`/`data`/
  `Portal-Version`, no version fields, multiple fields, unknown/new
  module, malformed JSON shape, `responseCode != SUCCESS`.
- `test_comparison.py` -- first run, no changes, one/many changed,
  new module, removed module, mixed combination, non-semver strings.
- `test_state.py` -- valid/corrupted/missing snapshot, atomic update,
  first-seen/last-changed bookkeeping, history append-only + duplicate
  prevention.
- `test_notifier.py` -- one/many changed, initial baseline, forced
  notification labeling, send failure raises, plus the enhanced
  release-intelligence sections (current/newer-available/not-found/
  order-unknown/lookup-failed badges, consolidated multi-module email,
  standalone public-release announcement).
- `test_api.py` -- 200, 401, 403, 429, 500, timeout, retry success,
  retry exhaustion.
- `test_monitoring.py`, `test_scheduling.py` -- staleness suppression
  windows; DST-safe schedule guard across winter/summer.
- `test_version_compare.py` -- numeric/zero-padding/SNAPSHOT/differing-
  suffix ordering, `latest_version()` confident vs. unable-to-determine.
- `test_release_notes.py` -- index/detail HTML parsing (real-structure
  fixtures), module-name-hint matching and fallback, API-companion-page
  filtering, host allowlist, HTTP 503/retry/exhaustion.
- `test_release_cache.py` -- missing/corrupted cache, put/get round
  trip, atomic save.
- `test_release_intelligence.py` -- exact match, not-found, cache hit/
  TTL-expiry/refresh, failed refresh keeps the prior good cache entry,
  lookup-failure never raises, standalone announcement dedup.
- `test_main_integration.py` -- full dry-run of `main.run()` against
  the sanitized fixture (release-notes correlation disabled here to
  stay focused on tenant tracking), exercising the whole pipeline.
- `test_main_release_integration.py` -- the same, with release-notes
  correlation enabled and mocked: tenant change + CURRENT, tenant
  change + newer-public-available, release-notes site down (tenant
  tracking unaffected), standalone announcement + its dedup, and
  `PUBLIC_RELEASE_NOTIFICATION=false` suppressing it.

## Future extensibility

Rollout-delay analytics ("version 4.9.4 was announced on day X and
reached this tenant on day Y, N days later") are a natural next step
now that both dates are known, but aren't computed yet -- see the
`ModuleReleaseIntelligence` model and `version_history.json` entries,
which already carry what such a feature would need.

This project still does not depend on or import any third-party
Qualys release-tracking repository, blog, or aggregator -- only
`qualys.com`/`docs.qualys.com` are ever contacted, and every fact
shown in a notification traces back to either the tenant's own
`GET /qps/rest/portal/version` response or Qualys's own published
release notes.
