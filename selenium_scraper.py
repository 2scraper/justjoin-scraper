"""
justjoin-scraper — Selenium edition (parity engine)

Behaviour is identical to `playwright_scraper.py`: same flags, same modes,
same exit codes, same output. Playwright is the primary engine; this one
exists so the family's claim of three interchangeable engines is testable
rather than asserted (CLAUDE.md §6).

Everything this file knows about justjoin.it it gets from
`product_parser.py`, and every retry / solve / blocked decision from
`page_flow.py` — including which URL a run fetches and which pages it
plans, which are shared FUNCTIONS rather than three copies of an if-chain.
What lives here is only HOW to ask Selenium.

See `playwright_scraper.py`'s docstring for what is different about this
site: the endpoint the front end calls is not the one the page config
names, `?page=2` on a rendered listing silently returns page 1, the salary
is not the first number in the list, and every query is capped at 10,000.

Usage
-----
    python3 selenium_scraper.py
    python3 selenium_scraper.py --category python --pages 3 --format csv
    python3 selenium_scraper.py --mode facets
    python3 selenium_scraper.py --mode offer --pages 50
"""

import argparse
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse, urlsplit

from selenium import webdriver
from selenium.common.exceptions import (TimeoutException, WebDriverException,
                                        JavascriptException)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from captcha_solver import (detect_recaptcha_v3, detect_recaptcha_in_page,
                            reconcile_detections, solve_recaptcha,
                            CaptchaUnsolvable, INJECT_TOKEN_JS,
                            RECAPTCHA_DISCOVERY_JS, detect_turnstile,
                            wait_for_turnstile, TURNSTILE_INTERCEPT_JS,
                            TURNSTILE_INJECT_JS, turnstile_task_for)
from product_parser import (API_PATH, MODES, DEFAULT_MODE, ROUTES,
                            DEFAULT_ROUTE, CATEGORY_KEYS, DEFAULT_ITEMS_COUNT,
                            EXPERIENCE_LEVELS, EMPLOYMENT_TYPES, WORKING_TIMES,
                            SORTS, ORDERS, DEFAULT_SORT, DEFAULT_ORDER,
                            MAX_ITEMS_COUNT, MAX_FROM, FACETS_JOIN,
                            cap_report, categories_api_url, category_from_url,
                            count_api_url, count_site_assets, detail_api_url,
                            detect_bot_challenge, facets_api_url,
                            filters_from_args, is_single_offer_url,
                            is_supported_url, mode_for_url, page_url,
                            parse_count_response, parse_for_mode,
                            route_of, site_recaptcha_sitekey,
                            split_facets_body, target_url)
from output_writer import (dedupe_by_key, finish_run, EXIT_API_ERROR,
                           ROW_CLASS_BY_MODE, SOURCE_DEFAULT)
import page_flow
from page_flow import MIN_CARD_MATCHES
from proxy_pool import (from_args as proxy_pool_from_args, mask, ROTATE_MODES,
                        ProxyError, split_credentials)
import env_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("selenium_scraper")

ITEM_LINK_SELECTOR = page_flow.READY_SELECTOR_LISTING

# The share of rows that must carry the columns justjoin.it fills on every
# record, below which the read has broken rather than the data being
# unusual. 1,000 of 1,000 listing records and 40 of 40 detail records
# carried all four on 2026-09-18.
#
# Deliberately NOT here, each with the measurement that keeps it out:
# `salary_min` (539 of 1,000 — justjoin.it does not require a salary),
# `languages` (465 of 1,000, and 0 on --route ssr), `nice_to_have_skills`
# (46 of 1,000), `apply_url` (800 of 1,000, null on exactly the 200 whose
# apply_method is "form"), `country_code` (0 on the listing route).
CORE_FIELD_FLOOR = 99
CORE_FIELDS = ("title", "url", "sku", "company_name")
CORE_FIELDS_OFFER = ("title", "url", "sku", "company_name", "description")
CORE_FIELDS_FACETS = ("title", "sku", "facet_group", "facet_key")


def _core_fields(mode: str):
    if mode == "offer":
        return CORE_FIELDS_OFFER
    if mode == "facets":
        return CORE_FIELDS_FACETS
    return CORE_FIELDS


# The endpoint serves exactly `itemsCount` records until the result set runs
# out, so any page but the last holding fewer means something went wrong.
# See playwright_scraper.py for the measurement.
THIN_PAGE_SHARE = 0.9

PAGE_LOAD_TIMEOUT = 60
SCRIPT_TIMEOUT = 30

# Chromium's own names for "the proxy is the problem, not the site". A dead
# proxy and a slow page want opposite responses — a different exit versus
# another try at the same one — so they are told apart by the error text.
_PROXY_ERROR_MARKERS = (
    "ERR_PROXY_CONNECTION_FAILED", "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_AUTH_UNSUPPORTED", "ERR_PROXY_AUTH_REQUESTED",
    "ERR_UNEXPECTED_PROXY_AUTH", "ERR_PROXY_CERTIFICATE_INVALID",
)


