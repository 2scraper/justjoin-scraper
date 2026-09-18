# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/) as
closely as a CLI toolkit can. A PATCH release means *fixes* — it does not
promise that every flag is frozen, so a behaviour-changing default can
appear in one. Where it does, the entry leads with that fact rather than
burying it.

## [0.1.3] — 2026-09-18

An audit against the family's own checklist, run after publishing rather
than before it. Everything here was found by a check or a live run, not by
reading.

### Fixed

- **`scraper_api_client.py` reported an upstream failure as an empty
  board.** It did not consult `page_flow.should_parse()` the way the three
  browser engines do, so a body the site never served came back as
  "Parsed 0 job(s)" and exit 4 — "the query matched nothing". Measured: the
  Scraper API rendering justjoin.it's 1.8 MB listing page returned upstream
  HTTP 500 with **zero bytes**, and that path called it an empty result. It
  now names the state, saves the body and exits 5. CLAUDE.md §8: blocked,
  empty and failed are three different answers.

- **A latent `NameError` in the same client** — `EXIT_NO_PRODUCTS` was used
  on the new `not_found` branch and never imported. Caught by the suite's
  undefined-name walk, and the control was verified: planting the fault
  makes the check name that exact symbol.

- **`--url` was checked against `HOST` while `HOSTS` sat unread.** The
  tuple is now the single source of truth rather than decoration.

### Corrected

- **The README had the Scraper API backwards.** It said the service suits
  the rendered listing page better than the endpoint, which was reasoned
  rather than measured. Measured 2026-09-18:

  | `--url` | result |
  |---|---|
  | `/api/candidate-api/offers?…` (47 KB) | HTTP 200 in 12s, 20 rows, $0.0005 |
  | `/job-offers/all-locations` (1.8 MB) | HTTP 408 at the default 60s |
  | the same page at `--timeout 120` | API 200, upstream HTTP 500, 0 bytes |

  The opposite of what was written: point it at the endpoint.

- **An inherited figure replaced with a measured one.** A comment repeated
  CLAUDE.md's "17 of 18 repos" for the fingerprint flags. Re-counted across
  the 26 sibling repos: 25 of 26, with the command beside it (§13).

### Removed

- `SITE_TOTAL_HINT` and `EXPIRED_SITEMAP_INDEX`: two constants nothing
  read. A sweep for public names with no consumer now finds exactly two,
  both in the shared captcha client and both dead in every sibling repo
  checked — those are pinned by a check rather than removed, so the list
  cannot grow silently (§17).

### Verified live, against a real 2Captcha key

- **`--fingerprint` applies what it fetches.** CLAUDE.md §16 lists four
  defects that shipped on this path in four repos at once; none is present
  here. A PL fingerprint reached the browser intact: user agent
  `Windows NT 10.0 … Chrome/151`, locale `pl-PL` (not `en-PL`), timezone
  `Europe/Warsaw`, and `--fp-tags Windows` is accepted by the API.
- **Credentials never reach a log**, including an exception message. A dead
  proxy with `secretuser:secretpass123` in the URL logged
  `http://***:***@127.0.0.1:9` — host and port kept, both credentials
  masked, zero occurrences of either in the whole run — and was named a
  PROXY failure rather than a timeout. Identical on all three engines.

[0.1.3]: https://github.com/2scraper/justjoin-scraper/releases/tag/v0.1.3

## [0.1.2] — 2026-09-18

### Fixed

- **`--mode offer` walked the sitemap from its most stale end**, so a small
  run could come back with nothing at all while the site was serving
  perfectly. The canary did exactly that.

  justjoin.it serves `active-jobs/part0.xml` OLDEST FIRST — its first
  `lastmod` was 2026-09-14 and its last 2026-09-18 — and generates it ahead
  of any fetch, so its head is where offers that have since been taken down
  collect. Measured by asking the endpoint for a sample of each end:

  | sample | alive | gone |
  |---|---|---|
  | first 12 entries | 10 | 2 |
  | last 12 entries | 12 | 0 |
  | 12 at random | 12 | 0 |

  The enumeration is now sorted by `lastmod` descending, which is also what
  `--pages N` should have meant all along: the N most recently updated
  offers, not the N most stale. Entries with no `lastmod` are kept and sort
  last rather than being dropped.

- The offer canary asks for five offers rather than three, so one delisted
  offer is an ordinary event rather than a red check — while zero rows out
  of five still means something real broke.

[0.1.2]: https://github.com/2scraper/justjoin-scraper/releases/tag/v0.1.2

## [0.1.1] — 2026-09-18

### Fixed

