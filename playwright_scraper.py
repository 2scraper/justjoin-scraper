"""
justjoin-scraper — Playwright edition (primary engine)

Scrapes justjoin.it, the Polish IT job board.

    --mode listings  justjoin.it/api/candidate-api/offers
                     The board. Every offer with its salary, skills,
                     seniority, workplace type, languages and locations.
                     Offset pagination, up to 1,000 rows a request, filtered
                     with --category / --city / --keyword and the rest.
    --mode offer     justjoin.it/api/candidate-api/offers/{slug}
                     One offer in full: the description body, per-skill
                     levels, the company URL and size. Enumerated from the
                     site's own sitemap, or from --url, or from --slugs-file.
    --mode facets    justjoin.it/api/candidate-api/offers/facets/count
                     The board's own taxonomy with its own counts — the tool
                     for planning a run that reaches past the 10,000 cap.

What is different about justjoin.it
===================================

* **Nothing on the read path is gated.** Measured 2026-09-18 from a
  datacentre address (netcup, Nuremberg, AS197540): sixteen candidate
  bot-challenge markers counted across seven captures, every one zero;
  HTTP 200 from curl with no headers at all, from python-requests, from
  wget and from a headless Chromium. No captcha, no proxy and no key is
  needed for any route this engine reads. The paid products buy volume and
  a specific country here, not access — see the README.

  One exception, and it is a single denylist entry rather than a gate:
  a `Python-urllib/*` User-Agent gets HTTP 403 on both hosts. Everything
  else measured was served.

* **The endpoint the front end calls is not the one the page config
  names.** `api.justjoin.it` — the host the site's own `baseApiUrl` points
  at — answered HTTP 503 from nginx to every request tried. The route that
  works is the same-origin proxy at `justjoin.it/api/candidate-api/`, which
  is what this engine uses. Gating is per-ROUTE, not per-site (§21).

* **`?page=2` on a rendered listing silently returns page 1.** Not an
  error, not an empty page: HTTP 200, `meta.from` still 0, a
  byte-identical first offer. So `page_url()` returns None for rendered
  pages on purpose, and `--route ssr` plans exactly one page rather than
  re-collecting page 1 and reporting a complete multi-page run.

* **The salary is not the first number in the list.** `employmentTypes`
  mixes the employer's real figure with currency conversions the site
  computed, and the real one is at index 0 on only 679 of 1,000 listing
  rows and 11 of 40 detail records. Worse, the endpoint's `from` is that
  rate NORMALISED TO A MONTH while `unit` names the employer's unit, so
  reading the two adjacent keys together says "18,480 PLN per hour" for a
  job paying 110. `product_parser` selects on `currencySource` and reads
  `fromPerUnit`, and its docstring has the numbers.

* **Every query is capped at 10,000 however many it matched.** The board
  held 19,381 offers on 2026-09-18. A run that fetches all 10,000 is
  genuinely `complete` — it fetched everything the site will serve — and is
  also a 52% sample, so the sidecar records both figures. Asking past the
  cap is HTTP 500, not an empty page, so the planner stops at it.

* **`--mode facets` is how you beat the cap.** Its counts are not capped:
  25 category runs of at most 1,991 rows each cover a board that one
  unfiltered run cannot.

Usage
-----
    python3 playwright_scraper.py
    python3 playwright_scraper.py --category python --pages 3 --format csv
    python3 playwright_scraper.py --city Krakow --with-salary --out krakow
    python3 playwright_scraper.py --mode facets
    python3 playwright_scraper.py --mode offer --pages 50 --concurrency 4
    python3 playwright_scraper.py --mode offer --url "https://justjoin.it/job-offer/goodylabs-senior-devops-engineer-k-m-x--lodz-devops"
    python3 playwright_scraper.py --route ssr      # robots.txt-clean, 100 rows

This site answered every one of those from a bare datacentre IP with no
credentials on 2026-09-18. `--proxy` and `--cdp-endpoint` work and are not
required.

Note on robots.txt: justjoin.it allows `/job-offers/` and `/job-offer/`,
advertises eight sitemaps of its own, and carries `Disallow: /api/` — which
is where the offers endpoint lives. `--route ssr` reads the rendered
listing page instead (100 rows, one page). The README states this plainly
rather than leaving it to be found.
"""

import argparse
import logging
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse, urljoin

from playwright.sync_api import (sync_playwright, Error as PWError,
                                 TimeoutError as PWTimeout)

from captcha_solver import (detect_recaptcha_v3, detect_recaptcha_in_page,
                            reconcile_detections, solve_recaptcha,
                            CaptchaUnsolvable, INJECT_TOKEN_JS,
                            RECAPTCHA_DISCOVERY_JS, detect_turnstile,
                            wait_for_turnstile, TURNSTILE_INTERCEPT_JS,
                            TURNSTILE_INJECT_JS, turnstile_task_for)
from product_parser import (API_PATH, MODES, DEFAULT_MODE, ROUTES,
                            DEFAULT_ROUTE,
                            CATEGORY_KEYS, DEFAULT_ITEMS_COUNT,
                            EXPERIENCE_LEVELS, EMPLOYMENT_TYPES, WORKING_TIMES,
                            WORKPLACE_TYPES, SORTS, ORDERS, DEFAULT_SORT,
                            DEFAULT_ORDER, MAX_ITEMS_COUNT, MAX_FROM,
                            SITEMAP_INDEX, DEFAULT_LISTING_URL, ROBOTS_NOTE,
                            Filters, api_page_url, api_url, cap_report,
                            category_from_url, count_api_url, detail_api_url,
                            detect_bot_challenge, facets_api_url,
                            filters_from_args, filters_from_listing_url,
                            is_single_offer_url, is_supported_url,
                            parse_for_mode, slugs_from_file, target_url,
                            listing_url, mode_for_url, offer_slug_or_url,
                            offer_url, page_url, parse_count_response,
                            parse_detail_response, parse_facets_response,
                            parse_offers_response, parse_ssr_listing,
                            categories_api_url, count_site_assets,
                            FACETS_JOIN, split_facets_body,
                            route_of, site_recaptcha_sitekey,
                            sitemap_is_index, sitemap_locs,
                            sitemap_offer_slugs, sku_from_url,
                            user_agent_is_refused)
from output_writer import (dedupe_by_key, finish_run, EXIT_API_ERROR,
                           ROW_CLASS_BY_MODE, SOURCE_DEFAULT)
import page_flow
from page_flow import MIN_CARD_MATCHES
from proxy_pool import (from_args as proxy_pool_from_args, to_playwright, mask,
                        ROTATE_MODES, ProxyError, ProxyPool)
import env_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("playwright_scraper")


def _chrome_ua(chromium_version: str) -> str:
    """Build a desktop-Chrome UA naming the browser's OWN real version.

    Not a hardcoded version number: that drifts the moment a newer Chromium
    ships, and a UA claiming an older Chrome than what the JS engine, WebGL
    strings and TLS ClientHello all actually report is itself a mismatch a
    fingerprinter can key on.
    """
    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{chromium_version} Safari/537.36")


@dataclass
class PageOutcome:
    """What one page produced.

    Collected per page and merged afterwards rather than folded into shared
    state as the loop goes. Two reasons, and the second is the point:
    dedupe that mutates a running set inside the loop makes the OUTPUT depend
    on the order pages happen to arrive in — fine while that order is fixed,
    wrong the moment pages are fetched concurrently, because which page
    "claims" a duplicate sku (and so which `scraped_at` the row carries)
    would vary between runs of the same command. Merging afterwards in page
    order is deterministic regardless of arrival order.
    """
    page_num: int
    url: str
    final_url: Optional[str] = None
    products: List = field(default_factory=list)
    blocked_by: Optional[str] = None
    load_failed: bool = False
    # The page_flow state this page came back as ("content", "blocked",
    # "challenge", "empty", "unknown"). Carried so the caller can tell an
    # EMPTY page — a /p/<slug> hub, a no-match query, or one page past the
    # end of a listing — from a page that failed. Both produce zero rows and
    # they mean opposite things.
    state: Optional[str] = None
    # What this response said about itself.
    #
    # `total_available` is the endpoint's `meta.totalItems` — how many
    # offers this QUERY matched, capped by the site at 10,000.
    # `urls_in_itemlist` is how many offer URLs a rendered page's own
    # `CollectionPage` JSON-LD names, which is only meaningful on
    # `--route ssr`. Carrying both means every run records the two views
    # rather than a reader inheriting a figure from a comment (§13).
    total_available: Optional[int] = None
    # What the BOARD holds, against what this query can reach. justjoin.it
    # caps every query at 10,000 however many it matched, so these two are
    # different numbers and keeping only the first would let a run report
    # "complete" while holding half the board (CLAUDE.md §21).
    site_total: Optional[int] = None
    # The `meta.from` the server echoed. The endpoint states the offset it
    # actually served, so a page that came back as a repeat of an earlier
    # one announces itself rather than being inferred from the rows.
    offset: Optional[int] = None
    pages_available: Optional[int] = None
    urls_in_itemlist: Optional[int] = None
    # The page number the SERVER answered with, derived from the
    # `meta.from` offset it echoed. This is what catches a route answering
    # an out-of-range request with page 1 — which is exactly what a
    # rendered listing does with `?page=N` on this site.
    echoed_page: Optional[int] = None

    @property
    def ok(self) -> bool:
        return not self.load_failed and self.blocked_by is None


ITEM_LINK_SELECTOR = page_flow.READY_SELECTOR_LISTING

# The share of rows that must carry the columns justjoin.it fills on every
# record, below which the read has broken rather than the data being
# unusual.
#
# There is no "price coverage" to check on a job board. What stands in its
# place is the handful of fields populated on 1,000 of 1,000 listing
# records and 40 of 40 detail records on 2026-09-18 — the title, the URL,
# the id and the company.
#
# Deliberately NOT in this check, each with the measurement that keeps it
# out. Every one of these would fire on healthy data:
#   salary_min            539 of 1,000. justjoin.it does not require a
#                         salary, and its own UI prints "Undisclosed
#                         Salary" for the other 461.
#   languages             465 of 1,000, and 0 on `--route ssr`, which does
#                         not publish them at all.
#   nice_to_have_skills    46 of 1,000
#   office_days           181 of 1,000 — only hybrid offers state one
#   apply_url             800 of 1,000, null on exactly the 200 whose
#                         `apply_method` is "form"
#   category                0 on `--route ssr`, which states an integer id
#                         with no table to read it with
#   country_code            0 on the listing route; the detail route has it
CORE_FIELD_FLOOR = 99
CORE_FIELDS = ("title", "url", "sku", "company_name")

# --mode offer. A detail record carries everything a listing row does plus
# the description body, which 40 of 40 sampled records had.
CORE_FIELDS_OFFER = ("title", "url", "sku", "company_name", "description")

# --mode facets. A facet row is four fields and the site states all four on
# every key it publishes — 60 keys across six groups plus 25 categories on
# 2026-09-18.
CORE_FIELDS_FACETS = ("title", "sku", "facet_group", "facet_key")


def _core_fields(mode: str):
    if mode == "offer":
        return CORE_FIELDS_OFFER
    if mode == "facets":
        return CORE_FIELDS_FACETS
    return CORE_FIELDS

# A page holding less than this share of the rows a full page carries is
# reported as thin.
#
# Set HIGH here, unlike most repos in this family, and the reason is that
# this route's page size is exact rather than incidental: the endpoint
# serves precisely `itemsCount` records until the result set runs out, so
# every page but the last holds the full complement. Measured 2026-09-18:
# 100, 100, 100 ... at every offset sampled from 0 to 9,900, and 1,000 of
# 1,000 when asked for 1,000.
#
# So a short page that is not the last page means something went wrong, and
# 0.9 catches it. The last page is exempted by the caller, which knows it
# is the last.
THIN_PAGE_SHARE = 0.9


