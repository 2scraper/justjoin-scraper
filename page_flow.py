"""
page_flow.py
------------
The retry / solve / blocked decision, as DATA rather than as three copies of
an if-chain (CLAUDE.md §1).

justjoin.it answers a request six ways, and five of them want a different
response:

    a payload holding offer records                  -> parse
    a payload holding none                           -> parse, it is an answer
    HTTP 404 on an offer                             -> stop, it is not a block
    HTTP 500 from asking past the site's 10,000 cap  -> stop, it is OUR bug
    a body we could not read                         -> parse_error, dump it
    something else entirely                          -> wait, then retry

Three copies of that triage across three engines would drift, and the drift
would be silent — one engine reporting exit 3 where its twin reports exit 0
on the same response.

There is no `challenge` state distinct from `blocked` because, measured on
2026-09-18, justjoin.it rendered none on any route this scraper reads:
sixteen candidate vendor markers counted across seven captures, all zero,
and identical 200s from curl, from python-requests, from wget and from a
headless Chromium. `blocked` is in the policy table anyway — an ungated
site today is not an ungated site forever, and a run should be able to say
"blocked" rather than "empty" on the day that changes.

Nothing here imports a browser, and **no JavaScript crosses this boundary**:
Selenium's `execute_script` takes a function BODY with an explicit `return`
while Playwright and pyppeteer take `() => expr`, so a shared snippet would
quietly acquire one driver's dialect. The callbacks below are named for the
OPERATION instead, and each engine spells it in its own dialect (§1).
"""

import logging
import re
from typing import Callable, List, Optional

from product_parser import (DEFAULT_ITEMS_COUNT, DEFAULT_ROUTE,  # noqa: F401
                            MAX_FROM, api_page_url, detail_api_url,
                            detect_bot_challenge, detect_page_state,
                            pages_available_for, route_of,
                            sitemap_is_index, sitemap_locs,
                            sitemap_offer_slugs, slugs_from_file,
                            SITEMAP_INDEX)

log = logging.getLogger("page_flow")


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

# How many offer links mean "this response is the board".
#
# `> 1` on purpose, per CLAUDE.md §5: waiting for ONE match resolves on
# something unrelated — a nav link, a footer — long before the grid is
# really there.
#
# Four, and the CEILING on this number is the interesting part, because on
# this site the anchor count goes DOWN as the page settles. Measured in a
# live headless Chromium on 2026-09-18, on
# `/job-offers/all-locations`:
#
#     selector                      at domcontentloaded   after 4s   after 9s
#     a[href*="/job-offer/"]                200              18         18
#     [data-index]                          100               9          9
#
# The list is VIRTUALISED. justjoin.it server-renders all 100 tiles and
# React then replaces them with a window of about nine, so a threshold set
# from the served markup — 100, or even 50 — would be satisfied on the
# first poll and then be false for the rest of the run. Four holds on both
# sides of hydration.
#
# Which is also why this is a readiness signal ONLY. The rows come out of
# the flight payload (`--route ssr`) or out of the endpoint's JSON
# (`--route api`), both complete in the first response, so a page whose
# grid never paints still parses to all 100 rows — and the engines
# therefore do not gate parsing on this.
MIN_CARD_MATCHES = 4

# Anchored on a URL pattern, never a CSS class: justjoin.it's classes are
# build-generated (`mui-1vaayb6`) and churn on every deploy, while
# `/job-offer/{slug}` is a contract with search engines (§4).
#
# One spelling is enough here, and that is worth stating because a sibling
# repo needed two: that site rewrites every job anchor during hydration, so
# its served and live DOMs share no selector at all. justjoin.it keeps the
# same href on both sides and only reduces how many of them exist.
READY_SELECTOR_LISTING = 'a[href*="/job-offer/"]'

# An offer page states its heading server-side.
READY_SELECTOR_OFFER = "h1"

CONTENT_TIMEOUT_MS = 45_000
CONTENT_TIMEOUT_MS_OFFER = 30_000


def ready_selector(mode: str) -> str:
    if mode == "offer":
        return READY_SELECTOR_OFFER
    return READY_SELECTOR_LISTING


def min_matches(mode: str) -> int:
    return 1 if mode == "offer" else MIN_CARD_MATCHES


