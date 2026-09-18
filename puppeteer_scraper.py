"""
justjoin-scraper — pyppeteer edition (parity engine)

Behaviour is identical to `playwright_scraper.py`: same flags, same modes,
same exit codes, same output. Playwright is the primary engine; this one
exists so the family's claim of three interchangeable engines is testable
rather than asserted (CLAUDE.md §6).

Everything this file knows about justjoin.it it gets from
`product_parser.py`, and every retry / solve / blocked decision from
`page_flow.py` — including which URL a run fetches and which pages it
plans, which are shared FUNCTIONS rather than three copies of an if-chain.
What lives here is only HOW to ask pyppeteer.

See `playwright_scraper.py`'s docstring for what is different about this
site: the endpoint the front end calls is not the one the page config
names, `?page=2` on a rendered listing silently returns page 1, the salary
is not the first number in the list, and every query is capped at 10,000.

Usage
-----
    python3 puppeteer_scraper.py
    python3 puppeteer_scraper.py --category python --pages 3 --format csv
    python3 puppeteer_scraper.py --mode facets
    python3 puppeteer_scraper.py --mode offer --pages 50
"""

import argparse
import asyncio
import concurrent.futures
import logging
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse, urljoin, parse_qsl

# At module level, deliberately, and not inside the launch path where it
# started out. The offline suite guards `import puppeteer_scraper` behind
# try/except ImportError and REPORTS the skip, and CI's engine-smoke job fails
# on any reported skip — that whole mechanism only works if importing this
# module actually requires the driver. With the import hidden inside
# _Session.open(), the module imported cleanly with no pyppeteer installed at
# all, the group never skipped, and CI could not have noticed a broken import.
# It also let CI install pyppeteer 0.0.25 (a stub, resolved from an unpinned
# `pip install pyppeteer`) without anything failing, because nothing ever
# imported it.
from pyppeteer import launch, connect

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
logger = logging.getLogger("puppeteer_scraper")

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

# Every await in this file goes through the bridge below with a timeout, so a
# hung remote call ends the operation instead of the run. pyppeteer provides
# no connect timeout of its own and its page methods' `timeout` option does
# not cover a browser that has stopped answering at all.
DEFAULT_OP_TIMEOUT = 120
CONNECT_TIMEOUT = 30


