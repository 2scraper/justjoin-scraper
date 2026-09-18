#!/usr/bin/env python3
"""
diff_runs.py
-------------
Compares two output files from this project (JSON, as written by
output_writer.save) and reports what changed between them, keyed on `sku`.

    python3 diff_runs.py --old python_2026-09-01.json \\
                          --new python_2026-09-07.json

Typical use is a scheduled re-run kept under a dated filename, diffed
against the previous one:

    python3 playwright_scraper.py --category python --pages 5 \\
        --out "python_$(date +%F)"
    python3 diff_runs.py --old "$(ls -t python_*.json | sed -n 2p)" \\
                          --new "python_$(date +%F).json" --out diff.json

Four buckets, each keyed on sku:

  added          — sku present in --new, absent from --old
  removed        — sku present in --old, absent from --new. See below: on
                   this site that has THREE possible meanings.
  changed        — sku present in both, with a different title, salary,
                   seniority, skills, workplace type or apply route. See
                   TRACKED_FIELDS.
  source_changed — sku present in both, but one row came from the endpoint
                   (`api`), a rendered page (`ssr`) or an offer's own record
                   (`detail`), and they differ on a column only some of
                   those fill. Reported separately because it says something
                   about our own two snapshots rather than about the offer —
                   and --fail-on-change deliberately ignores it.

THREE THINGS TO KNOW BEFORE READING A DIFF OF THIS SITE
--------------------------------------------------------
**`removed` has a third meaning here, and it is the common one.** Besides
"the offer was taken down" and "the run did not fetch that page", a row can
leave a file because it fell outside THIS RUN'S SLICE OF A CAPPED RESULT
SET. justjoin.it answers every query with at most 10,000 results however
many it matched — the board held 19,381 on 2026-09-18 — so an unfiltered
run holds a moving window, and offers are published continuously.

Two runs are comparable as a census only when both covered the same ground.
The way to get that on this site is a FILTERED run whose `total_results` is
comfortably under the cap: `--category python` matched 439, and two such
runs a week apart really do describe the same population. The sidecar
records `capped_by_site` so a reader can tell which kind of file they have,
and this tool warns when either run was capped.

**The ORDERING is part of the query, not a presentation choice.** Because
the result set is capped, `--sort publishedAt` and `--sort salary` return
different SAMPLES of the same board: measured 2026-09-18, the first 100 ids
under the two orderings overlapped on 5. So two runs that disagree on
`sort` or on their filters are refused outright rather than diffed — every
line of that diff would be an artefact of the query (CLAUDE.md §21).

**`position` and `page` are deliberately not tracked.** justjoin.it's order
was measured STABLE on 2026-09-18: two fetches 25 seconds apart returned
the identical 100 ids in the identical order, with no field differing on
any of them. So a position change would be a real change and not noise —
and it is still not diffed, because 25 seconds is evidence about 25
seconds. It says nothing about whether the ranking holds across a night,
which is the interval a nightly diff actually spans, and a column that
reports churn on every run teaches a reader to ignore the diff. If someone
measures the overnight case, tracking `position` becomes a defensible
change; until then this is the conservative default and the measurement
above is what a future decision should be argued from.

A row this project's parser could not recover a sku for (None) cannot be
matched across runs at all, so it is counted and reported separately rather
than silently folded into "added"/"removed", which would be wrong on its
face.
"""

import argparse
import json
import pathlib
import re
import sys
from typing import Dict, List, Optional, Tuple

from output_writer import UNIQUE_BY_SKU_MODES

