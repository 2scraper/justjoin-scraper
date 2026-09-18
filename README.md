# justjoin-scraper

[![release](https://img.shields.io/github/v/release/2scraper/justjoin-scraper?sort=semver)](https://github.com/2scraper/justjoin-scraper/releases)
[![tests](https://github.com/2scraper/justjoin-scraper/actions/workflows/tests.yml/badge.svg)](https://github.com/2scraper/justjoin-scraper/actions/workflows/tests.yml)
[![canary](https://github.com/2scraper/justjoin-scraper/actions/workflows/canary.yml/badge.svg)](https://github.com/2scraper/justjoin-scraper/actions/workflows/canary.yml)
[![python](https://img.shields.io/badge/python-3.9%20%7C%203.13-blue)](pyproject.toml)
[![licence](https://img.shields.io/badge/licence-MIT-green)](LICENSE)
[![engines](https://img.shields.io/badge/engines-Playwright%20%7C%20Selenium%20%7C%20Puppeteer-informational)](#engines)
[![runs without an account](https://img.shields.io/badge/runs%20without%20an%20account-yes-brightgreen)](#do-you-need-anything-paid-no)

A scraper for **[justjoin.it](https://justjoin.it)**, the Polish IT job
board: salaries, seniority, required skills and their levels, languages,
workplace type, locations and the full job description — as JSON or CSV,
through Playwright, Selenium, Puppeteer or the 2Captcha Scraping Browser
API over CDP.

```bash
pip install -r requirements.txt -r requirements-playwright.txt
python3 -m playwright install chromium
python3 playwright_scraper.py --category python --pages 5 --format csv
```

That command needs no account, no key and no proxy. Why, and what the paid
products actually buy here, is the next section.

---

## Do you need anything paid? No.

Every route this scraper reads was served in full to a bare datacentre
address with no credentials at all. Measured **2026-09-18** from a netcup
VPS in Nuremberg (AS197540):

| Route | Result |
|---|---|
| `/api/candidate-api/offers` | HTTP 200, 100 offers, 253 KB |
| `/api/candidate-api/offers/{slug}` | HTTP 200, the full record |
| `/api/candidate-api/offers/facets/count` | HTTP 200 |
| `/job-offers/all-locations` | HTTP 200, 1.8 MB |
| `/sitemaps/active-jobs/part0.xml` | HTTP 200, 10,646 offer URLs |

…to `curl` **with no headers at all**, to `python-requests`, to `wget` and
to a headless Chromium. Sixteen candidate bot-challenge markers were
counted across seven captures and every one was zero.

**One exception, and it is a denylist entry rather than a gate:** a
`Python-urllib/*` User-Agent gets HTTP 403 on both hosts. That is what the
standard library sends if you do not set one. Everything else measured was
served.

So what do the paid products buy on this site?

* **A proxy** buys VOLUME and politeness. Covering the whole board means
  many requests — see [the cap](#the-10000-cap-and-how-to-get-past-it) —
  and `--concurrency N` without a pool sends N times the traffic from one
  address.
* **The Scraping Browser API** buys a specific exit country and no local
  Chromium to install. It does not buy access: the endpoint is not
  geo-routed and answered identically from every exit tried.
* **A 2Captcha key** buys nothing today. See
  [captchas](#what-captcha-does-this-site-have).

---

## What this scraper reads

### Three modes

| Mode | Route | What you get |
|---|---|---|
| `--mode listings` *(default)* | `/api/candidate-api/offers` | The board, paginated and filtered. Salary, skills, seniority, locations, languages. |
| `--mode offer` | `/api/candidate-api/offers/{slug}` | One offer in full: the description body, per-skill levels, the company website and size, the country. |
| `--mode facets` | `.../offers/facets/count` | The board's own taxonomy with its own counts — and these are **not** capped. |

```bash
# the newest Python jobs, three pages of 100
python3 playwright_scraper.py --category python --pages 3

# remote senior roles in Warsaw that state a salary
python3 playwright_scraper.py --city Warszawa --experience senior \
    --with-salary --pages 5 --format csv --out warsaw_senior

# what is on the board at all
python3 playwright_scraper.py --mode facets

# one offer, in full
python3 playwright_scraper.py --mode offer \
    --url "https://justjoin.it/job-offer/goodylabs-senior-devops-engineer-k-m-x--lodz-devops"

# enrich a listings run: fetch full records for the offers you gathered
python3 playwright_scraper.py --mode offer --slugs-file justjoin_jobs.json --pages 50
```

### The endpoint, not the host the config names

justjoin.it's own page config points at `api.justjoin.it` as its
`baseApiUrl`. **Do not use it.** It answered **HTTP 503 from nginx** to
every request tried from outside — bare, with browser headers, and with the
`x-api-version` header its own bundle sends, on every path tried.

The route that works is the same-origin proxy the front end actually calls:
`justjoin.it/api/candidate-api/`. Gating is per-ROUTE, not per-site. The
scraper refuses an `api.justjoin.it` URL *with that reason* rather than
telling you it is not a justjoin.it URL.

### `--route ssr`, and a word about robots.txt

justjoin.it's `robots.txt` allows `/job-offers/` and `/job-offer/`,
advertises eight sitemaps of its own, and carries `Disallow: /api/` — which
is where the offers endpoint lives.

This scraper defaults to the endpoint, because it is what the site's own
front end calls from the browser and it is the only route with pagination.
That is stated here rather than left to be found. `--route ssr` reads the
rendered listing page instead: **one page of 100 rows** with a poorer
schema (no category, no languages, no skill levels), entirely within
robots.txt.

---

## The 10,000 cap, and how to get past it

**Every query is capped at 10,000 results however many it matched.**
Measured 2026-09-18: `/offers/count` said **19,381** offers on the board
while every query's `meta.totalItems` was at most 10,000.

So a run that fetches everything reachable is genuinely `complete` — it
fetched everything the site will serve for that query — and is also about a
**52% sample**. The sidecar records both figures (`total_results`,
`site_total`, `reachable_max`, `capped_by_site`, `share_of_board_pct`) so a
consumer never has to infer one from the other. "Complete" and "exhaustive"
are different words.

Walking off the end is not free either: `from=10000` answers **HTTP 500**,
not an empty page. The planner stops at the ceiling rather than
manufacturing an error that reads like a bug in this scraper.

**`--mode facets` is how you cover the board.** Its counts are *not*
capped — they describe all 19,381 offers — so they tell you how to
partition a run. On 2026-09-18 the largest category held 1,991 offers, so
25 category runs cover a board that one unfiltered run cannot:

```bash
python3 playwright_scraper.py --mode facets --out board
python3 -c "
import json
for r in json.load(open('board.json')):
    if r['facet_group']=='categories': print(r['facet_key'], r['offer_count'])
"
# then one run per category
for c in data java analytics pm devops erp architecture testing ai security \
         other python net admin javascript support mobile c ux php go game \
         scala ruby html; do
  python3 playwright_scraper.py --category "$c" --pages 20 --per-page 100 \
      --out "board_$c"
done
```

---

## Traps that look like bugs

Each of these is real, measured, and will make you think the scraper is
broken if you meet it unwarned.

### The salary is not the first number in the list

`employmentTypes` mixes the employer's own figure with currency
conversions the site computed. The real one carries
`currencySource: "original"` — and it is **not first**. Measured over 1,000
listing records and 40 detail records:

| Route | `original` at `[0]` | elsewhere | of those, disclosed |
|---|---|---|---|
| listing | 679 | 321 | 12 |
| detail | 11 | 29 | **16** |

Indexing `[0]` gives a real-looking number in the wrong currency — and on
the detail route it gives a wrong number too, on 40% of records: **39.15
CHF** printed for a job offering **180 PLN**. This scraper selects on
`currencySource` and never on position.

### `from` is a monthly figure; `unit` is the employer's unit

The endpoint publishes both. `from`/`to` are the pay **normalised to a
month**; `fromPerUnit`/`toPerUnit` are the rate in the unit the employer
quoted, which is what `unit` names. Reading the two adjacent keys together
— the obvious thing to do — says **"18,480 PLN per hour"** for a job paying
110 an hour, on 307 of 582 quoted entries.

So `salary_min`/`salary_max` carry the **quoted** rate beside
`salary_unit`, and the monthly figure lives in columns that say
`salary_monthly_*`. The rendered page (`--route ssr`) publishes no
normalisation at all, so those columns are null there unless the employer
quoted a month.

### `?page=2` on a listing page silently returns page 1

Not an error, not an empty page: HTTP 200, `meta.from` still 0, a
byte-identical first offer. A scraper that built `?page=N` would fetch page
1 N times, find no new id on the second, and report a **complete**
multi-page run holding 100 rows of 10,000. `--route ssr` therefore plans
exactly one page and says why.

### A salary of 140 PLN a month is the employer's typo, not ours

justjoin.it publishes what employers type. One offer states **19,000 PLN
per hour** (the site's own normalisation turns that into 3,192,000 a
month); another states 140 PLN a month for a full-stack role. Both are
reproduced exactly as the site serves them. The scraper reports the site,
not what it guesses the employer meant.

### Half the offers disclose no salary at all

461 of 1,000. The site's own UI prints "Undisclosed Salary" for these, and
the scraper writes **null** — never zero, which would drag every average a
consumer computes. `--with-salary` filters them out at the site (4,641
offers on 2026-09-18).

### A null `apply_url` is the site saying something

`apply_method` is `external` on 800 of 1,000 offers and `form` on 200, and
`apply_url` is non-null on exactly the 800 externals. The null means
"applied to on justjoin.it's own page", not a missing field. Read the two
columns together.

### Several filter names are accepted and ignored

The endpoint answers HTTP 200 with the full unfiltered result for
`categoryKeys`, `keyword`, `cities`, `workplaceTypes`,
`openToHireUkrainians`, `remoteInterview` and `salaryFrom` — several of
which are the spelling used in the payload's own field names. Only
parameters **measured to change `meta.totalItems`** are exposed as flags:
`--category`, `--city`, `--remote`, `--keyword`, `--experience`,
`--employment`, `--working-time`, `--language`, `--with-salary`.

### The sort decides *which* offers you get

Because every query is capped, the ordering changes the sample and not just
the order. The same query under `--sort publishedAt` and `--sort salary`
returned first pages whose 100 ids overlapped on **five**. `diff_runs.py`
refuses to compare two runs that disagree on `sort`, `order` or filters —
every line of that diff would be an artefact of the query.

**The site's default ordering is not a paid placement**, and that was
checked rather than assumed. `isSuperOffer` — justjoin.it's paid
placement — was true on 357 of 1,000 rows and spread through the result
(33, 32, 10, 34 and 9 per hundred at offsets 0, 500, 2,000, 5,000 and
9,000) rather than banked at the top. So this scraper keeps the site's own
default sort.

### `--route ssr` rows are a different shape

The rendered page's payload spells the same offer differently: skills are
bare strings with no levels, `locations` is called `multilocation`,
`categoryId` is an integer with no table to read it with, and languages are
absent entirely. One parser reads both routes, and `data_source` records
which one a row came from so a diff does not read the difference as the
site having changed.

---

## What captcha does this site have?

**Google reCAPTCHA v2, on the job-application form.** Not on any route this
scraper reads.

justjoin.it publishes its own key in the page config on every page:

```
"googleRecaptchaV2Key": "6LeTZ-ErAAAAAA_fFNEN3N575ErA7CPrGYyZX50o"
```

The field name says v2, and the only string in the bundle that references
it lives in the job-application form's i18n namespace ("reCAPTCHA
verification failed. Please try again."). It guards submitting a CV, which
this scraper never does.

That was measured, not inferred — "configured" and "rendered" are different
questions. In a live headless Chromium, seven seconds after load:

| Route | `window.grecaptcha` | captcha iframes | `[data-sitekey]` |
|---|---|---|---|
| `/job-offers/all-locations` | undefined | 0 | 0 |
| `/job-offer/{slug}` | undefined | 0 | 0 |
| `/api/candidate-api/offers` | undefined | 0 | 0 |

Not merely un-rendered — the reCAPTCHA script is never even **loaded** on
the read path. And no other vendor appears: zero for Cloudflare
challenges, DataDome, PerimeterX, Incapsula, Kasada and AWS WAF across all
seven captures.

Two markers were deliberately **not** carried, each after counting it:

* `cf-turnstile` — 2Captcha's own auto-solve extension injects it into
  every page it loads, so it fires on good pages. `challenges.cloudflare.com`
  is the one that works.
* `akamai` — four occurrences on a served offer page, because an employer
  lists "DigitalOcean, GCP, AWS, Azure lub **Akamai** Linode" among the
  clouds a candidate should know. A marker that fires on a real page is
  worse than no marker.

**None of this means a captcha here could not be solved.** If justjoin.it
ever puts its v2 widget in front of a listing, 2Captcha solves exactly that
with `RecaptchaV2TaskProxyless`, and the sitekey is published in the page
source where this code already looks for it. What is true today is
narrower: no route this scraper reads renders a challenge, so no run has
ever reached that code.

---

## Output

One row per offer, same first five columns as every repo in this family
(`source`, `scraped_at`, `url`, `sku`, `title`). See
[`sample_output.json`](sample_output.json) and
[`sample_output.csv`](sample_output.csv), both cut from a real run.

```json
{
  "source": "justjoin.it",
  "url": "https://justjoin.it/job-offer/…-senior-software-engineer-warszawa-python",
  "sku": "9c8f…-…-…",
  "title": "Senior Software Engineer – Python / Rust",
  "company_name": "…",
  "category": "python",
  "experience_level": "senior",
  "workplace_type": "remote",
  "working_time": "b2b_contract",
  "salary_min": 200.0,
  "salary_max": 240.0,
  "salary_currency": "PLN",
  "salary_unit": "hour",
  "salary_is_gross": false,
  "salary_contract_type": "b2b",
  "salary_monthly_min": 33600.0,
  "salary_monthly_max": 40320.0,
  "city": "Warszawa",
  "locations": ["Warszawa"],
  "required_skills": ["Python", "Rust", "AWS"],
  "required_skill_levels": [5, 4, 3],
  "languages": ["en"],
  "language_levels": ["B2"],
  "apply_method": "external",
  "data_source": "api",
  "page": 1,
  "position": 1
}
```

`sku` is the offer's `guid`, a UUID. It is the same value the detail route
calls `id` (equal on 40 of 40 sampled), which is what lets `--mode offer`
enrich a `--mode listings` run and what `diff_runs.py` joins on. **It
appears in no URL on this site** — unlike most repos in this family, there
is no id to recover from an address.

### Sidecar

Every run writes `<out>.meta.json`: `status`, `stop_reason`, which pages
failed by number, and this site's cap arithmetic. A **failed** run writes
none, and `save` leaves the previous good output in place rather than
overwriting it with an empty file (`--allow-empty` opts out).

### Exit codes

`0` ok · `1` crash · `2` bad usage · `3` blocked · `4` zero offers ·
`5` remote API error · `6` partial.

### Diffing two runs

```bash
python3 diff_runs.py --old python_2026-09-01.json --new python_2026-09-07.json
```

`removed` has **three** meanings here: the offer came down, the run did not
fetch that page, or the row fell outside this run's slice of a capped
result set. `diff_runs.py` warns when either run was capped, and refuses a
pair that used different modes, sorts, orderings or filters.

---

## Engines

Playwright is primary. Selenium and Puppeteer are parity engines — same
flags, same modes, same exit codes, same output. Verified on 2026-09-18:
all three produced **byte-identical rows** (modulo `scraped_at`) and
identical sidecars for the same command.

```bash
pip install -r requirements.txt -r requirements-playwright.txt   # primary
pip install -r requirements.txt -r requirements-selenium.txt     # parity
pip install -r requirements.txt -r requirements-puppeteer.txt    # parity
```

**Install exactly one per virtualenv.** playwright and pyppeteer declare
mutually unsatisfiable pins (`pyee` <12 vs ≥13), and pyppeteer and selenium
collide on `urllib3` (<2.0 vs ≥2.6).

Known limits, stated rather than left to be discovered:

* **Selenium cannot use an authenticated remote CDP endpoint.**
  chromedriver's `debuggerAddress` takes a bare `host:port` with nowhere to
  put a password. Playwright and Puppeteer take a full
  `ws://user:pass@host:port`.
* **Selenium's `--proxy-server` cannot authenticate at all.** That engine
  strips the credentials and warns rather than letting you believe a
  `user:pass` URL is doing something.
* **pyppeteer is effectively unmaintained** and its own README points at
  Playwright. It gains `--chromium-path`, which its twins do not need
  because they do not ship a browser.

There is also `scraper_api_client.py`, which renders a page on 2Captcha's
infrastructure and feeds the result to the same parser. It suits
`--route ssr` better than the endpoint, where a browser only adds a JSON
viewer around a document that was already plain text.

### Concurrency

`--concurrency N` fetches pages through N parallel workers, each with its
own browser and its own proxy exit. It is meaningful here because the
endpoint paginates by numeric offset, so page 5's address is knowable
without fetching page 4.

It is clamped to 1 where it would be pointless — `--mode facets` (one
request) and `--route ssr` (one page) — and refused with `--cdp-endpoint`,
because a Scraping Browser profile allows one live connection and workers
would collide. Use several `pid`s, one run each.

Without a proxy pool, N workers send N times the traffic from one address.
The scraper warns and continues.

---

## Configuration

Credentials live in `.env` next to the scripts, never on a command line —
a secret in `argv` is readable by anything that can run `ps` and lands in
your shell history.

```bash
cp .env.example .env
python3 env_config.py     # prints what was picked up, without any secret
```

Precedence, highest first: **explicit flag → exported environment variable
→ `.env` → default.**

| Variable | Fills |
|---|---|
| `TWOCAPTCHA_KEY` | `--twocaptcha-key` |
| `JUSTJOIN_CDP_ENDPOINT` | `--cdp-endpoint` |
| `JUSTJOIN_PROXY` | `--proxy` |
| `JUSTJOIN_URL` | `--url` |

A copied `.env.example` reads as **unset** for every credential — the
placeholders are recognised, so `cp .env.example .env` followed by a run
does not connect with `{login}-zone-…` as its username and get a 401 a long
way from its cause.

---

## Tests

```bash
python3 smoke_test.py          # the offline suite, no engine needed
python3 smoke_test.py -v       # print every check
pytest                         # same checks, via tests/test_smoke.py
```

The suite passes with **no engine library installed at all**, and records
the skip when one is absent — CI installs each engine in its own venv and
fails if that engine's group reports a skip, because "skipped, engine
absent" reads identically to a real import error.

Fixtures are cut from real captures and verified to parse identically to
their untrimmed originals.

The daily [canary](.github/workflows/canary.yml) runs a real 3-page scrape
from a bare GitHub runner and is **expected to be green**: the listing path
needs no credentials, so gating it on a secret would hide the day that
stops being true.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security reports:
[SECURITY.md](SECURITY.md).

Licence: [MIT](LICENSE).

Captcha solving, the Scraping Browser API, proxies and fingerprints are
four separately-billed [2Captcha](https://2captcha.com) products behind one
key.
