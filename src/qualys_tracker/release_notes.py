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

DEFAULT_INDEX_URL = "https://www.qualys.com/documentation/release-notes"
ALLOWED_HOSTS = {"www.qualys.com", "qualys.com", "docs.qualys.com"}

_VERSION_TAIL_RE = re.compile(r"([0-9][0-9A-Za-z_.\-]*)\s*$")
_MAX_FEATURES = 12
_MAX_FEATURE_DESC_LEN = 400

# Best-effort mapping from a Qualys API "*-VERSION" short code to the
# full product name(s) used on the public release-notes site. This is
# deliberately not exhaustive -- new/renamed Qualys modules are common,
# and an unmapped module simply falls back to matching its own code
# against product names, then to "release notes: not found" (Strategy
# 4) rather than a guessed mapping. Extend this table as gaps are found.
MODULE_NAME_HINTS: dict[str, list[str]] = {
    "FIM": ["File Integrity Monitoring"],
    "WAS": ["Web Application Scanning"],
    "WAF": ["Web Application Firewall"],
    "VM": ["Vulnerability Management", "Enterprise TruRisk"],
    "VMDR": ["Vulnerability Management", "Enterprise TruRisk"],
    "PC": ["Policy Compliance"],
    "PCI": ["PCI Compliance"],
    "PM": ["Patch Management"],
    "CS": ["Container Security"],
    "CA": ["Cloud Agent"],
    "CM": ["Certificate View", "CertView"],
    "EDR": ["Endpoint Detection and Response"],
    "CAR": ["Custom Assessment and Remediation"],
    "QUESTIONNAIRE": ["Security Assessment Questionnaire", "Questionnaire"],
    "SAQ": ["Security Assessment Questionnaire"],
    "TC": ["TotalCloud"],
    "MDS": ["Multi-Vector EDR", "MDS"],
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
    names = [n.lower() for n in candidate_product_names(module)]
    product = entry.product_name.lower()
    return any(name in product or product in name for name in names)


def find_entries_for_module(entries: list[IndexEntry], module: str) -> list[IndexEntry]:
    return [e for e in entries if _matches_module(e, module)]


def find_entry_for_version(entries: list[IndexEntry], version: str) -> IndexEntry | None:
    for entry in entries:
        if entry.version_text == version:
            return entry
    return None


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
                if text:
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
        if self._index_entries is None:
            html = self._get(self._index_url)
            self._index_entries = parse_index(html)
        return self._index_entries

    def fetch_release_detail(self, url: str) -> ReleaseDetail:
        html = self._get(url)
        return parse_release_detail(html, url)

    def _get(self, url: str) -> str:
        host = urlparse(url).netloc
        if host not in ALLOWED_HOSTS:
            raise ReleaseNotesError(f"Refusing to fetch from non-official host: {host}")

        last_exception: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = requests.get(
                    url,
                    headers={"Accept": "text/html", "User-Agent": "qualys-tenant-version-tracker"},
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                last_exception = exc
                if attempt == self._max_retries:
                    raise ReleaseNotesError(
                        f"Network error fetching {url}: {type(exc).__name__}"
                    ) from exc
                self._sleep(self._backoff_base * (2 ** (attempt - 1)))
                continue

            if response.status_code == 429 or 500 <= response.status_code < 600:
                last_exception = ReleaseNotesError(f"HTTP {response.status_code}")
                if attempt == self._max_retries:
                    raise ReleaseNotesError(
                        f"HTTP {response.status_code} persisted fetching {url}"
                    )
                self._sleep(self._backoff_base * (2 ** (attempt - 1)))
                continue

            if not response.ok:
                raise ReleaseNotesError(f"HTTP {response.status_code} fetching {url}")

            return response.text

        raise ReleaseNotesError(f"Retries exhausted fetching {url}: {last_exception}")