# What is worth watching on a justjoin.it offer, and nothing else.
#
# EVERY NAME HERE MUST EXIST ON THE ROW CLASS, and that is not a style rule.
# In the repo this was ported from the tuple named 25 fields, 21 of which
# the row class did not have — so the diff compared nothing, and a listing
# whose pay went from 100 to 999 reported "0 changed" and exit 0. A price
# monitor that cannot see a price change is worse than none, because it
# reports success. `smoke_test.py` pins every name against the dataclass.
#
# A shop's fields are absent because a job offer has no stock or discount —
# porting them would be dead code that looks load-bearing (CLAUDE.md §4).
# What changes on a justjoin.it offer is its PAY, its SENIORITY, its
# SKILLS, where it can be done from and how you apply to it.
#
# `required_skills`, `languages` and `locations` are lists and compare
# element-wise, which is what you want: an offer adding a city or dropping
# a required skill is a real change.
#
# DELIBERATELY NOT TRACKED, each with its reason:
#
#   last_published_at   moves every time an employer bumps the posting,
#                       which they do to stay near the top. Tracking it
#                       would make a nightly diff report hundreds of
#                       "changes" that are the board working normally. It
#                       is still a COLUMN.
#   is_super_offer      the same: a paid placement starting or ending is a
#                       billing event, not a change to the job.
#   salary_monthly_min  a derived figure. It moves only when the quoted
#   salary_monthly_max  rate moves, which IS tracked, so tracking both
#                       would double-count one change.
#   position, page, scraped_at, data_source (which drives `source_changed`)
TRACKED_FIELDS = (
    # what the job is
    "title",
    "category",
    "experience_level",
    "is_active",
    # what it pays
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_unit",
    "salary_is_gross",
    "salary_contract_type",
    # on what terms
    "working_time",
    "workplace_type",
    "office_days",
    "remote_days",
    # where
    "city",
    "locations",
    "country_code",
    # what it wants
    "required_skills",
    "nice_to_have_skills",
    "languages",
    # who for, and how to apply
    "company_name",
    "company_size",
    "apply_method",
    "apply_url",
    "expires_at",
)

# The subset that only SOME of the three routes populate.
#
# This repo reads the same offer three ways and they publish different
# subsets, so diffing a run from one route against a run from another would
# report each of these as a change on every row and none of it would be
# about the job. Measured 2026-09-18 over 1,000 endpoint rows, 100
# server-rendered rows and 40 detail records:
#
#   description       detail only. Neither listing route carries a body.
#   company_url       detail only, 5 of 40.
#   company_size      detail only, 5 of 40.
#   country_code      detail only. The listing route omits it entirely.
#   is_active         detail only.
#   category          absent on `ssr`: that payload states an integer
#                     `categoryId` with no table to read it with.
#   languages         absent on `ssr` — 0 of 100 against 465 of 1,000.
#   language_levels   the same.
#   required_skill_levels  absent on `ssr`: it publishes skills as bare
#                     strings with no levels.
#   salary_monthly_*  absent on `ssr` unless the employer quoted a month:
#                     that payload carries no normalisation.
#
# When two rows disagree on `data_source`, a difference on any of these is
# reported as `source_changed` rather than as a change (§8: a difference
# that comes with a provenance difference says something about our own two
# snapshots, not about the site).
#
# The list that was here on arrival named nine fields from a DIFFERENT
# repo's row class — `equity_min`, `company_badges`, `industry` — none of
# which exists here. It is the same defect as the TRACKED_FIELDS one above,
# and `smoke_test.py` pins both against the dataclass now.
DETAIL_ONLY_FIELDS = (
    "description", "company_url", "company_size", "country_code",
    "is_active", "category", "languages", "language_levels",
    "required_skill_levels", "salary_monthly_min", "salary_monthly_max",
)
# Kept as an alias so a caller written against the family's older name still
# works; the two are the same tuple.
PROFILE_ONLY_FIELDS = DETAIL_ONLY_FIELDS


