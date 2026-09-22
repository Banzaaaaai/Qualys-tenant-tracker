# Reliability and recovery

## Persistent state

`pending_transaction.json` contains the intended next snapshot, complete history, and pending email queue. It is written atomically before installing each target atomically. The journal is removed only after all targets are installed; any remaining journal replays at startup. A process lock prevents simultaneous local writers; Actions also serializes runs.

Do not edit state while the tracker is running. Recover an existing journal through a normal run before using `--dry-run`. Corrupt history, outbox, or snapshots stop processing. Preserve a copy and restore from a known-good commit or artifact before rerunning.

The outbox contains rendered email content, event IDs, message type, attempt counts, and last delivery errors, without SMTP credentials. Public-release deduplication now records versions durably queued; successful delivery removes pending messages. Existing state remains compatible.

Delivery is at least once. A crash after SMTP acceptance but before saving completion can resend the same Message-ID; email systems do not guarantee deduplication. Partial recipient rejection retries the whole message, so accepted recipients may see duplicates. Retries use the current SMTP settings and recipients. Disabling public-release notifications prevents new announcements from being queued; it does not cancel already queued messages.

## Recovering a failed Actions push

Each run uploads `tracker-recovery-<run-id>-<attempt>` before committing state, with 30-day retention. It includes the snapshot, history, reports, cache, outbox, public-release state, and any journal or reset backups.

1. Stop overlapping manual runs and download the failed run's artifact.
2. Compare it with the current branch; do not overwrite newer state blindly.
3. Restore the matching snapshot, history, outbox, and public-release state together. Include any journal, which contains the pending transaction.
4. Commit restored state and run manually to recover and retry pending mail.

Rejected pushes retry up to three times, rebasing over unrelated remote changes. Conflicts fail visibly; the artifact remains available and state is never force-pushed. Reset backups are uploaded even though they are excluded from commits.

## Scheduling

Slots are identified by timezone, local date, and local time. Checks run once a slot is due, however late the runner starts, up to a catch-up bound (default 24 hours). GitHub routinely delivers scheduled runs hours late, so this bound must stay well above the observed delay; a narrow window silently no-ops every scheduled run. A successful run log entry suppresses that slot's later triggers. Failed checks may retry; manual runs bypass the guard.

Changing the timezone or local run times also requires updating the workflow's UTC cron triggers. The guard does not create new triggers. Retain enough run-log entries to cover the current day's slots.

## External heartbeat

Provision an independent dead-man monitor with an HTTPS endpoint and an alert destination. For the default schedule, the longest planned gap is 16 hours; a 24-hour missed-ping threshold gives initial room for scheduler delays. Adjust it to your operational needs.

Store the endpoint in the `TRACKER_HEARTBEAT_URL` Actions secret. Secret path/query tokens are supported and never logged. Only 2xx is accepted, redirects are disabled, and timeout is 10 seconds. The workflow pings after successful checking and state persistence. Ping failure fails the step; skipped slots, test emails, and failed persistence do not ping.

Local scheduling uses the same environment variable. The CLI pings after a successful check and email delivery; failure is logged and returns nonzero. Leave it unset to disable. Existing API staleness emails cannot detect a workflow that never starts.

## Additional configuration

| Variable | Default | Allowed values |
| --- | --- | --- |
| `QUALYS_HTTP_TIMEOUT_SECONDS` | 30 | 1-300 seconds |
| `QUALYS_HTTP_MAX_RETRIES` | 5 | 1-10 total attempts |
| `RELEASE_NOTES_BUDGET_SECONDS` | 60 | 1-300 seconds per check |
| `RELEASE_NOTES_CACHE_TTL_DAYS` | 1 | Zero or greater |
| `TRACKER_SCHEDULE_GUARD_TOLERANCE_MINUTES` | 1440 | 1-1440 minutes after due time |
| `RUN_LOG_MAX_ENTRIES` | 500 | Positive integer |
| `STALE_AFTER_DAYS` | 3 | Positive integer |
| `STALE_ALERT_SUPPRESSION_DAYS` | 3 | Zero or greater |

Booleans accept `true/false`, `yes/no`, `on/off`, and `1/0`; invalid values fail validation. Timezones must be valid IANA names and run times valid hours and minutes. SMTP ports must be 1-65535. API URLs require HTTPS without embedded credentials, query, or fragment.

The release-note budget bounds new requests and sleeps and caps request timeouts by remaining budget. A requests timeout is a network-operation timeout, not a hard interruption of an in-flight download. Actions also imposes a 15-minute job limit. Lookup failures do not prevent tenant state updates or delivery.