# ---------------------------------------------------------------------------
# page_flow, bound to Playwright
# ---------------------------------------------------------------------------
# Every decision about WHAT to do with a page — how long to wait, when to
# scroll, when a fresh session is the only fix — lives in page_flow.py so all
# three engines make it identically. What lives here is only HOW to ask this
# particular driver. See page_flow's docstring for why that split exists.
def _driver(page):
    # Named OPERATIONS rather than JavaScript, and that is the point of the
    # split. Selenium's execute_script takes a function BODY with an explicit
    # `return` while Playwright and pyppeteer take `() => expr`, so a shared
    # module handing JS across this boundary would quietly acquire one
    # driver's dialect.
    #
    # There is no scroll primitive here, and its absence is measured
    # rather than forgotten: every route this scraper reads is complete in
    # the first response. The endpoint answers with the whole payload, and
    # a rendered listing server-renders all 100 offers into its flight
    # payload before any scrolling could happen.
    #
    # The grid is in fact VIRTUALISED, which makes the absence worth
    # stating twice: measured 2026-09-18, `a[href*="/job-offer/"]` was 200
    # in the served markup and 18 in the live DOM after hydration. So
    # scrolling would REPLACE DOM nodes rather than add rows, and no row is
    # read from the DOM anyway (CLAUDE.md §4).
    return {
        "count": lambda selector: len(page.query_selector_all(selector)),
        "sleep": page.wait_for_timeout,
        "content": lambda: _content_when_settled(page),
        "current_url": lambda: page.url,
    }


def _ready_selector(args) -> str:
    return page_flow.ready_selector(args.mode)


def _min_matches(args) -> int:
    return page_flow.min_matches(args.mode)


def _classify(page, body: str, status=None, mode: str = "listings",
              route: str = DEFAULT_ROUTE) -> str:
    """Name what came back.

    `mode` AND `route` are both threaded through because the four things
    this engine fetches are read by different mechanisms: an endpoint
    response is JSON, a rendered listing is a flight payload inside HTML,
    and a facets response is a JSON object of arrays with no `data` key at
    all. Classifying any of them with another one's reader would call a
    perfectly good response a parse_error.
    """
    return page_flow.classify(body, status, page.url, mode, route)

# Every readiness constant and every state policy lives in page_flow.py,
# with its measurement beside it. Nothing about WHAT to do with a page is
# duplicated here — this file only knows HOW to ask Playwright.


def _fetch_text(session, url: str, timeout_ms: int = 60000) -> str:
    """Navigate and return whatever the parser should be given for this URL."""
    session.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    return _snapshot(session.page, url) or ""


def _fetch_page_source(session, url: str, timeout_ms: int = 60000) -> str:
    """Navigate and return the serialised document.

    `content()` rather than `innerText`, and that matters for the sitemap.
    Chromium renders `application/xml` through its own XML viewer, and it
    was checked rather than assumed which of the two survives that: on
    `/sitemaps/active-jobs/part0.xml` `content()` keeps all 10,646 `<loc>`
    elements intact, so the parser's regex reads it unchanged. `innerText`
    gives the viewer's rendering, which happens to contain the URLs too but
    only by accident of how the viewer lays them out.

    Used for the enumeration fetches. The per-page fetches go through
    `_snapshot`, which is the path with the retry, classification and solve
    machinery around it.
    """
    session.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    return session.page.content() or ""


def _fetch_body(session, args, url: str) -> str:
    """The document the parser should be given for this fetch.

    Three shapes behind one call, so every retry, classification, dump and
    coverage check downstream works on one body per page:

      * an endpoint response is JSON. `innerText` rather than `content()`:
        Chromium wraps a JSON document in its own viewer markup, and the
        body text is exactly what the server sent.
      * a rendered listing is HTML, and the flight payload lives in its
        `<script>` tags — so `content()` is what carries it, and
        `innerText` would throw the payload away.
      * `--mode facets` is TWO endpoints that are one answer. The facet
        groups and the category counts are joined with a separator that
        `_split_facets_body` undoes, rather than giving the fetch path a
        second shape.
    """
    body = _snapshot(session.page, url) or ""
    if args.mode == "facets" and body.strip():
        try:
            categories = _fetch_text(session, categories_api_url())
        except (PWError, PWTimeout) as exc:
            logger.warning("The category counts did not load (%s); the facet "
                           "groups are still complete. Categories will be "
                           "absent from this run rather than wrong.",
                           _mask_credentials(str(exc))[:120])
            return body
        if categories.strip():
            return body + FACETS_JOIN + categories
    return body


# Chromium reports a DEAD PROXY as a generic error rather than as a
# timeout, and the two want opposite responses: a timeout deserves another
# try at the same exit, a dead proxy deserves a different one. Catching
# only the timeout type let this escape as a traceback in a sibling repo
# (CLAUDE.md §8).
_PROXY_ERROR_MARKERS = (
    "ERR_PROXY_CONNECTION_FAILED",     # nothing listening / refused
    "ERR_TUNNEL_CONNECTION_FAILED",    # CONNECT rejected by the proxy
    "ERR_PROXY_AUTH_UNSUPPORTED",      # auth scheme we cannot satisfy
    "ERR_PROXY_AUTH_REQUESTED",        # credentials missing or wrong
    "ERR_UNEXPECTED_PROXY_AUTH",
    "ERR_PROXY_CERTIFICATE_INVALID",
)


def _proxy_failure(exc) -> str:
    """The Chromium proxy-error name in `exc`, or "" if it is not one.

    Distinguishing this from an ordinary timeout matters because the two want
    opposite responses: a timeout deserves a retry from the same exit, while
    an unusable exit deserves a different exit — retrying it unchanged just
    spends the retry budget on a proxy that is not going to answer.
    """
    text = str(exc)
    for marker in _PROXY_ERROR_MARKERS:
        if marker in text:
            return marker
    return ""


def _launch_local(pw, args, pool):
    """Launch our own Chromium on `pool`'s current exit; return (browser, context, page).

    Factored out of scrape() so a proxy rotation can tear the whole browser
    down and call this again. Swapping the proxy under a live session would
    be cheaper and wrong: cookies a bot manager issued against one exit,
    replayed from another, are a stronger signal than either address alone.
    A rotation therefore means a genuinely fresh browser — new cookie jar,
    new storage — which is what an ordinary user on a different network
    looks like.
    """
    launch_kwargs = {"headless": args.headless}
    proxy = to_playwright(pool.current) if pool else None
    if proxy:
        launch_kwargs["proxy"] = proxy
        logger.info("Using proxy exit %s", mask(pool.current))

    browser = pw.chromium.launch(**launch_kwargs)
    # Only override the UA when we launched our own bundled Chromium.
    # Forcing a UA on a page reached via --cdp-endpoint mismatches the remote
    # browser's real TLS/JS fingerprint on purpose-matched values.
    ctx_kwargs = {"user_agent": _chrome_ua(browser.version), "locale": args.locale}
    init_script = None
    if args.fingerprint:
        # Only meaningful on this branch. Over --cdp-endpoint the Scraping
        # Browser already has its own fingerprint, and layering a second one
        # on top produces a mismatch rather than better cover.
        from fingerprint_client import (get_fingerprint,
                                        playwright_context_kwargs,
                                        playwright_init_script)
        fp = get_fingerprint(args.twocaptcha_key,
                             tags=args.fp_tags, country=args.fp_country)
        ctx_kwargs.update(playwright_context_kwargs(fp))
        init_script = playwright_init_script(fp)
        logger.info("Using 2captcha fingerprint %s (%s)", fp.get("id"), fp.get("country"))

    context = browser.new_context(**ctx_kwargs)
    if init_script:
        # Must be installed on the context, before any page script runs.
        context.add_init_script(init_script)
    # Records the arguments Cloudflare passes to `turnstile.render()`.
    #
    # This has to be installed HERE, on the context, and not on a page after
    # navigation: Cloudflare calls `turnstile.render(container, params)` once
    # and keeps nothing, so `sitekey`, `action`, `cData` and `chlPageData`
    # exist only inside that call. A static read of a challenge page — any
    # static read, however careful — cannot produce a solvable task
    # (CLAUDE.md §19). It costs nothing on a page that never renders one.
    context.add_init_script(TURNSTILE_INTERCEPT_JS)
    return browser, context, context.new_page()


class _BrowserSession:
    """One browser + context + page, relaunchable onto a different exit.

    Exists because a rotation replaces all three handles at once, and passing
    three mutable locals through every helper is how one of them ends up
    stale. It also gives a worker thread a single object to own: with
    Playwright's sync API, a browser and everything reachable from it belong
    to the thread that created them, so each worker builds its own.
    """

    def __init__(self, pw, args, pool, remote: bool = False):
        self.pw, self.args, self.pool, self.remote = pw, args, pool, remote
        self.browser = self.context = self.page = None

    def open(self):
        if self.remote:
            self.browser, self.context, self.page = _connect_remote(self.pw, self.args)
        else:
            self.browser, self.context, self.page = _launch_local(
                self.pw, self.args, self.pool)
        return self

    def relaunch(self):
        """Tear the browser down and come back on the pool's current exit.

        On a remote browser this is a no-op — its exit is not ours to change.
        """
        if self.remote:
            return
        try:
            self.browser.close()
        except Exception as e:  # noqa: BLE001 — teardown must not mask the reason we're here
            logger.debug("Ignoring error while closing browser for rotation: %s", e)
        self.open()

    def close(self):
        try:
            if self.remote:
                self.page.close()  # leave the remote browser app running
            else:
                self.browser.close()
        except Exception as e:  # noqa: BLE001
            logger.debug("Ignoring error during browser teardown: %s", e)


def _connect_remote(pw, args):
    """Attach to an already-running browser over CDP; return (browser, context, page)."""
    logger.info("Connecting to existing browser over CDP: %s",
                _mask_credentials(args.cdp_endpoint))
    # Explicit timeout. Playwright defaults to 30s here, but stating it makes
    # the contract visible next to the pyppeteer twin, which has no connect
    # timeout at all. A Scraping Browser session that is still held answers
    # with HTTP 500 rather than stalling, so this mostly guards against the
    # endpoint going quiet.
    try:
        browser = pw.chromium.connect_over_cdp(args.cdp_endpoint, timeout=30000)
    except (PWError, PWTimeout) as e:
        # Playwright puts the endpoint it tried into the exception text, and
        # the endpoint is a URL with the password in it. Unmasked, that
        # password lands in the terminal, in CI output and in any log the run
        # is piped to — which is the one thing this project promises does not
        # happen ("credentials never reach argv or logs"). The message is
        # rewritten with the credentials masked and the host and port kept,
        # because WHICH endpoint failed is the useful half and is not the
        # secret.
        raise PWError(
            f"could not connect to --cdp-endpoint "
            f"{_mask_credentials(args.cdp_endpoint)}: "
            f"{_mask_credentials(str(e))}\n"
            f"A Scraping Browser profile allows ONE live connection at a "
            f"time, so a 500 here usually means another run still holds this "
            f"`pid`. Wait for it to finish, or use a different pid."
        ) from None
    # Reuse the remote browser's existing context so its
    # fingerprint/session/proxy settings stay intact.
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()

    # The Scraping Browser API exposes a documented CDP domain
    # (`Captcha.setAutoSolve` / `Captcha.solve`) that clears supported
    # challenges inside the browser: https://2captcha.com/scraper/browser-api/api
    # Tried first when --cdp-endpoint is set; this script's own detect+solve
    # logic still runs as a fallback if the endpoint does not support it.
    # What it covers here is any challenge the site might render.
    # justjoin.it has never answered this scraper with one — sixteen
    # candidate vendor markers counted zero across seven captures on
    # 2026-09-18 — so this path is insurance rather than a route in use. That challenge publishes no
    # sitekey in its markup — Cloudflare calls `turnstile.render` once and
    # keeps nothing — so the LOCAL path needs the init-script hook that
    # records those arguments (see `handle_captcha_if_present`), while this
    # remote path is handled inside the browser and needs none of it.
    try:
        cdp_session = context.new_cdp_session(page)
        cdp_session.send("Captcha.setAutoSolve", {"autoSolve": True, "options": [{"type": "*"}]})
        cdp_session.on("Captcha.detected", lambda *_: logger.info("[Scraping Browser] CAPTCHA detected on page."))
        cdp_session.on("Captcha.waitForSolve", lambda *_: logger.info("[Scraping Browser] CAPTCHA sent to 2captcha for solving."))
        cdp_session.on("Captcha.solveFinished", lambda *_: logger.info("[Scraping Browser] CAPTCHA solved automatically."))
        cdp_session.on("Captcha.solveFailed", lambda *_: logger.warning("[Scraping Browser] CAPTCHA auto-solve failed."))
        logger.info("Scraping Browser API Captcha.setAutoSolve enabled — supported "
                    "challenge types will be solved automatically if this "
                    "--cdp-endpoint is a Scraping Browser API session.")
    except Exception as e:
        logger.info("Captcha.setAutoSolve not available on this --cdp-endpoint (%s) — "
                    "relying on this script's own detect+solve logic instead.", e)
    return browser, context, page


