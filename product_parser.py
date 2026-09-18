"""Extraction for justjoin.it — the site knowledge of this repo, in one file.

Everything this project knows about Just Join IT lives here. The engines
know how to drive a browser and nothing about job offers; `output_writer`
knows the row schema and nothing about where a field came from. If you are
editing site knowledge anywhere else, it belongs in this file (CLAUDE.md §1).

WHAT THIS SITE SERVES
---------------------
justjoin.it is a Polish IT job board. It is a Next.js App Router site, and
it publishes the SAME offer records three different ways. Measured
2026-09-18 from a datacentre address (netcup, Nuremberg, AS197540):

  1. `justjoin.it/api/candidate-api/offers` — the site's own endpoint, and
     the one its front end calls for every page after the first. HTTP 200
     with NO headers, NO cookies and NO key, 100 complete records in 253 KB.
     Offset pagination (`from`/`itemsCount`), so pages are addressable and
     `--concurrency` is meaningful (CLAUDE.md §7).

  2. The rendered listing page — `/job-offers/all-locations` and friends.
     Server-renders the first 100 offers into the React Server Component
     flight payload (`self.__next_f`), which holds a dehydrated React Query
     cache. No second page: see `page_url`.

  3. `justjoin.it/api/candidate-api/offers/{slug}` — one offer in full,
     including the description body, per-skill levels and the language
     requirements. Also ungated.

There is a fourth host, `api.justjoin.it`, which the page config names as
`baseApiUrl`. Do not use it: it answered **HTTP 503 from nginx to every
request** from this address — bare, with browser headers, with the
`x-api-version` header its own bundle sends, and on every path tried. The
`/api/candidate-api/` prefix on the main host is a same-origin proxy in
front of it, and that proxy is what works. This is CLAUDE.md §21's lesson
stated as code: gating is per-ROUTE, not per-site, and the route the front
end actually calls is the one to ask for.

TWO ROUTES, TWO SPELLINGS OF THE SAME OFFER
-------------------------------------------
The endpoint and the server-rendered payload do NOT agree on field names,
and that is the single most important thing to know before editing
`_normalise_record`. For one and the same offer:

    endpoint                      flight payload
    --------------------------    -----------------------------
    requiredSkills                requiredSkills
      [{"name": "Go", "level": 4}]  ["Go"]          <- levels absent
    locations                     multilocation
    isRemoteInterview             remoteInterview
    isOpenToHireUkrainians        openToHireUkrainians
    category {"key": "devops"}    categoryId 0      <- an int, not a key
    languages [...]               (absent entirely)
    hybridWorkSchedule            (absent entirely)
    (absent)                      body, informationClause, matchPercent

So one normaliser reads both spellings and records which route a row came
from in `data_source`. Anything the route did not publish stays null rather
than being guessed at — a `--route ssr` row has a null `category` because
the payload states an integer id whose meaning this parser cannot resolve,
not because the offer has no category.

THE SALARY TRAP — read this before touching `salary_from_employment_types`
--------------------------------------------------------------------------
`employmentTypes` is a LIST, and it mixes the real, advertised salary with
currency conversions the site computed. Each entry carries the
discriminator:

    {"from": 22000, "to": 28000, "currency": "PLN",
     "currencySource": "original",   <- what the employer actually offers
     "type": "b2b", "unit": "month", "gross": false}
    {"from": 5093.88, "to": 6483.13, "currency": "EUR",
     "currencySource": "conversion", <- computed by justjoin.it
     ...}

Taking `employmentTypes[0]` is wrong, and it is wrong OFTEN rather than
rarely. Measured 2026-09-18 over 1,000 listing records and 40 detail
records:

    route     'original' at [0]   elsewhere   of those, DISCLOSED
    listing    679                 321          12
    detail      11                  29          16

Both columns matter, and they break differently:

  * On the 321 listing rows whose `original` is not first, `[0]` yields a
    CONVERSION — so `salary_currency` comes back USD or CHF on a job
    advertised in PLN. 309 of those rows disclose no amount, so the harm
    is a wrong currency label on a null salary; the remaining 12 get a
    wrong NUMBER too, e.g. 129.37 PLN/hour printed for a job offering
    30 EUR/hour.
  * The DETAIL route is where it is severe: 16 of 40 sampled records
    disclose a salary whose `original` is not at index 0 — 40% — and one
    of them reads 39.15 CHF where the employer offers 180 PLN.

This is CLAUDE.md §4's "structured data is a fact" rule: `currencySource`
is the site telling you which number is real, so select on it and never on
position. The all-null conversion entries that lead an undisclosed row are
also why the selection cannot be "the first entry with a number in it" —
that would pick a real conversion over a real original on the 12 rows where
both exist.

Three more measured facts about that list:

  * 99 of 1000 rows carry TWO `original` entries — one salary for a B2B
    contract and another for an employment contract. They are genuinely
    different offers of pay for the same job, so all of them are kept in
    `salary_options` and the first is promoted into the scalar columns.
  * `unit` is spelled in MIXED CASE by the site itself: "month" 517,
    "Month" 275, "Hour" 229, "Day" 70, "Year" 8. Normalised to lower case
    here, or a consumer grouping by unit gets two buckets for one unit.
  * 461 of 1000 rows disclose no salary at all — `from`/`to` are null on
    the `original` entry. Those stay null. They are NOT zero: a zero would
    drag every average a reader computes (CLAUDE.md §21).

WHAT THE SITE CAPS
------------------
`/offers/count` said 19,381 offers on 2026-09-18 while every query's
`meta.totalItems` is capped at 10,000, and `from=10000` answers **HTTP
500** rather than an empty page. So a run that fetches everything reachable
is genuinely `complete` — it fetched everything the site will serve — and
is still a 52% sample. The sidecar records both figures; see
`cap_report()`.

CAPTCHA
-------
justjoin.it carries Google reCAPTCHA **v2**, and it is not on the path this
scraper walks. The site publishes its own key in the page config
(`googleRecaptchaV2Key`), and the only string that references it sits in
the job-application form's i18n namespace ("reCAPTCHA verification
failed."). Reading offers never renders it: across every capture taken for
this repo — served listing pages, offer pages, endpoint responses — the
count of every other vendor marker was zero.

This is a statement about what was MEASURED, not about what 2Captcha can
solve (CLAUDE.md §19). Should this site ever put a reCAPTCHA v2 in front of
a listing, `captcha_solver.py` solves exactly that with
`RecaptchaV2TaskProxyless`, and the sitekey is published in the page source.
"""

from __future__ import annotations

import html as _html
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from bs4 import BeautifulSoup

from output_writer import Facet, JobPosting, utc_now

# ---------------------------------------------------------------------------
# Hosts and routes
# ---------------------------------------------------------------------------

HOST = "justjoin.it"
WWW_HOST = "www." + HOST

# Both answer. `www.` 301s to the bare host for HTML and for the endpoint
# alike, so a URL rebuilt with either prefix reaches the same page — unlike
# mediamarkt, where two hosts of ten refuse `www.` outright (CLAUDE.md §5).
HOSTS = (HOST, WWW_HOST)

# The host the site's own config calls `baseApiUrl`. Named here ONLY so
# `is_supported_url` can refuse it with the real reason instead of "not a
# justjoin.it URL", which would send the reader hunting for a typo.
UPSTREAM_API_HOST = "api." + HOST

SOURCE_DEFAULT = HOST

BASE = "https://" + HOST

# The same-origin proxy the front end calls. NOT `api.justjoin.it`.
API_PATH = "/api/candidate-api"
API_BASE = BASE + API_PATH

OFFERS_PATH = API_PATH + "/offers"
FACETS_PATH = OFFERS_PATH + "/facets/count"
CATEGORIES_PATH = OFFERS_PATH + "/categories/count"
COUNT_PATH = OFFERS_PATH + "/count"

# Where a browsable listing lives. `all-locations` is the site's own word
# for "no city filter" and is what its nav links to.
LISTING_PATH = "/job-offers"
ALL_LOCATIONS = "all-locations"
DEFAULT_LISTING_URL = BASE + LISTING_PATH + "/" + ALL_LOCATIONS

OFFER_PATH = "/job-offer"

MODES = ("listings", "offer", "facets")
DEFAULT_MODE = "listings"

# `--route` applies to `--mode listings` only.
#
#   api  the endpoint. Every page, every filter, offset pagination.
#   ssr  the rendered listing page's flight payload. ONE page of 100 rows
#        with a poorer schema — and the only route `robots.txt` permits
#        (see ROBOTS_NOTE). Kept because a reader who wants to stay inside
#        robots.txt should not have to fork this repo to do it.
ROUTES = ("api", "ssr")
DEFAULT_ROUTE = "api"

# justjoin.it's robots.txt (read 2026-09-18) allows `/job-offers/` and
# `/job-offer/`, advertises eight sitemaps of its own, and carries
# `Disallow: /api/`. The endpoint this scraper prefers is under that
# prefix. It is the site's own front end calling its own origin rather than
# a crawler enumerating URLs, and it is stated plainly in the README rather
# than left for a reader to find — with `--route ssr` there for anyone who
# would rather take the 100 rows robots.txt invites.
ROBOTS_NOTE = ("justjoin.it's robots.txt disallows /api/, which is where "
               "the offers endpoint lives. --route ssr reads the rendered "
               "listing page instead (100 rows, one page).")