@dataclass
class PageOutcome:
    """What one page produced. Mirrors playwright_scraper.PageOutcome."""
    page_num: int
    url: str
    final_url: Optional[str] = None
    products: List = field(default_factory=list)
    blocked_by: Optional[str] = None
    load_failed: bool = False
    state: Optional[str] = None
    # What this response said about itself.
    #
    # `total_available` is the endpoint's `meta.totalItems` — how many
    # offers this QUERY matched, capped by the site at 10,000.
    # `urls_in_itemlist` is how many offer URLs a rendered page's own
    # `CollectionPage` JSON-LD names, which is only meaningful on
    # `--route ssr`.
    total_available: Optional[int] = None
    site_total: Optional[int] = None
    offset: Optional[int] = None
    # How many entries the same response's ItemList JSON-LD indexes. 326
    # against 390 records on 2026-09-18 — the gap is why the parser reads
    # the React Query cache, and recording it per run means nobody inherits
    # the figure from a comment (§13).
    urls_in_itemlist: Optional[int] = None
    pages_available: Optional[int] = None
    # The page number the SERVER answered with, read back out of the Apollo
    # cache key — not an echo of the request. Asking for page 48 of a
    # 47-page listing comes back stating page 1, which is how a run learns it
    # has walked off the end instead of silently re-collecting page 1.
    echoed_page: Optional[int] = None
    # The page number the SERVER answered with, read back from the response rather
    # than echoed from the request.

    @property
    def ok(self) -> bool:
        return not self.load_failed and self.blocked_by is None


# Every `scheme://user:pass@` in a string, however many times it occurs.
# Matching globally rather than once is the point: a driver's connection
# error can repeat the endpoint several times (the message plus a call log),
# so a masker that handled only the first occurrence would print the password
# the other times and look like it was working.
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


def _chrome_ua(version: str) -> str:
    """A desktop-Chrome UA naming the browser's OWN real version.

    `driver.capabilities["browserVersion"]` is the installed Chrome's version,
    so the claim matches what the JS engine and the TLS handshake report. A
    hardcoded number drifts the moment Chrome updates, and claiming an older
    Chrome than everything else reports is itself a signal.
    """
    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{version} Safari/537.36")


def _cdp_host_port(endpoint: str) -> str:
    """`host:port` for chromedriver's debuggerAddress, or exit 2 with a reason.

    chromedriver takes a bare address here and cannot send credentials, so an
    endpoint that carries them cannot work through this engine. Refused up
    front: connecting anyway would fail somewhere further in with an error
    that names none of this.
    """
    parts = urlsplit(endpoint if "//" in endpoint else f"//{endpoint}")
    if parts.username or parts.password:
        logger.error(
            "This --cdp-endpoint carries credentials (%s), and Selenium cannot "
            "send them: chromedriver's debuggerAddress is a bare host:port. "
            "Use playwright_scraper.py or puppeteer_scraper.py for a "
            "credentialed endpoint such as the Scraping Browser API — both "
            "authenticate on the WebSocket upgrade.",
            _mask_credentials(endpoint))
        sys.exit(2)
    host = parts.hostname or endpoint
    port = f":{parts.port}" if parts.port else ""
    return f"{host}{port}"