def _resolve_pagination_url(base_url: str, href: str) -> str:
    """Resolve a pagination link's raw href against the page it came from.

    Playwright's get_attribute("href") returns the raw HTML attribute,
    unresolved — unlike the DOM .href property Puppeteer/Selenium read for
    the same purpose in this project, which the browser resolves for you.
    urljoin handles every shape correctly — absolute, protocol-relative,
    absolute-path, and page-relative hrefs alike.
    """
    return urljoin(base_url, href)


# Every `scheme://user:pass@` in a string, however many times it occurs.
# Matching globally rather than once is the point: a Playwright connection
# error repeats the endpoint five times (the message plus a four-line call
# log), so a masker that handled only the first occurrence would print the
# password four times and look like it was working.
_CREDENTIALS_IN_URL_RE = re.compile(r"([a-z][a-z0-9+.\-]*://)[^\s/@]+:[^\s/@]+@",
                                    re.IGNORECASE)


def _mask_credentials(text: str) -> str:
    """`text` with any username:password in an embedded URL replaced.

    Takes arbitrary text, not just a URL, because the strings that most need
    this are exception messages with a URL inside them. The host and port are
    KEPT — which endpoint or exit a run used is the useful half of the line
    and is not the secret.
    """
    return _CREDENTIALS_IN_URL_RE.sub(r"\1***:***@", text or "")


def _content_when_settled(page, attempts: int = 4, pause_ms: int = 700):
    """page.content() that tolerates a page mid-navigation.

    Playwright raises `Page.content: Unable to retrieve content because the
    page is navigating and changing the content` if the document swaps
    under it. justjoin.it does not geo-redirect — the endpoint answered
    identically from every exit tried — but `www.justjoin.it` redirects to
    the bare host, and a rendered listing hydrates immediately after load
    (replacing 200 server-rendered anchors with a virtualised window of
    18), so a snapshot taken right after goto() can land exactly on a
    swap.

    Retries briefly and returns None if the page won't hold still, so the
    caller can skip a check instead of failing the run.
    """
    for attempt in range(1, attempts + 1):
        try:
            return page.content()
        except PWError as e:
            if "navigating" not in str(e).lower():
                raise
            if attempt == attempts:
                logger.warning("Page kept navigating through %d attempts — "
                               "continuing without a snapshot.", attempts)
                return None
            logger.info("Page is navigating (a URL canonicalisation?) — "
                        "retrying content() in %dms (%d/%d).",
                        pause_ms, attempt, attempts)
            page.wait_for_timeout(pause_ms)
    return None


def _is_endpoint(url: str) -> bool:
    """Whether this address answers with raw JSON rather than a document.

    True for everything under `/api/candidate-api/`, which is every route
    this scraper reads except `--route ssr`. Getting it wrong is SILENT in
    both directions, which is why it is a named predicate rather than an
    inline check:

      * treating the endpoint as a page returns Chromium's JSON-viewer
        markup, which parses as "not an offers payload" and reads as an
        empty result rather than as a bug;
      * treating a rendered listing as the endpoint returns `innerText`,
        which throws away the `<script>` tags the flight payload lives in —
        and the page would then hold 100 offers and yield none.
    """
    return API_PATH in (url or "")


def _snapshot(page, url: str) -> Optional[str]:
    """What the parser is given for this address.

    Two shapes, because the two addresses answer with two things and
    Chromium does not hand them over the same way. A rendered listing is
    read with `content()`, because its flight payload lives inside
    `<script>` tags. The ENDPOINT answers with JSON, which Chromium wraps
    in its own JSON-viewer markup — so `content()` there returns the
    viewer's HTML and the payload would be unreachable.
    `document.body.innerText` gives back exactly what the server sent.

    See `_is_endpoint` for why getting this wrong is silent either way.
    """
    if _is_endpoint(url):
        try:
            return page.evaluate("() => document.body.innerText") or ""
        except (PWError, PWTimeout) as e:
            logger.warning("Could not read the endpoint response: %s", e)
            return None
    return _content_when_settled(page)


def handle_captcha_if_present(page, args) -> bool:
    """Detect and solve a challenge. True if something was solved.

    Runs after EVERY navigation, for ANY page — not scoped to one URL. The
    static-HTML and runtime reCAPTCHA detectors are run and reconciled
    against each other rather than short-circuited, because they can disagree
    about the variant and the parameters for one are rejected for the other.

    WHAT THIS SITE ACTUALLY PUTS IN FRONT OF A RUN, measured 2026-09-18,
    because CLAUDE.md §19 is explicit that a sentence about what a solver
    can do is the most expensive thing this family can get wrong:

    * Cloudflare's **managed challenge**. justjoin.it sits behind
      Cloudflare and has never answered this scraper with one — every route
      measured on 2026-09-18 was served, from a bare datacentre address, to
      `curl` with no headers at all and to `wget` alike. The machinery is
      here for the day that changes. Such a challenge publishes no sitekey, because Cloudflare
      calls `turnstile.render()` once and keeps nothing. 2Captcha solves it
      with `TurnstileTaskProxyless`, but the task needs `sitekey`, `action`,
      `cData` and `chlPageData`, and the only way to get them is to hook
      `turnstile.render` before any page script runs. That hook is
      `TURNSTILE_INTERCEPT_JS`, installed on the context below.

    * The site's **own** reCAPTCHA, which is a different thing and IS
      configured — but not on any route this scraper reads.

      justjoin.it publishes its own key in the page config on every page:

          "googleRecaptchaV2Key": "6LeTZ-ErAAAAAA_fFNEN3N575ErA7CPrGYyZX50o"

      The field name says v2, and the only string in the bundle that
      references it sits in the job-APPLICATION form's i18n namespace
      ("reCAPTCHA verification failed. Please try again."). So the widget
      guards submitting an application — sending a CV — which this scraper
      never does.

      That was measured rather than reasoned, because CLAUDE.md §18 is
      explicit that a configured captcha and a rendered one are different
      questions. In a live headless Chromium on 2026-09-18, seven seconds
      after load, on a rendered listing, on an offer page and on the
      endpoint alike:

          window.grecaptcha              undefined
          window.___grecaptcha_cfg       absent
          window.turnstile               undefined
          iframes matching recaptcha     0
          elements with [data-sitekey]   0
          script[src] matching captcha   0

      Not merely un-rendered: the reCAPTCHA script is never even LOADED on
      the read path. The key in the config is the only trace of it, which
      is why a grep of a capture finds one and a browser finds none.

    Neither is "unsolvable" — that word belongs to a PAGE with no widget
    on it, not to a vendor (CLAUDE.md §19). If justjoin.it ever puts its v2
    widget in front of a listing, 2Captcha solves exactly that with
    `RecaptchaV2TaskProxyless`, and the sitekey is published in the page
    source where this code already looks for it. What is true today is
    narrower and is what the README says: no route this scraper reads
    renders a challenge, so no run has ever reached this code.
    """
    html = _content_when_settled(page)
    if html is None:
        # Couldn't get a stable snapshot — skip detection for this navigation
        # rather than taking the whole run down. The next navigation gets
        # another chance, and the parse below reads its own copy of the DOM.
        return False

    # Detected is not the same as blocking. A challenge on a page whose
    # products are already rendered guards nothing, and counting the anchors
    # is instant — no wait_for_function, no 20s — which is why this check
    # sits here rather than after the readiness wait. Doing it the other way
    # round would cost 20 wasted seconds on a page the captcha genuinely
    # gates, where solving FIRST is what makes the content appear.
    already_rendered = len(page.query_selector_all(_ready_selector(args)))
    when_blocked = getattr(args, "solve_captcha", "when-blocked") == "when-blocked"

    html_challenge = detect_recaptcha_v3(html, page.url)
    runtime_challenge = detect_recaptcha_in_page(
        lambda js: page.evaluate(js), page_url=page.url)
    challenge = reconcile_detections(html_challenge, runtime_challenge)

    if not challenge:
        # No reCAPTCHA. Turnstile is the other thing 2captcha solves, and on
        # this site it is the LIKELY one — Cloudflare is what stands in front
        # of a scored address here. The RUNTIME reading is the one that
        # matters: a Cloudflare challenge page publishes no sitekey in its
        # markup, so only the interception installed at context creation can
        # produce a solvable challenge. The static read is the fallback for
        # the site's OWN widget, whose sitekey IS published.
        # The RUNTIME reading is unconditional: it only returns something
        # when Cloudflare actually called `turnstile.render`, which happens
        # on a challenge page and nowhere else.
        challenge = wait_for_turnstile(lambda js: page.evaluate(js),
                                       lambda s: page.wait_for_timeout(s * 1000),
                                       page_url=page.url)
        # The STATIC reading is gated on the content not being there.
        # justjoin.it ships no Turnstile markers in its markup, so unlike a
        # sibling site this gate is not load-bearing against a false
        # positive here — it is kept so all three engines gate identically.
        #
        # The `already_rendered` check below is kept for the same reason
        # and is worth understanding before anyone "simplifies" it. On a
        # sibling repo a readiness selector that matched the SERVED markup
        # but not the hydrated DOM made this read 0 on a page holding every
        # row, and the engine went to "attempting to solve" — a charge per
        # page for a widget that was never stopping anything.
        #
        # This site inverts that hazard rather than removing it: its grid
        # is VIRTUALISED, so the anchor count falls after hydration (200 to
        # 18, measured 2026-09-18) instead of going to zero. Which is why
        # `page_flow.MIN_CARD_MATCHES` is 4 rather than a number read off
        # the served markup — a threshold of 100 would report "no content"
        # a few seconds after load on a perfectly good page.
        #
        # CLAUDE.md §18 states the rule for the PARSER's marker set; this
        # is the same rule applied to the SOLVER's gate — and the reminder
        # that a gate is only as good as the selector it counts with.
        if challenge is None and already_rendered <= MIN_CARD_MATCHES:
            challenge = detect_turnstile(html, page.url)
        if challenge and not challenge.sitekey:
            # justjoin.it publishes its own reCAPTCHA v2 key in the page
            # config (`googleRecaptchaV2Key`). It has never been seen
            # guarding a listing — see this function's docstring — but if
            # one ever renders, the key is right there and using it beats
            # refusing: a sitekey-less task is one the API rejects and
            # charges nothing for, while a real key is a solvable one.
            published = site_recaptcha_sitekey(html)
            if published:
                # The site's own widget, which does publish its key. Better
                # than refusing: this is a solvable challenge that the
                # interception simply did not see render.
                challenge.sitekey = published
                logger.info("No sitekey was intercepted, but the page "
                            "publishes one in its own config — using it.")
            else:
                # Raising a task without a sitekey buys a rejected token and
                # an ERROR_CAPTCHA_UNSOLVABLE bill (CLAUDE.md §19). Say so
                # instead.
                logger.warning(
                    "A Cloudflare Turnstile is on this page but no sitekey "
                    "was captured, so no task can be built and none will be "
                    "paid for. That means the widget rendered before this "
                    "run's interception script was installed — which should "
                    "not happen on a page this engine navigated to, and does "
                    "happen if the browser was attached to mid-flight.")
                return False
    if not challenge:
        return False

    if when_blocked and already_rendered > MIN_CARD_MATCHES:
        logger.info("%s detected via %s, but %d anchors are already on the "
                    "page — not solving it. Pass --solve-captcha always to "
                    "solve it anyway.", challenge.kind, challenge.source,
                    already_rendered)
        return False

    logger.warning("%s detected via %s (sitekey=%s, action=%s) — attempting to solve.",
                   challenge.kind, challenge.source, challenge.sitekey, challenge.action)
    if not args.twocaptcha_key:
        logger.warning("No 2captcha API key, so this challenge cannot be "
                       "solved — continuing with whatever the page already "
                       "holds.")
        return False
    try:
        token = solve_recaptcha(challenge, args.twocaptcha_key,
                               api_version=args.captcha_api,
                               min_score=args.min_score)
    except Exception as e:  # noqa: BLE001 — a solver failure is not a crash
        logger.error("Solving the challenge failed (%s) — continuing with "
                     "whatever the page holds.", e)
        return False

    if getattr(challenge, "is_turnstile", False):
        # Turnstile hands its token back through `cf-turnstile-response` and
        # the page's own callback, not through `g-recaptcha-response` and
        # grecaptcha's client registry.
        called_back = page.evaluate(TURNSTILE_INJECT_JS, token)
        logger.info("Turnstile token injected%s.",
                    " and handed to the page's callback" if called_back
                    else " (no callback was captured — relying on the form "
                         "field)")
        if getattr(challenge, "solved_user_agent", None):
            # Logged so it can be RULED OUT rather than guessed at. Measured
            # on a sibling site, a token minted under a mismatched UA was
            # accepted anyway — so this is evidence, not a known cause.
            logger.info("2captcha solved it against user agent %r. Cloudflare "
                        "checks that on a challenge page, so a mismatch here "
                        "is worth ruling out if a paid token is refused.",
                        challenge.solved_user_agent[:60] + "…")
    else:
        page.evaluate(INJECT_TOKEN_JS, token)
    logger.info("Token injected. Reloading page to continue.")
    page.wait_for_timeout(1500)
    page.reload(wait_until="domcontentloaded", timeout=60000)
    return True