def _load(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _by_sku(products: List[dict]) -> Tuple[Dict[str, dict], int]:
    indexed = {}
    unmatchable = 0
    for p in products:
        sku = p.get("sku")
        if sku is None:
            unmatchable += 1
            continue
        # A run's own output can already hold a duplicate sku (two rows in the
        # same category, or a rerun of dedupe_by_sku's job on older output
        # written before it existed) — keep the first and count the rest as
        # unmatchable rather than letting one clobber the other silently.
        if sku in indexed:
            unmatchable += 1
            continue
        indexed[sku] = p
    return indexed, unmatchable


def diff_products(old: List[dict], new: List[dict]) -> dict:
    old_by_sku, old_unmatchable = _by_sku(old)
    new_by_sku, new_unmatchable = _by_sku(new)

    added = [new_by_sku[sku] for sku in new_by_sku.keys() - old_by_sku.keys()]
    removed = [old_by_sku[sku] for sku in old_by_sku.keys() - new_by_sku.keys()]

    changed, source_changed = [], []
    for sku in old_by_sku.keys() & new_by_sku.keys():
        before, after = old_by_sku[sku], new_by_sku[sku]
        field_changes = {
            field: {"old": before.get(field), "new": after.get(field)}
            for field in TRACKED_FIELDS
            if before.get(field) != after.get(field)
        }
        if not field_changes:
            continue

        # A row whose `data_source` differs between runs is not comparable on
        # the profile-only columns: a listing row leaves them null and a
        # profile row fills them, so every one of them would read as a change
        # and none of it would be about the business. Reporting it as a
        # change would be a false alarm about the site; the other columns
        # still compare fine.
        sources = (before.get("data_source"), after.get("data_source"))
        if sources[0] != sources[1] and any(f in field_changes
                                            for f in PROFILE_ONLY_FIELDS):
            profile_part = {f: v for f, v in field_changes.items()
                            if f in PROFILE_ONLY_FIELDS}
            other_part = {f: v for f, v in field_changes.items()
                          if f not in PROFILE_ONLY_FIELDS}
            source_changed.append({
                "sku": sku, "title": after.get("title"),
                "data_source": {"old": sources[0], "new": sources[1]},
                "changes": profile_part,
            })
            field_changes = other_part
            if not field_changes:
                continue

        changed.append({"sku": sku, "title": after.get("title"),
                        "changes": field_changes})

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "source_changed": source_changed,
        "unmatchable_old": old_unmatchable,
        "unmatchable_new": new_unmatchable,
    }


def _print_summary(result: dict) -> None:
    print(f"[+] {len(result['added'])} added, {len(result['removed'])} removed, "
          f"{len(result['changed'])} changed, "
          f"{len(result['source_changed'])} not comparable across run kinds.")
    for p in result["added"]:
        print(f"  + {p.get('sku')}  {p.get('title')}  "
              f"@ {p.get('company_name') or '?'}  "
              f"{p.get('compensation') or 'pay not stated'}")
    for p in result["removed"]:
        print(f"  - {p.get('sku')}  {p.get('title')}  "
              f"@ {p.get('company_name') or '?'}  "
              f"{p.get('compensation') or 'pay not stated'}")
    for c in result["changed"]:
        deltas = ", ".join(f"{f}: {v['old']!r} -> {v['new']!r}"
                           for f, v in c["changes"].items())
        print(f"  ~ {c['sku']}  {c['title']}  {deltas}")
    for c in result["source_changed"]:
        src = c["data_source"]
        deltas = ", ".join(f"{f}: {v['old']!r} -> {v['new']!r}"
                           for f, v in c["changes"].items())
        print(f"  ? {c['sku']}  {c['title']}  {deltas}  "
              f"[data_source {src['old']!r} -> {src['new']!r}: a listing row "
              f"leaves these columns null and a profile row fills them, so "
              f"this is not a change in the business]")
    unmatchable = result["unmatchable_old"] + result["unmatchable_new"]
    if unmatchable:
        print(f"[!] {unmatchable} row(s) across both files had no sku or a "
              f"duplicate sku, and could not be matched across runs.")


def _run_status(path: str) -> Tuple[Optional[str], Optional[dict]]:
    """Read the `<out>.meta.json` sidecar beside a run's JSON output.

    Returns (status, meta), or (None, None) when there is no sidecar — which
    is the normal case for output written before run metadata existed, or by
    `scraper_api_client.py` (single fetch, no pagination to cut short).
    """
    meta_path = re.sub(r"\.json$", "", path) + ".meta.json"
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None, None
    return meta.get("status"), meta