def content_timeout_ms(mode: str) -> int:
    return CONTENT_TIMEOUT_MS_OFFER if mode == "offer" else CONTENT_TIMEOUT_MS


def needs_readiness_wait(mode: str, route: str = DEFAULT_ROUTE) -> bool:
    """Whether this fetch has a DOM worth waiting on at all.

    False for every route but `--route ssr`, and saying so saves a run real
    time rather than being a nicety. `--mode listings --route api`,
    `--mode offer` and `--mode facets` all fetch a JSON document: it is
    complete when the navigation resolves, there is no grid to paint, and
    polling a selector against Chromium's JSON viewer would simply burn the
    full 45-second budget on every page before timing out. A readiness wait
    that can never succeed is worse than none — CLAUDE.md §8 records what
    that cost in a sibling repo, where it turned a background captcha into
    a paid solve on a page that already held every row.
    """
    return mode == "listings" and route == "ssr"


# How long to keep polling for an anchor, and how often.
#
# Polled through `count(selector)` — a callback each engine implements with
# its own `querySelectorAll` call — and NEVER by handing the browser a
# string to evaluate. CLAUDE.md §18: a site whose Content-Security-Policy
# omits `unsafe-eval` kills `wait_for_function` with an `EvalError` and
# takes the run down with exit 1, on the site's most obvious URL.
#
# justjoin.it was measured for this on 2026-09-18 and does NOT have the
# problem: its listing response sends no `Content-Security-Policy` header
# at all, and `wait_for_function` with a string argument resolved normally
# in a live headless Chromium. The habit is kept anyway, because it costs
# nothing on a site that would have allowed the string and because a CSP
# is a header a site can add on any deploy — but the honest statement is
# that this is insurance here rather than a fix, and nobody should read
# the paragraph above as a measurement of THIS site.
READY_POLL_MS = 500


def wait_for_count(count: Callable[[str], int], selector: str, minimum: int,
                   timeout_ms: int, sleep_ms: Callable[[int], None]) -> int:
    """Poll `count(selector)` until it reaches `minimum` or the budget runs out.

    Returns the last count seen, so a caller can report "3 of 4 expected"
    rather than only that it timed out.
    """
    waited = 0
    seen = 0
    while waited <= timeout_ms:
        seen = count(selector)
        if seen >= minimum:
            return seen
        sleep_ms(READY_POLL_MS)
        waited += READY_POLL_MS
    return seen


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------

def classify(body: Optional[str], status: Optional[int] = None,
             url: str = "", mode: str = "listings",
             route: str = DEFAULT_ROUTE) -> str:
    """Name what justjoin.it answered with. See product_parser.detect_page_state.

    The argument ORDER is the contract: every engine calls
    `classify(body, status, url, mode, route)`. A sibling repo shipped
    `classify(html, url=...)` in two of three engines against a callee that
    took `status` second, and both crashed on their first fetch — invisible
    to import, `--help`, `compileall` and 400+ green offline assertions,
    because none of those calls a function the way a live run does (§17).
    `smoke_test.py` binds every engine's call against this signature for
    exactly that reason.
    """
    return detect_page_state(body or "", status, url, mode, route)