def _solve_budget(args, spent: int):
    """Whether another solve may be bought for this page, and the reason.

    `page_flow.SOLVES_PER_PAGE` says at most one purchase per page, and
    that is a MONEY limit rather than a style rule. It was not being
    enforced in the repo this engine was ported from:
    `handle_captcha_if_present` is called twice per attempt — once before
    the page is classified, so a challenge is cleared before anything is
    judged, and once after, for the state that says the page really is
    gated — and only the SECOND call was counted.

    INHERITED EVIDENCE, from a SIBLING repo and not from justjoin.it —
    labelled because §13 says a number you inherited is not a number you
    measured, and this one cannot be reproduced here: justjoin.it has never
    rendered a challenge to this scraper, so no page of this site has ever
    bought a solve at all.

    On that sibling, from a datacentre address that meets a real Cloudflare
    challenge on every fetch (2026-09-17): ONE page bought THREE Turnstile
    solves — two from the uncounted call across two block attempts, one
    from the counted one — and every token was refused. The cap read as
    enforced and was not (CLAUDE.md §17: a policy constant nothing reads is
    the same defect as dead code).

    The fix is carried here anyway. A budget that is never exercised is
    still the difference between a bill and no bill on the day it is.

    Both call sites go through here.
    """
    return spent < page_flow.SOLVES_PER_PAGE


