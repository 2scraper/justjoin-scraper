# Contributing

Bug reports, site-change reports and pull requests are all welcome. This file
covers the few things specific to a scraper, which are not the usual ones.

## Before you open anything

Run the offline suite. It needs no network, no browser and no API key, and takes
about a second:

```bash
pip install -r requirements.txt
python3 smoke_test.py
```

It prints its own check count, and lists any group it had to skip because an
engine library is absent.

**The suite must pass with no engine installed at all.** CI installs only
`beautifulsoup4` and `requests`, so any import of `playwright_scraper`,
`puppeteer_scraper` or `selenium_scraper` in a test has to sit inside
`try/except ImportError` with the skip recorded. This is easy to get wrong
locally, where you almost certainly have an engine installed and an unguarded
import passes.

If the suite fails on a clean clone, that is itself the bug — say so.

## Never commit a credential

`.env` is in `.gitignore`. Keep it there.

The scrapers mask `user:pass@` in their own log lines, but three things are **not**
masked: raw HTML dumps, the Scraper API's `x-debug` response header, and your
shell history. Before pasting any output into an issue or a PR, replace keys,
proxy passwords and full `ws://user:pass@host:9222` endpoints with `***`.

CI fails the build if something that looks like a credential is committed. That
check is a backstop, not a review — a leaked key has to be rotated whether or
not the check caught it.

## Reporting a site change

justjoin.it changing its endpoint or payload is the normal way this stops
working, and it has its own issue template. The detail that saves the most
time is WHICH source broke — and on this site that is never a CSS selector,
because the parser reads JSON, not the DOM.

There are **two** structured sources here, and a break is usually in one of
them and not the other:

1. **The same-origin endpoint `justjoin.it/api/candidate-api/`** — offers,
   one offer by slug, and the facet counts. This is what `--route api` (the
   default) reads. Note that `api.justjoin.it`, the `baseApiUrl` the page
   config names, answered HTTP 503 to every request tried; the scraper
   refuses it with that reason.

2. **The rendered listing page's payload** (`--route ssr`), which spells the
   same offer differently: bare skill strings with no levels,
   `multilocation` instead of `locations`, an integer `categoryId`, and no
   languages. `data_source` records which route a row came from.

`CORE_FIELDS` in the engines is the guard for a rename that goes QUIET —
rows that stop carrying `title`, `url`, `sku` or `company_name` rather than
rows that stop appearing.

## Before this repository goes public

One item cannot be undone later, so it belongs on a checklist rather than in
someone's head. **A commit on top cannot reach what a published tag and a
merged PR's refs already hold** — those stay attached to the PR and cannot be
deleted from it. Afterwards, only a fresh repository removes anything.

```bash
python3 .github/ci_checks.py --history-check
```

That applies the same credential rules CI enforces to **every blob that has
ever existed**, not just the working tree. It is deliberately not part of
`--all` and not run by CI: it shells out to git once per object, and a dirty
history needs a decision, not a red check on every push.

Then the rest of the presentation, in the order that matters:

1. `python3 smoke_test.py` green, and the canary dispatched at least once.
   Unlike most siblings in this family the canary here is **not** gated on a
   secret: every route this scraper reads is served to a bare GitHub runner,
   so it runs a real 3-page scrape daily and is expected to be GREEN. That
   is not a convenience — it is what keeps the README's central claim
   honest. If justjoin.it ever puts these routes behind a challenge or a key, the
   badge goes red the next morning and the claim is retested without anyone
   having to remember to.
2. The repo description, homepage and topics set (see the family notes on
   what those should say).
3. Only then the row in the org profile README — and check it with an
   ANONYMOUS request rather than your own logged-in browser. A row pointing
   at a private repo is a 404 for every visitor, which costs more trust than
   the missing row.

## Pull requests

**Add a test for the behaviour you are changing.** `smoke_test.py` is a single
file of plain functions with inline HTML/JSON fixtures — no pytest, no
conftest, no fixtures directory. Copy the nearest existing check and edit it.

Several properties in this repo exist because they were measured against
expectation. Tests pin them, so a PR that breaks one will fail rather than
silently regress. The README's "Traps that look like bugs" section has the
measurements; the short version:

- **A marker that matches a good page is worse than no marker.** Every
  candidate in `BOT_CHALLENGE_MARKERS` was counted across seven captures and
  is zero on all of them; the set is a tripwire for a change, not a
  description of something seen. `akamai` is NOT carried, because a served
  offer page names Akamai Linode four times in its job description.
  `cf-turnstile` is NOT carried either: 2Captcha's Scraping Browser injects
  it into every page it loads. Before adding any marker: count it on a page
  you know is good.

- **The salary is selected by `currencySource: "original"`, never by
  position.** It is not first on 321 of 1,000 listing records and 29 of 40
  detail records, and indexing `[0]` reads a converted figure in the wrong
  currency.

- **`from`/`to` are monthly; `fromPerUnit`/`toPerUnit` are in `unit`.**
  Reading adjacent keys together says "18,480 PLN per hour" for a job paying
  110 an hour. `salary_min`/`salary_max` carry the quoted rate and the
  monthly figure lives in `salary_monthly_*`.

- **Every query is capped at 10,000 results**, and `from=10000` answers HTTP
  500 rather than an empty page. The planner stops at the ceiling, and the
  sidecar records the cap arithmetic so "complete" is never mistaken for
  "exhaustive".

- **`?page=2` on a rendered listing page silently returns page 1.** `--route
  ssr` therefore plans exactly one page.

- **An undisclosed salary is null, never zero**, and a null `apply_url` on a
  `form` offer means "apply on justjoin.it", not a missing field.

- **Never write that a captcha cannot be solved.** Write that this repo does
  not implement X. justjoin.it configures reCAPTCHA v2 on its application
  form only; 2Captcha solves that with `RecaptchaV2TaskProxyless`.

Plus the family's own invariants, which are not negotiable:

- **A run that finds nothing writes nothing.** It must not replace a good
  output file with `[]`. `--allow-empty` is the opt-out.
- **Exit codes are a contract**, not decoration: `0` ok, `1` crash, `2` bad
  usage, `3` blocked, `4` zero rows — including a query that genuinely
  matched nothing, which is a correct answer — `5` remote API error, `6`
  partial. A pipeline branches on these.
- **An EMPTY page is never retried and never counted as blocked.** A query
  that matched nothing was served exactly as asked.
- **Credentials never reach argv or a log, and an exception message is a
  log.** The masker is global rather than first-occurrence: a Playwright
  connection error repeats the endpoint five times.
- **Merge in page order, not arrival order**, so concurrency cannot change
  the output.

### If your change needs a live run

Most do not — the suite covers the parser, the writers, the captcha
classifier and the CLI contract against inline fixtures. If yours genuinely
needs justjoin.it, say in the PR what you ran, which mode, route and filters,
from which exit, and what you got — including the sidecar's cap figures
(`total_results`, `site_total`, `capped_by_site`) and the coverage lines the
run prints.

Things about running this live that are specific to justjoin.it:

* **You need nothing.** No key, no proxy, no account. Every route this
  scraper reads was served to a bare datacentre address on 2026-09-18. If a
  run is refused, that is NEW, and the saved debug HTML is the finding — say
  so in the issue rather than reaching for a proxy. The one exception is a
  `Python-urllib/*` User-Agent, which gets HTTP 403.
* **Selenium cannot send proxy credentials.** `--proxy-server` takes an
  address only, so that engine strips them and warns.
* **The sort decides which offers you get**, because every query is capped.
  Two runs under different `--sort` values are different samples, and
  `diff_runs.py` refuses to compare them.
* **Be polite about `--mode offer`.** Use `--delay`, and do not run a full
  enumeration to test a one-line change.

**Run more than the primary engine.** "Mirror them exactly" is a design rule,
not a verification, and running the mirrors for the first release found two
defects that import, `--help`, `compileall` and the whole offline suite all
missed. All three were run live and returned byte-identical rows; that is
the bar.

Do not add anything that submits a form. justjoin.it's offer pages carry an
application form, and this project must never touch it — an application
submitted by a scraper is a false record about a real person and a real
company.

## Scope

This repo reads **public job offers** on justjoin.it: the offers endpoint,
single offers, the facet counts and the rendered listing page, exactly as an
anonymous visitor is served them. Note that the endpoint sits under
`/api/`, which `robots.txt` disallows; the README says so, and `--route ssr`
stays entirely within robots.txt.

Out of scope: anything behind a login, anything that submits a form
(including the job application), and anything that defeats a protection
rather than passing it the way an ordinary browser does.

## Licence

MIT. By opening a pull request you agree your contribution ships under it.