class _Session:
    """One Chrome driver, relaunchable onto a different exit.

    Same contract as the Playwright engine's _BrowserSession, including the
    rule that a rotation means a genuinely FRESH browser — and a
    fresh browser is also the only thing that re-rolls the served page
    fresh cookie jar is what an ordinary user on another network looks like.
    """

    def __init__(self, args, pool):
        self.args, self.pool = args, pool
        self.remote = bool(args.cdp_endpoint)
        self.driver = None

    def open(self):
        options = Options()
        if self.remote:
            options.debugger_address = _cdp_host_port(self.args.cdp_endpoint)
            logger.info("Attaching to an existing browser at %s.",
                        options.debugger_address)
            # No UA, no proxy, no fingerprint on this path: the remote browser
            # brings its own, and stacking a second creates a contradiction
            # rather than better cover.
            self.driver = webdriver.Chrome(options=options)
            self._apply_timeouts()
            return self

        if self.args.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1600,1000")
        # Not a fingerprint measure, a correctness one: without it Chrome
        # advertises "HeadlessChrome", which is a giveaway on any site with
        # a bot manager in front of it.
        options.add_argument("--disable-blink-features=AutomationControlled")
        # Flag parity with the Playwright engine, and really applied rather
        # than accepted and ignored: Chrome takes the locale as --lang. It
        # does NOT decide which listing is fetched — that is
        # find_country in the URL — so this only affects what the browser
        # claims about itself.
        options.add_argument(f"--lang={self.args.locale}")

        if self.pool:
            scrubbed, credentials = split_credentials(self.pool.current)
            options.add_argument(f"--proxy-server={scrubbed}")
            logger.info("Using proxy exit %s", mask(self.pool.current))
            if credentials:
                logger.warning(
                    "This proxy has credentials and SELENIUM CANNOT SEND "
                    "THEM: --proxy-server accepts an address only, and there "
                    "is no Selenium equivalent of pyppeteer's "
                    "page.authenticate. They have been stripped, so requests "
                    "will go out unauthenticated and the exit will most "
                    "likely refuse them. Use playwright_scraper.py or "
                    "puppeteer_scraper.py for an authenticated proxy.")

        self.driver = webdriver.Chrome(options=options)
        self._apply_timeouts()

        version = self.driver.capabilities.get("browserVersion", "")
        if version:
            # Set over CDP rather than as a launch switch, so it can use the
            # version the driver actually reports.
            try:
                self.driver.execute_cdp_cmd(
                    "Network.setUserAgentOverride",
                    {"userAgent": _chrome_ua(version)})
            except WebDriverException as e:
                logger.debug("Could not override the user agent: %s", e)

        if self.args.fingerprint:
            self._apply_fingerprint()
        return self

    def _apply_timeouts(self):
        # Explicit, because a driver that stops answering otherwise hangs the
        # run: "every remote call is bounded" applies to this engine too.
        self.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        self.driver.set_script_timeout(SCRIPT_TIMEOUT)

    def _apply_fingerprint(self):
        from fingerprint_client import get_fingerprint, playwright_init_script
        fp = get_fingerprint(self.args.twocaptcha_key, tags=self.args.fp_tags,
                             country=self.args.fp_country)
        ua = (fp.get("userAgent") or {}).get("value")
        script = playwright_init_script(fp)
        try:
            if ua:
                self.driver.execute_cdp_cmd("Network.setUserAgentOverride",
                                            {"userAgent": ua})
            # The same patch script the Playwright engine installs on its
            # context. Shared deliberately: two engines applying different
            # halves of one fingerprint would be a contradiction of exactly
            # the kind a fingerprint is meant to avoid.
            self.driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument", {"source": script})
            logger.info("Using 2captcha fingerprint %s (%s)", fp.get("id"),
                        fp.get("country"))
        except WebDriverException as e:
            logger.warning("Could not apply the fingerprint over CDP (%s) — "
                           "continuing without it.", e)

    def relaunch(self):
        if self.remote:
            return
        self.close()
        self.open()

    def close(self):
        try:
            if self.driver is not None:
                # quit(), not close(): close() ends one window and leaves the
                # driver process running, which on a per-page rotation would
                # leak a chromedriver per page.
                self.driver.quit()
        except Exception as e:  # noqa: BLE001 — teardown must not mask the reason we're here
            logger.debug("Ignoring error during driver teardown: %s", e)


# ---------------------------------------------------------------------------
# page_flow, bound to Selenium
# ---------------------------------------------------------------------------
# Only "how to ask this driver" lives here. Note the JS dialect: Selenium's
# execute_script runs a function BODY and needs an explicit `return`, unlike
# the `() => expr` both other engines take — which is why page_flow names
# operations instead of passing JavaScript.
def _driver(session):
    driver = session.driver

    def count(selector):
        try:
            return len(driver.find_elements(By.CSS_SELECTOR, selector))
        except WebDriverException as e:
            logger.debug("count(%s) failed: %s", selector, e)
            return 0

    def sleep(ms):
        time.sleep(ms / 1000.0)

    def content():
        try:
            return driver.page_source
        except WebDriverException as e:
            # A URL canonicalisation can navigate, so a snapshot can land on
            # the document swap. None tells the caller to skip a check rather
            # than fail the run.
            logger.debug("page_source unavailable (page navigating?): %s", e)
            return None

    def current_url():
        try:
            return driver.current_url
        except WebDriverException:
            return ""

    # No scroll primitive, and its absence is measured rather than
    # forgotten: every route this scraper reads is complete in the first
    # response, and justjoin.it's grid is virtualised on top of that — so
    # scrolling would REPLACE DOM nodes and produce not one extra row.
    # Mirrors the Playwright engine.
    return {"count": count, "sleep": sleep, "content": content,
            "current_url": current_url}


def _is_endpoint(url: str) -> bool:
    """Whether this address answers with raw JSON rather than a document.

    True for everything under `/api/candidate-api/`, which is every route
    this scraper reads except `--route ssr`. Getting it wrong is SILENT in
    both directions — see `playwright_scraper._is_endpoint` for the two
    failure modes. All three engines branch on this same predicate.
    """
    return API_PATH in (url or "")


def _snapshot(session, url: str):
    """What the parser is given for this address.

    A PAGE is read with `page_source`. The ENDPOINT answers with JSON, which
    Chromium wraps in its own JSON-viewer markup — so `page_source` there
    returns the viewer's HTML and the payload would be unreachable. Reading
    the body text gives back exactly what the server sent.

    Note the JS dialect: a function BODY with an explicit `return`, not the
    arrow expression the other two engines pass. That difference is exactly
    why no JavaScript crosses the page_flow boundary.
    """
    if _is_endpoint(url):
        try:
            return session.driver.execute_script(
                "return document.body.innerText;") or ""
        except WebDriverException as e:
            logger.warning("Could not read the endpoint response: %s", e)
            return None
    return _driver(session)["content"]()