def _check_comparable(args) -> bool:
    """Refuse an assortment diff between runs that are not both complete.

    This is the failure mode the sidecar exists for: a run cut short on page
    3 of 10 is missing every product on pages 4-10, and diffing it against
    yesterday's full run reports all of them as `removed` — reading as "these
    products were delisted" when in fact they were simply never fetched.
    Prices of the SKUs both runs DID see are still comparable, which is why
    this is a refusal with a --force escape hatch rather than a hard error.
    """
    problems = []
    modes = {}
    for label, path in (("--old", args.old), ("--new", args.new)):
        status, meta = _run_status(path)
        if status is None:
            continue  # no sidecar: nothing to check, see _run_status
        mode = (meta or {}).get("mode")
        if mode:
            modes[label] = mode
        if mode and mode not in UNIQUE_BY_SKU_MODES:
            # This tool's whole premise is one row per `sku`, diffed on
            # price. A mode that produces many rows per sku would give a diff
            # whose every line is an artefact of two rows sharing an id, so
            # it is refused outright rather than answered. Both of this
            # repo's current modes qualify; the check is here so that adding
            # one that does not is caught rather than discovered.
            problems.append(
                f"{label} ({path}) is a {mode!r} run, which is not one row "
                f"per sku. This tool diffs one row per sku on price, so there "
                f"is nothing here it can compare.")
        if status != "complete":
            problems.append(
                f"{label} ({path}) was a {status!r} run — stopped after "
                f"{meta.get('pages_completed')} of {meta.get('pages_requested')} "
                f"page(s), reason {meta.get('stop_reason')!r}")
    if len(set(modes.values())) > 1:
        kinds = set(modes.values())
        # `facets` against either job mode is the severe case and deserves
        # its own sentence: the two share NO ids at all and not even a row
        # SCHEMA, so every row would be reported as both added and removed.
        # `listings` against `offer` is milder — same id space, different
        # columns — but still describes the mode change rather than the
        # board.
        if "facets" in kinds and kinds - {"facets"}:
            problems.append(
                f"the two runs are different POPULATIONS ({modes}). A "
                f"`facets` run holds the board's taxonomy keyed "
                f"`{{group}}:{{key}}` and a different row class entirely; a "
                f"job run holds offers keyed by UUID. The two sets have no "
                f"id in common, so every row would be reported as both "
                f"added and removed.")
        else:
            problems.append(
                f"the two runs are different modes ({modes}). A listing row "
                f"and a detail row carry different columns — a detail row "
                f"has the description body, the company URL and size and "
                f"the country; a listing row has none of them — so "
                f"`added`/`removed` would describe the mode change rather "
                f"than the board.")

    # THE SORT GUARD, and on this site it is load-bearing rather than
    # ceremonial.
    #
    # Because justjoin.it caps every query at 10,000 results however many it
    # matched, the ordering decides WHICH offers are in the file and not
    # merely the order they appear in. Measured 2026-09-18: the same query
    # under `sortBy=publishedAt` and under `sortBy=salary` returned first
    # pages whose 100 ids overlapped on FIVE. Diffing one against the other
    # would report ~95 added and ~95 removed, every line an artefact of the
    # query rather than a fact about the board (CLAUDE.md §21).
    #
    # The same argument applies to the FILTERS, and more obviously: a
    # `--category python` run and a `--category java` run are different
    # populations that happen to share a schema.
    sorts, orders, filters = {}, {}, {}
    for label, path in (("--old", args.old), ("--new", args.new)):
        _, meta = _run_status(path)
        meta = meta or {}
        if meta.get("sort"):
            sorts[label] = meta["sort"]
        if meta.get("order"):
            orders[label] = meta["order"]
        if meta.get("mode") == "listings":
            filters[label] = meta.get("filters") or {}
    if len(set(sorts.values())) > 1:
        problems.append(
            f"the two runs used different orderings ({sorts}). Every query "
            f"on this site is capped at 10,000 results, so the ordering "
            f"decides WHICH offers are in the file: two orderings of one "
            f"query overlapped on 5 of their first 100 ids when this was "
            f"measured. Every line of that diff would be an artefact of the "
            f"sort.")
    elif len(set(orders.values())) > 1:
        problems.append(
            f"the two runs used different sort directions ({orders}), which "
            f"on a capped result set means they hold opposite ends of the "
            f"same query.")
    if len(filters) == 2:
        old_f, new_f = filters["--old"], filters["--new"]
        if old_f != new_f:
            problems.append(
                f"the two runs used different filters (old={old_f or 'none'}, "
                f"new={new_f or 'none'}). They are different populations, so "
                f"`added` and `removed` would describe the filter change "
                f"rather than the board.")

    # And whether either run was CAPPED, which changes what `removed` means.
    for label, path in (("--old", args.old), ("--new", args.new)):
        _, meta = _run_status(path)
        meta = meta or {}
        enumerated = meta.get("offers_enumerated")
        fetched = meta.get("offers_fetched")
        if enumerated and fetched and fetched < enumerated:
            print(f"[i] {label} ({path}) fetched {fetched} of the "
                  f"{enumerated} offer(s) it enumerated — a complete run, "
                  f"and a slice. A `removed` line may mean the slice moved "
                  f"rather than that an offer was taken down. Use --pages "
                  f"{enumerated} on both runs to compare a full census.")
        if meta.get("capped_by_site"):
            # CLAUDE.md §21: the third meaning of `removed`. A capped run
            # holds a moving window over a board that is published to
            # continuously, so rows leave the window without anything
            # happening to the job.
            reachable = meta.get("reachable_max") or 10000
            site_total = meta.get("site_total")
            print(f"[i] {label} ({path}) was CAPPED: its query matched more "
                  f"than the {reachable:,} justjoin.it will serve"
                  + (f", out of {site_total:,} on the board" if site_total else "")
                  + ". A `removed` line may mean the row fell outside this "
                    "run's slice rather than that the offer was taken down. "
                    "A filtered run whose total_results is under the cap — "
                    "--category, --city or --experience — is what makes two "
                    "runs comparable as a census.")

    if not problems:
        return True

    # A generic headline, because the reasons below are no longer only about
    # completeness: a mode mismatch and a reviews run are refused too, and a
    # message naming the wrong reason sends the reader looking in the wrong
    # place.
    print("[!] Refusing to diff these two runs:")
    for line in problems:
        print(f"      {line}")
    print("    Re-run the incomplete side, or pass --force to compare anyway "
          "(added/removed will include jobs that were simply never "
          "fetched).")
    return False


