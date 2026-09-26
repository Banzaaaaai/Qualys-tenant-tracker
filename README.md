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

   Optional behaviour is set under **... > Variables**; defaults suit most tenants:

   | Variable | Default | Purpose |
   | --- | --- | --- |
   | `TENANT_IDENTIFIER` | derived from `QUALYS_API_URL` | Friendly tenant label shown in emails |
   | `INITIAL_RUN_NOTIFY` | `false` | Email the inventory on the baseline run |
   | `CHECK_PUBLIC_RELEASES` | `false` | Also scan *unchanged* modules for newly announced public versions |
   | `STALE_AFTER_DAYS` | `3` | Days without a successful check before alerting |

   The [operations reference](docs/operations.md#configuration) lists every variable.

3. Enable Actions and allow the tracker workflow to write repository contents. Branch rules must allow its state commits.
4. Run **Qualys Tenant Version Tracker** manually with `send_test_email` enabled to check SMTP.
5. Run normally to establish the baseline. The first run records inventory without sending email by default.

The default schedule is 08:45 and 16:45 Europe/Amsterdam. Both seasonal UTC triggers are configured. A run claims the most recent due slot however late GitHub starts it -- delays of several hours are normal -- and a completed slot suppresses the duplicate trigger. Manual workflow runs bypass the schedule guard.

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

## What the email says

An email is sent when a module version changes on your tenant. Each module gets a card addressed to you in the second person -- *your tenant*, not *this tenant* -- with two sections:

- **Capabilities available on your tenant now.** Features Qualys documents for the version your tenant actually reports, with a link to the official release note.
- **Latest publicly announced version.** What Qualys has published, and a badge relating it to your tenant.

The wording is deliberately careful: the tracker never says your tenant is behind. Qualys performs phased rollouts, so a newer public version only means *"Qualys has publicly announced version X, but X has not yet been detected on your tenant."* A tenant running a version Qualys has not documented yet is reported as its own state rather than being called current.

Public release notes are labelled more coarsely than the portal API reports versions -- `FIM 4.9.4` against a tenant's `4.9.4.0-38`. A tenant version that extends a published label is treated as the same release, so modules are not reported as running ahead of the very release they are on.

Standalone *"Qualys announced X"* emails for modules whose tenant version has **not** moved are opt-in via `CHECK_PUBLIC_RELEASES=true`. By default this tracker reports tenant changes only.

Sample output: [change notification](docs/sample-email-change-notification.html), [public-release announcement](docs/sample-email-public-release-announcement.html). Badge meanings and every email type are in the [operations reference](docs/operations.md#how-email-works).

## Module map

A tenant reports module versions under short API codes (`CS`, `ISL`, `QWEB_PC`). Correlating one with its public release notes needs two things: the product's real name, for the email, and where its notes live, to search.

Product name alone is not enough to locate them. **"Cloud Agent" names two different products** on the documentation site -- the agent binary under `/ca/release-notes/cloud_agent/` (at 6.x) and the Cloud Agent application under `/ca/release-notes/ca_application/` (at 2.x). A tenant's `CA` module reports the application, so matching on the name alone correlates it against the wrong product entirely. `Patch Management` appears under several paths too.

So a module is located by **URL path first**, and narrowed by product name only where one path genuinely hosts several products: `/pm/release-notes/patch_management/` carries both *Isolation* and *Patch Management*, which is how `ISL` and `PM` are told apart even though they report the same version. A mapped module never falls back to name matching, because that would reintroduce the collisions the map exists to prevent.

An unmapped module falls back to its product-name hint, then to its own code, and finally to "release notes: not found". That last outcome is correct, not a gap: several modules publish no public release notes at all.

The table lives in `src/qualys_tracker/release_notes.py` as `MODULE_SOURCES` and `MODULE_FULL_NAMES`; paths there are matched as substrings of the full documentation URL. `tests/test_release_notes.py` asserts this section stays in step with them.

| Code | Product name | Release-notes path | Notes |
| --- | --- | --- | --- |
| `AV2` | VMDR | `/vm/release-notes/mergedProjects/qualys_vmdr_rn/` | Shares the VMDR notes with `QWEB_VM`. **Version line unconfirmed:** the tenant reports `0.1.0` against a published 2.x line, so its own release note will not be found. |
| `CA` | Cloud Agent | `/ca/release-notes/ca_application/` |  |
| `CERTVIEW` | Certificate View | `/certview/release-notes/certview/` |  |
| `CLOUDVIEW` | TotalCloud | `/tc/release-notes/totalcloud/` |  |
| `CM` | Continuous Monitoring | - | Publishes no public release notes. |
| `CONN` | Connectors | `/conn/release-notes/connector/` |  |
| `CS` | Container Security | `/cs/release-notes/container_security/` | Sensor notes under `/cs-sensor/` are a separate version line and are deliberately not included. |
| `EDR` | Endpoint Protection and Response | `/edr/release-notes/endpoint_detection_and_response/` | Not present on this tenant. |
| `ETM` | Enterprise TruRisk Management | `/etm/release-notes/etm/` |  |
| `FIM` | File Integrity Monitoring | `/fim/release-notes/file_integrity_monitoring/` |  |
| `ICS` | Industrial Control System | - | No public release notes found. |
| `IOC` | Indicator of Compromise | - | No public release notes found. |
| `ISL` | Isolation (part of Cloud Agent) | `/pm/release-notes/patch_management/` (*Isolation*) |  |
| `ISPM` | Identity Security Posture Management | - | No public release notes found. |
| `ITAM` | CyberSecurity Asset Management | `/csam/release-notes/cybersecurity_asset_management/` |  |
| `MDS` | Web Malware Detection | - | Publishes no public release notes. |
| `MROC` | Managed Risk Operations Center | `/mroc/release-notes/managed_risk_operations_center/` | Not present on this tenant. |
| `MTG` | Mitigation (part of Cloud Agent) | - | Unmapped. No product named "Mitigation" exists on the index. |
| `OCA` | Industrial OCA | `/oca/release-notes/oca/` |  |
| `PA` | Policy Audit | `/vm/release-notes/mergedProjects/qualys_pa/` | Not present on this tenant. |
| `PM` | Patch Management | `/pm/release-notes/patch_management/` (*Patch Management*) | **Unconfirmed.** The public *Patch Management* line tops out at 3.x; the tenant reports `4.1.0.0-281`, which matches *Isolation* 4.1. |
| `PortalApplication` | - | - | Internal portal build; no public release notes. |
| `PS` | Network Passive Sensor | `/ps/release-notes/ps/` |  |
| `QFLOW` | Qualys Flow | `/qflow/release-notes/qflow/` |  |
| `QGS` | Qualys Gateway Service | `/qgs/release-notes/qgs/` | Tenant reports `2.9.0-16` against a public 3.16.1; verify these are the same numbering line. |
| `QUESTIONNAIRE` | Security Assessment Questionnaire | - | Matched by product name only; the source table's `/car/` path belongs to `SM`. |
| `QUESTIONNAIRE_V2` | Security Assessment Questionnaire | - | Unmapped. No matching public line identified. |
| `QWEB_PC` | Policy Audit | `/vm/release-notes/mergedProjects/qualys_pa/` |  |
| `QWEB_VM` | Vulnerability Management | `/vm/release-notes/mergedProjects/qualys_vmdr_rn/` |  |
| `SA` | Virtual Scanner Appliance | `/scanner/release-notes/virtual_scanner/` | Not present on this tenant. |
| `SECURITY_ANALYTICS` | - | - | No public release notes found. |
| `SEM` | Secure Enterprise Mobility | `/vmdr-mobile/release-notes/vmdr_mobile/` |  |
| `SM` | Script Manager | `/car/release-notes/car/` |  |
| `SSC` | PCI SSC | - | Unmapped. The only public PCI line is *PCI Compliance*, which does not match. |
| `TA` | Total AI | `/ta/release-notes/total_ai/` |  |
| `TC` | TotalCloud | `/tc/release-notes/totalcloud/` | **Unconfirmed.** Shares TotalCloud with `CLOUDVIEW`, whose version matches the public line exactly; `TC` reports `1.4.0-16`. |
| `THREAT_PROTECT` | Threat Protect | - | Publishes no public release notes. |
| `UD` | Unified Dashboard | `/ud/release-notes/unified_dashboard/` |  |
| `WAF` | Web Application Firewall | - | Publishes no public release notes. |
| `WAF_V3` | Web Application Firewall | - | Publishes no public release notes. |
| `WAS` | Total Application Security | `/tas/release-notes/total_app_sec/` |  |

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