class _AsyncBridge:
    """Runs pyppeteer's coroutines on a private event loop, synchronously.

    Exists so this engine can reuse page_flow.py unchanged. That module holds
    the policy all three engines must share (how long to wait for
    challenge, when to scroll, when only a fresh session helps) and it is
    written against plain synchronous callables — which is the right shape for
    two of the three drivers. Bridging here keeps the policy in one place
    rather than growing an async copy of it that would drift.

    The second benefit is the one the family's rules actually require: every
    call gets an explicit, enforced timeout. `.result(timeout)` returns
    control even when the browser never answers, which is not something
    pyppeteer's own API offers.
    """

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, daemon=True,
                                        name="pyppeteer-loop")
        self._thread.start()

    def _serve(self):
        asyncio.set_event_loop(self.loop)
        # pyppeteer leaves CDP calls in flight when a browser closes, and the
        # loop then logs each one as "Future exception was never retrieved:
        # NetworkError('Protocol error Target.sendMessageToTarget: Target
        # closed.')" — at ERROR level, AFTER a successful run has printed its
        # results. Five of those under a "Saved 48 products" line read as a
        # failed run. Only that shape is swallowed; anything else still gets
        # the default handler, because silencing the loop wholesale would hide
        # real faults.
        self.loop.set_exception_handler(self._on_loop_exception)
        self.loop.run_forever()

    @staticmethod
    def _on_loop_exception(loop, context):
        # BOTH, not one or the other. asyncio puts its own words in
        # `message` ("Future exception was never retrieved") and the library's
        # in `exception` (a NetworkError about a closed CDP session), and an
        # `or` between them looks at the exception and never sees the message
        # — which is why these kept printing after they were "handled".
        message = " | ".join(
            str(context.get(k)) for k in ("exception", "message")
            if context.get(k))
        if any(m in message for m in (
                "Target closed", "Connection closed",
                # asyncio's own words when the loop stops with work in
                # flight. Emitted after a successful run; see close().
                "Task was destroyed but it is pending",
                "Future exception was never retrieved",
                # A CDP message addressed to a session that has gone away.
                # Routine over a remote browser: three of six captures of
                # this site had their target closed mid-scroll and succeeded
                # on the next attempt.
                "No session with given id",
                "Event loop is closed")):
            logger.debug("Ignoring teardown noise from pyppeteer: %s", message)
            return
        loop.default_exception_handler(context)

    def run(self, coro, timeout: Optional[float] = DEFAULT_OP_TIMEOUT):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError(
                f"pyppeteer call did not return within {timeout}s")

    def close(self):
        """Stop the loop, CANCELLING whatever it still has in flight.

        Stopping the loop outright leaves pyppeteer's own background tasks
        pending — its websocket reader and keepalive — and asyncio then prints
        "Task was destroyed but it is pending!" plus a traceback for each of
        them. That happens AFTER the output has been written, so the run is
        fine and the log looks like a crash. Four tracebacks under a
        successful run is how a reader learns to ignore the log.

        Cancelling first is the fix, and it has to happen ON the loop thread —
        `call_soon_threadsafe` is what gets it there.
        """
        def _cancel_and_stop():
            pending = [t for t in asyncio.all_tasks(self.loop)
                       if t is not asyncio.current_task(self.loop)]
            for task in pending:
                task.cancel()
            if pending:
                logger.debug("Cancelled %d pending pyppeteer task(s) on "
                             "teardown.", len(pending))
            self.loop.stop()

        self.loop.call_soon_threadsafe(_cancel_and_stop)
        self._thread.join(timeout=5)


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
    # The page number the SERVER answered with, read back out of the
    # Apollo cache key rather than echoed from the request.

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

    Not a hardcoded number: it drifts the moment a newer Chromium ships, and
    claiming an older Chrome than the JS engine and TLS handshake report is
    itself a mismatch a fingerprinter can key on. pyppeteer's
    `browser.version()` returns "HeadlessChrome/115.0.0.0"; the marketing
    part is what a real Chrome would send.
    """
    number = version.split("/")[-1] if "/" in version else version
    return (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{number} Safari/537.36")


class _Session:
    """One pyppeteer browser + page, relaunchable onto a different exit.

    Same contract as the Playwright engine's _BrowserSession, including the
    rule that a rotation means a genuinely FRESH browser: cookies a bot
    manager issued against one exit, replayed from another, are a stronger
    signal than either address alone, so the cookie jar goes with the exit.
    """

    def __init__(self, bridge: _AsyncBridge, args, pool):
        self.bridge, self.args, self.pool = bridge, args, pool
        self.remote = bool(args.cdp_endpoint)
        self.browser = self.page = None

    def open(self):
        if self.remote:
            logger.info("Connecting to an existing browser over CDP: %s",
                        _mask_credentials(self.args.cdp_endpoint))
            # pyppeteer's browserWSEndpoint takes the full ws://user:pass@host
            # form and authenticates on the WebSocket upgrade, so an
            # authenticated Scraping Browser endpoint works here — unlike
            # Selenium's debuggerAddress, which has nowhere to put a password.
            try:
                self.browser = self.bridge.run(
                    connect(browserWSEndpoint=self.args.cdp_endpoint,
                            ignoreHTTPSErrors=True), timeout=CONNECT_TIMEOUT)
            except Exception as e:  # noqa: BLE001 — see below
                # The websockets library raises InvalidStatusCode here, and
                # its message is just "server rejected WebSocket connection:
                # HTTP 500" — which names neither the endpoint nor the
                # reason, and matched none of the patterns __main__ uses to
                # map a remote failure onto exit 5. A live profile run
                # therefore died with a raw traceback and exit 1, telling a
                # harness to go looking for a bug in this code when the real
                # answer is "wait, or use a different pid".
                #
                # Re-raised with the endpoint MASKED and the meaning spelled
                # out. HTTP 500 from cb.2captcha.com is overwhelmingly
                # `profile_locked`: a Scraping Browser profile allows ONE live
                # connection, and the run before this one may still hold it.
                raise RuntimeError(
                    "could not connect to --cdp-endpoint %s: %s\n"
                    "A Scraping Browser profile allows ONE live connection at "
                    "a time, so an HTTP 500 here usually means another run "
                    "still holds this `pid`. Wait for it to finish, or use a "
                    "different pid."
                    % (_mask_credentials(self.args.cdp_endpoint),
                       _mask_credentials(str(e)))) from None
            self.page = self.bridge.run(self.browser.newPage())
            return self

        # --lang really applies the locale rather than accepting the flag
        # and ignoring it. It does NOT decide which listing is
        # searched; that is find_country in the URL.
        launch_args = ["--no-sandbox", "--disable-dev-shm-usage",
                       f"--lang={self.args.locale}"]
        launch_kwargs = {}
        if self.args.chromium_path:
            launch_kwargs["executablePath"] = self.args.chromium_path
            logger.info("Using the Chromium at %s instead of pyppeteer's own.",
                        self.args.chromium_path)
        credentials = None
        if self.pool:
            exit_url = self.pool.current
            # Credentials go through page.authenticate(), never onto the
            # command line: --proxy-server= becomes part of the browser's
            # argv, readable by anything that can run `ps`.
            scrubbed, credentials = split_credentials(exit_url)
            launch_args.append(f"--proxy-server={scrubbed}")
            logger.info("Using proxy exit %s", mask(exit_url))

        # handleSIGINT/TERM/HUP off, and not for tidiness: pyppeteer installs
        # signal handlers inside launch(), and `signal.signal` raises
        # "signal only works in main thread of the main interpreter" because
        # the event loop here lives on a worker thread. Teardown is handled by
        # _Session.close() in scrape()'s finally block instead, so nothing is
        # lost — the browser is still closed on both success and failure.
        self.browser = self.bridge.run(
            launch(headless=self.args.headless, args=launch_args,
                   ignoreHTTPSErrors=True, handleSIGINT=False,
                   handleSIGTERM=False, handleSIGHUP=False, **launch_kwargs),
            timeout=CONNECT_TIMEOUT * 2)
        self.page = self.bridge.run(self.browser.newPage())
        version = self.bridge.run(self.browser.version())
        self.bridge.run(self.page.setUserAgent(_chrome_ua(version)))
        self.bridge.run(self.page.setViewport({"width": 1600, "height": 1000}))
        if self.args.fingerprint:
            self._apply_fingerprint()
        if credentials:
            self.bridge.run(self.page.authenticate(
                {"username": credentials[0], "password": credentials[1]}))
        return self

    def _apply_fingerprint(self):
        """Apply a 2captcha fingerprint to this page.

        The SAME init script the Playwright and Selenium engines install,
        shared deliberately: two engines applying different halves of one
        fingerprint would be a contradiction of exactly the kind a
        fingerprint is meant to avoid.

        Never reached over --cdp-endpoint (the remote browser brings its own
        identity, and stacking a second is worse than none) — that branch
        returns before this is called.
        """
        from fingerprint_client import get_fingerprint, playwright_init_script
        fp = get_fingerprint(self.args.twocaptcha_key, tags=self.args.fp_tags,
                             country=self.args.fp_country)
        ua = (fp.get("userAgent") or {}).get("value")
        try:
            if ua:
                self.bridge.run(self.page.setUserAgent(ua))
            self.bridge.run(
                self.page.evaluateOnNewDocument(playwright_init_script(fp)))
            logger.info("Using 2captcha fingerprint %s (%s)", fp.get("id"),
                        fp.get("country"))
        except Exception as e:  # noqa: BLE001 — a fingerprint is not the run
            logger.warning("Could not apply the fingerprint (%s) — continuing "
                           "without it.", e)

    def relaunch(self):
        if self.remote:
            return
        try:
            self.bridge.run(self.browser.close(), timeout=30)
        except Exception as e:  # noqa: BLE001 — teardown must not mask the reason we're here
            logger.debug("Ignoring error while closing browser: %s", e)
        self.open()

    def close(self):
        """Close the page, and on a REMOTE browser disconnect from it too.

        The disconnect is not tidiness. Closing only the page leaves
        pyppeteer's websocket to the remote browser open, and when the
        bridge's event loop then shuts down, `websockets` unwinds its own
        connection outside a running loop — printing four "Exception ignored
        in: <coroutine …>" tracebacks AFTER the output has already been
        written. A successful run that ends in four tracebacks is how a
        reader learns to ignore the log, which is the same reasoning as
        _AsyncBridge.close()'s.

        The remote BROWSER is deliberately left running: it is not ours, and
        a Scraping Browser profile is reused across runs.
        """
        try:
            if self.remote:
                self.bridge.run(self.page.close(), timeout=30)
                self.bridge.run(self.browser.disconnect(), timeout=30)
            else:
                self.bridge.run(self.browser.close(), timeout=30)
        except Exception as e:  # noqa: BLE001
            logger.debug("Ignoring error during browser teardown: %s", e)


# ---------------------------------------------------------------------------
# page_flow, bound to pyppeteer
# ---------------------------------------------------------------------------
# Only "how to ask this driver" lives here; every decision about what to do
# with the answer is in page_flow.py so all three engines make it the same way.
def _driver(session):
    bridge, page = session.bridge, session.page

    def count(selector):
        return len(bridge.run(page.querySelectorAll(selector)))

    def sleep(ms):
        time.sleep(ms / 1000.0)

    def content():
        try:
            return bridge.run(page.content())
        except Exception as e:  # noqa: BLE001
            # A URL canonicalisation can navigate, so a snapshot can land
            # exactly on the document swap. None tells the caller to skip a
            # check rather than fail the run.
            logger.debug("content() unavailable (page navigating?): %s", e)
            return None

    def current_url():
        return page.url

    # Named OPERATIONS rather than JavaScript crossing the page_flow
    # boundary: pyppeteer takes `() => expr` while Selenium takes a function
    # body with an explicit `return`, so a shared module passing JS would
    # acquire one driver's dialect.
    #
    # No scroll primitive, and its absence is measured rather than forgotten:
    # every route this scraper reads is complete in the
    # first response. Mirrors the
    # other two engines.
    return {"count": count, "sleep": sleep, "content": content,
            "current_url": current_url}


def _content(session) -> Optional[str]:
    return _driver(session)["content"]()


def _is_endpoint(url: str) -> bool:
    """Whether this address answers with raw JSON rather than a document.

    True for everything under `/api/candidate-api/`, which is every route
    this scraper reads except `--route ssr`. Getting it wrong is SILENT in
    both directions — see `playwright_scraper._is_endpoint` for the two
    failure modes. All three engines branch on this same predicate.
    """
    return API_PATH in (url or "")


def _snapshot(session, url: str):
    """What the parser is given for this address. Mirrors the other engines.

    A PAGE is read with `content()`. The ENDPOINT answers with JSON, which
    Chromium wraps in its own JSON-viewer markup, so reading the body text is
    the only way to get back what the server actually sent.
    """
    if _is_endpoint(url):
        try:
            return session.bridge.run(session.page.evaluate(
                "() => document.body.innerText")) or ""
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not read the endpoint response: %s", e)
            return None
    return _driver(session)["content"]()


def _fetch_page_source(session, url: str, timeout_ms: int = 60000) -> str:
    """Navigate and return the serialised document. Used for enumeration.

    `content()` rather than a text reading, and that matters for the
    sitemap: Chromium renders `text/xml` through its own XML viewer, and
    `content()` keeps all 463 `<loc>` elements intact where the viewer's
    rendered text does not.
    """
    session.bridge.run(session.page.goto(
        url, {"waitUntil": "domcontentloaded", "timeout": timeout_ms}))
    return session.bridge.run(session.page.content()) or ""


def handle_captcha_if_present(session, args) -> bool:
    """Detect and solve a challenge. True if something was solved.

    Same two families, same order, same "detected is not blocking" rule as
    the Playwright engine — see its docstring for why the anchor count is
    checked here rather than after the readiness wait.
    """
    bridge, page = session.bridge, session.page
    html = _content(session)
    if html is None:
        return False

    selector = page_flow.ready_selector(args.mode)
    already_rendered = len(bridge.run(page.querySelectorAll(selector)))
    when_blocked = getattr(args, "solve_captcha", "when-blocked") == "when-blocked"

    html_challenge = detect_recaptcha_v3(html, page.url)
    runtime_challenge = detect_recaptcha_in_page(
        lambda js: bridge.run(page.evaluate(js)), page_url=page.url)
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
    bridge.run(page.evaluate(INJECT_TOKEN_JS, token))
    logger.info("Token injected. Reloading page to continue.")
    time.sleep(1.5)
    bridge.run(page.reload({"waitUntil": "domcontentloaded", "timeout": 60000}))
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

    The retry/rotate/wait policy is page_flow's and finish_run's; what differs
    here is only the driver calls. Kept structurally parallel on purpose —
    the two files are meant to be diffable, because "all three engines agree"
    is checked by reading them side by side as well as by the smoke suite.
    """
    outcome = PageOutcome(page_num=page_num, url=url)
    bridge, page = session.bridge, session.page
    d = _driver(session)

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
    html, state, load_failed = None, "ok", False

    for block_attempt in range(block_retries + 1):
        logger.info("Fetching page %d/%d: %s", page_num, args.pages, url)
        # `exit_failed` holds Chromium's own name for a proxy fault, or None.
        # Named and shaped exactly as in the other two engines: the give-up
        # branch below reports it, and three engines that structure this
        # differently drift (CLAUDE.md §6).
        load_failed, exit_failed = False, None
        for attempt in range(1, args.retries + 1):
            try:
                bridge.run(page.goto(url, {"waitUntil": "domcontentloaded",
                                           "timeout": 60000}))
                load_failed = False
                break
            except Exception as e:  # noqa: BLE001 — pyppeteer raises many types
                load_failed = True
                # pyppeteer surfaces a dead proxy as a page error whose text
                # carries Chromium's own name for it, exactly as Playwright
                # does; a timeout and an unusable exit want opposite
                # responses, so they are told apart by that text.
                text = str(e)
                marker = next((m for m in _PROXY_ERROR_MARKERS if m in text), None)
                if marker:
                    exit_failed = marker
                    logger.warning("Exit %s is unusable (%s).",
                                   mask(pool.current) if pool else "(none)", text[:120])
                    break
                if attempt < args.retries:
                    pause = args.retry_delay * (2 ** (attempt - 1))
                    logger.warning("Failed to load %s (attempt %d/%d: %s) — "
                                   "retrying in %.1fs.", url, attempt,
                                   args.retries, text[:120], pause)
                    time.sleep(pause)

        if load_failed and block_attempt < block_retries:
            pool.advance("unusable exit or repeated load failure")
            session.relaunch()
            bridge, page = session.bridge, session.page
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
        state = page_flow.classify(html, None, page.url, args.mode, args.route)

        # Every route here is complete in the FIRST response, so there is
        # nothing to wait for on a healthy one. The wait below is only for
        # the state that says the site served SOMETHING that is not a
        # payload. Mirrors the other two engines.
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
            state = page_flow.classify(html, None, page.url, args.mode, args.route)

        # The paid path is reached only for state "challenge" — Cloudflare's
        # Managed Challenge, which IS a test. It is NOT reached for
        # "blocked": that page carries no widget, so a solve there would be a
        # charge for nothing. Mirrors the other two engines.
        #
        # The paid path is reached only for state "challenge", which no
        # capture of this site has ever produced. Wired up because a bot
        # manager can be switched on between deploys, and bounded by
        # SOLVES_PER_PAGE so a speculative path cannot become a bill.
        if (page_flow.should_solve(state)
                and _solve_budget(args, solves_bought)):
            solves_bought += 1
            if handle_captcha_if_present(session, args):
                time.sleep(1)
                html = _content(session) or html
                state = page_flow.classify(html, None, page.url, args.mode, args.route)
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
                bridge, page = session.bridge, session.page
                d = _driver(session)
            else:
                # No pool, so nowhere else to go — but a plain re-fetch is
                # what clears this on a Scraping Browser profile. The
                # browser is NOT relaunched: over `--cdp-endpoint` a profile
                # allows one live connection, so tearing the session down
                # and reconnecting risks `profile_locked` and would lose the
                # very cookies the retry is meant to build on.
                #
                # This `else` was MISSING in the repo this engine was ported
                # from — both parity engines called `mask(pool.current)`
                # unguarded while the Playwright engine guarded it, so any
                # retryable state on a run with no proxy died with
                # `AttributeError: 'NoneType' object has no attribute
                # 'current'` instead of retrying. It survived because the
                # donor's site never produced a retryable state without a
                # pool; this one does, on its own `--mode facets` path, and
                # a live run found it in a minute (CLAUDE.md §15, §16).
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
        # playwright_scraper's twin of this block. This is the HARD refusal.
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
        outcome.final_url = page.url
        return outcome

    # No readiness wait and no scroll on the content path, and their absence
    # is MEASURED rather than forgotten — see the "unknown" branch above.

    if args.dump_html:
        dump_path = (args.dump_html if args.pages == 1
                     else f"{args.dump_html}.page{page_num}")
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Saved the snapshot the parser sees to %s (%d bytes).",
                    dump_path, len(html))

    # Only for a state page_flow already counts as BLOCKED. An EMPTY
    # page is a correct answer, and a live run of a /p/<slug> hub
    # reported exit 3 on a page the site had plainly served because the
    # hub's own performance script names `akamaihd.net`. Mirrors
    # playwright_scraper exactly.
    vendor = (detect_bot_challenge(html)
              if page_flow.counts_as_blocked(state) else None)
    if vendor:
        debug_html = f"{args.out}_page{page_num}_debug.html"
        with open(debug_html, "w", encoding="utf-8") as f:
            f.write(html)
        try:
            bridge.run(page.screenshot({"path": f"{args.out}_page{page_num}_debug.png",
                                        "fullPage": True}))
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not capture screenshot: %s", e)
        logger.error("Blocked by %s before parsing (%d bytes) — saved to %s. "
                     "This is exit 3, distinct from a genuinely empty result "
                     "(exit 4).", vendor, len(html), debug_html)
        outcome.blocked_by = vendor
        return outcome

    if not page_flow.should_parse(state):
        logger.info("Page %d came back as %s; nothing to parse.", page_num,
                    state)
        outcome.final_url = page.url
        return outcome

    products, listing = parse_for_mode(html, page.url, args, page_num)
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
            bridge.run(page.screenshot({"path": f"{args.out}_page{page_num}_debug.png",
                                        "fullPage": True}))
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not capture screenshot: %s", e)
        logger.warning("0 rows parsed — saved what the browser actually saw to "
                       "%s.", debug_html)

    outcome.products = products
    outcome.final_url = page.url
    return outcome