STATE_POLICY = {
    # A payload holding offer records.
    "content":      {"retry": False, "solve": False, "blocked": False, "parse": True},
    # A payload the site served that holds none. The request was answered
    # exactly as asked and there is nothing in it — a real answer, and
    # EXIT_NO_PRODUCTS rather than EXIT_BLOCKED. Reporting it as blocked
    # sends a user hunting for a proxy problem that is not there. On this
    # site it is the ordinary result of a filter nothing matches.
    "empty":        {"retry": False, "solve": False, "blocked": False, "parse": True},
    # Never yet observed on this site. `solve` is True so that a challenge
    # appearing tomorrow is met with the tools this repo already has rather
    # than with a code change; `retry` is True because on every other site
    # in this family a different exit clears a refusal far more cheaply
    # than a solve does.
    "blocked":      {"retry": True,  "solve": True,  "blocked": True,  "parse": False},
    # A 404. Retrying an address that does not exist is pure waste, and it
    # is not a block — saying so stops a user rotating proxies over a
    # delisted offer. Reachable in ordinary use: `--mode offer` works from
    # an enumeration that can outlive a posting, and justjoin.it publishes
    # an `expired-jobs` sitemap precisely because offers come down.
    "not_found":    {"retry": False, "solve": False, "blocked": False, "parse": False},
    # HTTP 500 from asking for an offset at or past the site's 10,000 cap.
    #
    # Not a retry and not a block: the address is one this scraper should
    # never have built, and the site answering 500 is it refusing an
    # impossible request rather than failing. A retry would ask the
    # identical impossible question again; reporting it as blocked would
    # send a reader after a proxy. The planner caps offsets so this should
    # be unreachable, and it is in the table because "unreachable" is a
    # claim about today's code (CLAUDE.md §21).
    "cap_exceeded": {"retry": False, "solve": False, "blocked": False, "parse": False},
    # A substantial body this parser could not read. NOT "zero offers": a
    # response the site plainly served that parses to nothing is OUR bug,
    # and reporting it as an empty board sends the reader to check their
    # filters instead of the parser (CLAUDE.md §20). Worth one retry in
    # case a document was caught mid-swap, and always worth a dump.
    "parse_error":  {"retry": True,  "solve": False, "blocked": False, "parse": False},
    # Something that is neither the site nor a recognised refusal — an
    # upstream error page, a proxy's own response, Chromium's network-error
    # page. A wait, not a spend.
    "unknown":      {"retry": True,  "solve": False, "blocked": False, "parse": False},
}