def _fetch_page_source(session, url: str, timeout_ms: int = 60000) -> str:
    """Navigate and return the serialised document. Used for enumeration.

    `page_source` rather than a text reading, and that matters for the
    sitemap: Chromium renders `text/xml` through its own XML viewer, and the
    serialised document keeps all 463 `<loc>` elements intact where the
    viewer's rendered text does not.
    """
    session.driver.set_page_load_timeout(max(1, timeout_ms // 1000))
    session.driver.get(url)
    return session.driver.page_source or ""


def handle_captcha_if_present(session, args) -> bool:
    """Detect and solve a challenge. True if something was solved.

    Same detectors, same reconciliation and the same "detected is not
    blocking" rule as the Playwright engine — the three must agree about
    when a run spends money.

    NOTE what this has never had to help with: justjoin.it has rendered no
    challenge to this scraper on any route, from any client, on any of the
    seven captures taken 2026-09-18. See product_parser.detect_page_state.
    """
    driver = session.driver
    d = _driver(session)
    html = d["content"]()
    if html is None:
        return False

    selector = page_flow.ready_selector(args.mode)
    already_rendered = d["count"](selector)
    when_blocked = getattr(args, "solve_captcha", "when-blocked") == "when-blocked"

    html_challenge = detect_recaptcha_v3(html, d["current_url"]())
    runtime_challenge = detect_recaptcha_in_page(
        lambda js: driver.execute_script(f"return ({js})();"),
        page_url=d["current_url"]())
    challenge = reconcile_detections(html_challenge, runtime_challenge)
    if not challenge:
        return False
    if when_blocked and already_rendered > MIN_CARD_MATCHES:
        logger.info("%s detected via %s, but %d anchors are already on the "
                    "page — not solving it.", challenge.kind, challenge.source,
                    already_rendered)
        return False
    logger.warning("%s detected via %s (sitekey=%s) — attempting to solve.",
                   challenge.kind, challenge.source, challenge.sitekey)
    if not args.twocaptcha_key:
        logger.warning("No 2captcha API key, so this challenge cannot be solved.")
        return False
    try:
        token = solve_recaptcha(challenge, args.twocaptcha_key,
                                api_version=args.captcha_api,
                                min_score=args.min_score)
    except Exception as e:  # noqa: BLE001
        logger.error("Solving the challenge failed (%s).", e)
        return False
    try:
        driver.execute_script(f"return ({INJECT_TOKEN_JS})(arguments[0]);", token)
    except WebDriverException as e:
        logger.error("Could not inject the token (%s).", e)
        return False
    logger.info("Token injected. Reloading page to continue.")
    time.sleep(1.5)
    driver.refresh()
    return True





def _solve_budget(args, spent: int):
    """Whether another solve may be bought for this page.

    `page_flow.SOLVES_PER_PAGE` says at most one purchase per page, and that
    is a MONEY limit rather than a style rule. It was not being enforced:
    `handle_captcha_if_present` is called twice per attempt — once before the
    page is classified and once after — and only the SECOND call was counted.

    INHERITED EVIDENCE, from a SIBLING repo and not from justjoin.it — labelled
    because §13 says a number you inherited is not a number you measured,
    and this one cannot be reproduced here: justjoin.it has never rendered a
    challenge to this scraper, so no page of this site has ever bought a
    solve at all.

    On that sibling, from a datacenter address that meets a real Cloudflare
    challenge on every fetch (2026-09-17): ONE page bought THREE Turnstile
    solves, and every token was refused. The cap read as enforced and was
    not (CLAUDE.md §17). Both call sites now go through here.

    The fix is carried here anyway. A budget that is never exercised is
    still the difference between a bill and no bill on the day it is.
    """
    return spent < page_flow.SOLVES_PER_PAGE


def _fetch_one_page(session, args, pool, page_num: int, url: str) -> PageOutcome:
    """Fetch and parse one page. Mirrors playwright_scraper._fetch_one_page.

    Kept structurally parallel to its twins on purpose — "all three engines
    agree" is checked by reading them side by side as well as by the smoke
    suite.
    """
    outcome = PageOutcome(page_num=page_num, url=url)
    d = _driver(session)
    html, state, load_failed = None, "ok", False

    # See the Playwright engine for the measurement: without a pool there is
    # no exit to rotate to, but a plain re-fetch is what clears a block on a
    # Scraping Browser profile, so the budget is not zero.
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

    for block_attempt in range(block_retries + 1):
        logger.info("Fetching page %d/%d: %s", page_num, args.pages, url)
        load_failed, exit_failed = False, None
        # Selenium exposes NO HTTP status: `driver.get()` returns None and
        # WebDriver has no response object. So this engine passes None and
        # relies on the parser reading the status out of the site's own
        # problem document instead — which is why that reader exists at all
        # (see `product_parser.problem_status`). Its twins pass the real
        # status, which is strictly better information; both reach the same
        # verdict on this site because justjoin.it states its own.
        http_status = None
        for attempt in range(1, args.retries + 1):
            try:
                session.driver.get(url)
                load_failed = False
                break
            except (TimeoutException, WebDriverException) as e:
                text = str(e)
                reason = next((m for m in _PROXY_ERROR_MARKERS if m in text), "")
                load_failed = True
                if reason:
                    exit_failed = reason
                    break  # a different exit is the only thing that helps
                if attempt < args.retries:
                    pause = args.retry_delay * (2 ** (attempt - 1))
                    logger.warning("Failed to load %s (attempt %d/%d: %s) — "
                                   "retrying in %.1fs.", url, attempt,
                                   args.retries, text[:120], pause)
                    time.sleep(pause)

        if exit_failed and has_pool and block_attempt < block_retries:
            logger.warning("Exit %s is unusable (%s) — rotating to another "
                           "one (%d/%d).", mask(pool.current), exit_failed,
                           block_attempt + 1, block_retries)
            pool.advance(f"unusable exit: {exit_failed}")
            session.relaunch()
            d = _driver(session)
            continue
        if load_failed:
            break


        # Counted, because it can BUY. See _solve_budget.
        if _solve_budget(args, solves_bought):
            solves_bought += 1
            if handle_captcha_if_present(session, args):
                time.sleep(1)

        elif solves_bought:
            logger.info("Not solving again on page %d: %d purchase(s) "
                        "already made for it and SOLVES_PER_PAGE is %d.",
                        page_num, solves_bought, page_flow.SOLVES_PER_PAGE)
        html = _snapshot(session, url) or ""
        state = page_flow.classify(html, http_status, d["current_url"](),
                                   args.mode, args.route)

        # every route here is complete in the first response, so a body is parseable in the
        # FIRST response and there is nothing to wait for on a healthy page.
        # Measured with no pause at all after the load. The wait below is
        # only for the state that says the site served SOMETHING that is not the
        # payload. Mirrors playwright_scraper exactly.
        if state == "unknown":
            wait_ms = page_flow.content_timeout_ms(args.mode)
            sel = page_flow.ready_selector(args.mode)
            need = page_flow.min_matches(args.mode)
            logger.info("Page %d is something justjoin.it served (%d bytes, its own "
                        "assets referenced %d time(s)) but carries no listing "
                        "payload — waiting up to %.0fs rather than spending a "
                        "retry.", page_num, len(html),
                        count_site_assets(html), wait_ms / 1000.0)
            found = page_flow.wait_for_count(d["count"], sel, need, wait_ms,
                                             d["sleep"])
            if found < need:
                logger.info("Still nothing after %.0fs (%d match(es) for %s).",
                            wait_ms / 1000.0, found, sel)
            html = _snapshot(session, url) or html
            state = page_flow.classify(html, http_status, d["current_url"](),
                                   args.mode, args.route)

        # The paid path is reached only for state "challenge" — Cloudflare's
        # Managed Challenge, which IS a test. It is NOT reached for
        # "blocked": the hard "You have been blocked" page carries no widget
        # and no sitekey, so a solve there would be a charge for nothing.
        # Bounded by SOLVES_PER_PAGE. Mirrors playwright_scraper.
        if (page_flow.should_solve(state)
                and _solve_budget(args, solves_bought)):
            solves_bought += 1
            if handle_captcha_if_present(session, args):
                time.sleep(1)
                html = d["content"]() or html
                state = page_flow.classify(html, http_status, d["current_url"](),
                                   args.mode, args.route)
                if state == "content":
                    logger.info("The solve was accepted — page %d is content "
                                "now.", page_num)
                else:
                    logger.warning("The solve was NOT accepted: page %d is "
                                   "still %s. The purchase is spent.",
                                   page_num, state)

        if not page_flow.should_retry(state):
            # "content" and "empty" are both final answers. An empty page is
            # a CORRECT one — a hub category has no grid — so retrying it
            # would re-confirm the same right answer, and rotating the exit
            # would blame an address for the URL it was given.
            break

        # Blocked or challenged. The ADDRESS is what was scored, not the URL,
        # so a different exit is the only thing that plausibly changes the
        # outcome.
        if block_attempt < block_retries:
            if has_pool:
                logger.warning("Page %d came back as %s from %s — retrying "
                               "from another exit (%d/%d).", page_num, state,
                               mask(pool.current), block_attempt + 1,
                               block_retries)
                pool.advance(f"{state} on page {page_num}")
                session.relaunch()
                d = _driver(session)
            else:
                # No pool, so nowhere else to go — a plain re-fetch through
                # the same access path is all that is left.
                #
                # This `else` was MISSING in the repo this engine was ported
                # from — both parity engines called `mask(pool.current)`
                # unguarded while the Playwright engine guarded it, so any
                # retryable state on a run with no proxy died with
                # `AttributeError: 'NoneType' object has no attribute
                # 'current'` instead of retrying (CLAUDE.md §16: copied core
                # is untested core).
                pause = args.retry_delay * (block_attempt + 1)
                logger.warning("Page %d came back as %s — re-fetching "
                               "through the same access path in %.1fs "
                               "(%d/%d).", page_num, state, pause,
                               block_attempt + 1, block_retries)
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
        # justjoin.it has never refused this scraper — see
        # playwright_scraper's twin of this block. This is the HARD refusal:
        # no widget, no sitekey, nothing a key could buy.
        debug_html = f"{args.out}_page{page_num}_debug.html"
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(html or "")
        logger.error(
            "justjoin.it did not serve this request — %d bytes, its own asset "
            "paths referenced %d time(s), saved to %s. This is NEW: on "
            "2026-09-18 this site served every route to a bare "
            "datacentre address with no challenge of any kind, so there "
            "is no measured remedy to recommend — the saved HTML is the "
            "evidence for what changed. This is exit 3, distinct from an "
            "empty result (exit 4).",
            len(html or ""), count_site_assets(html or ""), debug_html)
        outcome.blocked_by = "cloudflare (hard block)" if html else "no-response"
        outcome.final_url = d["current_url"]()
        return outcome

    # No readiness wait and no scroll on the content path, and their absence
    # is MEASURED rather than forgotten — see the "unknown" branch above.
    # Mirrors playwright_scraper.

    if args.dump_html:
        dump_path = (args.dump_html if args.pages == 1
                     else f"{args.dump_html}.page{page_num}")
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Saved the snapshot the parser sees to %s (%d bytes).",
                    dump_path, len(html))

    # Only when the page is NOT already content. A challenge marker on a
    # page whose products have rendered guards nothing — and over
    # --cdp-endpoint the Scraping Browser's own auto-solve extension injects
    # such markers into every page it loads.
    # Only for a state page_flow already counts as BLOCKED. An EMPTY page is
    # a correct answer, and a live run of a /p/<slug> hub reported exit 3 on
    # a page the site had plainly served because the hub's own performance
    # script names `akamaihd.net`. Mirrors playwright_scraper exactly.
    vendor = (detect_bot_challenge(html)
              if page_flow.counts_as_blocked(state) else None)
    if vendor:
        debug_html = f"{args.out}_page{page_num}_debug.html"
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(html)
        try:
            session.driver.save_screenshot(f"{args.out}_page{page_num}_debug.png")
        except WebDriverException as e:
            logger.warning("Could not capture screenshot: %s", e)
        logger.error("Blocked by %s before parsing (%d bytes) — saved to %s. "
                     "This is exit 3, distinct from a genuinely empty result "
                     "(exit 4).", vendor, len(html), debug_html)
        outcome.blocked_by = vendor
        return outcome

    final_url = d["current_url"]() or url
    if not page_flow.should_parse(state):
        logger.info("Page %d came back as %s; nothing to parse.", page_num,
                    state)
        outcome.final_url = final_url
        return outcome

    products, listing = parse_for_mode(html, final_url, args, page_num)
    logger.info("Parsed %d row(s) from page %d.", len(products), page_num)

    if listing is not None:
        # Recorded on every page so all three engines carry one shape; on
        # recorded every time and page 1's copy is what the run plans against.
        outcome.total_available = listing.total_jobs
        outcome.site_total = listing.site_total
        outcome.offset = listing.offset
        outcome.pages_available = listing.pages_available
        outcome.urls_in_itemlist = listing.urls_in_itemlist
        outcome.echoed_page = listing.echoed_page
        if page_num == 1:
            # justjoin.it states `meta.totalItems` per query, so what is
            # reported is this response's own two views of itself. Printing
            # a line of Nones about totals the site does not publish would
            # read like a parse failure on a page that parsed perfectly.
            logger.info("This response held %d listing(s); its own ItemList "
                        "JSON-LD indexes %d.", listing.records_in_payload,
                        listing.urls_in_itemlist)
        if listing.page_repeated:
            # The site clamped an out-of-range page back and answered 200.
            logger.info("Asked for page %s and justjoin.it answered with page "
                        "%s — that is this site's way of saying the listing "
                        "has ended.", listing.requested_page,
                        listing.echoed_page)

    if products:
        for field_name in _core_fields(args.mode):
            filled = sum(1 for row in products
                         if getattr(row, field_name, None) not in (None, "", []))
            share = 100.0 * filled / len(products)
            if share < CORE_FIELD_FLOOR:
                logger.warning(
                    "Only %.0f%% of page %d carries `%s`, against a measured "
                    "floor of %d%%. Every record of every capture had one, so "
                    "this is the payload shape moving rather than the "
                    "listings being unusual — re-run with --dump-html.",
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
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(html)
        try:
            session.driver.save_screenshot(f"{args.out}_page{page_num}_debug.png")
        except WebDriverException as e:
            logger.warning("Could not capture screenshot: %s", e)
        logger.warning("0 rows parsed — saved what the browser actually saw to "
                       "%s.", debug_html)

    outcome.products = products
    outcome.final_url = final_url
    return outcome


def scrape(args) -> int:
    outcomes: List[PageOutcome] = []
    seen_keys = set()
    blocked = False
    # All three modes are one row per business-at-a-location, so `sku` is the
    # key for all of them.
    dedupe_key = "sku"
    # Only --mode profile is single-page. Both listing modes paginate
    # identically, so neither may be treated as single-page — that is the
    # silent-success failure this family exists to avoid.
    # "single_page_route" is complete by construction for the two routes
    # that have exactly one page: `--mode facets` is one request, and
    # `--route ssr` serves one page and answers ?page=N with page 1 again.
    stop_reason = "completed"
    if args.mode == "facets" or (args.mode == "listings"
                                 and args.route == "ssr"):
        stop_reason = "single_page_route"

    # How many offers --mode offer found to fetch. Bound here rather than
    # inside the session block, so the reporting path cannot raise
    # UnboundLocalError on a run that had already done all its work.
    total_enumerated = 0
    # What the board says it holds, against what this query can reach.
    site_total: Optional[int] = None

    pool = proxy_pool_from_args(args)
    if pool and args.cdp_endpoint:
        logger.warning("Ignoring --proxy/--proxy-file: with --cdp-endpoint the "
                       "remote browser has its own exit, and layering a second "
                       "proxy on top would contradict it.")
        pool = None
    if args.concurrency > 1:
        logger.warning("--concurrency is ignored in this engine: parallel page "
                       "fetching is implemented in playwright_scraper.py, "
                       "which is the primary engine. Running one page at a "
                       "time.")

    session = None
    try:
        session = _Session(args, pool).open()

        target = target_url(args)

        # In --mode job the enumeration comes FIRST, because it is what
        # decides which address page 1 even is. Everywhere else page 1's own
        # content decides how many pages there are, so the order reverses.
        offer_slugs = []
        if args.mode == "offer" and not is_single_offer_url(args):
            offer_slugs = page_flow.enumerate_offers(
                lambda u: _fetch_page_source(session, u), args)
            if not offer_slugs:
                return finish_run(
                    [], args.out, args.format, args.allow_empty,
                    blocked=False, stop_reason="enumeration_empty",
                    pages_requested=args.pages, pages_completed=0,
                    mode=args.mode, source=SOURCE_DEFAULT,
                    start_url=args.url or "", final_url="")
            total_enumerated = len(offer_slugs)
            target = offer_slugs[0]

        if target != args.url:
            logger.info("Fetching %s", target)

            # The board-wide total, for the cap arithmetic. One extra
            # request, and it buys the difference between a sidecar that
            # says "complete" and one that says "complete, and a 52%
            # sample" (CLAUDE.md §21). Never fatal.
            if args.mode == "listings":
                try:
                    site_total = parse_count_response(
                        _fetch_page_source(session, count_api_url()))
                except Exception as exc:  # noqa: BLE001
                    logger.info("The board-wide count did not load (%s); the "
                                "run continues without the cap share.",
                                _mask_credentials(str(exc))[:120])

        first = _fetch_one_page(session, args, pool, 1, target)
        outcomes.append(first)

        if not first.ok:
            stop_reason = ("page_load_timeout" if first.load_failed
                           else f"blocked_{first.blocked_by}")
            blocked = first.blocked_by is not None
        elif page_flow.is_terminal(first.state):
            # A 404, or an offset past the site's 10,000 ceiling. Another
            # attempt at the same address cannot help and neither is a
            # block, so the run stops and says which it was.
            stop_reason = first.state
        elif args.mode == "offer" and not offer_slugs:
            pass  # `--url` named one offer; one page is the whole run
        else:
            seen_keys.update(p.sku for p in first.products if p.sku is not None)

            # Planned through the SHARED helper, so all three engines decide
            # this identically: one page for a route that has one, and one
            # page per enumerated job in --mode job.
            page_one = first.final_url or target
            planned = page_flow.plan_page_urls(args, page_one, first.pages_available,
                                      offer_slugs=offer_slugs)
            if len(planned) + 1 < args.pages:
                stop_reason = "page_cap_reached"

            for index, url in enumerate(planned):
                page_num = index + 2
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

                fresh_count = sum(1 for p in outcome.products
                                  if p.sku is None or p.sku not in seen_keys)
                seen_keys.update(p.sku for p in outcome.products
                                 if p.sku is not None)
                if not fresh_count:
                    logger.info("Page %d added no rows not already seen — "
                                "treating that as the end of the listing.",
                                page_num)
                    stop_reason = "no_new_products"
                    break

                if index + 1 < len(planned):
                    time.sleep(args.delay)
    finally:
        if session is not None:
            session.close()

    all_rows = []
    merged_seen = set()
    for oc in sorted(outcomes, key=lambda o: o.page_num):
        fresh = dedupe_by_key(oc.products, merged_seen, key=dedupe_key)
        if len(fresh) < len(oc.products):
            logger.info("Page %d: dropped %d duplicate row(s).",
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
            logger.info("%d of %d row(s) disclose a salary (%.0f%%).%s",
                        with_salary, len(all_rows),
                        100.0 * with_salary / len(all_rows),
                        "" if with_salary == len(all_rows) else
                        "  The rest are justjoin.it's own \"Undisclosed "
                        "Salary\" — null here, never zero.")

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
                    "full, or the board's own facet counts (Selenium "
                    "edition). Cannot authenticate a proxy or a remote CDP "
                    "endpoint — "
                    "see the module docstring; playwright_scraper.py is the "
                    "primary engine.")
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
                   help="Accepted for flag parity and IGNORED here: parallel "
                        "page fetching lives in playwright_scraper.py.")
    p.add_argument("--retries", type=int, default=3,
                   help="Attempts per page load before giving up (default 3). "
                        "A page that comes back EMPTY is not retried: an empty "
                        "hub category is a correct answer, not a fault.")
    p.add_argument("--retry-delay", type=float, default=2.0,
                   help="Seconds before the first retry, doubling thereafter")
    p.add_argument("--format", choices=["json", "csv", "both"], default="both")
    p.add_argument("--out", default="justjoin_jobs", help="Output file prefix")
    p.add_argument("--locale", default="en-US",
                   help="Browser locale (default en-US), passed to Chrome as "
                        "--lang. It does NOT decide which directory is "
                        "searched; that is find_country in the URL.")
    p.add_argument("--proxy", default=None,
                   help="Proxy URL. NOTE: Selenium cannot authenticate a "
                        "proxy; credentials are stripped and a warning says "
                        "so. Use the Playwright or pyppeteer engine for an "
                        "authenticated exit.")
    p.add_argument("--proxy-file", default=None,
                   help="File with one proxy URL per line to rotate across. "
                        "Wins over --proxy.")
    p.add_argument("--proxy-rotate", choices=list(ROTATE_MODES), default="per-run")
    p.add_argument("--proxy-shuffle", action="store_true")
    p.add_argument("--proxy-block-retries", type=int, default=2)
    p.add_argument("--twocaptcha-key", default=None, help="2captcha.com API key")
    p.add_argument("--allow-empty", action="store_true",
                   help="Write output files even when 0 rows were found.")
    p.add_argument("--fingerprint", action="store_true",
                   help="Fetch a fingerprint from 2captcha's Fingerprint API "
                        "and apply it over CDP. Needs --twocaptcha-key. "
                        "Ignored with --cdp-endpoint.")
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
                        "your proxy's exit country.")
    p.add_argument("--captcha-api", choices=["v2", "v1"], default="v2")
    p.add_argument("--solve-captcha", choices=["when-blocked", "always"],
                   default="when-blocked",
                   help="when-blocked (default): only pay to solve a "
                        "reCAPTCHA if the content is not already readable. "
                        "always: solve whenever one is detected. Note that "
                        "NO challenge has ever been observed on this site — a "
                        "refused request gets no page at all — so neither "
                        "setting has anything to act on today, and neither "
                        "helps with a refusal.")
    p.add_argument("--min-score", type=float, default=0.7)
    p.add_argument("--cdp-endpoint", default=None,
                   help="Attach to a running browser at host:port. Must NOT "
                        "carry credentials — chromedriver's debuggerAddress "
                        "cannot send them, so a credentialed endpoint is "
                        "refused with exit 2 rather than silently failing.")
    p.add_argument("--dump-html", default=None, metavar="PATH",
                   help="Save the exact HTML the parser is given, on success "
                        "as well as failure.")
    p.add_argument("--headless", action="store_true", default=True)
    p.add_argument("--headful", dest="headless", action="store_false")
    args = p.parse_args()
    env_config.apply(args)

    # Spelled identically to the other two engines.
    #
    # Every mode has a correct default URL, so there is nothing to refuse
    # about a missing one. justjoin.it's
    # index accepts no parameters, so there is no --role/--location
    # equivalent that could contradict a path.
    if args.url:
        ok, why = is_supported_url(args.url)
        if not ok:
            p.error(why)
        implied = mode_for_url(args.url)
        if implied and implied != args.mode:
            # Refused rather than silently corrected: reading a careers URL
            # under --mode listings would parse it with the wrong reader and
            # report zero rows for a perfectly good page (CLAUDE.md §20).
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
        logger.error("--fingerprint needs --twocaptcha-key.")
        sys.exit(2)
    if args.fingerprint and args.cdp_endpoint:
        logger.warning("--fingerprint is ignored with --cdp-endpoint: the "
                       "remote browser supplies its own.")
    try:
        sys.exit(scrape(args))
    except ProxyError as e:
        logger.error("%s", e)
        sys.exit(2)
    except Exception as e:
        # A remote browser that will not accept the connection is a REMOTE
        # API failure (exit 5), not a crash in this code (exit 1) and not bad
        # usage (exit 2). The distinction earns its keep on the commonest
        # one: `profile_locked` means another run still holds this `pid`, and
        # a harness that sees exit 1 goes looking for a bug in the scraper
        # instead of waiting or passing a different pid.
        text = _mask_credentials(str(e))
        if "profile_locked" in text or "connect to --cdp-endpoint" in text:
            logger.error("%s", text)
            sys.exit(EXIT_API_ERROR)
        raise
