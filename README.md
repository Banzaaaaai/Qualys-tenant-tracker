# Qualys Tenant Version Tracker

Monitors **your specific Qualys tenant** via the Qualys Version API and
emails you whenever Qualys upgrades, changes, or otherwise reports a
new version for any module in your subscription.

This is a standalone project. It does not depend on, import, or scrape
any public Qualys release-tracking repository. It answers one
question, and only one question:

> **What version is currently installed/reported by my Qualys tenant,
> and did it change?**

If you're looking for "what did Qualys publicly release," that's a
different (and deliberately separate) concern -- see
[Future extensibility](#future-extensibility).

## Table of contents

- [Architecture](#architecture)
- [Qualys API endpoint](#qualys-api-endpoint)
- [Required Qualys account permissions](#required-qualys-account-permissions)
- [Configuration](#configuration)
- [How email works](#how-email-works)
- [How the baseline works](#how-the-baseline-works)
- [How change detection works](#how-change-detection-works)
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
           ▼            ▼
        no email    HTML email                           src/qualys_tracker/notifier.py
                       │                                  raises on failure -- never
                       ▼                                  silently swallowed
                    User

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

Everything is read in `src/qualys_tracker/config.py`; nothing here is
ever printed to the console or logged (see [Security
considerations](#security-considerations)).

## How email works

`src/qualys_tracker/notifier.py` sends HTML email over SMTP
(`smtplib`, STARTTLS by default) so it works with any transactional
SMTP provider (SendGrid, Mailgun, Amazon SES, Office 365, Gmail app
passwords, etc.) -- just point `SMTP_HOST`/`SMTP_PORT` at that
provider's relay and use its credentials.

Four distinct email types exist, and they never get confused with each
other because each has a distinct subject and an explicit label in the
body:

1. **Change notification** -- one or more versions actually changed.
2. **Initial baseline created** -- only sent if `INITIAL_RUN_NOTIFY=true`.
3. **Manual / forced notification** -- `force_notify`, full inventory,
   clearly marked so it's never mistaken for a real upgrade.
4. **Staleness alert** -- the tracker itself has been failing to reach
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
| `[Qualys Tenant] Tracker has not succeeded in over N day(s)` | The tracker itself is unhealthy -- Qualys API has been failing, not that your tenant is fine |

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
│   ├── config.py                          # All env-var configuration
│   └── main.py                             # Orchestration + console output
├── tests/                                   # pytest suite (see below)
├── docs/sample-email-change-notification.html
├── tenant_snapshot.json      # created by the tracker (git-committed by CI)
├── version_history.json      # created by the tracker (git-committed by CI)
├── run_log.json               # created by the tracker (git-committed by CI)
├── tenant_report.{json,md}     # created by the tracker (git-committed by CI)
├── requirements.txt / requirements-dev.txt
├── pyproject.toml
└── pytest.ini
```

Test coverage (`pytest -q`, 60+ tests):

- `test_parser.py` -- valid response, missing `ServiceResponse`/`data`/
  `Portal-Version`, no version fields, multiple fields, unknown/new
  module, malformed JSON shape, `responseCode != SUCCESS`.
- `test_comparison.py` -- first run, no changes, one/many changed,
  new module, removed module, mixed combination, non-semver strings.
- `test_state.py` -- valid/corrupted/missing snapshot, atomic update,
  first-seen/last-changed bookkeeping, history append-only + duplicate
  prevention.
- `test_notifier.py` -- one/many changed, initial baseline, forced
  notification labeling, send failure raises.
- `test_api.py` -- 200, 401, 403, 429, 500, timeout, retry success,
  retry exhaustion.
- `test_monitoring.py`, `test_scheduling.py` -- staleness suppression
  windows; DST-safe schedule guard across winter/summer.
- `test_main_integration.py` -- full dry-run of `main.run()` against
  the sanitized fixture, exercising the whole pipeline end to end.

## Future extensibility

This first version deliberately does not correlate tenant versions
against Qualys's public release notes. A future second data source
(a public release tracker) could feed a separate "tenant release gap"
correlation engine, but that is out of scope here and must not couple
this project to any other repository. Today, a version change is
detected purely from an actual observed change in
`GET /qps/rest/portal/version` -- nothing is inferred from what Qualys
has publicly announced.