def parse_args():
    p = argparse.ArgumentParser(
        description="Diff two justjoin-scraper JSON outputs by sku.")
    p.add_argument("--old", required=True, help="Earlier run's JSON output.")
    p.add_argument("--new", required=True, help="Later run's JSON output.")
    p.add_argument("--out", default=None,
                   help="Write the full diff as JSON to this path too.")
    p.add_argument("--fail-on-change", action="store_true",
                   help="Exit 1 if anything was added, removed or changed — "
                        "for a cron job that should only notify on a real diff.")
    p.add_argument("--force", action="store_true",
                   help="Diff even when a run's .meta.json says it was partial "
                        "or failed. Products never fetched by the short run will "
                        "appear as added/removed.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.force and not _check_comparable(args):
        return 2

    try:
        old = _load(args.old)
        new = _load(args.new)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[!] Could not read one of the input files: {e}")
        return 2

    result = diff_products(old, new)
    _print_summary(result)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[+] Full diff written to {args.out}")

    # `source_changed` is not a reason to fail: it means one row came from a
    # listing run and the other from a profile run, so the columns only a
    # profile fills differ. That says something about our own two snapshots
    # rather than about the business, and alerting on it would train whoever
    # reads the alert to ignore it.
    if args.fail_on_change and (result["added"] or result["removed"] or result["changed"]):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
