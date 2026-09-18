"""
output_writer.py
-----------------
Shared row model + JSON/CSV writers used by all three engines.

Three modes, two row shapes
---------------------------
    --mode listings  justjoin.it/api/candidate-api/offers         the board
    --mode offer     justjoin.it/api/candidate-api/offers/{slug}  one offer
    --mode facets    .../offers/facets/count                      the taxonomy

`listings` and `offer` are two views of one thing, keyed by the same UUID —
the listing route calls it `guid` and the detail route calls it `id`, and
they were equal on 40 of 40 sampled offers. That is what lets an `offer`
run enrich a `listings` run and what `diff_runs.py` joins on.

What they populate is genuinely different, and that is why `data_source` is
a COLUMN rather than a sidecar field. A detail row carries the description
body, the company website and size, the country code and `isActive`; a
listing row carries none of those. Neither is a subset of the other, and a
diff between them would otherwise report five columns as having vanished
overnight.

There is a THIRD spelling of the same offer, and it is why `data_source`
has three values rather than two. `--route ssr` reads the server-rendered
listing page, whose flight payload publishes skills as bare strings with no
levels, calls `locations` `multilocation`, states an integer `categoryId`
in place of a category object, and omits languages entirely. One parser
reads all three so the routes cannot drift; the column says which one a row
came from.

`facets` is the OTHER shape
---------------------------
The board's own taxonomy — how many offers each category, seniority,
workplace type, contract type and language has. A different KIND of thing
from a job posting, so it gets its own dataclass (`Facet`) rather than
being bent into this one, with the family prefix kept byte-identical and in
order and `sku` reused as the id column.

What makes it worth a mode: those counts are NOT subject to the 10,000
ceiling every offers query is. They describe all 19,381 offers, which makes
`--mode facets` the tool for planning filtered runs that reach past the cap
— 25 category runs of at most 1,991 rows each cover a board that one
unfiltered run cannot.

`mode` goes in the sidecar and `diff_runs.py` REFUSES a cross-shape
comparison, which is CLAUDE.md §9's rule for a repo that reads more than
one kind of thing.

The row is `JobPosting` and not `Product`
-----------------------------------------
Every shop repo in this family names its row `Product` and keeps the
commerce columns even where they are null, because on a shop a null price
is a fact worth recording. justjoin.it sells no products: there is no
price, no discount, no stock and no brand, and those columns null on every
row of every run of every mode is exactly what §9 says must not exist.
`wellfound-scraper`, `bbb-scraper` and `mercor-scraper` are the precedent.

What a job board has where a shop has a price is a RANGE, which is two
columns and not one, plus a UNIT that is not always monthly — measured over
1,000 offers on 2026-09-18: month 792, hour 229, day 70, year 8 — plus a
CURRENCY the employer chose and a GROSS/NET flag that decides whether two
numbers are even comparable. A Polish B2B rate is quoted net and an
employment-contract rate gross, so `salary_is_gross` is not decoration.

What IS kept, byte-identical and in order, is the family prefix — `source`,
`scraped_at`, `url`, `sku`, `title` — so one column name works across the
family and a consumer reading several of these repos reads the same first
five columns in the same order (§9).

Columns that are absent, and the measurement for each
------------------------------------------------------
§9: a column that is null on every row of every run should not exist, and
removing one needs the measurement written down so someone can add it back
with a better one. Measured over 1,000 listing records and 40 detail
records on 2026-09-18:

    isPromoted                      false on 1,000 of 1,000, and at every
                                    offset sampled from 0 to 9,000.
                                    `isSuperOffer` is the real placement
                                    flag and IS a column (357 of 1,000).
    category.parentKey              null on all 1,040 records. The 25
                                    categories are a flat list, not a tree.
    companyProfileSlug              0 of 1,000 and 0 of 40.
    companyProfileCoverPhotoUrl     0 of 40.
    companyProfileShortDescription  0 of 40.
    videoUrl                        0 of 40.
    appliedAt                       0 of 40 — it answers "have YOU applied",
                                    which is null for an anonymous fetch.
    informationClause, futureConsent, customConsent
                                    present and non-empty, and deliberately
                                    not columns: they are the employer's
                                    GDPR boilerplate, several kilobytes of
                                    legal text per offer that says nothing
                                    about the job.

`rating` is absent, and that one is a measurement too
-----------------------------------------------------
justjoin.it publishes no rating, score or review count for an offer, a
company or an employer on any route this scraper reads: zero such fields
across 1,000 listing records, 40 detail records and 100 server-rendered
records in the 2026-09-18 captures. It is a job board, not a directory — a
`rating` column would be null on every row of every run.

Everything below the dataclass is row-class-agnostic: pass `row_cls` so an
empty CSV still gets the right header for the mode that produced it.
"""