- **A delisted offer was read as a parse error instead of a 404**, so
  `--mode offer` retried an address that will never exist again, wrote a
  debug dump for it, and spent a fetch doing so.

  All three engines were discarding what the navigation returned, so the
  404 reached the classifier as `status=None`. This is ordinary in that
  mode — justjoin.it's sitemap is generated ahead of the fetch, and the
  first slug in `active-jobs/part0.xml` had been taken down — and the
  canary found it on its first run.

  Playwright and pyppeteer now keep `response.status`. Selenium cannot:
  `driver.get()` returns None and WebDriver exposes no HTTP status at all,
  so `product_parser.problem_status` reads the status out of the site's own
  RFC 7231 problem document, which justjoin.it states in the body of every
  404. That reader is what makes the three engines agree rather than two of
  them being better informed than the third.

  Verified live against the real delisted offer: all three now report
  `not_found`, exit 4, no retry and no dump.

[0.1.1]: https://github.com/2scraper/justjoin-scraper/releases/tag/v0.1.1

## [0.1.0] — 2026-09-18

First release. Reads [justjoin.it](https://justjoin.it), the Polish IT job
board, through Playwright, Selenium, Puppeteer or the 2Captcha Scraping
Browser API over CDP.

### Added

- **`--mode listings`** (default): the board through justjoin.it's own
  offers endpoint, with offset pagination and nine filters measured to
  change what the site sends — `--category`, `--city` (+`--city-radius`),
  `--remote`, `--keyword`, `--experience`, `--employment`,
  `--working-time`, `--language`, `--with-salary`.
- **`--mode offer`**: one offer in full — the description body, per-skill
  levels, the company website and size, the country — enumerated from
  justjoin.it's own sitemap (10,646 offer URLs on 2026-09-18), from
  `--url`, or from `--slugs-file` pointed at a previous run's output.
- **`--mode facets`**: the board's own taxonomy with its own counts, in a
  second row schema (`Facet`). These counts are not subject to the
  per-query cap, which makes them the tool for planning runs that reach
  past it.
- **`--route ssr`**: reads the rendered listing page instead of the
  endpoint. One page of 100 rows with a poorer schema, and the only route
  justjoin.it's `robots.txt` permits — see the README.
- **`--sort` / `--order` / `--per-page`**: the query's shape. Recorded in
  the sidecar, because on a capped result set the ordering decides *which*
  offers are in the file.
- Three engines with byte-identical output, a fourth path through the
  Scraper API, proxy pools with per-run and per-page rotation, fingerprint
  support, and `--concurrency` for the routes whose pages are
  independently addressable.
- 573 offline checks, a daily canary that runs a real 3-page scrape with no
  credentials, and `diff_runs.py` keyed on the offer's UUID.

### Measured on 2026-09-18, from a netcup VPS in Nuremberg (AS197540)

- **Nothing on the read path is gated.** HTTP 200 to `curl` with no headers
  at all, to `python-requests`, to `wget` and to a headless Chromium.
  Sixteen candidate bot-challenge markers counted across seven captures,
  all zero. One exception: a `Python-urllib/*` User-Agent gets HTTP 403 —
  a denylist entry, not a gate.
- **The captcha is reCAPTCHA v2 on the application form**, and on no route
  this scraper reads. The site publishes its own key
  (`googleRecaptchaV2Key`); in a live browser `window.grecaptcha` was
  undefined on a rendered listing, an offer page and the endpoint alike —
  the script is never loaded on the read path.
- **`api.justjoin.it` — the host the page config names as `baseApiUrl` —
  answers HTTP 503 to everything.** The route that works is the
  same-origin proxy at `justjoin.it/api/candidate-api/`.
- **Every query is capped at 10,000 results** while the board held 19,381,
  and `from=10000` answers HTTP 500 rather than an empty page.
- **The salary is not `employmentTypes[0]`.** The employer's own figure
  carries `currencySource: "original"` and sits elsewhere on 321 of 1,000
  listing rows and 29 of 40 detail records.
- **`from` is the rate normalised to a month while `unit` names the
  employer's unit** — reading them together is out by a factor of 168 on
  307 of 582 quoted entries.
- **`?page=2` on a rendered listing returns page 1 again** under HTTP 200.
- **The site's default ordering is not a paid placement**: `isSuperOffer`
  was spread through the result (9–34 per hundred at five offsets) rather
  than banked at the top, so this scraper keeps the site's own default
  sort.

### Fixed in the family core

Both were inherited from the repo this one was ported from, and both are
the shape CLAUDE.md §16 describes — copied code with no evidence behind it.

- **The two parity engines crashed instead of retrying when no proxy pool
  was configured.** `mask(pool.current)` was called unguarded in the
  block-retry path of `puppeteer_scraper.py` and `selenium_scraper.py`
  while `playwright_scraper.py` guarded it, so any retryable state on a run
  without `--proxy` died with `AttributeError: 'NoneType' object has no
  attribute 'current'`. Found by a live run in under a minute.
- **`scraper_api_client.py` could not import.** It called a parser entry
  point that had been renamed, so `--help` itself was an `ImportError`.
  Caught by the suite's signature-binding check.

[0.1.0]: https://github.com/2scraper/justjoin-scraper/releases/tag/v0.1.0
