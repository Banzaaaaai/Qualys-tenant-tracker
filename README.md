# Qualys Tenant Version Tracker

Track the module versions reported by your Qualys tenant and receive email when they change. Official Qualys release notes add context about capabilities and newer public releases.

The tracker runs locally or through GitHub Actions. It keeps a tenant snapshot, change history, reports, and a durable notification queue. Python 3.11 or newer is required.

## Quick start with GitHub Actions

1. Fork or clone this repository where tenant inventory may be stored. The workflow commits tenant state and generates recovery artifacts.
2. Under **Settings > Secrets and variables > Actions**, configure these secrets:

   | Secret | Purpose |
   | --- | --- |
   | `QUALYS_API_URL` | Your Qualys platform HTTPS API URL |
   | `QUALYS_USERNAME`, `QUALYS_PASSWORD` | Dedicated API account |
   | `SMTP_HOST` | SMTP relay hostname |
   | `EMAIL_FROM`, `EMAIL_TO` | Sender and comma-separated recipients |
   | `SMTP_USERNAME`, `SMTP_PASSWORD` | Relay credentials, if required |
   | `SMTP_PORT` | Optional; defaults to 587 with STARTTLS |

3. Enable Actions and allow the tracker workflow to write repository contents. Branch rules must allow its state commits.
4. Run **Qualys Tenant Version Tracker** manually with `send_test_email` enabled to check SMTP.
5. Run normally to establish the baseline. The first run records inventory without sending email by default.

The default schedule is 08:45 and 16:45 Europe/Amsterdam. Both seasonal UTC triggers are configured. Early triggers are skipped and completed local slots suppress duplicates. Manual workflow runs bypass the schedule guard.

## Run locally

```sh
python -m venv .venv
# Activate .venv using your shell, then:
python -m pip install .
```

Set the same environment variables in your shell; `.env` files are not loaded automatically. Then run:

```sh
qualys-tracker --ignore-schedule-guard
```

Use `TRACKER_STATE_DIR` for a separate state directory. One process at a time may write to it.

Fetch and compare without changing state, sending email, looking up release notes, or pinging the heartbeat:

```sh
qualys-tracker --dry-run
```

Render an inventory email completely offline, without API or SMTP credentials:

```sh
qualys-tracker --preview-email email-preview.html --snapshot tenant_snapshot.json
```

Open the generated HTML file in a browser. It contains tenant inventory.

## Reliability

- A recovery journal commits snapshot, history, and pending notifications together. Interrupted writes replay before the next check.
- Failed emails stay queued and retry on subsequent checks. Run logs record outcomes by notification type. SMTP delivery is at least once: a crash immediately after acceptance can cause a duplicate with the same Message-ID.
- Invalid snapshots and history stop processing and remain available for recovery.
- Release-note lookups share a configurable time budget. Failed index lookups are cached for the rest of the run.
- Actions uploads state and reset backups as a 30-day recovery artifact before pushing. Rejected pushes retry after rebasing; conflicting state fails visibly and is never force-pushed.

See [recovery and monitoring](docs/reliability.md) and the [operations reference](docs/operations.md) for full configuration, email types, and troubleshooting.

## Detect a stopped workflow

Configure an independent monitor that alerts when HTTPS success pings stop arriving. Store its endpoint in the `TRACKER_HEARTBEAT_URL` Actions secret. The workflow pings only after a successful check and successful state persistence. Skipped triggers and test emails do not refresh it.

The monitor must run outside this workflow. Provisioning its endpoint and alert destination is required to activate this optional integration. See [heartbeat setup](docs/reliability.md#external-heartbeat).

## Development

```sh
python -m pip install . -r requirements-dev.txt
python -m pytest -q
```

CI tests Python 3.11 through 3.14 on Linux and Windows, builds a wheel, and invokes its installed CLI outside the source tree. Tests mock HTTP and SMTP traffic.
