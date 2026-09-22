"""Client for the official Qualys public release notes.

Isolated on purpose (spec: "do not mix web scraping/search logic into
comparison.py, api.py, notifier.py"). This module only knows how to:

- fetch and parse the release-notes index page into a flat list of
  (product name, version, url) entries,
- fetch and parse one individual release-note page into a title,
  release date, and a short feature list,
- match a module's short API code (e.g. "FIM") to a *documented,
  best-effort* set of full product-name substrings used on the site.

It never guesses a URL pattern -- both the index and the per-release
pages are discovered by fetching real pages and parsing real HTML, and
if the real site's markup doesn't match what's expected here, this
module fails closed (raises ReleaseNotesError) rather than fabricating
a result. Only https://www.qualys.com and https://docs.qualys.com are
ever contacted -- no third-party mirrors or aggregators.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .version_compare import is_same_release, version_tokens as _version_tokens

DEFAULT_INDEX_URL = "https://www.qualys.com/documentation/release-notes"
ALLOWED_HOSTS = {"www.qualys.com", "qualys.com", "docs.qualys.com"}

# www.qualys.com sits behind a WAF that returns 403 to any request whose
# User-Agent contains the substring "qualys" (case-insensitive) -- almost
# certainly an anti-impersonation rule. That includes the obvious
# self-identifying values ("qualys-tenant-version-tracker") AND any UA that
# merely cites this project's repo URL, because the repo name contains it.
# A 403 is not retried, so it fails the shared index fetch and every module
# in the run reports RELEASE_NOTE_LOOKUP_FAILED.
#
# So: identify honestly as a bot, but do NOT put "qualys" in this string.
# Verified against the live site -- see tests/test_release_notes.py.
USER_AGENT = "Mozilla/5.0 (compatible; tenant-version-tracker/1.0)"

_VERSION_TAIL_RE = re.compile(r"([0-9][0-9A-Za-z_.\-]*)\s*$")
_MAX_FEATURES = 12
_MAX_FEATURE_DESC_LEN = 400
_MAX_LABEL_LEN = 60

# Best-effort mapping from a Qualys API "*-VERSION" short code to the
# full product name(s) used on the public release-notes site. This is
# deliberately not exhaustive -- new/renamed Qualys modules are common,
# and an unmapped module simply falls back to matching its own code
# against product names, then to "release notes: not found" (Strategy
# 4) rather than a guessed mapping. Extend this table as gaps are found.
#
# Sourced in part from a user-maintained abbreviation reference; entries
# below marked (verified) replace earlier guesses that turned out wrong
# once checked against a real tenant (e.g. CM is Continuous Monitoring,
# not Certificate View -- CERTVIEW is its own separate module/code).
MODULE_NAME_HINTS: dict[str, list[str]] = {
    "FIM": ["File Integrity Monitoring"],
    "WAS": ["Web Application Scanning"],
    "TAS": ["TotalAppSec", "Web Application Scanning"],
    "WAF": ["Web Application Firewall"],
    "VM": ["Vulnerability Management", "Enterprise TruRisk"],
    "VMDR": ["Vulnerability Management, Detection and Response", "Vulnerability Management", "Enterprise TruRisk"],
    "VMDR_MOBILE": ["VMDR Mobile"],
    "PC": ["Policy Compliance"],
    "PA": ["Policy Audit"],
    "PCI": ["PCI Compliance"],
    "PM": ["Patch Management"],
    "CS": ["Container Security", "Qscanner"],
    "CA": ["Cloud Agent"],
    "CM": ["Continuous Monitoring"],  # (verified) not Certificate View
    "CERTVIEW": ["Certificate View"],
    "CONTINUOUSMONITORING": ["Continuous Monitoring"],
    "EDR": ["Endpoint Detection and Response"],
    "CAR": ["Custom Assessment and Remediation"],
    "QUESTIONNAIRE": ["Security Assessment Questionnaire", "Questionnaire"],
    "SAQ": ["Security Assessment Questionnaire"],
    "TC": ["TotalCloud"],
    "MDS": ["Web Malware Detection"],  # (verified) not Multi-Vector EDR
    "PS": ["Network Passive Sensor"],
    "QF": ["Qualys Flow"],
    "QFLOW": ["Qualys Flow"],
    "QGS": ["Qualys Gateway Service"],
    "UD": ["Unified Dashboard"],
    "GAV": ["Global AssetView"],
    "CSAM": ["CyberSecurity Asset Management"],
    "THREAT_PROTECT": ["Threat Protect"],
    "SCA": ["Security Configuration Assessment"],
}


# --- Authoritative module map (supplied by the tenant owner) -----------
#
# Matching a module to its release notes by product-name substring is not
# reliable: "Cloud Agent" names BOTH the agent binary notes
# (/en/ca/.../cloud_agent/, at 6.x) and the Cloud Agent application notes
# (/en/ca/.../ca_application/, at 2.x, which is what a tenant's CA module
# actually reports). Likewise "Patch Management" appears under several
# paths. The URL path is the thing that disambiguates, so a module is
# located by path first and only narrowed by product name where one path
# genuinely hosts several products (pm/patch_management carries both
# Isolation and Patch Management).
#
# A module absent from this table falls back to MODULE_NAME_HINTS and then
# to its own code, and finally to "release notes: not found" -- which is
# the correct outcome for the modules that have no public notes at all
# (verified: Threat Protect, WAF, Web Malware Detection and Continuous
# Monitoring publish none).


@dataclass(frozen=True)
class ModuleSource:
    """Where one module's public release notes live.

    `url_contains` is matched against the index entry's URL.
    `product_name`, when set, further narrows to one product on a path
    that hosts more than one.
    """

    url_contains: str
    product_name: str | None = None


MODULE_SOURCES: dict[str, list[ModuleSource]] = {
    "CA": [ModuleSource("/ca/release-notes/ca_application/")],
    "CERTVIEW": [ModuleSource("/certview/release-notes/certview/")],
    "CLOUDVIEW": [ModuleSource("/tc/release-notes/totalcloud/")],
    "CONN": [ModuleSource("/conn/release-notes/connector/")],
    "CS": [ModuleSource("/cs/release-notes/container_security/")],
    "EDR": [ModuleSource("/edr/release-notes/endpoint_detection_and_response/")],
    "ETM": [ModuleSource("/etm/release-notes/etm/")],
    "FIM": [ModuleSource("/fim/release-notes/file_integrity_monitoring/")],
    # One path, two products -- narrow by name or ISL and PM collide.
    "ISL": [ModuleSource("/pm/release-notes/patch_management/", "Isolation")],
    "PM": [ModuleSource("/pm/release-notes/patch_management/", "Patch Management")],
    "ITAM": [ModuleSource("/csam/release-notes/cybersecurity_asset_management/")],
    "MROC": [ModuleSource("/mroc/release-notes/managed_risk_operations_center/")],
    "OCA": [ModuleSource("/oca/release-notes/oca/")],
    "PA": [ModuleSource("/vm/release-notes/mergedProjects/qualys_pa/")],
    "PS": [ModuleSource("/ps/release-notes/ps/")],
    "QFLOW": [ModuleSource("/qflow/release-notes/qflow/")],
    "QGS": [ModuleSource("/qgs/release-notes/qgs/")],
    "QWEB_PC": [ModuleSource("/vm/release-notes/mergedProjects/qualys_pa/")],
    "QWEB_VM": [ModuleSource("/vm/release-notes/mergedProjects/qualys_vmdr_rn/")],
    "SA": [ModuleSource("/scanner/release-notes/virtual_scanner/")],
    "SEM": [ModuleSource("/vmdr-mobile/release-notes/vmdr_mobile/")],
    "SM": [ModuleSource("/car/release-notes/car/")],
    "TA": [ModuleSource("/ta/release-notes/total_ai/")],
    "TC": [ModuleSource("/tc/release-notes/totalcloud/")],
    "UD": [ModuleSource("/ud/release-notes/unified_dashboard/")],
    "WAS": [ModuleSource("/tas/release-notes/total_app_sec/")],
}

# Display names for the email ("TC (TotalCloud)"). Includes modules with
# no public release notes -- naming a module and locating its notes are
# separate concerns.
MODULE_FULL_NAMES: dict[str, str] = {
    "AV2": "VMDR",
    "CA": "Cloud Agent",
    "CERTVIEW": "Certificate View",
    "CLOUDVIEW": "TotalCloud",
    "CM": "Continuous Monitoring",
    "CONN": "Connectors",
    "CS": "Container Security",
    "EDR": "Endpoint Protection and Response",
    "ETM": "Enterprise TruRisk Management",
    "FIM": "File Integrity Monitoring",
    "ICS": "Industrial Control System",
    "IOC": "Indicator of Compromise",
    "ISL": "Isolation (part of Cloud Agent)",
    "ISPM": "Identity Security Posture Management",
    "ITAM": "CyberSecurity Asset Management",
    "MDS": "Web Malware Detection",
    "MROC": "Managed Risk Operations Center",
    "MTG": "Mitigation (part of Cloud Agent)",
    "OCA": "Industrial OCA",
    "PA": "Policy Audit",
    "PM": "Patch Management",
    "PS": "Network Passive Sensor",
    "QFLOW": "Qualys Flow",
    "QGS": "Qualys Gateway Service",
    "QUESTIONNAIRE": "Security Assessment Questionnaire",
    "QUESTIONNAIRE_V2": "Security Assessment Questionnaire",
    "QWEB_PC": "Policy Audit",
    "QWEB_VM": "Vulnerability Management",
    "SA": "Virtual Scanner Appliance",
    "SEM": "Secure Enterprise Mobility",
    "SM": "Script Manager",
    "SSC": "PCI SSC",
    "TA": "Total AI",
    "TC": "TotalCloud",
    "THREAT_PROTECT": "Threat Protect",
    "UD": "Unified Dashboard",
    "WAF": "Web Application Firewall",
    "WAF_V3": "Web Application Firewall",
    "WAS": "Total Application Security",
}


class ReleaseNotesError(Exception):
    """A release-note lookup could not be completed.

    Never allowed to propagate into tenant tracking -- see
    release_intelligence.py, which always catches this.
    """


@dataclass(frozen=True)
class IndexEntry:
    product_name: str
    version_text: str
    url: str


@dataclass
class ReleaseDetail:
    title: str | None
    release_date: str | None
    url: str
    features: list[tuple[str, str]]


def candidate_product_names(module: str) -> list[str]:
    hints = MODULE_NAME_HINTS.get(module.upper())
    if hints:
        return hints
    return [module]


def _matches_module(entry: IndexEntry, module: str) -> bool:
    sources = MODULE_SOURCES.get(module.upper())
    if sources:
        # An explicitly mapped module is located ONLY by its mapped path.
        # Falling back to name matching here would reintroduce exactly the
        # collisions the path mapping exists to prevent.
        return any(
            source.url_contains in entry.url
            and (
                source.product_name is None
                or source.product_name.lower() == entry.product_name.lower()
            )
            for source in sources
        )
    names = [n.lower() for n in candidate_product_names(module)]
    product = entry.product_name.lower()
    return any(name in product or product in name for name in names)


def find_entries_for_module(entries: list[IndexEntry], module: str) -> list[IndexEntry]:
    return [e for e in entries if _matches_module(e, module)]


def find_entry_for_version(entries: list[IndexEntry], version: str) -> IndexEntry | None:
    """Find the release note describing tenant `version`.

    An exact match wins. Failing that, Qualys often publishes the note
    under a coarser label than the portal reports ("FIM 4.9.4" for a
    tenant running "4.9.4.0-38"), so the most specific entry that the
    tenant version extends is accepted as the same release. If two
    equally specific labels would match, nothing is returned -- an
    ambiguous match is a guess, and a guess is worse than "not found".
    """
    for entry in entries:
        if entry.version_text == version:
            return entry

    matches = [e for e in entries if is_same_release(e.version_text, version)]
    if not matches:
        return None
    best = max(matches, key=lambda e: len(_version_tokens(e.version_text)))
    depth = len(_version_tokens(best.version_text))
    equally_specific = {
        (e.version_text, e.url) for e in matches if len(_version_tokens(e.version_text)) == depth
    }
    return best if len(equally_specific) == 1 else None


def parse_index(html: str) -> list[IndexEntry]:
    """Parse the release-notes index page into a flat list of entries.

    Entries whose link text or URL marks them as the API-reference
    companion document (e.g. "... 10.40 API") are skipped -- they
    describe the same release as their non-API counterpart and would
    otherwise register as a spurious duplicate "version".
    """
    soup = BeautifulSoup(html, "html.parser")
    entries: list[IndexEntry] = []

    for li in soup.find_all("li", class_="releasenotes-item"):
        a = li.find("a", href=True)
        if not a:
            continue
        link_text = a.get_text(strip=True)
        href = a["href"]
        if href.rstrip("/").lower().endswith("_api.htm") or link_text.lower().endswith("api"):
            continue

        version_match = _VERSION_TAIL_RE.search(link_text)
        if not version_match:
            continue
        version_text = version_match.group(1)

        tag_div = li.find("div", title=True)
        product_name = tag_div["title"].strip() if tag_div and tag_div.get("title") else None
        if not product_name:
            product_name = link_text[: version_match.start()].strip()
        if not product_name:
            continue

        entries.append(IndexEntry(product_name=product_name, version_text=version_text, url=href))

    return entries


# Bump whenever parse_release_detail's OUTPUT changes -- different fields,
# different feature titles, or different description text for the same page.
# `releases` cache entries never expire (a published release note doesn't
# change), so without this a parser fix would never reach an already-cached
# module: the "Applicable for:" descriptions survived the parser fix until
# this invalidation was added. See release_cache.load_cache.
PARSE_FORMAT_VERSION = 3


def _is_label_paragraph(text: str) -> bool:
    """True for a short heading-like paragraph that labels what follows.

    These carry no information on their own, e.g. "Applicable for:".
    """
    return text.endswith(":") and len(text) <= _MAX_LABEL_LEN


def full_product_name(module: str) -> str | None:
    """The public product name for an API short code, e.g. TC -> TotalCloud.

    Returns None for a module with no curated mapping, so callers render
    the bare code rather than inventing an expansion.
    """
    name = MODULE_FULL_NAMES.get(module.upper())
    if not name:
        hints = MODULE_NAME_HINTS.get(module.upper())
        if not hints:
            return None
        name = hints[0]
    name = name.strip()
    # An "expansion" identical to the code itself tells the reader nothing.
    return name if name.upper() != module.upper() else None


def parse_release_detail(html: str, url: str) -> ReleaseDetail:
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else None

    date_el = soup.find(class_="ReleaseDate")
    release_date = date_el.get_text(strip=True) if date_el else None

    features: list[tuple[str, str]] = []
    for h2 in soup.find_all("h2"):
        feature_title = h2.get_text(strip=True)
        if not feature_title:
            continue
        description = ""
        for sibling in h2.find_next_siblings():
            if sibling.name == "h2":
                break
            if sibling.name == "p":
                text = sibling.get_text(strip=True)
                if not text or _is_label_paragraph(text):
                    # Qualys release notes open each feature with label
                    # paragraphs ("Applicable for:", "Benefit",
                    # "Prerequisites") whose value lives in the element
                    # AFTER them. Taking the first non-empty <p> made every
                    # feature's description render as the bare word
                    # "Applicable for:". Keep scanning for real prose.
                    continue
                description = text
                break
        if len(description) > _MAX_FEATURE_DESC_LEN:
            description = description[:_MAX_FEATURE_DESC_LEN].rsplit(" ", 1)[0] + "…"
        features.append((feature_title, description))
        if len(features) >= _MAX_FEATURES:
            break

    if title is None and not features:
        raise ReleaseNotesError(f"Could not parse a release-note page structure at {url}")

    return ReleaseDetail(title=title, release_date=release_date, url=url, features=features)


class QualysReleaseNotesClient:
    def __init__(
        self,
        index_url: str = DEFAULT_INDEX_URL,
        timeout_seconds: int = 30,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
        sleep_fn=time.sleep,
    ) -> None:
        self._index_error = None
        self._deadline = time.monotonic() + 60
        self._index_url = index_url
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._backoff_base = backoff_base_seconds
        self._sleep = sleep_fn
        self._index_entries: list[IndexEntry] | None = None  # memoized per client instance

    def fetch_index_entries(self) -> list[IndexEntry]:
        """Fetch + parse the index page. Memoized for this client's lifetime
        so a single run never re-downloads the (large) index page once per
        module -- see release_intelligence.py, which shares one client
        across all modules checked in a run."""
        if self._index_error is not None:
            raise self._index_error
        if self._index_entries is None:
            try:
                html = self._get(self._index_url)
                self._index_entries = parse_index(html)
            except ReleaseNotesError as exc:
                self._index_error = exc
                raise
        return self._index_entries

    def fetch_release_detail(self, url: str) -> ReleaseDetail:
        html = self._get(url)
        return parse_release_detail(html, url)

    def set_budget(self, seconds: int) -> None:
        self._deadline = time.monotonic() + seconds

    def _remaining(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise ReleaseNotesError("Release-note lookup time budget exhausted")
        return remaining

    def _pause(self, seconds: float) -> None:
        self._sleep(min(seconds, self._remaining()))

    def _get(self, url: str) -> str:
        host = urlparse(url).netloc
        if host not in ALLOWED_HOSTS:
            raise ReleaseNotesError(f"Refusing to fetch from non-official host: {host}")

        last_exception: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = requests.get(
                    url,
                    headers={"Accept": "text/html", "User-Agent": USER_AGENT},
                    timeout=min(self._timeout, self._remaining()),
                )
            except requests.RequestException as exc:
                last_exception = exc
                if attempt == self._max_retries:
                    raise ReleaseNotesError(
                        f"Network error fetching {url}: {type(exc).__name__}"
                    ) from exc
                self._pause(self._backoff_base * (2 ** (attempt - 1)))
                continue

            if response.status_code == 429 or 500 <= response.status_code < 600:
                last_exception = ReleaseNotesError(f"HTTP {response.status_code}")
                if attempt == self._max_retries:
                    raise ReleaseNotesError(
                        f"HTTP {response.status_code} persisted fetching {url}"
                    )
                self._pause(self._backoff_base * (2 ** (attempt - 1)))
                continue

            if not response.ok:
                raise ReleaseNotesError(f"HTTP {response.status_code} fetching {url}")

            return response.text

        raise ReleaseNotesError(f"Retries exhausted fetching {url}: {last_exception}")