# ---------------------------------------------------------------------------
# The endpoint's own vocabulary
# ---------------------------------------------------------------------------
#
# Every value below was checked against the live endpoint on 2026-09-18 by
# asking for it and comparing `meta.totalItems` against the unfiltered
# 10,000. A parameter that answered 10,000 with an identical first row is
# NOT a filter, whatever its name suggests — CLAUDE.md §21 found the same
# thing on BBB, where `showOnlyAccredited` does nothing at all.
#
# These filter:                  measured totalItems
#   categories=python            439
#   categories=devops            832
#   city=Warszawa&cityRadius=30  5,731
#   remoteWorkOptions=remote     4,523
#   keywords=rust&keywordType=any 1,147
#   experienceLevels=senior      6,148
#   experienceLevels=junior      469
#   languages=en                 5,981
#   employmentTypes=b2b          8,494
#   workingTimes=full_time       9,198
#   withSalary=true              4,641
#
# These are ACCEPTED AND IGNORED — the endpoint answers 200 with the full
# unfiltered 10,000 and the same first row. They are deliberately not
# exposed as flags, because a flag that silently does nothing is worse than
# a missing one:
#   openToHireUkrainians / isOpenToHireUkrainians / ukrainian
#   remoteInterview / isRemoteInterview
#   salaryFrom (with or without salaryCurrencies)
#
# And these are the WRONG SPELLINGS, taken from the field names in the
# payload rather than from the request the site makes. Each answers 200
# with an unfiltered result, which is exactly how you ship a broken filter
# and never notice:
#   categoryKeys (the request sends `categories`)
#   keyword      (the request sends `keywords` + `keywordType`)
#   cities       (the request sends `city` + `cityRadius`)
#   workplaceTypes (the request sends `remoteWorkOptions`)

SORTS = ("publishedAt", "salary", "match")
DEFAULT_SORT = "publishedAt"

ORDERS = ("descending", "ascending")
DEFAULT_ORDER = "descending"

# `sortBy=published&orderBy=DESC` — the spelling in the field names — is
# HTTP 400. The vocabulary is the one above.

EXPERIENCE_LEVELS = ("intern", "junior", "mid", "senior", "manager", "c_level")
WORKPLACE_TYPES = ("office", "hybrid", "remote", "mobile")
EMPLOYMENT_TYPES = ("b2b", "permanent", "mandate_contract", "contract",
                    "internship", "freelance")
WORKING_TIMES = ("full_time", "part_time", "internship", "freelance",
                 "b2b_contract")

# The site's own 25 category keys, from `/offers/categories/count` on
# 2026-09-18. Kept as a hint for `--category`'s help text and for a
# suggestion when a user misspells one; NOT as a gate, because the site can
# add a category and refusing an unknown one would be this repo deciding
# what justjoin.it sells.
CATEGORY_KEYS = ("data", "java", "analytics", "pm", "devops", "erp",
                 "architecture", "testing", "ai", "security", "other",
                 "python", "net", "admin", "javascript", "support", "mobile",
                 "c", "ux", "php", "go", "game", "scala", "ruby", "html")

# `itemsCount=2000` is HTTP 400; 1000 is served.
MAX_ITEMS_COUNT = 1000
DEFAULT_ITEMS_COUNT = 100

# `from=10000` and `from=10100` are both HTTP 500 — walking off the end
# manufactures an error that reads like a bug in this scraper. Plan against
# the cap instead (CLAUDE.md §21).
MAX_FROM = 10_000

# What the site says it holds, against what any one query can reach.
SITE_TOTAL_HINT = COUNT_PATH

# ---------------------------------------------------------------------------
# User agent
# ---------------------------------------------------------------------------

# Measured 2026-09-18 against both the endpoint and the HTML host:
#
#   User-Agent                       endpoint   HTML page
#   (curl's default)                 200        200
#   python-requests/2.32.3           200        200
#   Wget/1.21                        200        200
#   justjoin-scraper/0.1.0           200        200
#   a Chrome UA                      200        200
#   Python-urllib/3.13               403        403
#
# One entry in a denylist, and it happens to be the User-Agent the standard
# library sends if you do not set one. Nothing else was refused — this is
# not a general bot gate, and sending a browser UA buys nothing over
# sending an honest one. The engines take theirs from the browser
# (CLAUDE.md §8); this constant is for the HTTP paths.
HTTP_USER_AGENT = "Mozilla/5.0 (compatible; justjoin-scraper; +https://github.com/2scraper/justjoin-scraper)"

_REFUSED_USER_AGENT_RE = re.compile(r"^Python-urllib/", re.I)


def user_agent_is_refused(value: Optional[str]) -> bool:
    """True for the one User-Agent justjoin.it answers 403 to.

    Exists so the HTTP paths can assert they are not about to send it, and
    so the suite can pin the measurement. A stdlib default that produces a
    403 a long way from its cause is exactly the confusing failure
    CLAUDE.md §3 wants prevented.
    """
    return bool(value) and bool(_REFUSED_USER_AGENT_RE.match(value.strip()))


# ---------------------------------------------------------------------------
# Small conversions
# ---------------------------------------------------------------------------


def _str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # The endpoint publishes conversions to 12 decimal places
    # (5897.01664566971). Rounded to cents because a salary is money, and
    # because an unrounded float makes two identical runs diff.
    return round(number, 2)