# Chromium's own names for "the proxy is the problem, not the site".
_PROXY_ERROR_MARKERS = (
    "ERR_PROXY_CONNECTION_FAILED", "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_AUTH_UNSUPPORTED", "ERR_PROXY_AUTH_REQUESTED",
    "ERR_UNEXPECTED_PROXY_AUTH", "ERR_PROXY_CERTIFICATE_INVALID",
)


def scrape(args) -> int:
    outcomes: List[PageOutcome] = []
    seen_keys = set()
    blocked = False
    # All three modes are one row per business-at-a-location.
    dedupe_key = "sku"
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

    bridge = _AsyncBridge()
    session = None
    try:
        session = _Session(bridge, args, pool).open()

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
        bridge.close()

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
                    "full, or the board's own facet counts (pyppeteer "
                    "edition). pyppeteer is effectively unmaintained and "
                    "its own README points at Playwright; "
                    "playwright_scraper.py is the primary engine.")
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
    p.add_argument("--proxy", default=None,
                   help="Proxy URL, e.g. http://ACCOUNT:PASSWORD@HOST:9999. "
                        "Credentials are sent over CDP (page.authenticate), "
                        "never on the browser's command line.")
    p.add_argument("--proxy-file", default=None,
                   help="File with one proxy URL per line to rotate across. "
                        "Wins over --proxy.")
    p.add_argument("--proxy-rotate", choices=list(ROTATE_MODES), default="per-run")
    p.add_argument("--proxy-shuffle", action="store_true")
    p.add_argument("--proxy-block-retries", type=int, default=2)
    p.add_argument("--twocaptcha-key", default=None, help="2captcha.com API key")
    p.add_argument("--allow-empty", action="store_true",
                   help="Write output files even when 0 rows were found.")
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
                   help="Connect to a running browser over CDP, e.g. "
                        "ws://user:pass@host:port. pyppeteer authenticates on "
                        "the WebSocket upgrade, so a credentialed Scraping "
                        "Browser endpoint works here.")
    p.add_argument("--dump-html", default=None, metavar="PATH",
                   help="Save the exact HTML the parser is given, on success "
                        "as well as failure.")
    p.add_argument("--locale", default="en-US",
                   help="Browser locale (default en-US), passed to Chromium "
                        "as --lang. It does NOT decide which directory is "
                        "searched; that is find_country in the URL.")
    p.add_argument("--fingerprint", action="store_true",
                   help="Fetch a browser fingerprint from 2captcha's "
                        "Fingerprint API and apply it to the launched "
                        "browser. Needs --twocaptcha-key. Ignored with "
                        "--cdp-endpoint, where the Scraping Browser supplies "
                        "its own.")
    p.add_argument("--fp-tags", default="Windows",
                   help="ONE OS-family tag for the fingerprint filter: "
                        "Windows, Microsoft Windows or Android. NOT a list — "
                        "Chrome, Desktop and Mobile are each rejected by the "
                        "API with 400. (default: Windows)")
    p.add_argument("--fp-country", default=None,
                   help="Fingerprint country, ISO 3166-1 alpha-2. Match it to "
                        "your proxy's exit country — a US fingerprint on a "
                        "German IP is a contradiction.")
    p.add_argument("--chromium-path", default=None, metavar="PATH",
                   help="Browser executable to drive, instead of the Chromium "
                        "pyppeteer downloads for itself. Needed where that "
                        "build will not start: on an Apple Silicon Mac "
                        "pyppeteer fetches an x86_64 Chromium 117, which runs "
                        "under Rosetta far enough to print --version and then "
                        "fails to open its DevTools socket (measured "
                        "2026-09-08; the same failure occurs with no wrapper "
                        "code at all, so it is the build, not this engine). "
                        "Point it at a Chrome or Chromium of your own — "
                        "Playwright's, if you have it installed.")
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
        if ("profile_locked" in text or "connect to --cdp-endpoint" in text
                or "rejected WebSocket connection" in text):
            logger.error("%s", text)
            sys.exit(EXIT_API_ERROR)
        raise