def should_retry(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["retry"]


def should_solve(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["solve"]


def counts_as_blocked(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["blocked"]


def should_parse(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["parse"]


def is_terminal(state: str) -> bool:
    """States where another attempt at the same address cannot help."""
    return state in ("not_found", "cap_exceeded")


# Whether a blocked page is worth re-fetching at all.
#
# True, and CONSULTED rather than merely documented — the engines read it,
# so setting it False really does stop the retry loop. (A sibling repo
# carried this constant with a paragraph of justification and no reader,
# which is the same defect as dead code that looks load-bearing: §17.)
#
# True is the cautious setting rather than a measured one here, and the
# honest statement of the evidence is that there is none: justjoin.it has
# never refused this scraper, so no retry has ever been exercised against a
# real refusal.
RETRY_ON_BLOCKED = True

# How many times to re-fetch a blocked page when there is no proxy pool to
# rotate into.
#
# One. Without a pool every retry leaves from the same address, and an
# address-level refusal answers a second identical request identically.
# WITH a pool the engines retry once per remaining exit instead, because
# there the retry changes the one variable the refusal would depend on.
BLOCK_RETRIES_WITHOUT_POOL = 1

# At most one solve per page. A challenge that survives a solved token is
# not a challenge this run can pass, and a second solve is a second charge
# for the same answer.
SOLVES_PER_PAGE = 1


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def pagination_is_addressable(url: str, route: str = DEFAULT_ROUTE) -> bool:
    """Whether page N of this run can be fetched without walking to it.

    CLAUDE.md §18 says to ask this PER URL rather than per site, and on
    justjoin.it the two listing routes give opposite answers:

      * the offers endpoint IS addressable. It paginates on a numeric
        `from` offset, so page 5's address is knowable without fetching
        page 4 — which is what makes `--concurrency` meaningful here, and
        it is the reason `--route api` is the default.

      * a RENDERED listing page is not addressable, and the failure mode is
        the dangerous one. `?page=2` on `/job-offers/all-locations` does
        not error and does not return an empty page — it answers HTTP 200
        with **page 1 again**, `meta.from` still 0 and a byte-identical
        first offer (measured 2026-09-18). A scraper that built `?page=N`
        would fetch page 1 N times, find no new `guid` on the second,
        conclude the board was exhausted and report a COMPLETE run holding
        one hundred rows out of ten thousand.

    So an `ssr` run plans exactly one page and says why.
    """
    if route == "ssr":
        return False
    return route_of(url or "") == "api_offers"


def pages_to_plan(pages_requested: int, pages_available: Optional[int]) -> int:
    """How many pages a run may ask for, given what the site will serve.

    justjoin.it states `meta.totalItems` on page 1, so unlike a site that
    states nothing there is real arithmetic to do — and CLAUDE.md §7's
    second layer says to use it. `pages_available` is computed from that
    total AND from the site's own 10,000 cap, because asking for the page
    after the cap is an HTTP 500 rather than an empty page.
    """
    wanted = max(1, int(pages_requested or 1))
    if pages_available and pages_available > 0:
        return min(wanted, int(pages_available))
    return wanted


def max_offset_for(items: int) -> int:
    """The largest `from` a run may build for this page size."""
    return max(0, MAX_FROM - max(1, int(items or DEFAULT_ITEMS_COUNT)))


def concurrency_limit(cdp_endpoint: Optional[str]) -> Optional[int]:
    """1 when workers would collide, else None for "no limit imposed here".

    The Scraping Browser API allows ONE live connection per profile, so N
    workers sharing a `pid` collide with `profile_locked`. Several `pid`s,
    one run each, is the way to parallelise that path (§7).
    """
    return 1 if cdp_endpoint else None


def concurrency_for_mode(mode: str, concurrency: int,
                         route: str = DEFAULT_ROUTE) -> int:
    """Workers this mode and route can actually use.

    `facets` is one request. `listings --route ssr` is one page, so any
    worker past the first would have nothing to do. Clamping here rather
    than letting the engine start idle threads keeps the run's own log
    honest about what it did.

    `listings --route api` and `offer` both address every page
    independently and may use every worker they are given — with CLAUDE.md
    §7's warning attached, that concurrency without a proxy pool is a
    faster way to get an address scored than to gather data.
    """
    if mode == "facets":
        return 1
    if mode == "listings" and route == "ssr":
        return 1
    return max(1, int(concurrency or 1))


# ---------------------------------------------------------------------------
# Planning a run — shared by all three engines
# ---------------------------------------------------------------------------
#
# Both take the driver's primitives in rather than importing one, which is
# this module's whole contract: `enumerate_offers` is handed a `fetch_source`
# callable that returns a URL's serialised document, and knows nothing about
# how its caller got it. No JavaScript crosses this boundary either — see
# the module docstring.

_CREDENTIALS_IN_URL_RE = re.compile(
    r"([a-z][a-z0-9+.\-]*://)[^\s/@]+:[^\s/@]+@", re.I)


def mask_secrets(text: str) -> str:
    """Credentials out of an exception message before it reaches a log.

    CLAUDE.md §8: an EXCEPTION MESSAGE is a log. A driver that fails while
    connecting through an authenticated proxy puts the whole URL into the
    text of the error, credentials included — so anything this module
    prints from an exception goes through here first. Host and port are
    KEPT: which exit a run used is the point of the log and is not the
    secret. Global, not first-occurrence: Playwright repeats a CDP endpoint
    five times in one error, and a masker that handles the first prints the
    password the other four times while looking like it works.
    """
    return _CREDENTIALS_IN_URL_RE.sub(r"\1***:***@", text or "")


def plan_page_urls(args, page_one_url: str,
                    pages_available: Optional[int],
                    offer_slugs: Optional[List[str]] = None) -> List[str]:
    """URLs for pages 2..N, decided once before any of them is fetched.

    CLAUDE.md §7 says to plan page URLs up front but to VERIFY first, and
    on justjoin.it the three routes give three different answers:

      * `--mode listings --route api` IS addressable. The endpoint
        paginates on a numeric `from` offset, so page 5's address is
        knowable without fetching page 4 — and the plan is capped twice
        over: by `meta.totalItems`, which page 1 states, and by the site's
        own 10,000 ceiling, past which `from` answers HTTP 500 rather than
        an empty page.

      * `--mode listings --route ssr` is NOT addressable, and the failure
        mode is the dangerous one. `?page=2` on a rendered listing returns
        page 1 again under HTTP 200 (measured 2026-09-18), so a plan built
        from a `?page=N` convention would re-collect page 1 and report a
        complete multi-page run holding a hundred rows of ten thousand.
        One page is planned and the log says why.

      * `--mode offer` plans from a REAL LIST. `offer_slugs` is the
        enumeration (see `_enumerate_offers`), so every planned page is an
        address the site published rather than one this code guessed, and
        walking off the end is impossible: the plan is capped by the length
        of the list.
    """
    if args.mode == "facets":
        return []

    if args.mode == "offer":
        if not offer_slugs:
            # `--url` named one offer. One page is the whole run.
            return []
        wanted = pages_to_plan(args.pages, len(offer_slugs))
        if wanted < args.pages:
            log.info("The enumeration holds %d offer(s) and %d page(s) "
                        "were asked for; planning %d. There is no more "
                        "catalogue to walk to.",
                        len(offer_slugs), args.pages, wanted)
        planned = []
        for slug in offer_slugs[1:wanted]:
            built = detail_api_url(slug)
            if built:
                planned.append(built)
        return planned

    if not pagination_is_addressable(page_one_url, args.route):
        if args.pages > 1:
            log.info(
                "--route ssr serves one page and ignores ?page=N — it "
                "answers HTTP 200 with page 1 again — so %d page(s) were "
                "asked for and 1 will be fetched. Planning a page 2 would "
                "re-collect page 1 and report a complete multi-page run. "
                "Use --route api (the default) for more than 100 rows.",
                args.pages)
        return []

    wanted = pages_to_plan(args.pages, pages_available)
    ceiling = max_offset_for(args.per_page)
    if pages_available and wanted < args.pages:
        log.info("This query reaches %d page(s) of %d at the site's own "
                    "cap; %d were asked for, so %d will be fetched.",
                    pages_available, args.per_page, args.pages, wanted)
    planned = []
    for n in range(2, wanted + 1):
        offset = (n - 1) * args.per_page
        built = None if offset > ceiling else api_page_url(
            page_one_url, n, items=args.per_page)
        if built is None:
            # Past the 10,000 ceiling. Stopping here is the whole point:
            # the next address would answer HTTP 500, an error that reads
            # like a bug in this scraper rather than like the site refusing
            # an impossible request (CLAUDE.md §21).
            log.info("Stopping the plan at page %d: offset %d is at "
                        "justjoin.it's own 10,000 ceiling, and asking past "
                        "it is an HTTP 500 rather than an empty page.",
                        n - 1, MAX_FROM)
            break
        planned.append(built)
    return planned


_PROXY_ERROR_MARKERS = (
    "ERR_PROXY_CONNECTION_FAILED",     # nothing listening / refused
    "ERR_TUNNEL_CONNECTION_FAILED",    # CONNECT rejected by the proxy
    "ERR_PROXY_AUTH_UNSUPPORTED",      # auth scheme we cannot satisfy
    "ERR_PROXY_AUTH_REQUESTED",        # credentials missing or wrong
    "ERR_UNEXPECTED_PROXY_AUTH",
    "ERR_PROXY_CERTIFICATE_INVALID",
)



def enumerate_offers(fetch_source, args) -> List[str]:
    """Every offer slug this run could fetch, in a stable order.

    Read from justjoin.it's OWN sitemap rather than walked out of the
    listing, and the reason is the 10,000 cap: the board held 19,381 offers
    on 2026-09-18 while any one query reaches 10,000, and
    `/sitemaps/active-jobs/part0.xml` listed 10,646 offer URLs with
    `lastmod` timestamps in one 2.5 MB file. It is also the route
    `robots.txt` advertises, which the endpoint is not.

    The outer `active-jobs.xml` is an INDEX — a 63-byte redirect to
    `public.justjoin.com`, which then names one part file. Both hops are
    followed here rather than assumed away, because a second part file
    appearing is exactly how a silent under-count would start.
    """
    if args.slugs_file:
        return slugs_from_file(args.slugs_file)

    slugs: List[str] = []
    seen = set()
    to_read = [SITEMAP_INDEX]
    read = 0
    while to_read and read < 12:
        url = to_read.pop(0)
        read += 1
        try:
            xml = fetch_source(url)
        except Exception as exc:
            log.warning("Sitemap %s did not load (%s); continuing with "
                           "what has been read.", url, mask_secrets(str(exc))[:120])
            continue
        if sitemap_is_index(xml):
            for loc in sitemap_locs(xml):
                if loc not in seen and loc.endswith(".xml"):
                    seen.add(loc)
                    to_read.append(loc)
            continue
        for slug in sitemap_offer_slugs(xml):
            if slug not in seen:
                seen.add(slug)
                slugs.append(slug)

    if not slugs:
        log.error("The sitemap yielded no offer URLs. Pass --url for one "
                     "offer, or --slugs-file with a previous run's output.")
        return []
    log.info("Enumerated %d offer(s) from justjoin.it's own sitemap.",
                len(slugs))
    return slugs