def _fetch_one_page(session, args, pool, page_num: int, url: str) -> PageOutcome:
    """Fetch and parse one page. Retries, rotations and debug dumps live here.

    Returns a PageOutcome and never raises for an EXPECTED failure — a
    timeout, a 403 refusal, a captcha page, a dead exit are all recorded on the
    outcome instead. What the run should do about them differs between the
    sequential and concurrent paths, so that decision belongs to the caller
    rather than to a raised exception unwinding through it.

    Always goes through `session.page`, never a captured local: a rotation
    replaces the browser, context and page together, and a stale handle is
    exactly the bug _BrowserSession exists to prevent.
    """
    outcome = PageOutcome(page_num=page_num, url=url)

    # How many times a blocked page may be retried.
    #
    # With a pool, each retry moves to a DIFFERENT exit and the budget is the
    # user's `--proxy-block-retries`. WITHOUT one — the ordinary case here,
    # because `--cdp-endpoint` brings its own exit — the retry re-fetches
    # through the same access path, and that is worth doing on this site
    # rather than giving up: a Scraping Browser profile was measured refusing
    # two requests and serving the third. Zero was the family default and it
    # made the first live run of this engine abandon page 1 on its first
    # block without retrying once.
    has_pool = bool(pool and len(pool) > 1)
    # `RETRY_ON_BLOCKED` is CONSULTED, not just documented. It was a
    # constant with a paragraph of justification that no engine read — a
    # policy statement nothing enforced, which is the same defect as dead
    # code that looks load-bearing. Setting it False now really does stop
    # the retry loop.
    block_retries = 0 if not page_flow.RETRY_ON_BLOCKED else (
        args.proxy_block_retries if has_pool
        else page_flow.BLOCK_RETRIES_WITHOUT_POOL)
    # Counted across the whole block-retry loop, not per attempt: a page that
    # keeps coming back as a challenge would otherwise buy one solve per
    # rotation, which is how a run quietly turns into a bill.
    solves_bought = 0
    body, state, load_failed = None, "ok", False

    for block_attempt in range(block_retries + 1):
        logger.info("Fetching page %d/%d: %s", page_num, args.pages, url)
        # Retry a navigation timeout rather than ending the run on it. One
        # network flap on page 12 of 50 should not break the loop.
        load_failed, exit_failed = False, None
        for attempt in range(1, args.retries + 1):
            try:
                session.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                load_failed = False
                break
            except (PWTimeout, PWError) as e:
                # A dead or misconfigured proxy raises PWError
                # (net::ERR_PROXY_CONNECTION_FAILED), not PWTimeout —
                # catching only the latter lets it escape as a traceback,
                # which is the likeliest failure the first time anyone points
                # --proxy-file at a real list.
                reason = _proxy_failure(e)
                if reason:
                    exit_failed = reason
                    load_failed = True
                    break  # a different exit is the only thing that helps
                load_failed = True
                if attempt < args.retries:
                    pause = args.retry_delay * (2 ** (attempt - 1))
                    logger.warning("Timeout loading %s (attempt %d/%d) — "
                                   "retrying in %.1fs.", url, attempt,
                                   args.retries, pause)
                    time.sleep(pause)

        if exit_failed and has_pool and block_attempt < block_retries:
            logger.warning("Exit %s is unusable (%s) — rotating to another "
                           "one (%d/%d).", mask(pool.current), exit_failed,
                           block_attempt + 1, block_retries)
            pool.advance(f"unusable exit: {exit_failed}")
            session.relaunch()
            continue
        if load_failed:
            break

        # Counted, because it can BUY. See _solve_budget.
        if _solve_budget(args, solves_bought):
            solves_bought += 1
            if handle_captcha_if_present(session.page, args):
                # A solve navigated the page. Give the destination a moment
                # before judging what came back.
                session.page.wait_for_timeout(1000)
        elif solves_bought:
            logger.info("Not solving again on page %d: %d purchase(s) already "
                        "made for it and SOLVES_PER_PAGE is %d. A challenge "
                        "that survives a paid token is not one this run can "
                        "pass.", page_num, solves_bought,
                        page_flow.SOLVES_PER_PAGE)

        body = _fetch_body(session, args, url)
        state = _classify(session.page, body, mode=args.mode,
                          route=args.route)

        # Every route this engine reads is complete in the FIRST response,
        # so there is nothing to wait for on a healthy page. The endpoint
        # answers with the whole payload; a rendered listing server-renders
        # all 100 offers into its flight payload. Measured 2026-09-18 with
        # no pause at all after `wait_until="domcontentloaded"`: the
        # endpoint gave 1,000 of 1,000 rows and a rendered listing gave 100
        # of 100.
        #
        # That is why this engine has no scroll step. It would be ceremony
        # that looks load-bearing: justjoin.it's grid is VIRTUALISED, so
        # scrolling replaces DOM nodes rather than adding rows, and the
        # rows never came from the DOM in the first place.
        #
        # The wait below is therefore only for the state that says the site
        # served SOMETHING that is not a payload, and only on the one route
        # that has a DOM worth waiting on — `page_flow.needs_readiness_wait`
        # says which. Polling a selector against Chromium's JSON viewer
        # would burn the full budget on every endpoint page and never
        # succeed.
        if state == "unknown" and page_flow.needs_readiness_wait(args.mode,
                                                                 args.route):
            wait_timeout = page_flow.content_timeout_ms(args.mode)
            logger.info("Page %d is something justjoin.it served (%d bytes, "
                        "its own assets referenced %d time(s)) but carries no "
                        "offer payload — waiting up to %.0fs rather than "
                        "spending a retry.", page_num, len(body),
                        count_site_assets(body), wait_timeout / 1000)
            found = page_flow.wait_for_count(
                lambda sel: len(session.page.query_selector_all(sel)),
                _ready_selector(args), _min_matches(args), wait_timeout,
                session.page.wait_for_timeout)
            if found < _min_matches(args):
                logger.info("Still nothing after %.0fs (%d match(es) for %s).",
                            wait_timeout / 1000, found, _ready_selector(args))
            body = _fetch_body(session, args, url) or body
            state = _classify(session.page, body, mode=args.mode,
                          route=args.route)

        # The paid path is reached only for state "challenge" — Cloudflare's
        # managed challenge, which IS a test and can be solved once
        # `TURNSTILE_INTERCEPT_JS` has captured its parameters. It is bounded
        # by SOLVES_PER_PAGE so a rotation loop cannot become a bill, and a
        # sitekey-less detection refuses to build a task at all rather than
        # paying for one the API will reject (CLAUDE.md §19).
        if (page_flow.should_solve(state)
                and _solve_budget(args, solves_bought)):
            solves_bought += 1
            if handle_captcha_if_present(session.page, args):
                session.page.wait_for_timeout(1000)
                body = _fetch_body(session, args, url) or body
                state = _classify(session.page, body, mode=args.mode,
                          route=args.route)
                # The VERIFIED outcome, and the only one worth reporting: a
                # "ready" task result is not evidence the token works. This
                # line is what says whether the money bought anything.
                if state == "content":
                    logger.info("The solve was accepted — page %d is content "
                                "now.", page_num)
                else:
                    logger.warning(
                        "The solve was NOT accepted: page %d is still %s. The "
                        "purchase is spent.", page_num, state)

        if not page_flow.should_retry(state):
            # "content" and "empty" are both final answers. An empty page is
            # a CORRECT one — a hub category has no grid, and one page past
            # the end of a listing has no products — so retrying it would
            # spend the user's budget re-confirming the same right answer,
            # and rotating the exit would blame an address for the URL it was
            # given.
            break

        # Blocked or challenged. A different exit is the one thing that
        # plausibly changes the outcome: the ADDRESS is what was scored, not
        # the URL, so retrying it unchanged would only confirm it. Measured
        # 2026-09-09 — the same URL that answers 403 from a datacentre exit
        # answers 200 from a residential one.
        if block_attempt < block_retries:
            if has_pool:
                logger.warning("Page %d came back as %s from %s — retrying "
                               "from another exit (%d/%d).", page_num, state,
                               mask(pool.current), block_attempt + 1,
                               block_retries)
                pool.advance(f"{state} on page {page_num}")
                session.relaunch()
            else:
                # No pool, so nowhere else to go — but a plain re-fetch is
                # what clears this on a Scraping Browser profile. The browser
                # is NOT relaunched: over `--cdp-endpoint` a profile allows
                # one live connection, so tearing the session down and
                # reconnecting risks `profile_locked` and would lose the very
                # cookies the retry is meant to build on.
                pause = args.retry_delay * (block_attempt + 1)
                logger.warning("Page %d came back as %s — re-fetching through "
                               "the same access path in %.1fs (%d/%d). On this "
                               "site that is often what clears it.",
                               page_num, state, pause, block_attempt + 1,
                               block_retries)
                time.sleep(pause)

    if load_failed:
        # NAME the proxy when it was the proxy. CLAUDE.md §8: a dead exit and
        # a timeout want opposite responses — another try at the same exit
        # versus a different exit — so a message that cannot tell them apart
        # leaves the reader guessing which they got.
        #
        # This branch used to drop `exit_failed` on the floor. With a POOL the
        # reason was logged on rotation, but WITHOUT one — a single --proxy,
        # which is the common case — the run said only "gave up loading" for
        # a proxy that had refused the connection outright. Found by running
        # it: `--proxy http://127.0.0.1:9` reported the generic message while
        # `_proxy_failure()` had correctly identified
        # ERR_PROXY_CONNECTION_FAILED one frame earlier.
        if exit_failed:
            logger.error(
                "Gave up loading %s: the PROXY refused the connection (%s), "
                "which is not a timeout and will not fix itself on a retry "
                "from the same exit. Check the exit, or pass --proxy-file so "
                "the run can rotate to another one.", url, exit_failed)
        else:
            logger.error("Gave up loading %s after %d attempt(s).",
                         url, args.retries)
        outcome.load_failed = True
        outcome.blocked_by = None
        return outcome

    outcome.state = state

    if state == "blocked":
        # What a caller needs here is WHICH refusal this is, because the two
        # want different answers and only one of them is solvable:
        #
        #   a branded refusal page  — no widget, no sitekey, nothing to
        #       solve. A different exit is the only move.
        #   "Just a moment..." with `cf_chl_opt`  — a test. That classifies
        #       as "blocked" with a solvable challenge on it.
        #
        # justjoin.it has produced NEITHER against this scraper. Reaching
        # this branch at all means the site's posture changed, so the
        # message below says what was measured and when rather than
        # asserting what will clear it — there is no measurement here to
        # offer.
        debug_html = f"{args.out}_page{page_num}_debug.html"
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(body or "")
        assets = count_site_assets(body or "")
        logger.error(
            "justjoin.it did not serve this request — %d bytes, its own asset "
            "paths referenced %d time(s), saved to %s. This is NEW: on "
            "2026-09-18 this site served every route to a bare Hetzner "
            "datacentre address (AS24940, Helsinki), to plain curl, to "
            "python-requests and to an empty User-Agent, with no challenge "
            "of any kind on any of seven captures. So there is no measured "
            "remedy to recommend here — what is known is only that the "
            "posture has changed since then. A residential --proxy or "
            "--cdp-endpoint for the Scraping Browser are the usual moves, "
            "and the saved HTML is what says whether this is a challenge "
            "(solvable) or a flat refusal (not). This is exit 3, distinct "
            "from a genuinely empty result (exit 4).%s",
            len(body or ""), assets, debug_html,
            (f" Tried {block_retries + 1} exit(s)." if has_pool
             else f" Re-fetched {block_retries + 1} time(s)."))
        outcome.blocked_by = "refused" if body else "no-response"
        outcome.final_url = session.page.url
        return outcome

    # No readiness wait and no scroll on the content path, and their
    # absence is MEASURED rather than forgotten — see the "unknown" branch
    # above. Every route here is complete in the first response: the
    # endpoint answers with the whole payload, and a rendered listing
    # server-renders all 100 offers into its flight payload. Porting the
    # sibling repos' scroll loop here would be dead code that looks
    # load-bearing (CLAUDE.md §4) — and worse than usual on this site,
    # because the grid is virtualised: scrolling REPLACES DOM nodes, and no
    # row is read from the DOM in the first place.

    # Dumping on success, not only on failure: a run can return the right
    # NUMBER of rows with a field silently unpopulated, and then the only way
    # to tell a parsing bug from a too-early snapshot is to inspect the exact
    # bytes the parser was given.
    if args.dump_html:
        dump_path = (args.dump_html if args.pages == 1
                     else f"{args.dump_html}.page{page_num}")
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write(body)
        logger.info("Saved the snapshot the parser sees to %s (%d bytes).",
                    dump_path, len(body))

    # Only for a state page_flow already counts as BLOCKED, and that
    # narrowing was earned twice.
    #
    # A marker on a page whose products have rendered guards nothing — that
    # is the "detected is not blocking" rule the captcha default follows,
    # applied to the blocking decision instead of the spending one. But
    # `state != "content"` is still too wide: an EMPTY page is a correct
    # answer, and a live run of a /p/<slug> hub reported exit 3 on a 191 KB
    # page the site had plainly served, because the hub's own performance
    # script names `akamaihd.net` and "akamai" was in the marker list. Both
    # halves were wrong; the marker is gone (see
    # product_parser.BOT_CHALLENGE_MARKERS) and this now only refines the
    # REASON for a page the policy had already given up on.
    vendor = (detect_bot_challenge(body)
              if page_flow.counts_as_blocked(state) else None)
    if vendor:
        debug_html = f"{args.out}_page{page_num}_debug.html"
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(body)
        try:
            session.page.screenshot(path=f"{args.out}_page{page_num}_debug.png",
                                    full_page=True)
        except Exception as e:
            logger.warning("Could not capture screenshot: %s", e)
        logger.error("Blocked by %s before parsing (%d bytes) — saved to %s%s. "
                     "This is exit 3, distinct from a genuinely empty result "
                     "(exit 4).", vendor, len(body), debug_html,
                     (f" (tried {block_retries + 1} exit(s))" if has_pool
                      else f" (re-fetched {block_retries + 1} time(s))"))
        outcome.blocked_by = vendor
        return outcome

    if not page_flow.should_parse(state):
        # Reached only for a state the policy says holds no rows — and it
        # says so in ONE place, so an engine cannot quietly decide to parse
        # something its twins would not.
        logger.info("Page %d came back as %s; nothing to parse.", page_num,
                    state)
        outcome.final_url = session.page.url
        return outcome

    products, listing = parse_for_mode(body, session.page.url, args, page_num)
    logger.info("Parsed %d row(s) from page %d.", len(products), page_num)

    if listing is not None:
        # Recorded on every page rather than only the first, so all three
        # engines carry the same outcome shape and so a page that starts
        # disagreeing with page 1 about the totals is visible.
        outcome.total_available = listing.total_jobs
        outcome.site_total = listing.site_total
        outcome.pages_available = listing.pages_available
        outcome.urls_in_itemlist = listing.urls_in_itemlist
        outcome.echoed_page = listing.echoed_page
        outcome.offset = listing.offset
        if page_num == 1 and listing.total_items is not None:
            logger.info("This query matches %d offer(s); this page holds "
                        "%d, from offset %s.", listing.total_items,
                        listing.records_in_payload,
                        listing.offset if listing.offset is not None else "?")
        if page_num == 1 and args.route == "ssr" and listing.urls_in_itemlist:
            # The rendered page's own `CollectionPage` JSON-LD names the
            # offers on it and publishes not one fact about any of them —
            # no title, no company, no salary, no id. Reported so the two
            # views are compared every run rather than a reader inheriting
            # the figure from a comment (§13): the flight payload should
            # hold at least as many records as the JSON-LD names URLs, and
            # an inversion would mean the primary source had started
            # dropping rows.
            logger.info("This page's own JSON-LD names %d offer URL(s) and "
                        "carries no other field about them; the flight "
                        "payload beside it held %d complete record(s).",
                        listing.urls_in_itemlist, listing.records_in_payload)
        if listing.page_repeated:
            # The site answered an out-of-range request with page 1 and
            # HTTP 200. Reported as a fact about the SITE rather than as a
            # failure: the rows are real, they are simply page 1's, and the
            # caller stops the run here instead of collecting them again.
            #
            # Reachable on this site, unlike on most in this family — it is
            # exactly what a rendered listing does with `?page=N`, which is
            # why `--route ssr` plans one page.
            logger.info("Asked for page %s and justjoin.it answered with "
                        "page %s — that is the site saying this route does "
                        "not paginate. Stopping rather than re-collecting "
                        "page %s.", listing.requested_page,
                        listing.echoed_page, listing.echoed_page)

    if products:
        # No price on a job board, so no price coverage. What stands in its
        # place is the handful of fields justjoin.it filled on 1,000 of
        # 1,000 listing records on 2026-09-18 — checked every page, so a
        # payload shape that moves is caught rather than being discovered
        # in the output weeks later.
        for field_name in _core_fields(args.mode):
            filled = sum(1 for row in products if getattr(row, field_name, None) not in (None, "", []))
            share = 100.0 * filled / len(products)
            if share < CORE_FIELD_FLOOR:
                logger.warning(
                    "Only %.0f%% of page %d carries `%s`, against a measured "
                    "floor of %d%%. Every record of every capture had one, "
                    "so this is the payload shape moving rather than the "
                    "offers being unusual — re-run with --dump-html.",
                    share, page_num, field_name, CORE_FIELD_FLOOR)

        if args.mode == "facets":
            groups = sorted({getattr(r, "facet_group", None) or "?"
                             for r in products})
            logger.info("Read %d facet key(s) across %d group(s): %s. These "
                        "counts are NOT capped at 10,000 — they describe the "
                        "whole board, which is what makes them the tool for "
                        "planning filtered runs that reach past the cap.",
                        len(products), len(groups), ", ".join(groups))
        elif args.mode == "offer":
            # One offer has no page statistics, so a listing sentence would
            # be nonsense about a run of one row.
            row = products[0]
            pay = ("not disclosed" if row.salary_min is None else
                   "%g-%g %s/%s %s" % (row.salary_min,
                                       row.salary_max or row.salary_min,
                                       row.salary_currency or "(currency not "
                                       "published)",
                                       row.salary_unit or "?",
                                       "gross" if row.salary_is_gross
                                       else "net"))
            logger.info("Offer: %s at %s, %s, pay %s.", row.title,
                        row.company_name, row.city or "?", pay.strip())
        else:
            # Reported, not floored, and the distinction is the point:
            # every one of these is legitimately absent on a large share of
            # healthy rows, so a threshold on any of them would fire on
            # good data every run. `salary_min` is the one that would most
            # tempt a floor and must not have one — justjoin.it does not
            # require a salary, and 461 of 1,000 offers disclosed none.
            paid = sum(1 for row in products if row.salary_min is not None)
            remote = sum(1 for row in products
                         if (row.workplace_type or "").lower() == "remote")
            langs = sum(1 for row in products if row.languages)
            skilled = sum(1 for row in products if row.required_skills)
            logger.info("Page %d: %d/%d disclose a salary, %d/%d remote, "
                        "%d/%d state a language requirement, %d/%d list "
                        "required skills.",
                        page_num, paid, len(products), remote, len(products),
                        langs, len(products), skilled, len(products))

            converted = sum(1 for row in products
                            if row.salary_currency and row.salary_min is None)
            if converted:
                logger.info("%d row(s) name a currency with no figures — "
                            "an employer who chose a currency and withheld "
                            "the amount.", converted)

    if not products:
        debug_html = f"{args.out}_page{page_num}_debug.html"
        debug_png = f"{args.out}_page{page_num}_debug.png"
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(body)
        try:
            session.page.screenshot(path=debug_png, full_page=True)
        except Exception as e:
            logger.warning("Could not capture screenshot: %s", e)
        logger.warning("0 rows parsed — saved what the browser actually saw to "
                       "%s and %s. Open the .png to see it.", debug_html, debug_png)

    outcome.products = products
    outcome.final_url = session.page.url
    return outcome


def _worker_pool(pool, worker_index: int):
    """A private ProxyPool for one worker, starting at a different exit.

    Each worker gets its OWN pool object holding the same exits rotated to a
    different offset. Two things fall out of that, both wanted:

      * Workers start on distinct exits, which is the point of running
        several — N workers all leaving from one address is just a faster way
        to burn that address.
      * No shared mutable state between threads, so rotation needs no lock.
        A worker that gets blocked can still walk the rest of the pool on its
        own.

    Its exit stays put for the worker's lifetime otherwise: a SESSION must
    not change address mid-flight, and a worker is one session.
    """
    if not pool:
        return None
    proxies = pool.proxies
    offset = worker_index % len(proxies)
    return ProxyPool(proxies[offset:] + proxies[:offset], rotate="per-run")