import csv
import json
from dataclasses import dataclass, asdict, field, fields
from datetime import datetime, timezone
from typing import Optional, List, Set, Sequence, Any, Type


# The site a row came from. One value, because justjoin.it serves
# everything this scraper reads — the rendered pages, the offers endpoint
# and the offer endpoint — from the one host. Which ROUTE a row came from
# is `data_source`, which is a different question and has its own column.
SOURCE_DEFAULT = "justjoin.it"


def utc_now() -> str:
    """The run's timestamp, as a UTC ISO-8601 string with a `Z`.

    One helper so every row in a run can be given the SAME stamp by the
    caller rather than each row calling the clock. Rows from one page that
    disagree in `scraped_at` by a few milliseconds make a diff noisier for
    no information.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class JobPosting:
    """One job offer on justjoin.it.

    The family prefix — `source`, `scraped_at`, `url`, `sku`, `title` — is
    byte-identical and in this order across every repo in the family, so a
    consumer reading several of them reads the same first five columns
    (CLAUDE.md §9). Everything after it is Just Join IT's.

    Every coverage figure quoted below was measured on 2026-09-18 over
    1,000 consecutive listing records and 40 randomly sampled offer
    records. They are a reading taken on one day, not a property of the
    site: re-measure before quoting them (CLAUDE.md §13).
    """

    source: str = SOURCE_DEFAULT
    scraped_at: str = ""
    # The offer's page on justjoin.it — `/job-offer/{slug}`. Built from the
    # slug on a listing row, taken from the record's own `url` field on a
    # detail row, and the two agree.
    url: str = ""
    # The record's `guid` — a UUID. The listing route calls it `guid` and
    # the detail route calls it `id`; they were equal on 40 of 40 sampled
    # offers, which is what lets a `--mode offer` run enrich a
    # `--mode listings` run and what `diff_runs.py` joins on.
    #
    # It appears NOWHERE in any URL on this site, so unlike most repos in
    # this family there is no id to recover from an address —
    # `product_parser.sku_from_url` returns None and says why.
    sku: Optional[str] = None
    title: Optional[str] = None

    # ---- addressing -----------------------------------------------------
    # The URL slug: `goodylabs-senior-devops-engineer-k-m-x--lodz-devops`.
    # Company, title, city and category, plus a short hex tail where those
    # four collide. It is what `--mode offer` takes, so it is a column
    # rather than something a consumer has to cut back out of `url`.
    slug: Optional[str] = None

    # ---- the employer ---------------------------------------------------
    # 1000/1000 on the listing route. justjoin.it does not run anonymous
    # postings, so a null here would be a parsing failure rather than a
    # withheld name.
    company_name: Optional[str] = None
    company_logo_url: Optional[str] = None
    # Detail route only, and sparse there: 5 of 40. A null means the
    # employer did not fill the field in.
    company_url: Optional[str] = None
    company_size: Optional[str] = None

    # ---- what kind of job -----------------------------------------------
    # The site's own category key — `devops`, `java`, `analytics`. 25 of
    # them. Null on a `--route ssr` row: the rendered payload states an
    # integer `categoryId` and publishes no table to read it with, and a
    # number nobody can interpret is worse than a null.
    category: Optional[str] = None
    #
    # `category.parentKey` is deliberately NOT a column beside it. The
    # endpoint publishes the field on every record and it was null on 1,040
    # of 1,040 — every one of 1,000 listing records and 40 detail records
    # measured 2026-09-18. justjoin.it's 25 categories are a flat list, not
    # a tree. A field that looks available and never is costs more than a
    # missing one (CLAUDE.md §9), and this note is the measurement someone
    # needs to add it back if the site grows subcategories.
    # intern / junior / mid / senior / manager / c_level. Measured over
    # 1,000: senior 519, mid 380, manager 58, junior 38, c_level 4, intern 1.
    experience_level: Optional[str] = None
    # office / hybrid / remote — and `mobile`, which the site's own facet
    # list carries with a count of 0. Measured: hybrid 558, remote 363,
    # office 79.
    workplace_type: Optional[str] = None
    # full_time / b2b_contract / freelance / part_time / internship. A
    # DIFFERENT axis from `salary_contract_type` below: this is the working
    # arrangement the posting is filed under, that is the contract the pay
    # is quoted against, and 149 rows are filed `b2b_contract` while 709
    # quote a `b2b` rate.
    working_time: Optional[str] = None

    # ---- pay ------------------------------------------------------------
    # The salary the EMPLOYER quoted, in the currency they quoted it in.
    #
    # Read from the `employmentTypes` entry whose `currencySource` is
    # "original" and never from `employmentTypes[0]`, which is a currency
    # conversion the site computed on 321 of 1,000 listing rows and on 29
    # of 40 detail records. Of those, the ones that also DISCLOSE an amount
    # — 12 listing rows and 16 of the 40 detail records — would get a wrong
    # NUMBER as well as a wrong currency: 39.15 CHF printed for a job
    # offering 180 PLN. See product_parser's docstring for the table.
    #
    # Null on 461 of 1,000 rows, where the employer disclosed no salary.
    # Null, not zero — the site's own UI says "Undisclosed Salary" for
    # these, and a zero would drag every average a reader computes
    # (CLAUDE.md §21).
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    # An ISO 4217 code the site states — never defaulted, never inferred
    # from the country. PLN on 1,076 of the 1,099 quoted entries, EUR on
    # 15, USD on 8.
    salary_currency: Optional[str] = None
    # month / hour / day / year, lower-cased here because the site spells
    # the same unit two ways: "month" 517 against "Month" 275, and "Hour"
    # 229. Left alone, one unit becomes two buckets in any GROUP BY.
    salary_unit: Optional[str] = None
    # True = gross, False = net, null = the entry did not say. This matters
    # more on this board than on most: a Polish B2B rate is quoted net and
    # an employment-contract rate gross, so comparing the two numbers
    # without this column compares different things. Measured: False 737,
    # True 362.
    salary_is_gross: Optional[bool] = None
    # Which contract the quoted rate is for: b2b 709, permanent 333,
    # any 40, mandate_contract 16, internship 1.
    salary_contract_type: Optional[str] = None
    # The same pay normalised to a month, which is how justjoin.it's own
    # cards let an hourly offer be compared with a salaried one — 110
    # PLN/hour is published beside 18,480 PLN/month, at 168 working hours.
    #
    # Kept in columns that SAY monthly, because the endpoint keeps this
    # figure in a field called `from`, directly beside a `unit` naming the
    # employer's unit. Reading those two together — the obvious thing to
    # do — yields "18,480 PLN per hour" on 307 of 582 quoted entries. See
    # `product_parser._salary_option`.
    #
    # Null on a `--route ssr` row unless the employer quoted a month: the
    # rendered payload carries no normalisation, and computing one here
    # would be this scraper inventing the site's arithmetic.
    salary_monthly_min: Optional[float] = None
    salary_monthly_max: Optional[float] = None
    # Every quoted rate, where an offer quotes more than one — 99 of 1,000
    # rows offer both a B2B and an employment-contract rate, which are
    # genuinely different offers of pay for the same job. Null where there
    # is only one, so the column is empty exactly when it would add
    # nothing. Format: `b2b 22000-28000 PLN/month net`.
    salary_options: Optional[List[str]] = None

    # ---- where ----------------------------------------------------------
    city: Optional[str] = None
    street: Optional[str] = None
    # Upper-cased: the detail route publishes both "PL" and "pl" (36 and 2
    # of 40).
    country_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # Every city the offer is open in. 289 of 1,000 rows name more than one
    # — up to nine — so `city` alone under-reports where the job is. The
    # endpoint calls this list `locations` and the rendered payload calls
    # the same list `multilocation`.
    locations: Optional[List[str]] = None
    location_count: Optional[int] = None

    # ---- what the job wants ---------------------------------------------
    # Skill names in the site's own order. 1000/1000 rows carry at least
    # one; the median is four.
    required_skills: Optional[List[str]] = None
    # The 1-5 level the employer set for each skill, positionally aligned
    # with `required_skills`. Null on a `--route ssr` row: the rendered
    # payload publishes skills as bare strings with no levels at all, and
    # inventing a level would be a guess in a column (CLAUDE.md §8).
    required_skill_levels: Optional[List[int]] = None
    # Sparse and real: 46 of 1,000 listing rows, 3 of 40 detail records.
    nice_to_have_skills: Optional[List[str]] = None
    # ISO language codes the posting requires. Non-null on 465 of 1,000 —
    # a null means the employer stated no language requirement, not that
    # the job needs none. Absent from the rendered payload entirely, so
    # always null on a `--route ssr` row.
    languages: Optional[List[str]] = None
    # CEFR levels (B2, C1, ...) aligned with `languages`.
    language_levels: Optional[List[str]] = None

    # ---- the arrangement ------------------------------------------------
    # How a hybrid week is split. Non-null on 181 of 1,000 rows; the object
    # itself can be present with both days null, which collapses to a pair
    # of nulls here rather than to a zero.
    office_days: Optional[int] = None
    remote_days: Optional[int] = None
    # True on 32 of 1,000. Note the endpoint ACCEPTS a
    # `remoteInterview=true` filter and ignores it, which is why there is
    # no flag for it — the column is read from the row, not filtered on.
    remote_interview: Optional[bool] = None
    # True on 21 of 1,000. Same story: the filter is accepted and ignored.
    open_to_hire_ukrainians: Optional[bool] = None

    # ---- placement and status -------------------------------------------
    # justjoin.it's paid placement. True on 357 of 1,000 rows, and spread
    # through the result rather than banked at the top: 33, 32, 10, 34 and
    # 9 per hundred at offsets 0, 500, 2,000, 5,000 and 9,000. So the
    # site's default ordering is NOT a paid ranking — measured, because
    # CLAUDE.md §21 found that it was on BBB, and this scraper therefore
    # keeps the site's own default sort rather than overriding it.
    #
    # `isPromoted` is deliberately NOT a column beside it: false on
    # 1,000 of 1,000 rows and at every offset sampled. A field that looks
    # available and never is costs more than a missing one (CLAUDE.md §9),
    # and this note is the measurement someone needs to add it back.
    is_super_offer: Optional[bool] = None
    # Detail route only. True on 40 of 40 sampled — the endpoint serves
    # active offers, and no expired one was reachable to check the other
    # value with. So a False here is UNPROVEN and a True says only what the
    # site said (CLAUDE.md §20). Carried because this is the one column a
    # withdrawn offer could ever announce itself in.
    is_active: Optional[bool] = None
    # external 800 / form 200 of 1,000.
    apply_method: Optional[str] = None
    # Non-null on exactly the 800 rows whose `apply_method` is "external".
    # The null is the site saying "applied to on our own page", not a
    # missing field — read the two columns together.
    apply_url: Optional[str] = None

    # ---- dates ----------------------------------------------------------
    # All three normalised to UTC ISO-8601 with a `Z`, from two different
    # precisions the site mixes. `published_at` can be in the FUTURE — one
    # row of 1,000 was, a posting scheduled ahead — so a consumer filtering
    # on "published before now" will drop it.
    published_at: Optional[str] = None
    last_published_at: Optional[str] = None
    expires_at: Optional[str] = None

    # ---- the posting itself ---------------------------------------------
    # The description, rendered from the employer's HTML to text. Detail
    # route only: neither the endpoint's listing route nor the rendered
    # payload's own records carry a body on a listing row.
    description: Optional[str] = None

    # ---- provenance -----------------------------------------------------
    # Which route this row was read out of: `api` (the offers endpoint),
    # `ssr` (a rendered listing page's flight payload) or `detail` (one
    # offer's endpoint). CLAUDE.md §8 — provenance of a value goes in a
    # column, and `diff_runs.py` reports a difference that comes with a
    # `data_source` difference as `source_changed` rather than as a real
    # change. That matters more here than in most repos in this family,
    # because the three routes genuinely publish different subsets: an
    # `ssr` row has no category, no languages and no skill levels, and a
    # diff against an `api` row would otherwise read as the site having
    # dropped three columns overnight.
    data_source: Optional[str] = None
    page: Optional[int] = None
    position: Optional[int] = None


@dataclass
class Facet:
    """One key in one of justjoin.it's own facet groups, with its count.

    A different KIND of thing from a job posting — it describes the board
    rather than a job — so it gets its own row class rather than being
    bent into `JobPosting` (CLAUDE.md §9). The family prefix is kept
    byte-identical and in order, and `sku` is reused as the id column so
    one column name works across the family.

    What makes it worth a mode of its own: these counts are NOT subject to
    the 10,000 cap every offers query is. They describe all 19,381 offers,
    which makes `--mode facets` the tool for planning a run that reaches
    past the cap — 25 category runs of at most 1,991 rows each cover the
    board where one unfiltered run cannot.
    """

    source: str = SOURCE_DEFAULT
    scraped_at: str = ""
    url: str = ""
    # `{group}:{key}` — `experienceLevels:senior`. Unique within a run, so
    # the family's dedupe and diff machinery works unchanged.
    sku: Optional[str] = None
    # The key itself, so the family's fifth column carries the human-
    # readable thing the way it does on a job row.
    title: Optional[str] = None

    facet_group: Optional[str] = None
    facet_key: Optional[str] = None
    offer_count: Optional[int] = None

    # True for `publishedSinceDays`, whose keys are CUMULATIVE windows —
    # 1, 7, 14 and 30 days — rather than a partition. Its counts sum to
    # 22,127 against a board of 19,381, and a reader summing them without
    # this column would conclude the site contradicts itself.
    is_cumulative: Optional[bool] = None
    # True for `employmentTypes` and `languages`, where one offer can be
    # counted under several keys: an offer may quote both a B2B and a
    # permanent rate, and may require two languages. Their sums are 36,996
    # and 15,535.
    overlaps_other_keys: Optional[bool] = None
    # The board-wide total the counts should be read against.
    site_total: Optional[int] = None


# Row classes by --mode, so an engine maps its mode to a schema in one
# place.
ROW_CLASS_BY_MODE = {"listings": JobPosting, "offer": JobPosting,
                     "facets": Facet}

# Modes whose rows are one-per-sku, and therefore safe to dedupe on `sku`
# and to hand to diff_runs.py.
#
# All three qualify, for different reasons. `listings` names each offer
# once per query — 1,000 records, 1,000 distinct `guid`s measured
# 2026-09-18 — and its pages do not overlap, because the endpoint
# paginates by a numeric offset rather than by a cursor into a shifting
# result set. `offer` is one offer per fetch. `facets` keys each row by
# `{group}:{key}`, which the site states once each.
#
# A drop during dedupe on `listings` therefore means something real: either
# the board changed underneath a multi-page run — offers are published
# continuously, and a new one at the top shifts every later offset by one,
# which is the ordinary way a row gets fetched twice — or a page was
# fetched twice. Both are worth logging rather than silently applying.
UNIQUE_BY_SKU_MODES = ("listings", "offer", "facets")


def dedupe_by_key(rows: Sequence[Any], seen: Set[str], key: str = "sku") -> List[Any]:
    """Drop rows whose key already appeared earlier in this same run.

    `seen` is mutated in place, so callers thread the same set across pages —
    a repeated page then re-parses without duplicating its rows into the
    final output.

    A drop here has a specific and interesting cause on this site, which is
    why it is logged rather than quietly applied. The endpoint paginates by
    numeric OFFSET over a board that is published to continuously, so one
    new offer arriving at the top during a run shifts every later offset by
    one and the row at the seam is fetched twice. That is the site moving
    under a paginated run, not a bug, and it is worth seeing in the log of a
    long one.

    Within a single response there are no duplicates — 1,000 records, 1,000
    distinct `guid`s measured 2026-09-18 — and `--mode offer` fetches a
    de-duplicated enumeration, one URL per offer. So a drop means either the
    board shifted, or two workers were handed the same address.
    The function stays regardless — it is the backstop that keeps the output
    clean, and "should never fire" is a poor reason to remove a guard that
    costs one pass over a list.

    A row with no key is always kept: there is nothing to check a duplicate
    against, and dropping it would be a silent data loss rather than a
    duplicate removal.

    All three of this repo's modes are one row per `sku`, so `key` is never
    overridden here — the parameter exists because the rest of the family
    shares this function and one of them needs it.
    """
    fresh = []
    for r in rows:
        val = getattr(r, key, None)
        if val is None or val not in seen:
            if val is not None:
                seen.add(val)
            fresh.append(r)
    return fresh


# Kept under its old name: the engines and smoke tests in this family all
# call it, and a listing run does dedupe by sku.
def dedupe_by_sku(rows: Sequence[Any], seen: Set[str]) -> List[Any]:
    return dedupe_by_key(rows, seen, key="sku")


# CSV cannot hold a list. Joining with " | " keeps the cell readable in a
# spreadsheet and round-trippable by splitting on the same separator; the
# JSON output keeps the real list, so nothing is lost for a consumer that
# wants structure. `repr()` of a Python list (the default if this is not
# handled) is neither readable nor parseable by anything but Python.
LIST_CSV_SEPARATOR = " | "


def _csv_value(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return LIST_CSV_SEPARATOR.join(str(x) for x in v)
    return v


def write_json(rows: Sequence[Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in rows], f, ensure_ascii=False, indent=2)


def write_csv(rows: Sequence[Any], path: str, row_cls: Type = JobPosting) -> None:
    # An empty result still gets the header row. A zero-byte file makes a
    # consumer fail on read (no columns to parse) instead of reading a valid
    # table with zero rows — and "an empty result is still a well-formed
    # result" is the same principle as `save` refusing to overwrite good data.
    #
    # The header comes from `row_cls`, not from the first row, so an empty
    # run still writes the columns of the mode that produced it.
    fieldnames = [f.name for f in fields(row_cls)]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _csv_value(v) for k, v in asdict(r).items()})


# Exit code used when a run completes but produced nothing. Distinct from 1
# (crash) so a caller can tell "ran, found nothing" from "blew up".
EXIT_NO_PRODUCTS = 4

# Exit code for a run blocked by a bot-check/challenge page before parsing
# even started — distinct from EXIT_NO_PRODUCTS so a caller can tell "the
# search genuinely matched nothing" from "something stood between us and the
# content". See product_parser.detect_bot_challenge.
#
# On justjoin.it this code does NOT cover a response that holds no offers.
# That answers HTTP 200 with a payload carrying an empty set, and it is
# EXIT_NO_PRODUCTS: the request was served exactly as asked and has nothing
# in it. Reporting it as blocked would send a user hunting for a proxy
# problem that does not exist.
#
# Nor does it cover a 404, which is its own state (`not_found`): a listing
# taken down between the sitemap being read and its page being fetched is
# an ordinary event in `--mode job`, and it is not a block.
#
# What EXIT_BLOCKED would mean here is unmeasured, and saying so is more
# use than inventing a description. justjoin.it is fronted by Cloudflare but
# refused nothing on 2026-09-18: eighteen candidate challenge markers across
# seven captures all zero, and identical 200s from curl, from
# python-requests and from an empty User-Agent, to a bare Hetzner datacentre
# address. This scraper has never seen this site block it, so there is no
# refusal shape to document. If a run reports exit 3, the saved debug HTML
# is the evidence, and it is new.
EXIT_BLOCKED = 3

# Exit code for a run that gathered SOME rows and then stopped early — a
# page-load timeout, a 503 throttle, or a challenge on page 3 of 10. The
# output file is still written (throwing away three good pages would be
# worse), but it is not a complete picture, and a consumer that cannot tell
# the difference will read the pages that were never fetched as products that
# disappeared from the catalogue. See write_run_meta.
# A REMOTE service failed — the Scraping Browser refusing the connection
# (`profile_locked` is the common one: a profile allows a single live
# connection), or the Scraper API answering an error. Distinct from 1 (a
# crash in this code) and from 2 (bad usage) because it means "try again, or
# use a different profile", not "there is a bug here". Defined once, here,
# because the browser engines and scraper_api_client.py both return it and
# two definitions of the same code is exactly how a family's exit contract
# drifts.
EXIT_API_ERROR = 5

EXIT_PARTIAL = 6


def write_run_meta(out_prefix: str, meta: dict) -> str:
    """Write a run-metadata sidecar next to the output, return its path.

    Deliberately a separate `<out>.meta.json` rather than columns on every
    row: this describes the RUN, not the product, and repeating it across
    every row would both bloat the output and change the schema every
    consumer of this project already parses.

    diff_runs.py reads it to refuse a comparison between runs that are not
    both complete, and between runs of different `mode`.
    """
    path = f"{out_prefix}.meta.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[+] Wrote run metadata -> {path} (status={meta.get('status')})")
    return path


def run_meta(status: str, stop_reason: str, pages_requested: int,
             pages_completed: int, start_url: str, final_url: str,
             products: int, pages_failed: Optional[List[int]] = None,
             mode: str = "listing", source: str = SOURCE_DEFAULT,
             extra: Optional[dict] = None) -> dict:
    """Build the metadata dict for a finished run.

    `status` is the field a consumer branches on:
      complete — every requested page was fetched, or the site's own
                 pagination genuinely ran out (nothing more existed to get)
      partial  — rows were gathered, then the run stopped early
      failed   — nothing was gathered at all

    `mode` and `source` are recorded because `mode` is not implied by the
    repo: the same output prefix can hold a listings run, an offer run or a
    facets run, and those populate different columns — an offer row has the
    description body, the company website and size and the country; a
    listings row has none of them; a facets row has a different SCHEMA
    entirely. `diff_runs.py` refuses a pair whose modes differ, which
    matters most for `facets`: it shares no id with either job mode, so a
    diff of the two would report every row as both added and removed.

    `sort`, `order` and `filters` are recorded for the same reason and it
    is specific to this site: because every query is capped at 10,000
    results, the ordering and the filters decide WHICH offers are in the
    file and not merely their order. Two orderings of one query overlapped
    on 5 of their first 100 ids when this was measured, so `diff_runs.py`
    refuses a pair that disagrees on either (CLAUDE.md §21).

    `source` is `justjoin.it` on every row of every run. The site is served
    from one host and this column
    names the SITE rather than the host, so one value covers both; which
    host a row came from is recoverable from `url`. It is kept because
    consumers read these columns by name across the family.

    `extra` carries facts about the run that are not about any single row.
    justjoin.it states `meta.totalItems` on every response, so unlike a site
    that states nothing there IS a figure to record — and recording it alone
    would be the lie §21 warns about, because the site caps that figure at
    10,000 however many the query matched. So a listings run writes the
    whole arithmetic: `total_results`, `site_total`, `reachable_max`,
    `capped_by_site`, `share_of_query_pct` and `share_of_board_pct`, plus
    the `sort`, `order` and `filters` that decide WHICH offers are in the
    file rather than merely their order.

    That is the only honest way to say what a run holds here, because
    "complete" and "exhaustive" come apart on this site. A run that fetches
    all 10,000 reachable offers really did fetch everything justjoin.it
    will serve for that query — genuinely complete — and is also about half
    of a board that held 19,381 when this was measured. The way to get a
    comparable census is a FILTERED run whose `total_results` is under the
    cap; `--mode facets` is how you find one.
    """
    meta = {
        "source": source,
        "mode": mode,
        "status": status,
        "stop_reason": stop_reason,
        "pages_requested": pages_requested,
        "pages_completed": pages_completed,
        "pages_failed": pages_failed or [],
        # Named "products" even though these are job listings, and kept that
        # way deliberately: every repo in this family writes this key, and a
        # consumer reading several of them reads one sidecar shape.
        # quora-scraper made the same call for answers. The row TYPE is
        # `mode` plus `source`, which are right beside it.
        "products": products,
        "start_url": start_url,
        "final_url": final_url,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        # Merged rather than nested under a key, so a consumer reads
        # `shop_rating` at the top level beside `products`. Run fields win a
        # name collision: a caller cannot accidentally overwrite `status`.
        meta.update({k: v for k, v in extra.items() if k not in meta})
    return meta


def save(rows: Sequence[Any], out_prefix: str, fmt: str,
         allow_empty: bool = False, row_cls: Type = JobPosting) -> int:
    """Write JSON/CSV and return a process exit code.

    Returns 0 when rows were written, EXIT_NO_PRODUCTS when there were none.
    Callers are expected to exit with it.

    On zero rows, nothing is written at all unless `allow_empty`. Two reasons,
    and a live run demonstrated both. A page-load timeout produced
    `Saved 0 jobs -> out.json` and exit 0: a two-byte `[]` that a
    consuming pipeline reads as a successful run with no stock. Worse, if the
    file already held a good result from an earlier run, that result is now
    gone — the failure destroyed the last known good data. So an empty result
    leaves the previous file intact and says why.

    `allow_empty=True` is for the legitimate case: a filter that genuinely
    matches nothing, where an empty file is the answer.
    """
    if not rows and not allow_empty:
        print(f"[!] 0 jobs — refusing to write {out_prefix}.json/.csv, so an "
              f"earlier good result isn't overwritten with an empty one. "
              f"Pass --allow-empty if an empty result is the expected answer.")
        return EXIT_NO_PRODUCTS

    if fmt in ("json", "both"):
        write_json(rows, f"{out_prefix}.json")
        print(f"[+] Saved {len(rows)} jobs -> {out_prefix}.json")
    if fmt in ("csv", "both"):
        write_csv(rows, f"{out_prefix}.csv", row_cls=row_cls)
        print(f"[+] Saved {len(rows)} jobs -> {out_prefix}.csv")
    return 0 if rows else EXIT_NO_PRODUCTS


# Stop reasons that mean the run saw everything there was to see. Anything
# else ended the page loop early, so the result is only a partial view.
#
# "no_new_products" belongs here and "pagination_exhausted" is kept for the
# engines that still stop on a missing next-link: the first is a property of
# the DATA (a page contributed nothing not already seen, so the listing is
# over), while the second is a property of a CSS SELECTOR and is therefore
# the weaker signal — a renamed attribute looks identical to a short
# catalogue.
#
# On justjoin.it there IS a third signal and it is the strongest of the
# three: the endpoint states `meta.totalItems` on every response, so a run
# plans against the site's own arithmetic rather than discovering the end by
# overshooting. "page_cap_reached" fires here for real — it is what a run
# says when the plan was bounded by that figure, or by the site's 10,000
# ceiling, rather than by `--pages`.
#
# "page_echo_mismatch" is carried for the family's shared vocabulary and is
# reachable here too: a rendered listing answers `?page=2` with page 1 and
# echoes `meta.from` 0, which is exactly what it names. `--route ssr` plans
# one page so a run should never reach it, and it is left in so that a route
# which starts clamping is noticed rather than silently re-collected.
#
# "single_page_route" is complete by construction for the two routes that
# have one page: `--mode facets` is one request, and `--route ssr` serves
# one page and ignores `?page=N`.
#
# Note what "complete" does NOT mean here. justjoin.it caps every query at
# 10,000 results however many it matched — the board held 19,381 when this
# was measured — so a complete run can still be about half the board.
# CLAUDE.md §21: complete and exhaustive are different words, and the
# sidecar records both figures so a consumer is not left inferring one from
# the other.
COMPLETE_STOP_REASONS = ("completed", "pagination_exhausted", "no_new_products",
                         "page_cap_reached", "page_echo_mismatch",
                         "single_page_route")


def finish_run(rows: Sequence[Any], out_prefix: str, fmt: str,
               allow_empty: bool, *, blocked: bool, stop_reason: str,
               pages_requested: int, pages_completed: int,
               start_url: str, final_url: str,
               pages_failed: Optional[List[int]] = None,
               mode: str = "listing", source: str = SOURCE_DEFAULT,
               extra: Optional[dict] = None) -> int:
    """Write output + the run-metadata sidecar; return the exit code.

    Shared by all three browser engines so the status/exit-code mapping
    cannot drift between them.

    The metadata sidecar is written ONLY when the row file was written.
    Otherwise a failed run would leave a "status": "failed" sidecar next to
    the previous run's still-intact good output (which `save` deliberately
    does not overwrite) — the two files would contradict each other, and
    diff_runs.py would refuse to compare data that is in fact fine.
    """
    complete = stop_reason in COMPLETE_STOP_REASONS
    row_cls = ROW_CLASS_BY_MODE.get(mode, JobPosting)
    rc = save(rows, out_prefix, fmt, allow_empty=allow_empty, row_cls=row_cls)
    wrote_output = bool(rows) or allow_empty

    if wrote_output:
        status = "complete" if (rows and complete) else (
            "partial" if rows else "failed")
        write_run_meta(out_prefix, run_meta(
            status=status, stop_reason=stop_reason,
            pages_requested=pages_requested, pages_completed=pages_completed,
            pages_failed=pages_failed, mode=mode, source=source,
            start_url=start_url, final_url=final_url, products=len(rows),
            extra=extra))

    if not rows:
        # Nothing gathered at all: a challenge outranks "empty result",
        # because it says something stood between the run and the content.
        return EXIT_BLOCKED if blocked else rc
    if not complete:
        print(f"[!] Partial run: stopped after {pages_completed} of "
              f"{pages_requested} page(s) ({stop_reason}). The output holds "
              f"what was gathered, but it is NOT a complete view — see "
              f"{out_prefix}.meta.json.")
        return EXIT_PARTIAL
    return rc