def _bool_or_none(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    return None


def _lower_or_none(value: Any) -> Optional[str]:
    """Lower-cased, for the fields the site spells in two cases.

    `unit` arrives as both "month" and "Month"; `countryCode` as both "PL"
    and "pl" (36 and 2 of 40 detail records). Neither difference means
    anything, and left alone each one splits a consumer's GROUP BY in two.
    """
    text = _str_or_none(value)
    return text.lower() if text else None


def _upper_or_none(value: Any) -> Optional[str]:
    text = _str_or_none(value)
    return text.upper() if text else None


def _iso(value: Any) -> Optional[str]:
    """An ISO-8601 instant normalised to UTC with a trailing `Z`.

    The endpoint mixes `2026-09-20T07:45:07.364Z` with
    `2026-09-18T09:51:12.673298Z` — different precisions, and `lastPublishedAt`
    uses microseconds where `publishedAt` uses milliseconds. Normalised so
    two runs do not diff on formatting.
    """
    text = _str_or_none(value)
    if not text:
        return None
    try:
        from datetime import datetime, timezone
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        from datetime import timezone as _tz
        parsed = parsed.replace(tzinfo=_tz.utc)
    from datetime import timezone as _tz2
    parsed = parsed.astimezone(_tz2.utc)
    return parsed.strftime("%Y-%m-%dT%H:%M:%S") + (
        ".%03dZ" % (parsed.microsecond // 1000) if parsed.microsecond else "Z")


def _plain_text(value: Any) -> Optional[str]:
    """The description body as text.

    An offer's `body` is HTML the employer wrote — `<p>`, `<ul>`, `<strong>`
    and the occasional `<a>`. Rendered to text rather than stored as markup
    because the row goes into a CSV as often as into JSON, and a 5.8 KB
    HTML blob in a spreadsheet cell is unreadable. Block tags become line
    breaks so a bullet list survives the conversion.
    """
    text = _str_or_none(value)
    if not text:
        return None
    if "<" not in text:
        return text
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup.find_all(["br"]):
        tag.replace_with("\n")
    for tag in soup.find_all(["p", "li", "div", "tr", "h1", "h2", "h3",
                              "h4", "h5", "h6"]):
        tag.append("\n")
    rendered = soup.get_text()
    rendered = _html.unescape(rendered)
    lines = [line.strip() for line in rendered.splitlines()]
    out = "\n".join(line for line in lines if line)
    return out or None


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def _split_url(url: str) -> Tuple[str, str, str, str]:
    parts = urllib.parse.urlsplit(url or "")
    return (parts.scheme or "", (parts.netloc or "").lower(),
            parts.path or "", parts.query or "")


def _bare_host(netloc: str) -> str:
    host = netloc.split("@")[-1].split(":")[0].lower()
    return host[4:] if host.startswith("www.") else host


_OFFER_RE = re.compile(r"^/job-offer/(?P<slug>[^/?#]+)/?$", re.I)
_LISTING_RE = re.compile(r"^/job-offers(?:/(?P<rest>.*))?$", re.I)
_API_OFFERS_RE = re.compile(r"^%s/?$" % re.escape(OFFERS_PATH), re.I)
_API_OFFER_RE = re.compile(r"^%s/(?P<slug>[^/?#]+)/?$" % re.escape(OFFERS_PATH),
                           re.I)
_API_FACETS_RE = re.compile(r"^%s/?$" % re.escape(FACETS_PATH), re.I)

# The slug's own tail is a short hex disambiguator on offers whose
# company+title+city+category collide ("...-warszawa-analytics-fa3614ce").
# It is part of the slug and is NOT the id — the id is the `guid`, which
# appears nowhere in the URL. So `sku` cannot be recovered from a URL on
# this site, and `sku_from_url` says so by returning None rather than
# handing back a slug that would not join against anything (CLAUDE.md §5).
_SKU_IN_URL_RE = None


def route_of(url: str) -> Optional[str]:
    """Which of this site's routes a URL names, or None."""
    _, netloc, path, _ = _split_url(url)
    if _bare_host(netloc) not in (HOST,):
        return None
    if _OFFER_RE.match(path):
        return "offer"
    if _API_OFFER_RE.match(path):
        return "api_offer"
    if _API_OFFERS_RE.match(path):
        return "api_offers"
    if _API_FACETS_RE.match(path):
        return "api_facets"
    if _LISTING_RE.match(path):
        return "listing"
    return None


def is_supported_url(url: str) -> Tuple[bool, str]:
    """(supported, reason). The reason is shown to the user on a refusal."""
    scheme, netloc, path, _ = _split_url(url)
    if not scheme or not netloc:
        return False, "%r is not an absolute URL." % (url,)
    if scheme not in ("http", "https"):
        return False, "%r is not an http(s) URL." % (url,)
    host = _bare_host(netloc)
    if host == UPSTREAM_API_HOST:
        return False, (
            "%s is justjoin.it's upstream API host and answers HTTP 503 to "
            "every request from outside its own infrastructure — measured "
            "bare, with browser headers and with the x-api-version header "
            "its own bundle sends. The route that works is %s on %s, which "
            "is what this scraper uses. Pass a %s URL instead."
            % (UPSTREAM_API_HOST, API_PATH, HOST, HOST))
    if host != HOST:
        return False, (
            "%r is not a justjoin.it URL. This scraper reads %s (and its "
            "www. alias) only." % (url, HOST))
    if route_of(url) is None:
        return False, (
            "%r is on justjoin.it but is not a route this scraper reads. "
            "Expected a listing page (%s/...), an offer page (%s/{slug}) or "
            "the offers endpoint (%s)." % (path, LISTING_PATH, OFFER_PATH,
                                           OFFERS_PATH))
    return True, ""


def mode_for_url(url: str) -> Optional[str]:
    """The `--mode` a URL implies, so a user need not pass both."""
    route = route_of(url)
    if route in ("offer", "api_offer"):
        return "offer"
    if route == "api_facets":
        return "facets"
    if route in ("listing", "api_offers"):
        return "listings"
    return None


def canonical_url(url: str) -> str:
    """The bare-host spelling of a URL, with `www.` dropped."""
    scheme, netloc, path, query = _split_url(url)
    if not netloc:
        return url
    host = _bare_host(netloc)
    return urllib.parse.urlunsplit(
        (scheme or "https", host, path, query, ""))


def slug_from_url(url: str) -> Optional[str]:
    """The offer slug out of an offer URL, or None."""
    _, _, path, _ = _split_url(url)
    for pattern in (_OFFER_RE, _API_OFFER_RE):
        match = pattern.match(path)
        if match:
            slug = urllib.parse.unquote(match.group("slug"))
            return slug or None
    return None


def sku_from_url(url: str) -> Optional[str]:
    """Always None on this site, and deliberately so.

    Every other repo in this family recovers an id from the URL when the
    structured data omits one. justjoin.it publishes no id in any URL: the
    `sku` is the record's `guid`, a UUID that appears in the payload and
    nowhere in the address. Returning the slug here would look like a
    recovery and would join against nothing, so this returns None and the
    caller keeps the row's own guid.
    """
    return None


def offer_url(slug: str) -> Optional[str]:
    """The browsable page for an offer slug."""
    clean = (slug or "").strip().strip("/")
    if not clean:
        return None
    if clean.startswith("http"):
        return canonical_url(clean)
    return BASE + OFFER_PATH + "/" + urllib.parse.quote(clean, safe="-._~")


def offer_slug_or_url(value: str) -> Optional[str]:
    """Accept either a slug or a full offer URL, return the slug."""
    text = (value or "").strip()
    if not text:
        return None
    if "://" in text:
        return slug_from_url(text)
    return text.strip("/") or None


def _clean_params(params: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Drop unset values and flatten sequences, preserving order.

    Sequence values are repeated (`categories=a&categories=b`), which is
    what the site's own `qs.stringify(..., {arrayFormat: "repeat"})` does.
    """
    out: List[Tuple[str, str]] = []
    for key, value in params.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, bool):
            out.append((key, "true" if value else "false"))
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                if item is None or item == "":
                    continue
                out.append((key, str(item)))
        else:
            out.append((key, str(value)))
    return out


@dataclass(frozen=True)
class Filters:
    """The subset of the endpoint's parameters that measurably filter.

    Only parameters proven to change `meta.totalItems` are here; see the
    vocabulary block at the top of this file for the ones that are accepted
    and ignored, and for the plausible spellings that are not the request
    the site makes.
    """

    category: Optional[str] = None
    city: Optional[str] = None
    city_radius: Optional[int] = None
    remote: Optional[str] = None
    keyword: Optional[str] = None
    experience: Optional[Sequence[str]] = None
    language: Optional[Sequence[str]] = None
    employment: Optional[Sequence[str]] = None
    working_time: Optional[Sequence[str]] = None
    with_salary: bool = False

    def is_empty(self) -> bool:
        return not self.as_params()

    def as_params(self) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if self.category:
            params["categories"] = self.category
        if self.city:
            params["city"] = self.city
            # The site's own nav sends 30 with every city. Sent explicitly
            # rather than relying on a server default, so a run's radius is
            # in its own URL and therefore in its own debug dump.
            params["cityRadius"] = (self.city_radius
                                    if self.city_radius is not None else 30)
        if self.remote:
            params["remoteWorkOptions"] = self.remote
        if self.keyword:
            params["keywords"] = self.keyword
            # The site sends `any` with every keyword search. Without it the
            # endpoint answers unfiltered.
            params["keywordType"] = "any"
        if self.experience:
            params["experienceLevels"] = list(self.experience)
        if self.language:
            params["languages"] = list(self.language)
        if self.employment:
            params["employmentTypes"] = list(self.employment)
        if self.working_time:
            params["workingTimes"] = list(self.working_time)
        if self.with_salary:
            params["withSalary"] = True
        return params


def api_url(*, offset: int = 0, items: int = DEFAULT_ITEMS_COUNT,
            sort: str = DEFAULT_SORT, order: str = DEFAULT_ORDER,
            filters: Optional[Filters] = None) -> str:
    """The offers endpoint URL for one page.

    `offset` is the site's `from`. Non-ASCII city names MUST be
    percent-encoded — `city=Kraków` sent raw is HTTP 400, which reads like
    the city being unsupported rather than like an encoding bug.
    `urlencode` does it; the note is here because sending it by hand is how
    the 400 was first produced.
    """
    params: Dict[str, Any] = {
        "from": max(0, int(offset)),
        "itemsCount": max(1, min(int(items), MAX_ITEMS_COUNT)),
        "sortBy": sort if sort in SORTS else DEFAULT_SORT,
        "orderBy": order if order in ORDERS else DEFAULT_ORDER,
    }
    if filters is not None:
        params.update(filters.as_params())
    query = urllib.parse.urlencode(_clean_params(params))
    return BASE + OFFERS_PATH + "?" + query


def detail_api_url(slug: str) -> Optional[str]:
    """The endpoint URL for one offer.

    Refuses an empty slug, and that refusal is load-bearing: `/offers/`
    with nothing after it is NOT a 404 — it answers HTTP 200 with the whole
    first page of the LISTING. A caller that let an empty slug through
    would parse a listing payload as a detail record and report success.
    """
    clean = (slug or "").strip().strip("/")
    if not clean:
        return None
    return BASE + OFFERS_PATH + "/" + urllib.parse.quote(clean, safe="-._~")


def facets_api_url() -> str:
    return BASE + FACETS_PATH


def categories_api_url() -> str:
    return BASE + CATEGORIES_PATH


def count_api_url() -> str:
    return BASE + COUNT_PATH


def listing_url(*, city: Optional[str] = None,
                category: Optional[str] = None) -> str:
    """A browsable listing page, the way the site's own nav builds one."""
    where = (city or ALL_LOCATIONS).strip().strip("/") or ALL_LOCATIONS
    path = LISTING_PATH + "/" + urllib.parse.quote(where.lower(), safe="-._~")
    if category:
        path += "/" + urllib.parse.quote(category.strip().lower(),
                                         safe="-._~")
    return BASE + path


def filters_from_listing_url(url: str) -> Filters:
    """Read a browsable listing URL back into endpoint filters.

    `/job-offers/warszawa/python` is the site's own address for "Python
    jobs within 30 km of Warsaw", and its own front end turns that into
    `city=Warszawa&cityRadius=30&categories=python`. Doing the same here is
    what lets `--url` take an address a user copied out of their browser.

    `/job-offers/remote` is a special case: `remote` is not a city, it is
    the site's word for the remote-work filter, and it becomes
    `remoteWorkOptions=remote`.
    """
    _, _, path, query = _split_url(url)
    match = _LISTING_RE.match(path)
    if not match:
        return Filters()
    rest = (match.group("rest") or "").strip("/")
    segments = [urllib.parse.unquote(part) for part in rest.split("/") if part]
    city = None
    category = None
    remote = None
    if segments:
        head = segments[0].lower()
        if head == ALL_LOCATIONS:
            pass
        elif head in ("remote", "zdalna"):
            remote = "remote"
        else:
            # The site's city segments are lower-case and unaccented
            # ("warszawa", "lodz"), while the endpoint filters on the
            # city's real name. Left as the user typed it: the endpoint
            # accepts "warszawa" and "Warszawa" alike, and inventing an
            # unaccenting table here would be this parser guessing at
            # Polish orthography.
            city = segments[0]
        if len(segments) > 1:
            category = segments[1].lower()
    keyword = None
    for key, value in urllib.parse.parse_qsl(query):
        if key in ("keyword", "keywords") and value:
            keyword = value
    return Filters(city=city, category=category, remote=remote,
                   keyword=keyword)


def page_url(url: str, page: int) -> Optional[str]:
    """None for a browsable listing URL, and that is the finding.

    CLAUDE.md §7 asks for a `page_url` that reconstructs page N when no
    next-link matches. On justjoin.it's rendered pages there is no such
    address, and the failure mode is the dangerous one: `?page=2` on a
    listing URL does not error and does not return an empty page — it
    answers HTTP 200 with **page 1 again**, with `meta.from` still 0 and a
    byte-identical first offer. Measured 2026-09-18 on
    `/job-offers/all-locations?page=2`.

    So a scraper that built `?page=N` would fetch page 1 N times, find no
    new `guid` on the second, conclude the listing was exhausted and report
    a COMPLETE run holding a hundred rows out of ten thousand. Returning
    None is what makes `--route ssr` say "one page only" instead.

    The endpoint is the addressable route; see `api_page_url`.
    """
    return None


def api_page_url(url: str, page: int, *, items: int = DEFAULT_ITEMS_COUNT
                 ) -> Optional[str]:
    """Page N of an offers-endpoint URL, by rewriting `from`.

    Offset pagination, so page N's address is knowable without fetching
    page N-1 — which is what makes `--concurrency` meaningful here
    (CLAUDE.md §7). Returns None past the site's cap rather than building a
    URL that answers HTTP 500.
    """
    if page < 1:
        return None
    scheme, netloc, path, query = _split_url(url)
    if not _API_OFFERS_RE.match(path):
        return None
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    size = items
    for key, value in pairs:
        if key == "itemsCount":
            size = _int_or_none(value) or items
    offset = (page - 1) * size
    if offset >= MAX_FROM:
        return None
    kept = [(k, v) for k, v in pairs if k != "from"]
    kept.insert(0, ("from", str(offset)))
    return urllib.parse.urlunsplit(
        (scheme or "https", netloc or HOST, path,
         urllib.parse.urlencode(kept), ""))


def pages_available_for(total_items: Optional[int], items: int) -> Optional[int]:
    """How many pages of `items` a run may actually ask for.

    Bounded by the site's own cap as well as by the result size, because
    asking for the page after the cap is an HTTP 500 rather than an empty
    page.
    """
    if total_items is None or items < 1:
        return None
    reachable = min(int(total_items), MAX_FROM)
    if reachable <= 0:
        return 0
    return (reachable + items - 1) // items


def category_from_url(url: str) -> Optional[str]:
    """The category a URL names, from either route."""
    _, _, path, query = _split_url(url)
    if _API_OFFERS_RE.match(path):
        for key, value in urllib.parse.parse_qsl(query):
            if key == "categories" and value:
                return value
        return None
    return filters_from_listing_url(url).category


# ---------------------------------------------------------------------------
# Bot-challenge detection
# ---------------------------------------------------------------------------

# Every candidate marker below was COUNTED on pages this site plainly
# served before being kept, which is CLAUDE.md §18's rule and which threw
# two of them out:
#
#   `akamai`        4 occurrences on a served offer page — in the job
#                   description, where an employer lists "DigitalOcean,
#                   GCP, AWS, Azure lub Akamai Linode" among the clouds a
#                   candidate should know. A marker that fires on a real
#                   page is worse than no marker.
#   `cf-turnstile`  not carried, for the reason CLAUDE.md §8 gives: the
#                   2Captcha Scraping Browser's auto-solve extension
#                   injects it into every page it loads, so it fires on
#                   good pages and has been measured ABSENT from real
#                   Cloudflare challenges. `challenges.cloudflare.com` is
#                   the one that works, and it is 0 on every capture taken
#                   here.
#
# Counts across seven captures taken 2026-09-18 — the homepage, two
# rendered listing pages, a filtered listing, an offer page, an endpoint
# response and the sitemap — are zero for every marker in this set. This
# site has never refused this scraper, so the set is a tripwire for a
# change rather than a description of something seen.
BOT_CHALLENGE_MARKERS = (
    "challenges.cloudflare.com",
    "/cdn-cgi/challenge-platform",
    "just a moment...",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
    "captcha-delivery.com",          # DataDome
    "geo.captcha-delivery.com",
    "px-captcha",                    # PerimeterX
    "_px_",
    "perimeterx",
    "incapsula incident id",
    "_incapsula_resource",
    "kasada",
    "awswaf",
    "access denied",
    "request unsuccessful",
)

# The site's own asset host. Every page justjoin.it actually serves is
# built out of it; an interstitial would not be (CLAUDE.md §8's
# `assets.mmsrg.com` trick, now on its third site). Counted 2026-09-18:
# 5 references on the homepage, 4 on an offer page, 5 on a filtered
# listing.
SITE_ASSET_MARKERS = ("public.justjoin.it", "imgproxy.justjoinit.tech",
                      "_next/static")

# The threshold is 1 rather than the 2-3 this trick usually takes, and
# deliberately: CLAUDE.md §17's classification-order trap is that a
# minimal-but-real page with fewer references than the measured average
# comes back "blocked". The asset check is a WEAK secondary signal here,
# consulted only after the unambiguous ones.
MIN_ASSET_MARKERS = 1

_ENTITY_PREFIX_BYTES = 4000


def _normalised_head(html: str) -> str:
    """Lower-cased, entity-unescaped first few KB, for marker matching.

    CLAUDE.md §20: an edge's refusal page reaches a parser spelled two ways
    — entity-escaped over a raw HTTP client, plain out of a browser DOM. So
    unescape before matching, and bound it: a refusal page is a few hundred
    bytes, and unescaping 1.8 MB of listing would let a job title deep in
    the payload read as a marker.
    """
    head = (html or "")[:_ENTITY_PREFIX_BYTES]
    return _html.unescape(head).lower()


def detect_bot_challenge(html: Optional[str]) -> Optional[str]:
    """The marker a challenge page carries, or None."""
    if not html:
        return None
    head = _normalised_head(html)
    for marker in BOT_CHALLENGE_MARKERS:
        if marker in head:
            return marker
    return None


def count_site_assets(html: Optional[str]) -> int:
    if not html:
        return 0
    lowered = html.lower()
    return sum(lowered.count(marker) for marker in SITE_ASSET_MARKERS)


# ---------------------------------------------------------------------------
# The site's own captcha configuration
# ---------------------------------------------------------------------------

# justjoin.it publishes its reCAPTCHA key in the Next.js runtime config
# that every page carries:
#
#     "publicRuntimeConfig":{...,"googleRecaptchaV2Key":"6LeTZ-Er...",...}
#
# CLAUDE.md §18 says to grep a GOOD page for the site's own captcha config
# rather than building a marker set from a vendor list, and this is that
# grep. What it found, and what the engines' docstrings state at length:
# the key is v2 by the site's own field name, the only string referencing
# it lives in the job-application form's i18n namespace, and in a live
# browser on 2026-09-18 no route this scraper reads had `grecaptcha`
# defined at all — the script is never loaded on the read path.
#
# So this function has never returned a key that mattered. It is here for
# the day one does: a solvable challenge whose key the page publishes is
# better answered with that key than refused (§19 — never pay for a task
# the API will reject, but do not decline a task it would accept).
_RECAPTCHA_KEY_RE = re.compile(
    r'googleRecaptchaV2Key\\?"\s*:\s*\\?"([0-9A-Za-z_-]{20,})')


def site_recaptcha_sitekey(html: Optional[str]) -> Optional[str]:
    """The reCAPTCHA v2 sitekey justjoin.it publishes, or None."""
    match = _RECAPTCHA_KEY_RE.search(html or "")
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Reading the endpoint's payloads
# ---------------------------------------------------------------------------


def parse_json(text: Optional[str]) -> Optional[Any]:
    """JSON out of a response body, or None.

    Tolerates a body that arrived through a browser: navigating Chromium to
    a JSON URL wraps the document in its own viewer markup, so the text may
    have leading whitespace or, in a headful browser, surrounding markup.
    The braces are found rather than assumed.
    """
    if text is None:
        return None
    raw = text.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        pass
    # A browser's JSON viewer: take the outermost object or array.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        end = raw.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except ValueError:
                continue
    return None


def offers_from_payload(payload: Any) -> List[Dict[str, Any]]:
    """The offer records out of an endpoint response.

    `{"data": [...], "meta": {...}}` is the offers route; `{"items": [...]}`
    is `/offers/popular`. Anything else yields nothing rather than a guess.
    """
    if isinstance(payload, dict):
        for key in ("data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    return []


def meta_from_payload(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("meta"), dict):
        return payload["meta"]
    return {}


def looks_like_detail_record(payload: Any) -> bool:
    """True for a single-offer payload.

    The guard for `detail_api_url`'s empty-slug hazard: a listing response
    has `data`/`meta` and no `slug` of its own, and calling it a detail
    record would publish one row made of a hundred offers' worth of nothing.
    """
    if not isinstance(payload, dict):
        return False
    if "data" in payload or "meta" in payload:
        return False
    return bool(payload.get("slug") or payload.get("id"))


# ---------------------------------------------------------------------------
# Salary
# ---------------------------------------------------------------------------

ORIGINAL_CURRENCY_SOURCE = "original"
CONVERTED_CURRENCY_SOURCE = "conversion"


@dataclass(frozen=True)
class SalaryOption:
    """One way this employer offers to pay, in the unit they quoted it in.

    `minimum`/`maximum` are the QUOTED rate and `unit` is its unit, so the
    two always read together correctly. `monthly_minimum`/`monthly_maximum`
    are the site's own normalisation of that rate to a month, which is what
    makes an hourly offer comparable with a salaried one.
    """

    contract_type: Optional[str] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    currency: Optional[str] = None
    unit: Optional[str] = None
    is_gross: Optional[bool] = None
    monthly_minimum: Optional[float] = None
    monthly_maximum: Optional[float] = None

    def as_text(self) -> str:
        """A single cell for the CSV: `b2b 22000-28000 PLN/month net`."""
        parts: List[str] = []
        if self.contract_type:
            parts.append(self.contract_type)
        if self.minimum is not None or self.maximum is not None:
            low = "" if self.minimum is None else _number(self.minimum)
            high = "" if self.maximum is None else _number(self.maximum)
            parts.append(low if low == high else "%s-%s" % (low, high))
        else:
            parts.append("undisclosed")
        if self.currency:
            parts.append(self.currency + ("/" + self.unit if self.unit else ""))
        if self.is_gross is not None:
            parts.append("gross" if self.is_gross else "net")
        return " ".join(p for p in parts if p)


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def original_salary_entries(employment_types: Any) -> List[Dict[str, Any]]:
    """The entries the employer actually quoted, in the site's own order.

    Selected on `currencySource == "original"` and never on position — see
    this module's docstring for why, with the numbers.

    An entry with NO `currencySource` at all is treated as original: the
    field is the site's marker for a computed conversion, so its absence
    cannot mean "converted". No such entry was seen in 1,000 listing rows
    or 40 detail records; the branch is here so an unanticipated shape
    degrades to "believe the site" rather than to "publish nothing".
    """
    if not isinstance(employment_types, list):
        return []
    out: List[Dict[str, Any]] = []
    for entry in employment_types:
        if not isinstance(entry, dict):
            continue
        source = _str_or_none(entry.get("currencySource"))
        if source is None or source.lower() == ORIGINAL_CURRENCY_SOURCE:
            out.append(entry)
    return out


# `from`/`to` and `fromPerUnit`/`toPerUnit` are NOT two spellings of one
# number, and reading the wrong pair is the most expensive mistake
# available on this site. Measured 2026-09-18 over 1,000 listing records,
# 40 detail records and 100 server-rendered records:
#
#   route     unit     from vs fromPerUnit       from / fromPerUnit
#   api       hour     differ  (229 entries)     168   = working hours/month
#   api       day      differ  (70)              21    = working days/month
#   api       year     differ  (8)               1/12
#   api       month    equal   (275)             1
#   detail    same shape as api
#   ssr       ALWAYS equal, for every unit
#
# So on the endpoint `from` is the rate NORMALISED TO A MONTH while `unit`
# names the unit the EMPLOYER quoted. Read together — which is the obvious
# thing to do, they are adjacent keys — they say "18480 PLN per hour" for
# a job paying 110 PLN an hour: out by a factor of 168, with a real number
# and a real unit, on 307 of 582 quoted entries. No test catches that; the
# column is populated and plausible.
#
# `fromPerUnit` + `unit` is the pair that is self-consistent on all three
# routes, so that is what the scalar salary columns carry. The monthly
# figure is genuinely useful — it is what makes an hourly offer comparable
# with a salaried one — so it is kept, in columns that say `monthly`.
#
# The rendered payload publishes no normalisation at all (its `from` IS
# the quoted rate), so a `--route ssr` row gets a monthly figure only where
# the employer already quoted a month. Deriving one by multiplying would
# be this parser inventing the site's own arithmetic.
def _salary_option(entry: Dict[str, Any]) -> SalaryOption:
    unit = _lower_or_none(entry.get("unit"))
    quoted_min = _float_or_none(entry.get("fromPerUnit"))
    quoted_max = _float_or_none(entry.get("toPerUnit"))
    normalised_min = _float_or_none(entry.get("from"))
    normalised_max = _float_or_none(entry.get("to"))

    # An entry that states only one of the pair — never seen, but the
    # fallback keeps a shape this parser has not met from losing the
    # salary entirely.
    if quoted_min is None and quoted_max is None:
        quoted_min, quoted_max = normalised_min, normalised_max

    monthly_min: Optional[float] = None
    monthly_max: Optional[float] = None
    distinct = (normalised_min is not None and quoted_min is not None
                and abs(normalised_min - quoted_min) > 0.01)
    if distinct or unit == "month":
        monthly_min, monthly_max = normalised_min, normalised_max

    return SalaryOption(
        contract_type=_lower_or_none(entry.get("type")),
        minimum=quoted_min,
        maximum=quoted_max,
        # An ISO 4217 code the site states — the most trustworthy rung of
        # CLAUDE.md §4's ladder. Never defaulted: a null means the entry
        # named no currency.
        currency=_upper_or_none(entry.get("currency")),
        # Lower-cased: the site spells the same unit "month" and "Month".
        unit=unit,
        is_gross=_bool_or_none(entry.get("gross")),
        monthly_minimum=monthly_min,
        monthly_maximum=monthly_max,
    )


def salary_from_employment_types(employment_types: Any
                                 ) -> Tuple[Optional[SalaryOption],
                                            List[SalaryOption]]:
    """(the salary to put in the scalar columns, every quoted option).

    The first original entry is promoted into the scalar columns and the
    rest are kept in `salary_options`, so a row that quotes both a B2B and
    an employment-contract rate loses neither. Which one is "first" is the
    site's own order, stated so rather than this parser ranking them: 99 of
    1,000 rows carry two, and preferring (say) the higher one would put
    this parser's opinion into the data.
    """
    entries = original_salary_entries(employment_types)
    options = [_salary_option(entry) for entry in entries]
    if not options:
        return None, []
    # A row may quote a contract type with no numbers beside one that has
    # them. Promote a disclosed option over an undisclosed one — that is
    # not a ranking between two real salaries, it is preferring the entry
    # that says something to the entry that says nothing.
    disclosed = [o for o in options
                 if o.minimum is not None or o.maximum is not None]
    primary = disclosed[0] if disclosed else options[0]
    return primary, options


def converted_currencies(employment_types: Any) -> List[str]:
    """The currencies the site computed, for the record.

    Not a column — it is the same set on nearly every row. Exposed so the
    suite can assert that no conversion ever reaches a salary column.
    """
    if not isinstance(employment_types, list):
        return []
    out: List[str] = []
    for entry in employment_types:
        if not isinstance(entry, dict):
            continue
        if _str_or_none(entry.get("currencySource")) == CONVERTED_CURRENCY_SOURCE:
            code = _upper_or_none(entry.get("currency"))
            if code and code not in out:
                out.append(code)
    return out


# ---------------------------------------------------------------------------
# Skills, languages, locations
# ---------------------------------------------------------------------------


def _skills(value: Any) -> Tuple[Optional[List[str]], Optional[List[int]]]:
    """(names, levels) from either route's spelling of a skill list.

    The endpoint publishes `[{"name": "Go", "level": 4}]`; the rendered
    page's flight payload publishes `["Go"]` for the same offer. Both are
    read, and a `--route ssr` row gets a null levels list rather than a
    fabricated one.
    """
    if not isinstance(value, list) or not value:
        return None, None
    names: List[str] = []
    levels: List[int] = []
    any_level = False
    for item in value:
        if isinstance(item, dict):
            name = _str_or_none(item.get("name"))
            if not name:
                continue
            names.append(name)
            level = _int_or_none(item.get("level"))
            levels.append(level if level is not None else 0)
            if level is not None:
                any_level = True
        else:
            name = _str_or_none(item)
            if name:
                names.append(name)
                levels.append(0)
    if not names:
        return None, None
    return names, (levels if any_level else None)


def _languages(value: Any) -> Tuple[Optional[List[str]], Optional[List[str]]]:
    """(codes, levels) from `[{"code": "pl", "level": "B2"}]`.

    Null on 535 of 1,000 listing rows and on 19 of 40 detail records: many
    employers state no language requirement, and a null here means they did
    not say rather than that the job needs none.
    """
    if not isinstance(value, list) or not value:
        return None, None
    codes: List[str] = []
    levels: List[str] = []
    any_level = False
    for item in value:
        if not isinstance(item, dict):
            code = _lower_or_none(item)
            if code:
                codes.append(code)
                levels.append("")
            continue
        code = _lower_or_none(item.get("code"))
        if not code:
            continue
        codes.append(code)
        level = _upper_or_none(item.get("level")) or ""
        levels.append(level)
        if level:
            any_level = True
    if not codes:
        return None, None
    return codes, (levels if any_level else None)


def _locations(record: Dict[str, Any]) -> Tuple[Optional[List[str]], int]:
    """Every city this offer is open in, and how many there are.

    The endpoint calls the list `locations`; the flight payload calls the
    same list `multilocation`. 289 of 1,000 rows name more than one city —
    up to nine — so a row's `city` column alone under-reports where the job
    is.
    """
    raw = record.get("locations")
    if not isinstance(raw, list) or not raw:
        raw = record.get("multilocation")
    if not isinstance(raw, list) or not raw:
        return None, 0
    cities: List[str] = []
    for item in raw:
        if isinstance(item, dict):
            city = _str_or_none(item.get("city"))
        else:
            city = _str_or_none(item)
        if city and city not in cities:
            cities.append(city)
    return (cities or None), len(raw)


def _category_key(record: Dict[str, Any]) -> Optional[str]:
    """The category key from the endpoint's `category` object.

    The flight payload states `categoryId`, an integer, and publishes no
    table mapping it to a key. So a `--route ssr` row's category is null
    rather than a number nobody can read — a guess presented as a fact is
    the one thing CLAUDE.md §8 will not allow.

    `parentKey` is read and discarded: it was null on all 1,040 records
    measured, so it gets no column. See the note in `output_writer`.
    """
    value = record.get("category")
    if isinstance(value, dict):
        return _lower_or_none(value.get("key"))
    return _lower_or_none(value)


def _hybrid_days(record: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """(office days, remote days) for a hybrid offer.

    Present as an object on 181 of 1,000 rows and null on the rest — but
    note the object itself can be `{"officeDays": null, "remoteDays": null}`,
    which carries nothing. Both spellings collapse to a pair of nulls here.
    """
    value = record.get("hybridWorkSchedule")
    if not isinstance(value, dict):
        return None, None
    return (_int_or_none(value.get("officeDays")),
            _int_or_none(value.get("remoteDays")))


# ---------------------------------------------------------------------------
# Record -> row
# ---------------------------------------------------------------------------

# `applyMethod` is "external" on 800 of 1,000 rows and "form" on 200, and
# `applyUrl` is non-null on exactly the 800 externals. So the null is the
# site saying "applied to on our own page", not a missing field — the same
# shape as CLAUDE.md §21's "use the site's own should-I-show-this flag".
APPLY_METHODS = ("external", "form")


def _row_from_record(record: Dict[str, Any], *, data_source: str,
                     url: str = "", page: Optional[int] = None,
                     position: Optional[int] = None,
                     scraped_at: Optional[str] = None) -> Optional[JobPosting]:
    """One `JobPosting` from one offer record, from any of the three routes.

    Returns None for a record with no id, which is what a payload shape
    this parser does not understand looks like. Failing loudly beats
    emitting a row of nulls that a consumer cannot tell from a real offer
    (CLAUDE.md §8).
    """
    if not isinstance(record, dict):
        return None
    # The endpoint's listing route calls it `guid`; the detail route calls
    # the same UUID `id`. Measured equal on 40 of 40 sampled offers, which
    # is what lets a `--mode offer` run enrich a `--mode listings` run and
    # what `diff_runs.py` joins on.
    sku = _str_or_none(record.get("guid")) or _str_or_none(record.get("id"))
    slug = _str_or_none(record.get("slug"))
    if not sku:
        return None

    primary, options = salary_from_employment_types(record.get("employmentTypes"))
    required_names, required_levels = _skills(record.get("requiredSkills"))
    nice_names, _ = _skills(record.get("niceToHaveSkills"))
    language_codes, language_levels = _languages(record.get("languages"))
    cities, location_count = _locations(record)
    category = _category_key(record)
    office_days, remote_days = _hybrid_days(record)

    # Two spellings of the same two booleans, one per route.
    remote_interview = _bool_or_none(record.get("isRemoteInterview"))
    if remote_interview is None:
        remote_interview = _bool_or_none(record.get("remoteInterview"))
    ukrainians = _bool_or_none(record.get("isOpenToHireUkrainians"))
    if ukrainians is None:
        ukrainians = _bool_or_none(record.get("openToHireUkrainians"))

    row_url = _str_or_none(record.get("url")) or offer_url(slug or "") or url

    return JobPosting(
        source=SOURCE_DEFAULT,
        scraped_at=scraped_at or utc_now(),
        url=row_url or "",
        sku=sku,
        title=_str_or_none(record.get("title")),

        slug=slug,
        company_name=_str_or_none(record.get("companyName")),
        company_logo_url=(_str_or_none(record.get("companyLogoUrl"))
                          or _str_or_none(record.get("companyLogoThumbUrl"))),
        company_url=_str_or_none(record.get("companyUrl")),
        company_size=_str_or_none(record.get("companySize")),

        category=category,
        experience_level=_lower_or_none(record.get("experienceLevel")),
        workplace_type=_lower_or_none(record.get("workplaceType")),
        working_time=_lower_or_none(record.get("workingTime")),

        salary_min=primary.minimum if primary else None,
        salary_max=primary.maximum if primary else None,
        salary_currency=primary.currency if primary else None,
        salary_unit=primary.unit if primary else None,
        salary_is_gross=primary.is_gross if primary else None,
        salary_contract_type=primary.contract_type if primary else None,
        salary_monthly_min=primary.monthly_minimum if primary else None,
        salary_monthly_max=primary.monthly_maximum if primary else None,
        salary_options=([o.as_text() for o in options] if len(options) > 1
                        else None),

        city=_str_or_none(record.get("city")),
        street=_str_or_none(record.get("street")),
        country_code=_upper_or_none(record.get("countryCode")),
        latitude=_float_or_none(record.get("latitude")),
        longitude=_float_or_none(record.get("longitude")),
        locations=cities,
        location_count=location_count or None,

        required_skills=required_names,
        required_skill_levels=required_levels,
        nice_to_have_skills=nice_names,
        languages=language_codes,
        language_levels=language_levels,

        office_days=office_days,
        remote_days=remote_days,
        remote_interview=remote_interview,
        open_to_hire_ukrainians=ukrainians,

        is_super_offer=_bool_or_none(record.get("isSuperOffer")),
        is_active=_bool_or_none(record.get("isActive")),
        apply_method=_lower_or_none(record.get("applyMethod")),
        apply_url=_str_or_none(record.get("applyUrl")),

        published_at=_iso(record.get("publishedAt")),
        last_published_at=_iso(record.get("lastPublishedAt")),
        expires_at=_iso(record.get("expiredAt")),

        description=_plain_text(record.get("body")),

        data_source=data_source,
        page=page,
        position=position,
    )


# ---------------------------------------------------------------------------
# The flight payload on a rendered listing page
# ---------------------------------------------------------------------------

_FLIGHT_RE = re.compile(
    r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)', re.S)


def flight_payload(html: Optional[str]) -> str:
    """The React flight payload of a rendered page, decoded and joined.

    Next.js App Router streams its payload as a series of
    `self.__next_f.push([1, "..."])` calls whose argument is a
    JSON-escaped string fragment. The fragments must be concatenated AFTER
    unescaping, not before: a record can be split across two pushes, and
    two captures here were.
    """
    if not html:
        return ""
    parts: List[str] = []
    for match in _FLIGHT_RE.finditer(html):
        try:
            parts.append(json.loads('"' + match.group(1) + '"'))
        except ValueError:
            continue
    return "".join(parts)


# An offer record in the flight payload always opens with `applyUrl`, which
# is the first key in the site's own serialisation order. Anchoring on a
# KEY rather than on a CSS class is the same reasoning CLAUDE.md §4 gives
# for anchoring on a URL pattern: the class names here are build hashes and
# the key names are the site's API contract.
_FLIGHT_RECORD_RE = re.compile(r'\{"applyUrl":')


def _json_object_at(text: str, start: int) -> Optional[str]:
    """The complete JSON object beginning at `start`, or None.

    A brace counter that knows about strings and escapes — a job
    description containing a `}` inside a quoted string would otherwise end
    the object early and every following record would be lost.
    """
    depth = 0
    index = start
    length = len(text)
    while index < length:
        char = text[index]
        if char == '"':
            index += 1
            while index < length:
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == '"':
                    break
                index += 1
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
        index += 1
    return None


def offers_from_flight(payload: str) -> List[Dict[str, Any]]:
    """Offer records out of a decoded flight payload."""
    out: List[Dict[str, Any]] = []
    for match in _FLIGHT_RECORD_RE.finditer(payload or ""):
        chunk = _json_object_at(payload, match.start())
        if not chunk:
            continue
        try:
            record = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(record, dict) and (record.get("guid") or record.get("id")):
            out.append(record)
    return out


_FLIGHT_META_RE = re.compile(
    r'"meta":\{"from":(?P<from>\d+),"totalItems":(?P<total>\d+)')


def meta_from_flight(payload: str) -> Dict[str, Any]:
    """`meta.from` / `meta.totalItems` as the rendered page states them."""
    match = _FLIGHT_META_RE.search(payload or "")
    if not match:
        return {}
    return {"from": int(match.group("from")),
            "totalItems": int(match.group("total"))}


def site_total_from_flight(payload: str) -> Optional[int]:
    """The board-wide offer count the rendered page embeds.

    The page carries an `OFFERS_COUNT` query holding the real total
    (19,376 when the homepage was captured) beside the capped
    `totalItems`. Reading it means `--route ssr` can report the cap
    honestly without a second request.
    """
    match = re.search(r'"data":\{"count":(\d+)\}[^}]*\},"queryKey":\["OFFERS_COUNT"',
                      payload or "")
    if match:
        return int(match.group(1))
    match = re.search(r'"queryKey":\["OFFERS_COUNT"', payload or "")
    if not match:
        return None
    window = payload[max(0, match.start() - 900):match.start()]
    counts = re.findall(r'"count":(\d+)', window)
    return int(counts[-1]) if counts else None


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------

_LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S)


def jsonld_blocks(html: Optional[str]) -> List[Any]:
    """Every JSON-LD block on a page.

    Counted before a line of the parser was written, which CLAUDE.md §15
    asks for and which decided this repo's whole shape: a rendered listing
    page publishes exactly TWO blocks, a `CollectionPage` and a
    `BreadcrumbList`. The `CollectionPage` carries `hasPart` — a list of
    100 bare URLs with `@type: CreativeWork` and NOTHING else: no title, no
    company, no salary, no id.

    So JSON-LD is not the primary path here and cannot be: it names the
    offers on the page and publishes not one fact about any of them. The
    flight payload beside it carries all 100 records in full. This function
    exists so `itemlist_urls` can cross-check what the page claims to list
    against what was parsed out of it.
    """
    out: List[Any] = []
    for match in _LD_JSON_RE.finditer(html or ""):
        try:
            out.append(json.loads(match.group(1)))
        except ValueError:
            continue
    return out


def itemlist_urls(html: Optional[str]) -> List[str]:
    """The offer URLs the page's own `CollectionPage` names."""
    out: List[str] = []
    for block in jsonld_blocks(html):
        nodes = block if isinstance(block, list) else [block]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            parts = node.get("hasPart")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if isinstance(part, dict):
                    url = _str_or_none(part.get("url"))
                    if url:
                        out.append(url)
    return out


# ---------------------------------------------------------------------------
# Sitemaps
# ---------------------------------------------------------------------------

SITEMAP_INDEX = BASE + "/sitemaps/active-jobs.xml"
EXPIRED_SITEMAP_INDEX = BASE + "/sitemaps/expired-jobs.xml"

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


def sitemap_locs(xml: Optional[str]) -> List[str]:
    """Every `<loc>` in a sitemap or sitemap index."""
    return [_html.unescape(m) for m in _LOC_RE.findall(xml or "")]


def sitemap_offer_slugs(xml: Optional[str]) -> List[str]:
    """Offer slugs out of a sitemap part file, in document order.

    robots.txt advertises eight sitemaps of its own, so this is the route
    the site invites — `active-jobs.xml` held 10,646 offer URLs with
    `lastmod` timestamps on 2026-09-18. It is an index: the outer file
    names `part0.xml`, which is where the URLs are.
    """
    out: List[str] = []
    seen = set()
    for loc in sitemap_locs(xml):
        slug = slug_from_url(loc)
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def sitemap_is_index(xml: Optional[str]) -> bool:
    return "<sitemapindex" in (xml or "").lower()


# ---------------------------------------------------------------------------
# Page-level results
# ---------------------------------------------------------------------------


@dataclass
class ListingPage:
    """One listing response: its rows, and what it says about itself.

    The shape every engine in this family expects from a listing parser.
    `total_items` is the site's own capped figure and `site_total` is what
    the board actually holds; keeping both is what lets a run report
    `complete` honestly while still saying it holds half the board
    (CLAUDE.md §21).
    """

    rows: List[JobPosting] = field(default_factory=list)
    records_in_payload: int = 0
    urls_in_itemlist: int = 0
    requested_page: Optional[int] = None
    echoed_page: Optional[int] = None
    pages_available: Optional[int] = None
    page_size: Optional[int] = None
    page_repeated: bool = False
    total_items: Optional[int] = None
    site_total: Optional[int] = None
    offset: Optional[int] = None
    next_offset: Optional[int] = None

    @property
    def total_jobs(self) -> Optional[int]:
        return self.total_items

    @property
    def capped_by_site(self) -> Optional[bool]:
        """True when the board holds more than this query can reach."""
        if self.site_total is None or self.total_items is None:
            return None
        return self.site_total > self.total_items


def parse_offers_response(text: Optional[str], *, url: str = "",
                          page: Optional[int] = None,
                          items: int = DEFAULT_ITEMS_COUNT,
                          site_total: Optional[int] = None,
                          scraped_at: Optional[str] = None) -> ListingPage:
    """Rows from one offers-endpoint response.

    `page` is threaded in rather than defaulted. CLAUDE.md §18 names the
    arithmetic bug that prevents: `position` restarts at 1 on every page,
    so a `page` column that is 1 on every row makes `page`+`position`
    collide and the position column worthless.
    """
    stamp = scraped_at or utc_now()
    payload = parse_json(text)
    records = offers_from_payload(payload)
    meta = meta_from_payload(payload)
    rows: List[JobPosting] = []
    for index, record in enumerate(records, start=1):
        row = _row_from_record(record, data_source="api", url=url, page=page,
                               position=index, scraped_at=stamp)
        if row is not None:
            rows.append(row)
    total = _int_or_none(meta.get("totalItems"))
    offset = _int_or_none(meta.get("from"))
    next_meta = meta.get("next") if isinstance(meta.get("next"), dict) else {}
    size = _int_or_none(meta.get("itemsCount")) or len(records) or items
    echoed = (offset // size + 1) if (offset is not None and size) else None
    return ListingPage(
        rows=rows,
        records_in_payload=len(records),
        urls_in_itemlist=0,
        requested_page=page,
        echoed_page=echoed,
        pages_available=pages_available_for(total, items),
        page_size=len(records) or None,
        # The endpoint echoes the offset it served, so a page that came
        # back as a repeat of an earlier one announces itself rather than
        # being inferred from the rows.
        page_repeated=bool(page and echoed and page > 1 and echoed == 1),
        total_items=total,
        site_total=site_total,
        offset=offset,
        next_offset=_int_or_none(next_meta.get("cursor")),
    )


def parse_ssr_listing(html: Optional[str], *, url: str = "",
                      page: Optional[int] = None,
                      scraped_at: Optional[str] = None) -> ListingPage:
    """Rows from a rendered listing page's flight payload."""
    stamp = scraped_at or utc_now()
    payload = flight_payload(html)
    records = offers_from_flight(payload)
    meta = meta_from_flight(payload)
    rows: List[JobPosting] = []
    for index, record in enumerate(records, start=1):
        row = _row_from_record(record, data_source="ssr", url=url, page=page,
                               position=index, scraped_at=stamp)
        if row is not None:
            rows.append(row)
    return ListingPage(
        rows=rows,
        records_in_payload=len(records),
        urls_in_itemlist=len(itemlist_urls(html)),
        requested_page=page,
        # A rendered page always answers with the first slice, whatever was
        # asked of it — see `page_url`.
        echoed_page=1 if records else None,
        pages_available=1 if records else 0,
        page_size=len(records) or None,
        page_repeated=bool(page and page > 1 and records),
        total_items=_int_or_none(meta.get("totalItems")),
        site_total=site_total_from_flight(payload),
        offset=_int_or_none(meta.get("from")),
        next_offset=None,
    )


def parse_detail_response(text: Optional[str], *, url: str = "",
                          scraped_at: Optional[str] = None
                          ) -> Optional[JobPosting]:
    """One row from one offer's endpoint response, or None.

    None means the body was not a single-offer payload — a 404 page, or the
    listing that `/offers/` with an empty slug answers with. The caller
    tells those apart through the HTTP status; this only refuses to invent
    a row.
    """
    payload = parse_json(text)
    if not looks_like_detail_record(payload):
        return None
    return _row_from_record(payload, data_source="detail", url=url,
                            scraped_at=scraped_at or utc_now())


# ---------------------------------------------------------------------------
# Facets
# ---------------------------------------------------------------------------

# The groups `/offers/facets/count` publishes, with what each one counts.
# Every group but one partitions the board — its counts sum to the total.
# The exceptions are named here because a reader summing them would
# otherwise conclude the site contradicts itself:
#
#   workplaceTypes    sums to 19,387  = the board
#   workingTimes      sums to 19,387  = the board
#   experienceLevels  sums to 19,387  = the board
#   employmentTypes   sums to 36,996  — an offer may quote several
#                                       contract types, and most do
#   languages         sums to 15,535  — an offer may require several, and
#                                       many require none
#   publishedSinceDays sums to 22,127 — CUMULATIVE windows (1, 7, 14, 30
#                                       days), not a partition at all
FACET_GROUPS = ("workplaceTypes", "employmentTypes", "workingTimes",
                "experienceLevels", "languages", "publishedSinceDays")

CUMULATIVE_FACET_GROUPS = ("publishedSinceDays",)
OVERLAPPING_FACET_GROUPS = ("employmentTypes", "languages")

CATEGORY_FACET_GROUP = "categories"


# `--mode facets` reads TWO endpoints — the facet groups and the category
# counts — and they are genuinely one answer: the site splits categories out
# of `/offers/facets/count` for its own UI reasons, while a user asking
# "what is on this board" wants both.
#
# They are carried through the engines' single-body plumbing as one string
# joined with this separator, rather than by giving the fetch path a second
# shape — every retry, classification, dump and coverage check in those
# engines works on one body per page. The format lives HERE rather than in
# an engine because `detect_page_state` has to understand it too: a
# classifier that tried to parse the joined string as JSON would call a
# perfectly good pair of responses a parse_error, which is exactly what it
# did before this moved.
FACETS_JOIN = "\n/*---justjoin-categories---*/\n"


def split_facets_body(body: Optional[str]) -> Tuple[str, Optional[str]]:
    """(facets json, categories json or None)."""
    text = body or ""
    if FACETS_JOIN in text:
        head, _, tail = text.partition(FACETS_JOIN)
        return head, tail
    return text, None


def parse_facets_response(text: Optional[str], *,
                          categories_text: Optional[str] = None,
                          site_total: Optional[int] = None,
                          url: str = "",
                          scraped_at: Optional[str] = None) -> List[Facet]:
    """The board's own taxonomy with its own counts.

    A different KIND of object from a job posting, so it gets its own row
    class rather than being bent into `JobPosting` (CLAUDE.md §9). What
    makes it worth a mode: these counts are NOT capped at 10,000. They
    describe all 19,381 offers, which makes them the tool for planning a
    run that reaches past the cap — 25 category runs of at most 1,991 rows
    each cover the board where one unfiltered run cannot.
    """
    stamp = scraped_at or utc_now()
    payload = parse_json(text)
    out: List[Facet] = []

    def add(group: str, key: Any, count: Any) -> None:
        name = _str_or_none(key)
        if name is None:
            return
        out.append(Facet(
            source=SOURCE_DEFAULT,
            scraped_at=stamp,
            url=url or facets_api_url(),
            sku="%s:%s" % (group, name),
            title=name,
            facet_group=group,
            facet_key=name,
            offer_count=_int_or_none(count),
            is_cumulative=group in CUMULATIVE_FACET_GROUPS,
            overlaps_other_keys=group in (OVERLAPPING_FACET_GROUPS),
            site_total=site_total,
        ))

    if isinstance(payload, dict):
        for group, items in payload.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict):
                    add(group, item.get("key"), item.get("count"))

    categories = parse_json(categories_text)
    if isinstance(categories, list):
        for item in categories:
            if isinstance(item, dict):
                add(CATEGORY_FACET_GROUP, item.get("key"), item.get("count"))

    return out


def parse_count_response(text: Optional[str]) -> Optional[int]:
    """The board-wide offer count from `/offers/count`."""
    payload = parse_json(text)
    if isinstance(payload, dict):
        return _int_or_none(payload.get("count"))
    return None


# ---------------------------------------------------------------------------
# What a run should say about the cap
# ---------------------------------------------------------------------------


def cap_report(total_items: Optional[int], site_total: Optional[int],
               rows_collected: int) -> Dict[str, Any]:
    """The arithmetic a sidecar records beside `status: complete`.

    CLAUDE.md §21: "complete" and "exhaustive" are different words. A run
    that fetches all 10,000 reachable rows really did fetch everything the
    site will serve for that query, and it is also a 52% sample of a board
    holding 19,381. Recording only "complete" is lying by omission, and the
    third thing `diff_runs.py`'s `removed` can mean on this site is
    "outside this run's slice of a capped result set".

    `capped_by_site` asks whether THIS QUERY was truncated — whether its
    own `totalItems` hit the ceiling — and not whether the board is bigger
    than the query. Those are different questions and conflating them is
    how a `--category python` run matching 439 offers would report itself
    as capped: it is not, it can reach every one of its 439. Only a query
    whose result set the site cut off is capped.
    """
    reachable = None
    if total_items is not None:
        reachable = min(int(total_items), MAX_FROM)
    # The site reports `totalItems` as exactly the ceiling when it has
    # truncated, so that equality IS the signal. A query matching exactly
    # 10,000 offers would be indistinguishable from a truncated one — the
    # site gives nothing better to read, and over-reporting the cap is the
    # safe side of that coin.
    capped = (None if total_items is None
              else int(total_items) >= MAX_FROM)
    matched_share = None
    if total_items:
        matched_share = round(100.0 * rows_collected / min(int(total_items),
                                                           MAX_FROM), 1)
    board_share = None
    if site_total:
        board_share = round(100.0 * rows_collected / site_total, 1)
    return {
        "total_results": total_items,
        "site_total": site_total,
        "reachable_max": reachable,
        "capped_by_site": capped,
        "rows_collected": rows_collected,
        # Of what this query can reach — the number that says whether the
        # run finished its job.
        "share_of_query_pct": matched_share,
        # Of the whole board. Expected to be small on any filtered run, and
        # it is context rather than a shortfall.
        "share_of_board_pct": board_share,
    }


# ---------------------------------------------------------------------------
# Run planning — shared by all three engines
# ---------------------------------------------------------------------------
#
# These five are pure functions of the parsed arguments, with no driver and
# no JavaScript in them, and they live here rather than in each engine for
# the reason CLAUDE.md §1 gives for `page_flow`: three copies of "which URL
# does this run fetch" would drift, and the drift would be silent — one
# engine reading the endpoint where its twin reads the rendered page, on
# the same command line.
#
# It is the same argument §17 makes about a shared module's signature, one
# step earlier: an engine cannot disagree with its twins about a decision
# it does not make.

import csv
import logging

log = logging.getLogger("product_parser")


def filters_from_args(args) -> Filters:
    """The endpoint filters this run asks for.

    A `--url` naming a browsable listing is translated into the same
    filters the site's own front end would send for it, so a user can paste
    an address out of their browser and the flags stay optional. Explicit
    flags win over anything read from the URL — CLAUDE.md §3's precedence
    rule, applied to filters rather than to credentials.
    """
    from_url = (filters_from_listing_url(args.url)
                if args.url and route_of(args.url) == "listing" else Filters())
    return Filters(
        category=args.category or from_url.category,
        city=args.city or from_url.city,
        city_radius=args.city_radius,
        remote=("remote" if args.remote else from_url.remote),
        keyword=args.keyword or from_url.keyword,
        experience=args.experience or None,
        language=args.language or None,
        employment=args.employment or None,
        working_time=args.working_time or None,
        with_salary=bool(args.with_salary),
    )



def target_url(args) -> str:
    """The address this run actually fetches.

    Every mode has a correct default, so a bare
    `python3 playwright_scraper.py` is a working command rather than a
    usage error.

    A `--url` naming ONE offer is honoured exactly. A `--url` naming a
    browsable listing is NOT fetched as given under `--route api`: its
    filters are read off it and the endpoint is asked instead, because the
    endpoint is the route with pagination. The run's log says so, so nobody
    has to infer it from a URL in the sidecar that is not the one they
    passed.
    """
    if args.mode == "facets":
        return facets_api_url()

    if args.mode == "offer":
        if args.url:
            slug = offer_slug_or_url(args.url)
            built = detail_api_url(slug) if slug else None
            if built:
                return built
        # Page 1 of an offer run is the first slug in the enumeration,
        # which `_plan_page_urls` builds. Until then, the board's first
        # page is a sensible thing to have asked for.
        return api_url(items=1)

    if args.route == "ssr":
        if args.url and route_of(args.url) == "listing":
            return args.url
        filters = filters_from_args(args)
        if filters.category or filters.city or filters.remote:
            return listing_url(city=filters.city or (
                "remote" if filters.remote else None),
                category=filters.category)
        return DEFAULT_LISTING_URL

    if args.url and route_of(args.url) == "api_offers":
        return args.url
    return api_url(items=args.per_page, sort=args.sort, order=args.order,
                   filters=filters_from_args(args))



def is_single_offer_url(args) -> bool:
    """Whether `--url` names one specific offer, rather than a route to walk.

    A `--mode offer` run given an offer URL fetches exactly that offer and
    nothing else — no sitemap, no enumeration, one row. That is the "look
    this one up" case and it should cost one request.
    """
    return bool(args.url) and route_of(args.url) in ("offer", "api_offer")



def slugs_from_file(path: str) -> List[str]:
    """Offer slugs out of a file, accepting the three shapes people have.

    A previous run's JSON output, a CSV of it, or a plain list of slugs or
    URLs one per line. Reading a previous run's output is the point:
    `--mode listings` gathers ten thousand rows cheaply and `--mode offer`
    enriches whichever of them you actually care about, and making the user
    cut a column out first would be a worse tool for no reason.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        log.error("Could not read --slugs-file %s: %s", path, exc)
        return []

    slugs: List[str] = []
    lines = text.splitlines()
    first = lines[0] if lines else ""
    stripped = text.lstrip()

    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            log.error("--slugs-file %s looks like JSON but did not parse.",
                         path)
            payload = None
        for row in (payload if isinstance(payload, list) else []):
            if isinstance(row, dict):
                value = row.get("slug") or row.get("url")
                slug = offer_slug_or_url(value) if value else None
                if slug:
                    slugs.append(slug)
    elif "," in first and ("slug" in first or "url" in first):
        # A CSV, and only when its HEADER names a column this can read. A
        # comma alone is not enough: an offer slug cannot contain one, but
        # a hand-written list might be comma-separated, and guessing wrong
        # would silently read zero offers out of a file full of them.
        for row in csv.DictReader(lines):
            value = row.get("slug") or row.get("url")
            slug = offer_slug_or_url(value) if value else None
            if slug:
                slugs.append(slug)
    else:
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            slug = offer_slug_or_url(line)
            if slug:
                slugs.append(slug)

    seen = set()
    out = []
    for slug in slugs:
        if slug not in seen:
            seen.add(slug)
            out.append(slug)
    log.info("--slugs-file %s: %d distinct offer(s).", path, len(out))
    return out



def parse_page(text: Optional[str], *, mode: str = DEFAULT_MODE,
               route: str = DEFAULT_ROUTE, url: str = "",
               page: Optional[int] = None,
               items: int = DEFAULT_ITEMS_COUNT,
               scraped_at: Optional[str] = None) -> List[Any]:
    """Rows for one body, from explicit parameters.

    The low-level entry point. `parse_for_mode` below is the one the
    engines call — it reads the same decision off a parsed argument
    namespace — and both go through here so a caller that has parameters
    rather than an `args` object (scraper_api_client.py) cannot drift from
    the engines about how a body is read.
    """
    if mode == "offer":
        row = parse_detail_response(text, url=url, scraped_at=scraped_at)
        return [row] if row is not None else []
    if mode == "facets":
        body, categories = split_facets_body(text)
        return parse_facets_response(body, categories_text=categories,
                                     url=url, scraped_at=scraped_at)
    if route == "ssr":
        return parse_ssr_listing(text, url=url, page=page,
                                 scraped_at=scraped_at).rows
    return parse_offers_response(text, url=url, page=page, items=items,
                                 scraped_at=scraped_at).rows


def parse_for_mode(body: str, url: str, args, page_num: int = 1):
    """(rows, listing) for this mode and route. `listing` is None off the
    listing routes.

    `parse_detail_response` returns a single row or None; wrapping it here
    keeps every caller downstream — dedupe, merge, coverage logging, the
    writers — working on one shape instead of branching on the mode again.

    `page_num` is threaded through rather than defaulted, because
    `position` restarts at 1 on every page: without the page number beside
    it, a row from page 2 claims the same position as one from page 1 and
    the two are indistinguishable in the output. `smoke_test.py` asserts
    page+position is unique across a multi-page run for exactly that
    reason.
    """
    if args.mode == "offer":
        row = parse_detail_response(body, url=url)
        return ([row] if row is not None else []), None
    if args.mode == "facets":
        # Fetched in one request, and the categories come from a second
        # route that `_fetch_one_page` attaches to the body — see
        # `_facets_body`.
        body, categories = split_facets_body(body)
        return parse_facets_response(body, categories_text=categories,
                                     url=url), None
    if args.route == "ssr":
        listing = parse_ssr_listing(body, url=url, page=page_num)
        return listing.rows, listing
    listing = parse_offers_response(body, url=url, page=page_num,
                                    items=args.per_page)
    return listing.rows, listing




# ---------------------------------------------------------------------------
# What did the site just answer with?
# ---------------------------------------------------------------------------

# The states this site can answer in. Each one wants a different response,
# and `page_flow.STATE_POLICY` is where that decision lives.
PAGE_STATES = ("content", "empty", "blocked", "not_found", "cap_exceeded",
               "parse_error", "unknown")

# Below this many bytes a body is too small to be a served page or a real
# payload. An offers response is 250 KB at the default page size and the
# smallest rendered page captured here was 1.7 MB; the site's 404 JSON is a
# few dozen bytes and is caught by its status first.
_MIN_BODY_BYTES = 200


def detect_page_state(body: Optional[str], status: Optional[int] = None,
                      url: str = "", mode: str = DEFAULT_MODE,
                      route: str = DEFAULT_ROUTE) -> str:
    """Name what justjoin.it answered with.

    The signals are ordered by how much each one PROVES, not by how cheap
    it is to test — CLAUDE.md §17's classification-order trap, where a
    threshold heuristic ran ahead of an unambiguous marker and reported
    exit 3 for a correct answer. So:

      1. a body that parses as this site's own payload and holds records
         is `content`, whatever else is in it;
      2. the same payload holding zero records is `empty` — the site
         answered exactly what was asked and there is nothing in it, which
         is an answer and not a refusal;
      3. HTTP 404 is `not_found` — an offer taken down between an
         enumeration and its fetch is an ordinary event in `--mode offer`,
         and it is not a block;
      4. HTTP 500 on an offers URL asking past the cap is `cap_exceeded`,
         which is OUR planning bug rather than the site's fault and should
         say so;
      5. a vendor challenge marker, or HTTP 403, is `blocked`;
      6. a substantial body this parser could not read is `parse_error` —
         NOT "zero offers". A served response that parses to nothing is
         this repo's bug, and reporting it as an empty board sends the
         reader to check their filters instead of the parser (§20);
      7. anything else is `unknown`: wait and retry, do not spend.
    """
    text = body or ""
    stripped = text.strip()

    # (1) and (2) — the site's own payload, on whichever route.
    if mode == "facets":
        stripped, _categories = split_facets_body(stripped)
        stripped = stripped.strip()
        payload = parse_json(stripped)
        if isinstance(payload, dict) and any(
                isinstance(v, list) for v in payload.values()):
            return "content" if any(v for v in payload.values()
                                    if isinstance(v, list)) else "empty"
        if isinstance(payload, list):
            return "content" if payload else "empty"
    elif mode == "offer":
        payload = parse_json(stripped)
        if looks_like_detail_record(payload):
            return "content"
        # The empty-slug hazard: `/offers/` answers 200 with the LISTING.
        # Calling that `content` would publish one row made of a hundred
        # offers' worth of nothing, so it is a parse_error and gets dumped.
        if isinstance(payload, dict) and "data" in payload:
            return "parse_error"
    elif route == "ssr":
        payload = flight_payload(stripped)
        if payload:
            return "content" if offers_from_flight(payload) else "empty"
    else:
        payload = parse_json(stripped)
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return "content" if payload["data"] else "empty"

    # (3) A 404 is the site saying the address is not a thing.
    if status == 404:
        return "not_found"

    # (4) Walking off the end of a capped result set. `from=10000` and
    # `from=10100` are both HTTP 500 — the site manufactures an error that
    # reads like a bug in this scraper, so name it for what it is (§21).
    if status == 500 and _asks_past_cap(url):
        return "cap_exceeded"

    # (5) A refusal. The marker scan is bounded and entity-unescaped; see
    # `_normalised_head`.
    if detect_bot_challenge(stripped):
        return "blocked"
    if status in (401, 403, 429):
        return "blocked"

    # (6) Something substantial that this parser could not read. A weak
    # secondary check only, and consulted LAST: a response built out of the
    # site's own assets is one the site served, so the failure is ours.
    if len(stripped) >= _MIN_BODY_BYTES:
        if count_site_assets(stripped) >= MIN_ASSET_MARKERS:
            return "parse_error"
        if stripped.startswith("{") or stripped.startswith("["):
            return "parse_error"

    return "unknown"


def _asks_past_cap(url: str) -> bool:
    """True for an offers URL whose `from` is at or past the site's cap."""
    _, _, path, query = _split_url(url or "")
    if not _API_OFFERS_RE.match(path):
        return False
    for key, value in urllib.parse.parse_qsl(query):
        if key == "from":
            offset = _int_or_none(value)
            if offset is not None and offset >= MAX_FROM:
                return True
    return False