def _fetch_pages_concurrently(args, pool, specs, concurrency: int):
    """Fetch `specs` [(page_num, url), ...] across `concurrency` workers.

    Each worker owns its own Playwright instance, browser and exit: with the
    sync API a browser belongs to the thread that made it, so sharing one
    across threads is not an option even if it were desirable.
    """
    work = queue.Queue()
    for spec in specs:
        work.put(spec)

    results = []
    results_lock = threading.Lock()
    # Set when a page comes back with no rows at all — the end of the
    # listing. Without it, asking for 50 pages of a 5-page result would fetch
    # 45 empty ones. Workers check it before taking more work, so at most
    # (concurrency - 1) extra pages are in flight when it trips.
    exhausted = threading.Event()

    def worker(index: int):
        name = f"worker-{index + 1}"
        try:
            with sync_playwright() as pw:
                session = _BrowserSession(pw, args, _worker_pool(pool, index)).open()
                try:
                    first = True
                    while not exhausted.is_set():
                        try:
                            page_num, url = work.get_nowait()
                        except queue.Empty:
                            break
                        if not first:
                            time.sleep(args.delay)
                        first = False
                        outcome = _fetch_one_page(session, args, session.pool,
                                                  page_num, url)
                        with results_lock:
                            results.append(outcome)
                        if outcome.ok and not outcome.products:
                            logger.info("[%s] page %d returned no rows — "
                                        "treating that as the end of the listing "
                                        "and stopping dispatch.", name, page_num)
                            exhausted.set()
                finally:
                    session.close()
        except Exception:  # noqa: BLE001 — a dead worker must not hang the run
            logger.exception("[%s] died; its pages will be reported as failed.", name)

    threads = [threading.Thread(target=worker, args=(i,), name=f"page-worker-{i + 1}")
               for i in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Anything still queued was never attempted (a worker died, or dispatch
    # stopped at the end of the listing). Not reported as failed pages: they
    # were not tried, and claiming otherwise would overstate the damage.
    unattempted = []
    while True:
        try:
            unattempted.append(work.get_nowait()[0])
        except queue.Empty:
            break
    return results, sorted(unattempted), exhausted.is_set()


def scrape(args) -> int:
    # One entry per page attempted, merged after the loop rather than folded
    # into shared state during it — see PageOutcome for why that ordering
    # matters more than it looks.
    outcomes: List[PageOutcome] = []
    seen_keys = set()
    blocked = False
    # Every mode here is one row per id, so `sku` is the key for all of
    # them: an offer's `guid` on the two job modes, `{group}:{key}` on
    # `--mode facets`.
    dedupe_key = "sku"
    # Why the loop ended. "completed" means every requested page was
    # fetched; "no_new_products" means the result set itself ran out (also
    # a complete result). Anything else is an early stop, and the run is
    # only a partial view.
    #
    # "single_page_route" is complete by construction for the two routes
    # that have exactly one page: `--mode facets` is one request, and
    # `--route ssr` serves one page and answers ?page=N with page 1 again.
    stop_reason = "completed"
    if args.mode == "facets" or (args.mode == "listings"
                                 and args.route == "ssr"):
        stop_reason = "single_page_route"

    # How many offers `--mode offer` found to fetch. Set by the enumeration
    # below and read after the browser is closed, so it is bound here
    # rather than inside the `with` block — an UnboundLocalError on the
    # reporting path would lose a run that had already done all its work.
    total_enumerated = 0
    # What the board says it holds, against what this query can reach. Both
    # are read from the run rather than inherited from a comment (§13).
    site_total: Optional[int] = None

    pool = proxy_pool_from_args(args)
    if pool and args.cdp_endpoint:
        logger.warning("Ignoring --proxy/--proxy-file: with --cdp-endpoint the "
                       "remote browser has its own exit, and layering a second "
                       "proxy on top would contradict it.")
        pool = None

    concurrency = max(1, args.concurrency)
    if concurrency > 1:
        if page_flow.concurrency_for_mode(args.mode, concurrency,
                                          args.route) == 1:
            logger.info("--concurrency is ignored here: --mode %s%s fetches "
                        "a single page, so a second worker would have "
                        "nothing to do. --mode listings --route api and "
                        "--mode offer are the modes that parallelise — both "
                        "address every page independently.",
                        args.mode,
                        " --route ssr" if args.mode == "listings" else "")
            concurrency = 1
        elif page_flow.concurrency_limit(args.cdp_endpoint) == 1:
            # The limit is page_flow's to state, not this engine's, so all
            # three engines refuse in the same place for the same reason.
            logger.warning("--concurrency is ignored with --cdp-endpoint: the "
                           "Scraping Browser API allows one live connection per "
                           "profile, and several workers would collide on it "
                           "(profile_locked). Use several pids instead, one run "
                           "each.")
            concurrency = 1
        elif not pool:
            logger.warning("--concurrency %d with no proxy pool: every worker "
                           "leaves from the SAME address, which is a faster "
                           "way to get that address scored than to gather "
                           "data. justjoin.it served every request measured "
                           "on 2026-09-18 from a bare datacentre IP, so an "
                           "address that works is one worth not burning. "
                           "Pass --proxy-file to spread the load.",
                           concurrency)
        if pool and pool.rotates_per_page():
            logger.info("--proxy-rotate per-page is redundant under "
                        "--concurrency: each worker already holds its own exit "
                        "for its lifetime, which is the same spread without a "
                        "browser relaunch per page.")
        if concurrency > 8:
            logger.warning("--concurrency %d means %d browsers at once "
                           "(~150-300MB each). Make sure the machine has the "
                           "memory for it.", concurrency, concurrency)

    with sync_playwright() as pw:
        session = _BrowserSession(pw, args, pool,
                                  remote=bool(args.cdp_endpoint)).open()
        try:
            target = target_url(args)

            # In --mode offer the enumeration comes FIRST, because it is
            # what decides which address page 1 even is. Everywhere else
            # page 1's own content is what decides how many pages there
            # are, so the order is the other way round.
            offer_slugs: List[str] = []
            if args.mode == "offer" and not is_single_offer_url(args):
                offer_slugs = page_flow.enumerate_offers(
                    lambda u: _fetch_page_source(session, u), args)
                total_enumerated = len(offer_slugs)
                if not offer_slugs:
                    # Nothing to fetch, so nothing is written — `save`
                    # leaves the previous run's good output in place and
                    # no sidecar contradicts it (CLAUDE.md §9).
                    return finish_run(
                        [], args.out, args.format, args.allow_empty,
                        blocked=False, stop_reason="enumeration_empty",
                        pages_requested=args.pages, pages_completed=0,
                        mode=args.mode, source=SOURCE_DEFAULT,
                        start_url=args.url or "", final_url="")
                first_url = detail_api_url(offer_slugs[0])
                if first_url:
                    target = first_url

            if args.url and target != args.url:
                logger.info("Reading %s through %s — the endpoint is the "
                            "route with pagination, and the filters your URL "
                            "names are carried over to it.", args.url, target)
            logger.info("Fetching %s", target)

            # The board-wide total, for the cap arithmetic. One extra
            # request, and it buys the difference between a sidecar that
            # says "complete" and one that says "complete, and a 52%
            # sample" (CLAUDE.md §21). Never fatal: a run without it is
            # still a good run, it just cannot state the share.
            if args.mode == "listings":
                try:
                    site_total = parse_count_response(
                        _fetch_text(session, count_api_url()))
                except (PWError, PWTimeout) as exc:
                    logger.info("The board-wide count did not load (%s); the "
                                "run continues without the cap share.",
                                _mask_credentials(str(exc))[:120])

            # Page 1 is always fetched on its own: its content is what
            # decides how many pages 2..N there are to address at all.
            first = _fetch_one_page(session, args, pool, 1, target)
            outcomes.append(first)

            if not first.ok:
                stop_reason = ("page_load_timeout" if first.load_failed
                               else f"blocked_{first.blocked_by}")
                blocked = first.blocked_by is not None
            elif page_flow.is_terminal(first.state):
                # Terminal by nature: another attempt at the same address
                # cannot help, and neither is a block.
                stop_reason = first.state
            elif args.mode == "offer" and not offer_slugs:
                pass  # `--url` named one offer; one page is the whole run
            else:
                seen_keys.update(p.sku for p in first.products if p.sku is not None)
                planned = page_flow.plan_page_urls(
                    args, first.final_url or target, first.pages_available,
                    offer_slugs=offer_slugs)
                if len(planned) + 1 < args.pages:
                    # Capped by the site's own arithmetic — a complete
                    # answer rather than an early stop.
                    stop_reason = "page_cap_reached"

                if (args.pages > 1 and concurrency > 1
                        and not page_flow.pagination_is_addressable(
                            first.final_url or target, args.route)):
                    logger.warning("--concurrency %d requested, but this "
                                   "listing's pages cannot be addressed "
                                   "independently — falling back to one page "
                                   "at a time.", concurrency)
                    concurrency = 1

                if planned and concurrency > 1:
                    # Close the page-1 browser before starting workers: it
                    # has done its job, and holding it open would cost one
                    # more browser than asked for.
                    session.close()
                    specs = [(n, planned[n - 2]) for n in range(2, len(planned) + 2)]
                    logger.info("Fetching pages 2-%d across %d workers%s.",
                                len(planned) + 1, concurrency,
                                f" over {len(pool)} exit(s)" if pool else "")
                    rest, unattempted, exhausted = _fetch_pages_concurrently(
                        args, pool, specs, concurrency)
                    outcomes.extend(rest)

                    failed = [o for o in rest if not o.ok]
                    if failed:
                        worst = min(failed, key=lambda o: o.page_num)
                        stop_reason = ("page_load_timeout" if worst.load_failed
                                       else f"blocked_{worst.blocked_by}")
                        blocked = any(o.blocked_by for o in rest)
                    elif exhausted:
                        stop_reason = "no_new_products"
                    elif unattempted:
                        # Should not happen without a failure or exhaustion,
                        # but say so rather than reporting a complete run.
                        stop_reason = "pages_unattempted"
                    session = None  # already closed
                elif planned:
                    url = planned[0]
                    for page_num in range(2, len(planned) + 2):
                        # A new exit per page is what actually spreads a
                        # run's volume, and it costs a browser relaunch:
                        # carrying the session across exits would defeat the
                        # point.
                        if pool and pool.rotates_per_page():
                            pool.advance(f"per-page rotation, page {page_num}")
                            session.relaunch()

                        outcome = _fetch_one_page(session, args, pool, page_num, url)
                        outcomes.append(outcome)
                        if not outcome.ok:
                            stop_reason = ("page_load_timeout" if outcome.load_failed
                                           else f"blocked_{outcome.blocked_by}")
                            blocked = outcome.blocked_by is not None
                            break
                        if page_flow.is_terminal(outcome.state):
                            # `not_found` is ordinary in --mode offer: an
                            # offer taken down between the sitemap being
                            # read and its record being fetched. It ends
                            # THIS page, not the run.
                            if args.mode != "offer" or outcome.state != "not_found":
                                stop_reason = outcome.state
                                break

                        # Whether this page contributed anything not already
                        # seen. Kept as a running check because the
                        # condition is inherently sequential — "new" only
                        # means anything relative to the pages before it.
                        # The authoritative dedupe happens once, after the
                        # loop, in page order.
                        fresh_count = sum(1 for p in outcome.products
                                          if p.sku is None or p.sku not in seen_keys)
                        seen_keys.update(p.sku for p in outcome.products
                                         if p.sku is not None)

                        # A page past the first that contributes nothing new
                        # means the end of the results — or that pagination
                        # is looping back on itself. Either way there is
                        # nothing further to fetch, and this is the honest
                        # terminating condition: a property of the DATA, not
                        # of a CSS selector that may have been renamed
                        # (CLAUDE.md §7).
                        if not fresh_count:
                            logger.info("Page %d added no rows not already seen "
                                        "— treating that as the end of the "
                                        "results.", page_num)
                            stop_reason = "no_new_products"
                            break

                        if page_num - 1 < len(planned):
                            url = planned[page_num - 1]
                            time.sleep(args.delay)
        finally:
            if session is not None:
                session.close()

    # Merge once, in PAGE order — not in the order pages happened to finish.
    # At one page at a time the two are identical, which is the point: this
    # is what keeps the output byte-for-byte the same while removing the
    # dependency on arrival order that concurrency would otherwise
    # introduce.
    all_rows = []
    merged_seen = set()
    for oc in sorted(outcomes, key=lambda o: o.page_num):
        fresh = dedupe_by_key(oc.products, merged_seen, key=dedupe_key)
        if len(fresh) < len(oc.products):
            # Logged rather than silently applied, because on this site a
            # duplicate has a specific and interesting cause: the endpoint
            # paginates by numeric OFFSET over a board that is published to
            # continuously, so one new offer arriving at the top during a
            # run shifts every later offset by one and the row at the seam
            # is fetched twice. That is the site moving, not a bug, and it
            # is worth seeing in the log of a long run.
            logger.info("Page %d: dropped %d duplicate row(s) — most likely "
                        "the board shifting under a paginated run.",
                        oc.page_num, len(oc.products) - len(fresh))
        all_rows.extend(fresh)

    # Completeness, checked over the MERGED result rather than per page — a
    # per-page check cannot see a gap BETWEEN two pages, which is exactly
    # where a short page hides (CLAUDE.md §8).
    total_available = next((o.total_available for o in outcomes
                            if o.total_available is not None), None)
    pages_available = next((o.pages_available for o in outcomes
                            if o.pages_available is not None), None)

    cap = cap_report(total_available, site_total, len(all_rows))

    if args.mode == "listings" and all_rows:
        if cap["capped_by_site"]:
            # CLAUDE.md §21: "complete" and "exhaustive" are different
            # words, and a run that says only "complete" is lying by
            # omission. Reported every run rather than inherited from a
            # README measured on some other day (§13).
            logger.info("This query was CAPPED: justjoin.it reports at most "
                        "%s results for any one query, and this one hit that "
                        "ceiling. The board holds %s offer(s). Collected %d "
                        "(%s%% of what this query can reach, %s%% of the "
                        "board). Use --mode facets and then filtered runs — "
                        "by --category, --city or --experience — to cover "
                        "what one query cannot.",
                        f"{MAX_FROM:,}",
                        f"{site_total:,}" if site_total else "an unknown "
                        "number of",
                        len(all_rows),
                        cap["share_of_query_pct"]
                        if cap["share_of_query_pct"] is not None else "?",
                        cap["share_of_board_pct"]
                        if cap["share_of_board_pct"] is not None else "?")
        elif total_available is not None:
            logger.info("This query matched %s offer(s) and is not capped; "
                        "this run collected %d of them (%s%%).%s",
                        f"{total_available:,}", len(all_rows),
                        cap["share_of_query_pct"]
                        if cap["share_of_query_pct"] is not None else "?",
                        "" if len(all_rows) >= (total_available or 0)
                        else "  Raise --pages to fetch the rest.")

        # The salary columns are the ones most worth a coverage line,
        # because they are the ones a silent parser change would empty.
        # A floor is NOT asserted on them: 461 of 1,000 offers disclose no
        # salary at all, which is the site and not a fault.
        with_salary = sum(1 for r in all_rows
                          if getattr(r, "salary_min", None) is not None)
        if all_rows:
            logger.info("%d of %d row(s) disclose a salary (%.0f%%). The "
                        "rest are justjoin.it's own \"Undisclosed Salary\" "
                        "— null here, never zero.",
                        with_salary, len(all_rows),
                        100.0 * with_salary / len(all_rows))

    if args.mode == "offer" and total_enumerated:
        done = len([o for o in outcomes if o.ok])
        logger.info("Fetched %d of the %d offer(s) this run enumerated "
                    "(%.1f%%).%s", done, total_enumerated,
                    100.0 * done / total_enumerated,
                    "" if done >= total_enumerated
                    else "  Pass --pages %d to cover the rest." % total_enumerated)

    ok_pages = [o for o in outcomes if o.ok]
    failed_pages = [o.page_num for o in outcomes if not o.ok]
    final_url = (max(ok_pages, key=lambda o: o.page_num).final_url
                 if ok_pages else args.url)

    # One-per-run context, in the sidecar rather than repeated down a
    # column. What goes here is this run's OWN arithmetic about how much of
    # the board it covered — which is what a bare "complete" hides (§21),
    # and what stops a reader of the file inheriting a number from a README
    # that was measured on some other day.
    extra = {"mode": args.mode, "route": args.route,
             "route_is_paginated": page_flow.pagination_is_addressable(
                 final_url or "", args.route) or args.mode == "offer"}
    if args.mode == "listings":
        extra.update(cap)
        extra["pages_available"] = pages_available
        extra["items_per_page"] = args.per_page
        extra["sort"] = args.sort
        extra["order"] = args.order
        # The ordering decides WHICH rows are in the file, not just their
        # order: the same query under `publishedAt` and under `salary`
        # overlapped on 5 of 100 ids (measured 2026-09-18), because each is
        # a different slice of a capped result set. So it is recorded, and
        # `diff_runs.py` refuses to compare two runs that disagree on it
        # (CLAUDE.md §21).
        filters = filters_from_args(args).as_params()
        if filters:
            extra["filters"] = filters
    elif args.mode == "offer":
        extra["offers_enumerated"] = total_enumerated or None
        extra["offers_fetched"] = len(ok_pages)

    return finish_run(all_rows, args.out, args.format, args.allow_empty,
                      blocked=blocked, stop_reason=stop_reason,
                      pages_requested=args.pages, pages_completed=len(ok_pages),
                      pages_failed=failed_pages, mode=args.mode,
                      source=SOURCE_DEFAULT,
                      start_url=args.url, final_url=final_url,
                      extra=extra)


def parse_args():
    p = argparse.ArgumentParser(
        description="justjoin.it job scraper — the board, one offer in "
                    "full, or the board's own facet counts "
                    "(Playwright edition)")
    p.add_argument("--url", default=None,
                   help="A justjoin.it URL. A browsable listing "
                        "(https://justjoin.it/job-offers/warszawa/python) "
                        "has its filters read off it and is fetched through "
                        "the endpoint, which is the route with pagination. "
                        "An offer URL "
                        "(https://justjoin.it/job-offer/{slug}) with --mode "
                        "offer fetches exactly that offer and skips the "
                        "enumeration. OPTIONAL — every mode has a correct "
                        "default. Also read from JUSTJOIN_URL in the "
                        "environment or in .env.")
    p.add_argument("--mode", choices=list(MODES), default=DEFAULT_MODE,
                   help="listings (default): the board, paginated and "
                        "filtered. offer: one offer per page, in full — the "
                        "description body, per-skill levels, the company URL "
                        "and size — enumerated from justjoin.it's own "
                        "sitemap (10,646 offers on 2026-09-18), or from "
                        "--url, or from --slugs-file. facets: the board's "
                        "own taxonomy with its own counts, which are NOT "
                        "subject to the 10,000 cap and are therefore how you "
                        "plan a run that reaches past it. --mode facets "
                        "writes a different row schema, and diff_runs.py "
                        "will not compare it against the other two.")
    p.add_argument("--route", choices=list(ROUTES), default=DEFAULT_ROUTE,
                   help="Which route --mode listings reads. api (default): "
                        "justjoin.it's offers endpoint — every page, every "
                        "filter, up to 1,000 rows a request. ssr: the "
                        "rendered listing page, ONE page of 100 rows with a "
                        "poorer schema (no category, no languages, no skill "
                        "levels), and the only route justjoin.it's "
                        "robots.txt permits — it carries Disallow: /api/. "
                        "Kept so a reader who wants to stay inside "
                        "robots.txt does not have to fork this repo.")
    p.add_argument("--category", default=None, metavar="KEY",
                   help="Filter to one of justjoin.it's own category keys. "
                        "The 25 it published on 2026-09-18: " +
                        ", ".join(CATEGORY_KEYS) + ". This filters at the "
                        "SITE — it changes what is downloaded — which is "
                        "worth saying because several plausible parameter "
                        "names do not: `categoryKeys`, the spelling in the "
                        "payload's own field names, is accepted and "
                        "silently ignored.")
    p.add_argument("--city", default=None, metavar="NAME",
                   help="Filter to a city, by the name justjoin.it uses "
                        "(Warszawa, Krakow, Wroclaw, Gdansk...). Accented "
                        "spellings work and are percent-encoded for you; "
                        "sending one raw is an HTTP 400 that reads like the "
                        "city being unsupported.")
    p.add_argument("--city-radius", type=int, default=30, metavar="KM",
                   help="Kilometres around --city (default 30, which is what "
                        "justjoin.it's own nav sends). Ignored without "
                        "--city.")
    p.add_argument("--remote", action="store_true",
                   help="Remote offers only (4,523 of the board on "
                        "2026-09-18). Note this is the site's "
                        "`remoteWorkOptions` filter — `workplaceTypes`, the "
                        "field name on the row, is accepted and ignored.")
    p.add_argument("--keyword", default=None, metavar="TEXT",
                   help="Full-text search. Sent as the site sends it, with "
                        "keywordType=any; the singular `keyword` is accepted "
                        "and ignored. Consider --sort match with this.")
    p.add_argument("--experience", action="append", default=None,
                   metavar="LEVEL",
                   help="Seniority, repeatable: " +
                        ", ".join(EXPERIENCE_LEVELS) + ".")
    p.add_argument("--employment", action="append", default=None,
                   metavar="TYPE",
                   help="Contract type, repeatable: " +
                        ", ".join(EMPLOYMENT_TYPES) + ".")
    p.add_argument("--working-time", action="append", default=None,
                   metavar="TIME",
                   help="Working arrangement, repeatable: " +
                        ", ".join(WORKING_TIMES) + ".")
    p.add_argument("--language", action="append", default=None, metavar="CODE",
                   help="Required language, repeatable, as an ISO code (en, "
                        "pl, de...). 5,981 of the board required English on "
                        "2026-09-18.")
    p.add_argument("--with-salary", action="store_true",
                   help="Only offers that disclose a salary (4,641 of the "
                        "board on 2026-09-18). Without it, 461 rows in 1,000 "
                        "carry a null salary — justjoin.it does not require "
                        "one, and a null here is the site rather than a "
                        "parsing fault.")
    p.add_argument("--sort", choices=list(SORTS), default=DEFAULT_SORT,
                   help="publishedAt (default, newest first — the site's own "
                        "default), salary, or match. The ordering decides "
                        "WHICH offers are in the file and not just their "
                        "order, because every query is capped at 10,000: the "
                        "same query under publishedAt and under salary "
                        "overlapped on 5 of the first 100 ids. It is "
                        "recorded in the sidecar and diff_runs.py refuses to "
                        "compare two runs that disagree on it.")
    p.add_argument("--order", choices=list(ORDERS), default=DEFAULT_ORDER,
                   help="descending (default) or ascending.")
    p.add_argument("--per-page", type=int, default=DEFAULT_ITEMS_COUNT,
                   metavar="N",
                   help="Offers per request, 1-%d (default %d, which is what "
                        "the site's own front end asks for). Larger pages "
                        "mean fewer requests for the same rows; %d is the "
                        "ceiling, and asking for 2,000 is an HTTP 400."
                        % (MAX_ITEMS_COUNT, DEFAULT_ITEMS_COUNT,
                           MAX_ITEMS_COUNT))
    p.add_argument("--slugs-file", default=None, metavar="PATH",
                   help="For --mode offer: read offer slugs from a file "
                        "instead of the sitemap. Accepts a previous run's "
                        "JSON or CSV output directly (it reads the slug or "
                        "url column), or a plain list one per line. This is "
                        "the enrichment path — gather the board cheaply with "
                        "--mode listings, then fetch full records for the "
                        "offers you care about.")
    p.add_argument("--pages", type=int, default=1,
                   help="Number of pages to fetch. A run PLANS against the "
                        "`totalItems` the endpoint states on page 1 AND "
                        "against justjoin.it's own 10,000 ceiling, and never "
                        "asks past either — an offset at the ceiling answers "
                        "HTTP 500 rather than an empty page, which would "
                        "read like a bug in this scraper. In --mode offer a "
                        "page is one offer. Does not apply to --mode facets "
                        "(one request) or --route ssr (one page).")
    p.add_argument("--delay", type=float, default=2.0, help="Delay between pages, seconds")
    p.add_argument("--concurrency", type=int, default=1, metavar="N",
                   help="Fetch pages through N parallel workers (default 1 — "
                        "unchanged sequential behaviour). Each worker runs its "
                        "own browser and holds its own proxy exit, so N>1 "
                        "without --proxy-file just sends N times the traffic "
                        "from one address. Ignored with --cdp-endpoint.")
    p.add_argument("--retries", type=int, default=3,
                   help="Attempts per page load before giving up (default 3). "
                        "The pause between attempts doubles each time. A page "
                        "that comes back EMPTY is not retried — see "
                        "page_flow.STATE_POLICY — because an empty hub "
                        "category is a correct answer, not a fault.")
    p.add_argument("--retry-delay", type=float, default=2.0,
                   help="Seconds before the first page-load retry, doubling "
                        "thereafter (default 2.0)")
    p.add_argument("--format", choices=["json", "csv", "both"], default="both")
    p.add_argument("--out", default="justjoin_jobs", help="Output file prefix")
    p.add_argument("--locale", default="en-US",
                   help="Browser locale (default en-US). It does NOT change "
                        "which offers come back: justjoin.it's endpoint is "
                        "not geo-routed, and the same query answered "
                        "identically from a Polish and a German exit. This "
                        "only affects what the browser claims about itself. "
                        "The site does serve Polish and English copy, but "
                        "the offer records are the employers' own text "
                        "either way.")
    p.add_argument("--proxy", default=None,
                   help="Proxy URL, e.g. http://ACCOUNT:PASSWORD@HOST:9999 "
                        "(2captcha.com/proxy)")
    p.add_argument("--proxy-file", default=None,
                   help="File with one proxy URL per line (# comments and blank "
                        "lines skipped) to rotate across. Wins over --proxy.")
    p.add_argument("--proxy-rotate", choices=list(ROTATE_MODES), default="per-run",
                   help="per-run (default): one exit for the whole run. per-page: "
                        "a new exit for every page — this is what spreads volume, "
                        "and it relaunches the browser each time so the session "
                        "does not follow the IP around.")
    p.add_argument("--proxy-shuffle", action="store_true",
                   help="Shuffle the pool at startup, so concurrent runs do not "
                        "all begin on the first exit in the file.")
    p.add_argument("--proxy-block-retries", type=int, default=2,
                   help="When a page comes back refused (HTTP 403) or behind "
                        "a captcha, retry it from this many OTHER exits before "
                        "giving up (default 2). Needs a pool of more than one; "
                        "ignored otherwise. This is the flag that matters most "
                        "on this site: the refusal is a property of the "
                        "ADDRESS, and a different exit is what clears it.")
    p.add_argument("--twocaptcha-key", default=None, help="2captcha.com API key")
    p.add_argument("--allow-empty", action="store_true",
                   help="Write output files even when 0 rows were found. Off by "
                        "default so a failed run can't overwrite a good result "
                        "with an empty one; exit code is 4 either way.")
    p.add_argument("--fingerprint", action="store_true",
                   help="Fetch a browser fingerprint from 2captcha's Fingerprint "
                        "API and apply it to the launched browser. Needs "
                        "--twocaptcha-key. Ignored with --cdp-endpoint, where the "
                        "Scraping Browser supplies its own.")
    # ONE OS-family tag, not a list — and the default is what makes
    # --fingerprint work at all. It shipped as "Windows,Chrome,Desktop" in
    # this family, which the API rejects with HTTP 400 ("Request parameters
    # are invalid"), so --fingerprint failed on every invocation. Measured
    # 2026-09-10: `Windows` succeeds, and `Windows,Chrome,Desktop`, `Chrome`
    # and `Desktop` each 400. fingerprint_client.py's own --tags help has
    # said so all along; the engines' default contradicted it.
    p.add_argument("--fp-tags", default="Windows",
                   help="ONE OS-family tag for the fingerprint filter: "
                        "Windows, Microsoft Windows or Android. NOT a list — "
                        "Chrome, Desktop and Mobile are each rejected by the "
                        "API with 400, and no combination is accepted. Use "
                        "--fp-country to narrow further. (default: Windows)")
    p.add_argument("--fp-country", default=None,
                   help="Fingerprint country, ISO 3166-1 alpha-2. Match it to "
                        "your proxy's exit country — a US fingerprint on a "
                        "German IP is a contradiction.")
    p.add_argument("--captcha-api", choices=["v2", "v1"], default="v2",
                   help="Which 2captcha solver API to use. v2 is the current "
                        "JSON API (api.2captcha.com/createTask); v1 is the "
                        "legacy in.php/res.php pair. Applies to both the image "
                        "captcha and reCAPTCHA.")
    p.add_argument("--solve-captcha", choices=["when-blocked", "always"],
                   default="when-blocked",
                   help="when-blocked (default): only pay to solve a "
                        "captcha if the content is not already readable. "
                        "always: solve whenever one is detected. On "
                        "justjoin.it neither setting has ever fired: no "
                        "route this scraper reads rendered a challenge on "
                        "2026-09-18. The site DOES carry Google reCAPTCHA "
                        "v2 — it publishes its own key as "
                        "`googleRecaptchaV2Key` — but only on the job "
                        "APPLICATION form, which this scraper never "
                        "submits. Should that change, a v2 widget is what "
                        "captcha_solver.py solves.")
    p.add_argument("--min-score", type=float, default=0.7,
                   help="reCAPTCHA v3 minimum score to request (0.3, 0.7 or 0.9 "
                        "— the API only accepts these three). Ignored for v2 "
                        "widgets.")
    p.add_argument("--cdp-endpoint", default=None,
                   help="Connect to an already-running browser over CDP instead "
                        "of launching Playwright's bundled Chromium, e.g. "
                        "ws://user:pass@host:port — the Scraping Browser API "
                        "endpoint, or any browser that exposes a CDP URL. "
                        "--proxy and --headless/--headful are ignored when this "
                        "is set.")
    p.add_argument("--dump-html", default=None, metavar="PATH",
                   help="Save the exact HTML the parser is given, on success as "
                        "well as failure. Useful when the row count is right but "
                        "a column comes back empty — see TROUBLESHOOTING.md.")
    p.add_argument("--headless", action="store_true", default=True)
    p.add_argument("--headful", dest="headless", action="store_false")
    args = p.parse_args()
    # Fill --twocaptcha-key / --cdp-endpoint / --proxy / --url from the
    # environment or .env when the flag was not given. An explicit flag wins.
    env_config.apply(args)

    # Every mode has a correct default URL, so `--url` is optional
    # everywhere. It is checked for being a route this scraper reads, and a
    # URL naming a different route than `--mode` is a real conflict.
    #
    # A listing URL and the filter flags do NOT conflict: a browsable
    # address is read back into the same filters the site's own front end
    # would send for it, and an explicit flag wins over anything read off
    # the URL (CLAUDE.md §3's precedence rule, applied to filters).
    if args.url:
        ok, why = is_supported_url(args.url)
        if not ok:
            p.error(why)
        implied = mode_for_url(args.url)
        if implied and implied != args.mode:
            # Refused rather than silently corrected. Reading an offer URL
            # under --mode listings would parse it with the wrong reader
            # and report zero rows for a perfectly good response — the
            # failure CLAUDE.md §20 describes, where a served page that
            # parses to nothing sends the reader to check the URL instead
            # of the code.
            p.error("--url is a %s URL but --mode is %s. %s is read by "
                    "--mode %s; pass that, or drop --url and let --mode %s "
                    "use its own default."
                    % (implied, args.mode, args.url, implied, args.mode))

    if args.mode == "facets" and args.pages != 1:
        logger.warning("--pages %d is ignored in --mode facets: the board's "
                       "facet counts are one request. The run status will "
                       "say single_page_route.", args.pages)
        args.pages = 1

    if args.mode == "listings" and args.route == "ssr" and args.pages != 1:
        # Said out loud rather than silently ignored: a user who passed
        # --pages 5 expects five pages of something. Measured rather than
        # assumed — ?page=2 on /job-offers/all-locations answered HTTP 200
        # with page 1 again, `meta.from` still 0, on 2026-09-18.
        logger.warning("--pages %d is ignored with --route ssr: a rendered "
                       "listing serves ONE page and answers ?page=N with "
                       "page 1 again, so planning more would re-collect page "
                       "1 and report a complete multi-page run. Use --route "
                       "api (the default) for more than 100 rows. The run "
                       "status will say single_page_route.", args.pages)
        args.pages = 1

    if not (1 <= args.per_page <= MAX_ITEMS_COUNT):
        p.error("--per-page must be between 1 and %d; justjoin.it answers "
                "HTTP 400 above that." % MAX_ITEMS_COUNT)

    if args.mode != "listings":
        # Refused rather than ignored, so a filtered `--mode offer` run
        # cannot look like it narrowed the enumeration when it did not.
        used = [name for name, value in (
            ("--category", args.category), ("--city", args.city),
            ("--keyword", args.keyword), ("--remote", args.remote),
            ("--experience", args.experience), ("--language", args.language),
            ("--employment", args.employment),
            ("--working-time", args.working_time),
            ("--with-salary", args.with_salary)) if value]
        if used:
            p.error("%s filter%s the offers endpoint, which --mode %s does "
                    "not read. Use --mode listings to filter, then --mode "
                    "offer --slugs-file on its output to fetch those offers "
                    "in full."
                    % (", ".join(used), "" if len(used) == 1 else "s",
                       args.mode))

    # The sidecar's run label. `--category` is a real site filter here, so
    # unlike the repo this engine was ported from it is NOT reused as the
    # label: a flag that both filters and names the run would make a
    # filtered run and an unfiltered one look the same in the sidecar.
    args.run_label = (args.category or category_from_url(args.url or "")
                      or args.mode)

    return args


if __name__ == "__main__":
    args = parse_args()
    if args.fingerprint and not args.twocaptcha_key:
        logger.error("--fingerprint needs --twocaptcha-key (the Fingerprint API "
                     "uses the same key, though it's a separate subscription "
                     "from solving).")
        sys.exit(2)
    if args.fingerprint and args.cdp_endpoint:
        logger.warning("--fingerprint is ignored with --cdp-endpoint: the "
                       "Scraping Browser supplies its own fingerprint, and "
                       "stacking a second one on top creates a mismatch rather "
                       "than better cover.")
    try:
        sys.exit(scrape(args))
    except ProxyError as e:
        # Bad usage, not a crash: a typo in a proxy list would otherwise
        # surface as a connection failure on page 1 with nothing naming it.
        logger.error("%s", e)
        sys.exit(2)
    except PWError as e:
        # A remote browser that will not accept the connection is a REMOTE
        # API failure (exit 5), not a crash in this code (exit 1) and not bad
        # usage (exit 2). The distinction earns its keep on the commonest one:
        # `profile_locked` means another run still holds this `pid`, and a
        # harness that sees exit 1 goes looking for a bug in the scraper
        # instead of waiting or passing a different pid.
        text = _mask_credentials(str(e))
        if "profile_locked" in text or "connect to --cdp-endpoint" in text:
            logger.error("%s", text)
            sys.exit(EXIT_API_ERROR)
        raise
