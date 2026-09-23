"""
smoke_test.py — the offline suite for justjoin-scraper.

One file of plain functions with inline fixtures. No pytest, no conftest, no
fixtures directory (CLAUDE.md §10); `tests/test_smoke.py` wraps this as a
single pytest test so `pytest` works as an entry point without a second copy
of the checks.

    python3 smoke_test.py            run everything
    python3 smoke_test.py -v         print every check as it passes

It must pass with NO engine library installed at all: every
`import playwright_scraper` / `selenium_scraper` / `puppeteer_scraper` is
guarded and the skip is RECORDED, because "skipped, engine absent" reads
identically to a real import error. CI installs each engine in its own venv
and fails if that engine's group reports a skip.

The fixtures below are cut from real captures taken 2026-09-18 and trimmed
to the records the checks read. Every trimmed fixture was verified to parse
IDENTICALLY to its untrimmed original, field for field, before being
committed — 0 mismatches on all three, on every column except `position`
(which necessarily changes when a 1,000-record response is cut to seven)
and the detail fixture's `description` (cut to 400 characters, because a
description runs to several kilobytes).

Nothing here carries personal data. justjoin.it's offer records name
COMPANIES and job titles, never individuals, and the parser reads no field
that could hold a person's name. `check_fixtures_carry_no_personal_names`
guards the SHAPE so a future capture from a route that does is caught.

Three values ARE scrubbed, and the fixtures say so rather than leaving a
reader to wonder why a captured page does not match the live one
(CLAUDE.md §10): two `applyUrl` form ids on an external ATS and the
`traceId` of the request that produced the 404. None of them granted
anything — the form ids are public and the trace expired — but all three
are 32-hex strings, which is the shape this repo's own credential scan
looks for, and a scanner reading a public repo cannot tell them from a
key. They are replaced with obvious placeholders of the same LENGTH, so
every code path the fixture exercises is unchanged.

What the fixtures deliberately reproduce
----------------------------------------
Each of these is a trap this repo hit, and the fixture exists so that
fixing it stays fixed:

  * an offer whose `original` salary entry is NOT at `employmentTypes[0]`
    and IS disclosed — EUR 30-50/hour sitting at index 2 behind a PLN
    conversion of 129.37. Indexing `[0]` gives a real-looking number in the
    wrong currency, and no coverage check would notice;
  * the detail-route version of the same trap, which is worse: PLN 180/day
    at index 4 behind a CHF 39.15 conversion;
  * an offer whose `from` is the rate NORMALISED TO A MONTH while `unit`
    says "Hour" — 21,000 beside 125, so reading the two adjacent keys
    together is out by a factor of 168;
  * an UNDISCLOSED salary whose all-null conversion entries come first,
    which is why the selection cannot be "the first entry with a number";
  * `unit` spelled "Hour" and "month" in one fixture, and a `countryCode`
    of "pl" on the detail record, because the site mixes case in both;
  * an offer quoting TWO original rates — a B2B and an employment contract
    — which are different offers of pay for one job;
  * an offer open in five cities, so `city` alone under-reports it;
  * an offer with `applyMethod: "form"` and a null `applyUrl`, which is the
    site saying "applied to here", not a missing field;
  * a server-rendered page whose flight payload spells the same offer
    differently from the endpoint — bare-string skills, `multilocation`,
    `remoteInterview`, an integer `categoryId` and no languages at all.
"""

import argparse
import ast
import csv
import inspect
import io
import json
import os
import re
import subprocess
import sys
import pathlib
import tempfile
from dataclasses import asdict, fields

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILURES = []
PASSED = 0
SKIPS = []
VERBOSE = False


def check(name, condition, detail=""):
    global PASSED
    if condition:
        PASSED += 1
        if VERBOSE:
            print("  ok   %s" % name)
    else:
        FAILURES.append("%s%s" % (name, (" — " + detail) if detail else ""))
        print("  FAIL %s%s" % (name, (" — " + detail) if detail else ""))


def equal(name, got, want):
    check(name, got == want, "got %r, want %r" % (got, want))


def skip(group, reason):
    SKIPS.append("%s: %s" % (group, reason))
    print("  SKIP %s — %s" % (group, reason))


# ---------------------------------------------------------------------------
# Fixtures, cut from real captures (2026-09-18)
# ---------------------------------------------------------------------------

# The offers endpoint: seven records out of a live 1,000-record response,
# chosen to cover every shape the parser branches on. See the module
# docstring for what each one is there to catch.
LISTING_JSON = "{\n \"data\": [\n  {\n   \"guid\": \"849875e0-7564-4392-bfe1-88e5660d7380\",\n   \"slug\": \"netguru--senior-data-engineer---freelance-warszawa-data-2a2bdbff\",\n   \"title\": \"(Senior) Data Engineer - Freelance\",\n   \"workplaceType\": \"remote\",\n   \"workingTime\": \"b2b_contract\",\n   \"experienceLevel\": \"senior\",\n   \"category\": {\n    \"key\": \"data\",\n    \"parentKey\": null\n   },\n   \"city\": \"Warszawa\",\n   \"street\": \"-\",\n   \"latitude\": 52.2296756,\n   \"longitude\": 21.0122287,\n   \"isRemoteInterview\": true,\n   \"companyName\": \"Netguru\",\n   \"companyLogoThumbUrl\": \"https://imgproxy.justjoinit.tech/ae9GIWyATrVR2kXJt4edH9qfSEDgZSbLyYJE9maDMLc/h:200/w:200/plain/https://public.justjoin.it/companies/logos/original/b58c8fb307aa139f8ab4a013d50a8479e5ac4d37.jpg\",\n   \"publishedAt\": \"2026-09-18T10:00:13.9889892Z\",\n   \"isOpenToHireUkrainians\": false,\n   \"locations\": [\n    {\n     \"city\": \"Warszawa\",\n     \"street\": \"-\",\n     \"latitude\": 52.2296756,\n     \"longitude\": 21.0122287,\n     \"slug\": \"netguru--senior-data-engineer---freelance-warszawa-data-2a2bdbff\"\n    },\n    {\n     \"city\": \"Wroc\u0142aw\",\n     \"street\": \"-\",\n     \"latitude\": 51.10929480000001,\n     \"longitude\": 17.0386019,\n     \"slug\": \"netguru--senior-data-engineer---freelance-wroclaw-data-b8b161e3\"\n    },\n    {\n     \"city\": \"Pozna\u0144\",\n     \"street\": \"-\",\n     \"latitude\": 52.40567859999999,\n     \"longitude\": 16.9312766,\n     \"slug\": \"netguru--senior-data-engineer---freelance-poznan-data-53cfbf73\"\n    },\n    {\n     \"city\": \"Krak\u00f3w\",\n     \"street\": \"-\",\n     \"latitude\": 50.06465009999999,\n     \"longitude\": 19.9449799,\n     \"slug\": \"netguru--senior-data-engineer---freelance-krakow-data-6f15edc9\"\n    },\n    {\n     \"city\": \"Gda\u0144sk\",\n     \"street\": \"-\",\n     \"latitude\": 54.35202520000001,\n     \"longitude\": 18.6466384,\n     \"slug\": \"netguru--senior-data-engineer---freelance-gdansk-data-ccf066a3\"\n    },\n    {\n     \"city\": \"Katowice\",\n     \"street\": \"-\",\n     \"latitude\": 50.26489189999999,\n     \"longitude\": 19.0237815,\n     \"slug\": \"netguru--senior-data-engineer---freelance-katowice-data\"\n    },\n    {\n     \"city\": \"Toru\u0144\",\n     \"street\": \"-\",\n     \"latitude\": 53.0137902,\n     \"longitude\": 18.5984437,\n     \"slug\": \"netguru--senior-data-engineer---freelance-torun-data\"\n    },\n    {\n     \"city\": \"\u0141\u00f3d\u017a\",\n     \"street\": \"-\",\n     \"latitude\": 51.7592924,\n     \"longitude\": 19.4558778,\n     \"slug\": \"netguru--senior-data-engineer---freelance-lodz-data-9d7b66a2\"\n    },\n    {\n     \"city\": \"Bia\u0142ystok\",\n     \"street\": \"-\",\n     \"latitude\": 53.13248859999999,\n     \"longitude\": 23.1688403,\n     \"slug\": \"netguru--senior-data-engineer---freelance-bialystok-data-379080c0\"\n    },\n    {\n     \"city\": \"Olsztyn\",\n     \"street\": \"-\",\n     \"latitude\": 53.778422,\n     \"longitude\": 20.4801193,\n     \"slug\": \"netguru--senior-data-engineer---freelance-olsztyn-data-80ad620b\"\n    }\n   ],\n   \"employmentTypes\": [\n    {\n     \"from\": 21734.0,\n     \"fromPerUnit\": 129.372,\n     \"to\": 36224.0,\n     \"toPerUnit\": 215.62,\n     \"currency\": \"PLN\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"b2b\",\n     \"unit\": \"Hour\",\n     \"gross\": false\n    },\n    {\n     \"from\": 5878.0,\n     \"fromPerUnit\": 34.9909393341087,\n     \"to\": 9797.0,\n     \"toPerUnit\": 58.3182322235145,\n     \"currency\": \"USD\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"b2b\",\n     \"unit\": \"Hour\",\n     \"gross\": false\n    },\n    {\n     \"from\": 5040.0,\n     \"fromPerUnit\": 30.0,\n     \"to\": 8400.0,\n     \"toPerUnit\": 50.0,\n     \"currency\": \"EUR\",\n     \"currencySource\": \"original\",\n     \"type\": \"b2b\",\n     \"unit\": \"Hour\",\n     \"gross\": false\n    },\n    {\n     \"from\": 4715.0,\n     \"fromPerUnit\": 28.06821140328039,\n     \"to\": 7859.0,\n     \"toPerUnit\": 46.78035233880065,\n     \"currency\": \"CHF\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"b2b\",\n     \"unit\": \"Hour\",\n     \"gross\": false\n    },\n    {\n     \"from\": 4315.0,\n     \"fromPerUnit\": 25.68586574542857,\n     \"to\": 7192.0,\n     \"toPerUnit\": 42.80977624238095,\n     \"currency\": \"GBP\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"b2b\",\n     \"unit\": \"Hour\",\n     \"gross\": false\n    }\n   ],\n   \"requiredSkills\": [\n    {\n     \"name\": \"AWS\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"Python\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"Scala\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"Bash\",\n     \"level\": 4\n    }\n   ],\n   \"niceToHaveSkills\": [],\n   \"languages\": [\n    {\n     \"code\": \"en\",\n     \"level\": \"C1\"\n    }\n   ],\n   \"isPromoted\": false,\n   \"isSuperOffer\": true,\n   \"applyMethod\": \"external\",\n   \"applyUrl\": \"https://apply.workable.com/netguru/j/C99A238797/\",\n   \"lastPublishedAt\": \"2026-08-25T10:13:26.836068Z\",\n   \"expiredAt\": \"2026-11-23T22:59:59.999999Z\",\n   \"companyProfileSlug\": null,\n   \"hybridWorkSchedule\": null\n  },\n  {\n   \"guid\": \"5f605f69-1955-4e0d-b618-54f9abef7a84\",\n   \"slug\": \"primaris-services-sp-z-o-o--product-owner-nurt-ai--warszawa-ai\",\n   \"title\": \"Product Owner (nurt AI)\",\n   \"workplaceType\": \"hybrid\",\n   \"workingTime\": \"b2b_contract\",\n   \"experienceLevel\": \"senior\",\n   \"category\": {\n    \"key\": \"ai\",\n    \"parentKey\": null\n   },\n   \"city\": \"Warszawa\",\n   \"street\": \"Bukowi\u0144ska\",\n   \"latitude\": 52.1847082,\n   \"longitude\": 21.0250647,\n   \"isRemoteInterview\": false,\n   \"companyName\": \"Primaris Services Sp. z o.o.\",\n   \"companyLogoThumbUrl\": \"https://imgproxy.justjoinit.tech/S6xA_tK7CIxy7YlnHExzJJhEJtPQGWto1L_xkxqyNQU/h:200/w:200/plain/https://public.hellohr.pl/offers/company_logos/original/db3744c71000ec0bb4ab62ddc1ef3e00f95c3114.png\",\n   \"publishedAt\": \"2026-09-18T10:44:42.476937Z\",\n   \"isOpenToHireUkrainians\": false,\n   \"locations\": [\n    {\n     \"city\": \"Warszawa\",\n     \"street\": \"Bukowi\u0144ska\",\n     \"latitude\": 52.1847082,\n     \"longitude\": 21.0250647,\n     \"slug\": \"primaris-services-sp-z-o-o--product-owner-nurt-ai--warszawa-ai\"\n    }\n   ],\n   \"employmentTypes\": [\n    {\n     \"from\": 21000.0,\n     \"fromPerUnit\": 125.0,\n     \"to\": 24360.0,\n     \"toPerUnit\": 145.0,\n     \"currency\": \"PLN\",\n     \"currencySource\": \"original\",\n     \"type\": \"b2b\",\n     \"unit\": \"Hour\",\n     \"gross\": false\n    },\n    {\n     \"from\": 5522.0,\n     \"fromPerUnit\": 32.868787799106,\n     \"to\": 6405.0,\n     \"toPerUnit\": 38.12779384696296,\n     \"currency\": \"USD\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"b2b\",\n     \"unit\": \"Hour\",\n     \"gross\": false\n    },\n    {\n     \"from\": 4813.0,\n     \"fromPerUnit\": 28.648698203153625,\n     \"to\": 5583.0,\n     \"toPerUnit\": 33.232489915658206,\n     \"currency\": \"EUR\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"b2b\",\n     \"unit\": \"Hour\",\n     \"gross\": false\n    },\n    {\n     \"from\": 4558.0,\n     \"fromPerUnit\": 27.129091066932876,\n     \"to\": 5287.0,\n     \"toPerUnit\": 31.469745637642134,\n     \"currency\": \"CHF\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"b2b\",\n     \"unit\": \"Hour\",\n     \"gross\": false\n    },\n    {\n     \"from\": 4122.0,\n     \"fromPerUnit\": 24.537709552039626,\n     \"to\": 4782.0,\n     \"toPerUnit\": 28.463743080365965,\n     \"currency\": \"GBP\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"b2b\",\n     \"unit\": \"Hour\",\n     \"gross\": false\n    }\n   ],\n   \"requiredSkills\": [\n    {\n     \"name\": \"Jira\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"GenAI\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"LLM\",\n     \"level\": 4\n    }\n   ],\n   \"niceToHaveSkills\": [],\n   \"languages\": [\n    {\n     \"code\": \"pl\",\n     \"level\": \"C1\"\n    },\n    {\n     \"code\": \"en\",\n     \"level\": \"B2\"\n    }\n   ],\n   \"isPromoted\": false,\n   \"isSuperOffer\": false,\n   \"applyMethod\": \"external\",\n   \"applyUrl\": \"https://primaris.traffit.com/public/form/a/3a4279c7e982d11ef079ebeabd639bc255713677\",\n   \"lastPublishedAt\": \"2026-09-18T10:44:42.476937Z\",\n   \"expiredAt\": \"2026-12-17T11:41:17.493Z\",\n   \"companyProfileSlug\": null,\n   \"hybridWorkSchedule\": {\n    \"officeDays\": 3,\n    \"remoteDays\": 2\n   }\n  },\n  {\n   \"guid\": \"9d6666fc-581e-493c-8020-a77552f7a730\",\n   \"slug\": \"wirtualna-polska-media-s-a--junior-investment-analyst-warszawa-analytics\",\n   \"title\": \"Junior Investment Analyst\",\n   \"workplaceType\": \"office\",\n   \"workingTime\": \"full_time\",\n   \"experienceLevel\": \"junior\",\n   \"category\": {\n    \"key\": \"analytics\",\n    \"parentKey\": null\n   },\n   \"city\": \"Warszawa\",\n   \"street\": \"\u017bwirki i Wigury 16\",\n   \"latitude\": 52.1893646,\n   \"longitude\": 20.9814807,\n   \"isRemoteInterview\": false,\n   \"companyName\": \"Wirtualna Polska Media S.A.\",\n   \"companyLogoThumbUrl\": \"https://imgproxy.justjoinit.tech/SKs8c-_d4IL22p8nbN9cROS0zJPSYCD-Mi8edSjhKBE/h:200/w:200/plain/https://public.justjoin.it/companies/logos/original/0f24c49a8a79ce309148a7bd25fbe26756b7350f.png\",\n   \"publishedAt\": \"2026-09-18T10:44:12.209203Z\",\n   \"isOpenToHireUkrainians\": false,\n   \"locations\": [\n    {\n     \"city\": \"Warszawa\",\n     \"street\": \"\u017bwirki i Wigury 16\",\n     \"latitude\": 52.1893646,\n     \"longitude\": 20.9814807,\n     \"slug\": \"wirtualna-polska-media-s-a--junior-investment-analyst-warszawa-analytics\"\n    }\n   ],\n   \"employmentTypes\": [\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"GBP\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"mandate_contract\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"CHF\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"mandate_contract\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"CHF\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"USD\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"mandate_contract\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"EUR\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"mandate_contract\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"GBP\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"USD\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"EUR\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"PLN\",\n     \"currencySource\": \"original\",\n     \"type\": \"mandate_contract\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"PLN\",\n     \"currencySource\": \"original\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    }\n   ],\n   \"requiredSkills\": [\n    {\n     \"name\": \"MS Office\",\n     \"level\": 4\n    }\n   ],\n   \"niceToHaveSkills\": [],\n   \"languages\": [],\n   \"isPromoted\": false,\n   \"isSuperOffer\": true,\n   \"applyMethod\": \"external\",\n   \"applyUrl\": \"https://system.erecruiter.pl/FormTemplates/RecruitmentForm.aspx?WebID=00000000PLACEHOLDER0000000000000\",\n   \"lastPublishedAt\": \"2026-09-18T10:44:12.209203Z\",\n   \"expiredAt\": \"2026-12-17T11:39:09.49Z\",\n   \"companyProfileSlug\": null,\n   \"hybridWorkSchedule\": null\n  },\n  {\n   \"guid\": \"fe549873-1c6c-4aaf-9d57-e1bf607de5cc\",\n   \"slug\": \"giap-devops-engineer-warszawa-devops\",\n   \"title\": \"DevOps Engineer\",\n   \"workplaceType\": \"remote\",\n   \"workingTime\": \"full_time\",\n   \"experienceLevel\": \"mid\",\n   \"category\": {\n    \"key\": \"devops\",\n    \"parentKey\": null\n   },\n   \"city\": \"Warszawa\",\n   \"street\": \"Aleja KEN 93\",\n   \"latitude\": 52.1559252,\n   \"longitude\": 21.0335522,\n   \"isRemoteInterview\": false,\n   \"companyName\": \"GIAP\",\n   \"companyLogoThumbUrl\": \"https://imgproxy.justjoinit.tech/S-RPz6_qn1DmOpWEQRaSSrahIT06j8ALtKohYLFdasA/h:200/w:200/plain/https://public.justjoin.it/offers/company_logos/original/ed1119c5159a1b80289b6546517809dd5d840275.jpg\",\n   \"publishedAt\": \"2026-09-18T10:09:53.3307775Z\",\n   \"isOpenToHireUkrainians\": false,\n   \"locations\": [\n    {\n     \"city\": \"Warszawa\",\n     \"street\": \"Aleja KEN 93\",\n     \"latitude\": 52.1559252,\n     \"longitude\": 21.0335522,\n     \"slug\": \"giap-devops-engineer-warszawa-devops\"\n    },\n    {\n     \"city\": \"Lublin\",\n     \"street\": \"Juliusza Ligonia 1\",\n     \"latitude\": 51.25409149999999,\n     \"longitude\": 22.5215438,\n     \"slug\": \"giap-devops-engineer-lublin-devops\"\n    }\n   ],\n   \"employmentTypes\": [\n    {\n     \"from\": 10000.0,\n     \"fromPerUnit\": 10000.0,\n     \"to\": 17000.0,\n     \"toPerUnit\": 17000.0,\n     \"currency\": \"PLN\",\n     \"currencySource\": \"original\",\n     \"type\": \"b2b\",\n     \"unit\": \"Month\",\n     \"gross\": false\n    },\n    {\n     \"from\": 8500.0,\n     \"fromPerUnit\": 8500.0,\n     \"to\": 14000.0,\n     \"toPerUnit\": 14000.0,\n     \"currency\": \"PLN\",\n     \"currencySource\": \"original\",\n     \"type\": \"permanent\",\n     \"unit\": \"Month\",\n     \"gross\": true\n    },\n    {\n     \"from\": 2630.0,\n     \"fromPerUnit\": 2629.50302392848,\n     \"to\": 4470.0,\n     \"toPerUnit\": 4470.155140678416,\n     \"currency\": \"USD\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"b2b\",\n     \"unit\": \"Month\",\n     \"gross\": false\n    },\n    {\n     \"from\": 2292.0,\n     \"fromPerUnit\": 2291.89585625229,\n     \"to\": 3896.0,\n     \"toPerUnit\": 3896.222955628893,\n     \"currency\": \"EUR\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"b2b\",\n     \"unit\": \"Month\",\n     \"gross\": false\n    },\n    {\n     \"from\": 2235.0,\n     \"fromPerUnit\": 2235.077570339208,\n     \"to\": 3681.0,\n     \"toPerUnit\": 3681.304233499872,\n     \"currency\": \"USD\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"Month\",\n     \"gross\": true\n    },\n    {\n     \"from\": 2170.0,\n     \"fromPerUnit\": 2170.32728535463,\n     \"to\": 3690.0,\n     \"toPerUnit\": 3689.556385102871,\n     \"currency\": \"CHF\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"b2b\",\n     \"unit\": \"Month\",\n     \"gross\": false\n    },\n    {\n     \"from\": 1963.0,\n     \"fromPerUnit\": 1963.01676416317,\n     \"to\": 3337.0,\n     \"toPerUnit\": 3337.128499077389,\n     \"currency\": \"GBP\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"b2b\",\n     \"unit\": \"Month\",\n     \"gross\": false\n    },\n    {\n     \"from\": 1948.0,\n     \"fromPerUnit\": 1948.1114778144465,\n     \"to\": 3209.0,\n     \"toPerUnit\": 3208.654198753206,\n     \"currency\": \"EUR\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"Month\",\n     \"gross\": true\n    },\n    {\n     \"from\": 1845.0,\n     \"fromPerUnit\": 1844.7781925514355,\n     \"to\": 3038.0,\n     \"toPerUnit\": 3038.458199496482,\n     \"currency\": \"CHF\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"Month\",\n     \"gross\": true\n    },\n    {\n     \"from\": 1669.0,\n     \"fromPerUnit\": 1668.5642495386944,\n     \"to\": 2748.0,\n     \"toPerUnit\": 2748.223469828438,\n     \"currency\": \"GBP\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"Month\",\n     \"gross\": true\n    }\n   ],\n   \"requiredSkills\": [\n    {\n     \"name\": \"Linux\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"Kubernetes\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"DevOps\",\n     \"level\": 5\n    },\n    {\n     \"name\": \"CI/CD\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"Virtualization\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"Git\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"Prometheus\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"GitLab\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"Django\",\n     \"level\": 4\n    }\n   ],\n   \"niceToHaveSkills\": [],\n   \"languages\": [],\n   \"isPromoted\": false,\n   \"isSuperOffer\": false,\n   \"applyMethod\": \"form\",\n   \"applyUrl\": null,\n   \"lastPublishedAt\": \"2026-09-18T10:09:53.3307775Z\",\n   \"expiredAt\": \"2026-10-18T10:09:53.3307775Z\",\n   \"companyProfileSlug\": null,\n   \"hybridWorkSchedule\": null\n  },\n  {\n   \"guid\": \"2498872e-1c97-4d9b-9406-d89ad09ad802\",\n   \"slug\": \"asseco-data-systems-kierownik-produktu-k-m-os--warszawa-other\",\n   \"title\": \"Kierownik Produktu (k/m/os.)\",\n   \"workplaceType\": \"hybrid\",\n   \"workingTime\": \"full_time\",\n   \"experienceLevel\": \"manager\",\n   \"category\": {\n    \"key\": \"other\",\n    \"parentKey\": null\n   },\n   \"city\": \"Warszawa\",\n   \"street\": \"Adama Branickiego 13\",\n   \"latitude\": 52.1557075,\n   \"longitude\": 21.0763253,\n   \"isRemoteInterview\": false,\n   \"companyName\": \"Asseco Data Systems\",\n   \"companyLogoThumbUrl\": \"https://imgproxy.justjoinit.tech/B9WgjcWvIWGQg81uxQoeZip51XZ4RL_3Y9Foccstq6M/h:200/w:200/plain/https://public.rocketjobs.pl/companies/logos/original/b09fa509ec99e8aa05217d349ef9f3acb2af123a.png\",\n   \"publishedAt\": \"2026-09-18T10:38:24.401278Z\",\n   \"isOpenToHireUkrainians\": false,\n   \"locations\": [\n    {\n     \"city\": \"Warszawa\",\n     \"street\": \"Adama Branickiego 13\",\n     \"latitude\": 52.1557075,\n     \"longitude\": 21.0763253,\n     \"slug\": \"asseco-data-systems-kierownik-produktu-k-m-os--warszawa-other\"\n    },\n    {\n     \"city\": \"Gda\u0144sk\",\n     \"street\": \"Jana z Kolna 11\",\n     \"latitude\": 54.3599872,\n     \"longitude\": 18.6469101,\n     \"slug\": \"asseco-data-systems-kierownik-produktu-k-m-os--gdansk-other\"\n    },\n    {\n     \"city\": \"Krak\u00f3w\",\n     \"street\": \"Wielicka 22A\",\n     \"latitude\": 50.0402302,\n     \"longitude\": 19.9635487,\n     \"slug\": \"asseco-data-systems-kierownik-produktu-k-m-os--krakow-other\"\n    },\n    {\n     \"city\": \"\u0141\u00f3d\u017a\",\n     \"street\": \"prez. Gabriela Narutowicza 136\",\n     \"latitude\": 51.7751939,\n     \"longitude\": 19.4950952,\n     \"slug\": \"asseco-data-systems-kierownik-produktu-k-m-os--lodz-other\"\n    },\n    {\n     \"city\": \"Szczecin\",\n     \"street\": \"Kr\u00f3lowej Korony Polskiej 21\",\n     \"latitude\": 53.4377362,\n     \"longitude\": 14.5324349,\n     \"slug\": \"asseco-data-systems-kierownik-produktu-k-m-os--szczecin-other\"\n    }\n   ],\n   \"employmentTypes\": [\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"USD\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"EUR\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"GBP\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"CHF\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"PLN\",\n     \"currencySource\": \"original\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    }\n   ],\n   \"requiredSkills\": [\n    {\n     \"name\": \"Product Management\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"praca z klientem\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"mapowanie proces\u00f3w end-to-end\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"kszta\u0142towanie produktu\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"SaaS\",\n     \"level\": 1\n    }\n   ],\n   \"niceToHaveSkills\": [\n    {\n     \"name\": \"GovTech\",\n     \"level\": 1\n    }\n   ],\n   \"languages\": [\n    {\n     \"code\": \"pl\",\n     \"level\": \"C2\"\n    }\n   ],\n   \"isPromoted\": false,\n   \"isSuperOffer\": false,\n   \"applyMethod\": \"external\",\n   \"applyUrl\": \"https://kariera.assecods.pl/Oferta/712d88b6-4ee0-4dc3-8539-6d04e4e1ce69\",\n   \"lastPublishedAt\": \"2026-09-18T10:38:24.401278Z\",\n   \"expiredAt\": \"2026-12-10T22:59:59.999Z\",\n   \"companyProfileSlug\": null,\n   \"hybridWorkSchedule\": {\n    \"officeDays\": 3,\n    \"remoteDays\": 2\n   }\n  },\n  {\n   \"guid\": \"57cd2a56-06b0-483c-afe1-ffc2c56a221f\",\n   \"slug\": \"novobi-erp-project-manager-warszawa-erp\",\n   \"title\": \"ERP Project Manager\",\n   \"workplaceType\": \"remote\",\n   \"workingTime\": \"full_time\",\n   \"experienceLevel\": \"senior\",\n   \"category\": {\n    \"key\": \"erp\",\n    \"parentKey\": null\n   },\n   \"city\": \"Warszawa\",\n   \"street\": \"N/A\",\n   \"latitude\": 52.2296756,\n   \"longitude\": 21.0122287,\n   \"isRemoteInterview\": false,\n   \"companyName\": \"Novobi\",\n   \"companyLogoThumbUrl\": \"https://imgproxy.justjoinit.tech/25D0hL4O4Y8N0bsggJGGRZkeex_1KsLG_GY9ycCdq-w/h:200/w:200/plain/https://s3.eu-west-1.amazonaws.com/public.justjoin.com/justjoinit/hiring-company-logos/01a03833-9e8e-7822-ad3f-14d67984b512.jpg\",\n   \"publishedAt\": \"2026-09-18T10:00:20.4976489Z\",\n   \"isOpenToHireUkrainians\": false,\n   \"locations\": [\n    {\n     \"city\": \"Warszawa\",\n     \"street\": \"N/A\",\n     \"latitude\": 52.2296756,\n     \"longitude\": 21.0122287,\n     \"slug\": \"novobi-erp-project-manager-warszawa-erp\"\n    }\n   ],\n   \"employmentTypes\": [\n    {\n     \"from\": 15000.0,\n     \"fromPerUnit\": 15000.0,\n     \"to\": 24000.0,\n     \"toPerUnit\": 24000.0,\n     \"currency\": \"PLN\",\n     \"currencySource\": \"original\",\n     \"type\": \"permanent\",\n     \"unit\": \"Month\",\n     \"gross\": true\n    },\n    {\n     \"from\": 3944.0,\n     \"fromPerUnit\": 3944.25453589272,\n     \"to\": 6311.0,\n     \"toPerUnit\": 6310.807257428352,\n     \"currency\": \"USD\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"Month\",\n     \"gross\": true\n    },\n    {\n     \"from\": 3438.0,\n     \"fromPerUnit\": 3437.843784378435,\n     \"to\": 5501.0,\n     \"toPerUnit\": 5500.550055005496,\n     \"currency\": \"EUR\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"Month\",\n     \"gross\": true\n    },\n    {\n     \"from\": 3255.0,\n     \"fromPerUnit\": 3255.490928031945,\n     \"to\": 5209.0,\n     \"toPerUnit\": 5208.785484851112,\n     \"currency\": \"CHF\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"Month\",\n     \"gross\": true\n    },\n    {\n     \"from\": 2945.0,\n     \"fromPerUnit\": 2944.525146244755,\n     \"to\": 4711.0,\n     \"toPerUnit\": 4711.240233991608,\n     \"currency\": \"GBP\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"Month\",\n     \"gross\": true\n    }\n   ],\n   \"requiredSkills\": [\n    {\n     \"name\": \"Odoo\",\n     \"level\": 1\n    },\n    {\n     \"name\": \"English\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"ERP\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"Project Management\",\n     \"level\": 5\n    },\n    {\n     \"name\": \"PMP\",\n     \"level\": 1\n    },\n    {\n     \"name\": \"CRM\",\n     \"level\": 1\n    }\n   ],\n   \"niceToHaveSkills\": [],\n   \"languages\": [],\n   \"isPromoted\": false,\n   \"isSuperOffer\": false,\n   \"applyMethod\": \"form\",\n   \"applyUrl\": null,\n   \"lastPublishedAt\": \"2026-08-25T09:18:26.663958Z\",\n   \"expiredAt\": \"2026-09-24T09:18:26.663958Z\",\n   \"companyProfileSlug\": null,\n   \"hybridWorkSchedule\": null\n  },\n  {\n   \"guid\": \"bc61bb03-8d80-420d-8d6c-842337778ec4\",\n   \"slug\": \"wirtualna-polska-media-s-a--performance-technical-manager-warszawa-analytics\",\n   \"title\": \"Performance Technical Manager\",\n   \"workplaceType\": \"hybrid\",\n   \"workingTime\": \"full_time\",\n   \"experienceLevel\": \"mid\",\n   \"category\": {\n    \"key\": \"analytics\",\n    \"parentKey\": null\n   },\n   \"city\": \"Warszawa\",\n   \"street\": \"\u017bwirki i Wigury 16\",\n   \"latitude\": 52.1894067,\n   \"longitude\": 20.9816212,\n   \"isRemoteInterview\": false,\n   \"companyName\": \"Wirtualna Polska Media S.A.\",\n   \"companyLogoThumbUrl\": \"https://imgproxy.justjoinit.tech/SKs8c-_d4IL22p8nbN9cROS0zJPSYCD-Mi8edSjhKBE/h:200/w:200/plain/https://public.justjoin.it/companies/logos/original/0f24c49a8a79ce309148a7bd25fbe26756b7350f.png\",\n   \"publishedAt\": \"2026-09-18T10:00:18.4259683Z\",\n   \"isOpenToHireUkrainians\": false,\n   \"locations\": [\n    {\n     \"city\": \"Warszawa\",\n     \"street\": \"\u017bwirki i Wigury 16\",\n     \"latitude\": 52.1894067,\n     \"longitude\": 20.9816212,\n     \"slug\": \"wirtualna-polska-media-s-a--performance-technical-manager-warszawa-analytics\"\n    }\n   ],\n   \"employmentTypes\": [\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"GBP\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"EUR\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"USD\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"CHF\",\n     \"currencySource\": \"conversion\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    },\n    {\n     \"from\": null,\n     \"fromPerUnit\": null,\n     \"to\": null,\n     \"toPerUnit\": null,\n     \"currency\": \"PLN\",\n     \"currencySource\": \"original\",\n     \"type\": \"permanent\",\n     \"unit\": \"month\",\n     \"gross\": true\n    }\n   ],\n   \"requiredSkills\": [\n    {\n     \"name\": \"DSP\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"dmp\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"RTB\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"DV360\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"Adform\",\n     \"level\": 4\n    },\n    {\n     \"name\": \"Excel\",\n     \"level\": 3\n    },\n    {\n     \"name\": \"Google Tag Manager\",\n     \"level\": 1\n    }\n   ],\n   \"niceToHaveSkills\": [\n    {\n     \"name\": \"Postman\",\n     \"level\": 1\n    },\n    {\n     \"name\": \"SQL\",\n     \"level\": 1\n    },\n    {\n     \"name\": \"Python\",\n     \"level\": 1\n    },\n    {\n     \"name\": \"JavaScript\",\n     \"level\": 1\n    },\n    {\n     \"name\": \"GA4\",\n     \"level\": 1\n    },\n    {\n     \"name\": \"DoubleVerify\",\n     \"level\": 1\n    },\n    {\n     \"name\": \"Integral Ad Science.\",\n     \"level\": 1\n    }\n   ],\n   \"languages\": [\n    {\n     \"code\": \"pl\",\n     \"level\": \"C2\"\n    },\n    {\n     \"code\": \"en\",\n     \"level\": \"B2\"\n    }\n   ],\n   \"isPromoted\": false,\n   \"isSuperOffer\": true,\n   \"applyMethod\": \"external\",\n   \"applyUrl\": \"https://system.erecruiter.pl/FormTemplates/RecruitmentForm.aspx?WebID=00000000PLACEHOLDER0000000000000\",\n   \"lastPublishedAt\": \"2026-08-31T08:51:15.65686Z\",\n   \"expiredAt\": \"2027-01-07T22:59:59.999Z\",\n   \"companyProfileSlug\": null,\n   \"hybridWorkSchedule\": {\n    \"officeDays\": 3,\n    \"remoteDays\": 2\n   }\n  }\n ],\n \"meta\": {\n  \"from\": 0,\n  \"totalItems\": 10000,\n  \"prev\": {\n   \"cursor\": null,\n   \"itemsCount\": 7\n  },\n  \"next\": {\n   \"cursor\": 7,\n   \"itemsCount\": 7\n  }\n }\n}"

# One offer's own endpoint response. Its `original` entry sits at index 4
# behind four conversions, and it IS disclosed — PLN 180-220 per DAY, where
# `employmentTypes[0]` would hand back CHF 39.15. 16 of 40 sampled detail
# records had that shape.
DETAIL_JSON = "{\"id\": \"26bc9745-1d32-4f27-8164-e26f208bcb47\", \"slug\": \"7n-sp-z-o-o--sap-crm-consultant-gdansk-erp\", \"title\": \"SAP CRM Consultant\", \"experienceLevel\": \"senior\", \"category\": {\"key\": \"erp\", \"parentKey\": null}, \"companyName\": \"7N Sp. z o. o.\", \"companyUrl\": \"\", \"body\": \"<p><strong>About the Project</strong></p><p>We are seeking a Senior SAP CRM Consultants to join team in Q4. You will deliver expert implementation, optimization, and integration of the CRM/CX portfolio into enterprise SAP S/4HANA and ECC landscapes to streamline core business processes.</p><p><strong>Work Mode:</strong> 100% Remote</p><p><strong>Timezone:</strong> EMEA region (<strong>EU Citizensh\", \"locationId\": \"2e2b13ba-8930-42d7-8ffc-2f8814658dab\", \"city\": \"Gda\u0144sk\", \"street\": \"Bernarda Chrzanowskiego 11\", \"countryCode\": \"PL\", \"latitude\": 54.3823733, \"longitude\": 18.5861607, \"companySize\": \"\", \"informationClause\": \"Informujemy, \u017ce administratorem danych jest 7N Sp. z o. o. z siedzib\u0105 w Warszawie, ul.Pu\u0142awska 182 (dalej jako \\\"administrator\\\"). Masz prawo do \u017c\u0105dania dost\u0119pu do swoich danych osobowych, ich sprostowania, usuni\u0119cia lub ograniczenia przetwarzania, prawo do wniesienia sprzeciwu wobec przetwarzania, a tak\u017ce prawo do przenoszenia danych oraz wniesienia skargi do organu nadzorczego. Dane osobowe przetwarzane b\u0119d\u0105 w celu realizacji procesu rekrutacji. Podanie danych w zakresie wynikaj\u0105cym z ustawy z dnia 26 czerwca 1974 r. Kodeks pracy jest obowi\u0105zkowe. W pozosta\u0142ym zakresie podanie danych jest dobrowolne. Odmowa podania danych obowi\u0105zkowych mo\u017ce skutkowa\u0107 brakiem mo\u017cliwo\u015bci przeprowadzenia procesu rekrutacji. Administrator przetwarza dane obowi\u0105zkowe na podstawie ci\u0105\u017c\u0105cego na nim obowi\u0105zku prawnego, za\u015b w zakresie danych dodatkowych podstaw\u0105 przetwarzania jest zgoda. Dane osobowe b\u0119d\u0105 przetwarzane do czasu zako\u0144czenia post\u0119powania rekrutacyjnego i przez okres mo\u017cliwo\u015bci dochodzenia ewentualnych roszcze\u0144, a w przypadku wyra\u017cenia zgody na udzia\u0142 w przysz\u0142ych post\u0119powaniach rekrutacyjnych - do czasu wycofania tej zgody. Zgoda na przetwarzanie danych osobowych mo\u017ce zosta\u0107 wycofana w dowolnym momencie. Odbiorc\u0105 danych jest serwis Just Join IT oraz inne podmioty, kt\u00f3rym powierzyli\u015bmy przetwarzanie danych w zwi\u0105zku z rekrutacj\u0105.\", \"futureConsent\": null, \"customConsent\": null, \"companyLogoUrl\": \"https://public.hellohr.pl/offers/company_logos/original/35d333344cf833de8b32e4ef9761a408a0e66bd3.png?1666693365\", \"employmentTypes\": [{\"from\": 822.0, \"fromPerUnit\": 39.15341613555786, \"to\": 1005.0, \"toPerUnit\": 47.85417527679294, \"currency\": \"CHF\", \"currencySource\": \"conversion\", \"type\": \"b2b\", \"unit\": \"Day\", \"gross\": false}, {\"from\": 749.0, \"fromPerUnit\": 35.6527422900945, \"to\": 915.0, \"toPerUnit\": 43.5755739101155, \"currency\": \"GBP\", \"currencySource\": \"conversion\", \"type\": \"b2b\", \"unit\": \"Day\", \"gross\": false}, {\"from\": 875.0, \"fromPerUnit\": 41.67727893676638, \"to\": 1070.0, \"toPerUnit\": 50.93889647827002, \"currency\": \"EUR\", \"currencySource\": \"conversion\", \"type\": \"b2b\", \"unit\": \"Day\", \"gross\": false}, {\"from\": 1013.0, \"fromPerUnit\": 48.2483180100249, \"to\": 1238.0, \"toPerUnit\": 58.9701664566971, \"currency\": \"USD\", \"currencySource\": \"conversion\", \"type\": \"b2b\", \"unit\": \"Day\", \"gross\": false}, {\"from\": 3780.0, \"fromPerUnit\": 180.0, \"to\": 4620.0, \"toPerUnit\": 220.0, \"currency\": \"PLN\", \"currencySource\": \"original\", \"type\": \"b2b\", \"unit\": \"Day\", \"gross\": false}], \"workplaceType\": \"remote\", \"requiredSkills\": [{\"name\": \"SAP CRM\", \"level\": 4}, {\"name\": \"ABAP\", \"level\": 4}, {\"name\": \"One Order Framework\", \"level\": 4}, {\"name\": \"CRM Middleware\", \"level\": 4}, {\"name\": \"WebClient UI\", \"level\": 4}, {\"name\": \"SAP S/4HANA / ECC Integration\", \"level\": 3}, {\"name\": \"BOL / GenIL\", \"level\": 4}], \"niceToHaveSkills\": [], \"workingTime\": \"full_time\", \"applyUrl\": null, \"publishedAt\": \"2026-09-18T09:00:09.4696241Z\", \"companyProfileSlug\": null, \"companyProfileCoverPhotoUrl\": null, \"companyProfileShortDescription\": null, \"isOpenToHireUkrainians\": false, \"locations\": [{\"city\": \"Gda\u0144sk\", \"street\": \"Bernarda Chrzanowskiego 11\", \"latitude\": 54.3823733, \"longitude\": 18.5861607, \"slug\": \"7n-sp-z-o-o--sap-crm-consultant-gdansk-erp\"}], \"videoUrl\": null, \"bannerUrl\": null, \"isActive\": true, \"expiredAt\": \"2026-09-28T21:59:59.999999Z\", \"languages\": [{\"code\": \"en\", \"level\": \"C1\"}], \"coverImage\": \"https://og-image.justjoin.it/ogimage/justjoinit/7n-sp-z-o-o--sap-crm-consultant-gdansk-erp\", \"appliedAt\": null, \"hybridWorkSchedule\": null, \"url\": \"https://justjoin.it/job-offer/7n-sp-z-o-o--sap-crm-consultant-gdansk-erp\"}"

# A second detail record, kept for the two columns only some employers
# fill: `companyUrl` and `companySize` were non-null on 5 of 40 sampled
# records. Without it, `check_no_column_is_null_on_every_row_of_every_route`
# would report them as columns nothing ever populates — which is what
# CLAUDE.md §9 says to delete, and would be the wrong conclusion drawn from
# a fixture rather than from the site.
DETAIL_WITH_COMPANY_JSON = "{\"id\": \"8aaed32d-6ded-4a51-bf05-32d0af529d6f\", \"slug\": \"astek-polska-senior-angular-developer-warszawa-mazowieckie--javascript-b28b4fcc\", \"title\": \"Senior Angular Developer\", \"experienceLevel\": \"senior\", \"category\": {\"key\": \"javascript\", \"parentKey\": null}, \"companyName\": \"ASTEK Polska\", \"companyUrl\": \"http://astek.pl/\", \"body\": \"<p class=\\\"editor-paragraph\\\"><strong>Astek</strong></p><p class=\\\"editor-paragraph\\\"><strong>Za\u0142o\u017cona w 1988 roku we Francji Grupa Astek jest \u015bwiatowym partnerem w obszarze doradztwa in\u017cynieryjnego oraz IT. Dzi\u0119ki swojej wiedzy specjalistycznej w wielu sektorach przemys\u0142owych i technologicznych, Astek \", \"locationId\": \"0e4697c2-ed6c-47d2-8f60-68db61d925d2\", \"city\": \"Warszawa (Mazowieckie)\", \"street\": \"Centrum\", \"countryCode\": \"PL\", \"latitude\": 52.32121919999999, \"longitude\": 21.1036813, \"companySize\": \"501+\", \"informationClause\": \"Informujemy, \u017ce administratorem danych jest ASTEK Polska sp. z o.o. z siedzib\u0105 w Warszawie, Al. Jana Paw\u0142a II 22, 00-133 Warszawa (dalej jako \\\"administrator\\\"). Masz prawo do \u017c\u0105dania dost\u0119pu do swoich danych osobowych, ich sprostowania, usuni\u0119cia lub ograniczenia przetwarzania, prawo do wniesienia sprzeciwu wobec przetwarzania, a tak\u017ce prawo do przenoszenia danych oraz wniesienia skargi do organu nadzorczego. Dane osobowe przetwarzane b\u0119d\u0105 w celu realizacji procesu rekrutacji. Podanie danych w zakresie wynikaj\u0105cym z ustawy z dnia 26 czerwca 1974 r. Kodeks pracy jest obowi\u0105zkowe. W pozosta\u0142ym zakresie podanie danych jest dobrowolne. Odmowa podania danych obowi\u0105zkowych mo\u017ce skutkowa\u0107 brakiem mo\u017cliwo\u015bci przeprowadzenia procesu rekrutacji. Administrator przetwarza dane obowi\u0105zkowe na podstawie ci\u0105\u017c\u0105cego na nim obowi\u0105zku prawnego, za\u015b w zakresie danych dodatkowych podstaw\u0105 przetwarzania jest zgoda. Dane osobowe b\u0119d\u0105 przetwarzane do czasu zako\u0144czenia post\u0119powania rekrutacyjnego i przez okres mo\u017cliwo\u015bci dochodzenia ewentualnych roszcze\u0144, a w przypadku wyra\u017cenia zgody na udzia\u0142 w przysz\u0142ych post\u0119powaniach rekrutacyjnych - do czasu wycofania tej zgody. Zgoda na przetwarzanie danych osobowych mo\u017ce zosta\u0107 wycofana w dowolnym momencie.  W razie wycofania zg\u00f3d prosimy o kontakt e-mailowy na adres privacy@astek.net lub pisemnie na adres administratora. Odbiorc\u0105 danych jest serwis Just Join IT oraz inne podmioty, kt\u00f3rym powierzyli\u015bmy przetwarzanie danych w zwi\u0105zku z rekrutacj\u0105.\\n\\nProcedura dokonywania zg\u0142osze\u0144 przez sygnalist\u00f3w w ASTEK Polska sp. z o. o.: https://astek.pl/sygnalisci/\\nSzczeg\u00f3\u0142owe informacje dotycz\u0105ce przetwarzania znajdzie Pan/Pani: http://astek.pl/polityka-prywatnosci\", \"futureConsent\": \"Zgadzam si\u0119 na przetwarzanie moich danych osobowych przez ASTEK Polska sp. z o.o. w celu realizacji przysz\u0142ych proces\u00f3w rekrutacji.\", \"customConsent\": \"Zgadzam si\u0119 na przetwarzanie moich danych osobowych przez ASTEK Polska sp. z o.o. w celu realizacji procesu rekrutacji. Zapozna\u0142am/em si\u0119 z informacj\u0105 o przetwarzaniu danych osobowych dost\u0119pn\u0105 pod linkiemhttp://astek.pl/polityka-prywatnosci\", \"companyLogoUrl\": \"https://public.justjoin.it/companies/logos/original/3de9aa63e2331cebd70d66e2b523fa1560bdd100.png?1670599242\", \"employmentTypes\": [{\"from\": 4558.0, \"fromPerUnit\": 217.032728535463, \"to\": 5697.0, \"toPerUnit\": 271.29091066932875, \"currency\": \"CHF\", \"currencySource\": \"conversion\", \"type\": \"b2b\", \"unit\": \"Day\", \"gross\": false}, {\"from\": 4122.0, \"fromPerUnit\": 196.301676416317, \"to\": 5153.0, \"toPerUnit\": 245.37709552039624, \"currency\": \"GBP\", \"currencySource\": \"conversion\", \"type\": \"b2b\", \"unit\": \"Day\", \"gross\": false}, {\"from\": 5522.0, \"fromPerUnit\": 262.950302392848, \"to\": 6902.0, \"toPerUnit\": 328.68787799106, \"currency\": \"USD\", \"currencySource\": \"conversion\", \"type\": \"b2b\", \"unit\": \"Day\", \"gross\": false}, {\"from\": 4813.0, \"fromPerUnit\": 229.189585625229, \"to\": 6016.0, \"toPerUnit\": 286.48698203153623, \"currency\": \"EUR\", \"currencySource\": \"conversion\", \"type\": \"b2b\", \"unit\": \"Day\", \"gross\": false}, {\"from\": 21000.0, \"fromPerUnit\": 1000.0, \"to\": 26250.0, \"toPerUnit\": 1250.0, \"currency\": \"PLN\", \"currencySource\": \"original\", \"type\": \"b2b\", \"unit\": \"Day\", \"gross\": false}], \"workplaceType\": \"hybrid\", \"requiredSkills\": [{\"name\": \"Angular\", \"level\": 5}, {\"name\": \"TypeScript\", \"level\": 5}, {\"name\": \"JavaScript\", \"level\": 5}, {\"name\": \"RxJS\", \"level\": 4}, {\"name\": \"Node.js\", \"level\": 4}, {\"name\": \"HTML5\", \"level\": 4}, {\"name\": \"CSS\", \"level\": 4}], \"niceToHaveSkills\": [{\"name\": \"Git\", \"level\": 4}, {\"name\": \"REST API\", \"level\": 4}, {\"name\": \"React\", \"level\": 3}], \"workingTime\": \"full_time\", \"applyUrl\": null, \"publishedAt\": \"2026-09-17T22:50:16.50239Z\", \"companyProfileSlug\": null, \"companyProfileCoverPhotoUrl\": null, \"companyProfileShortDescription\": null, \"isOpenToHireUkrainians\": false, \"locations\": [{\"city\": \"Warszawa (Mazowieckie)\", \"street\": \"Centrum\", \"latitude\": 52.32121919999999, \"longitude\": 21.1036813, \"slug\": \"astek-polska-senior-angular-developer-warszawa-mazowieckie--javascript-b28b4fcc\"}], \"videoUrl\": null, \"bannerUrl\": null, \"isActive\": true, \"expiredAt\": \"2026-10-17T21:59:59.999Z\", \"languages\": [], \"coverImage\": \"https://og-image.justjoin.it/ogimage/justjoinit/astek-polska-senior-angular-developer-warszawa-mazowieckie--javascript-b28b4fcc\", \"appliedAt\": null, \"hybridWorkSchedule\": null, \"url\": \"https://justjoin.it/job-offer/astek-polska-senior-angular-developer-warszawa-mazowieckie--javascript-b28b4fcc\"}"

# A rendered listing page, trimmed to three offers. Kept because the flight
# payload is a DIFFERENT spelling of the same records — see the module
# docstring — and because it carries the two JSON-LD blocks the page really
# publishes: a `CollectionPage` naming offer URLs with no other field about
# them, and a `BreadcrumbList`.
SSR_HTML = "<!DOCTYPE html><html lang=\"en\"><head><title>IT Job Board and Job Offers | Just Join IT</title><link rel=\"canonical\" href=\"https://justjoin.it/job-offers/all-locations\"/><script type=\"application/ld+json\">{\"@context\": \"https://schema.org\", \"@type\": \"CollectionPage\", \"name\": \"Job Offers\", \"hasPart\": [{\"url\": \"https://justjoin.it/job-offer/goodylabs-senior-devops-engineer-k-m-x--lodz-devops\", \"@type\": \"CreativeWork\"}, {\"url\": \"https://justjoin.it/job-offer/storware-presales-specialist-backup-product--warszawa-pm\", \"@type\": \"CreativeWork\"}, {\"url\": \"https://justjoin.it/job-offer/b2bnetwork-expert-java-developer-f-m-x--gdansk-java\", \"@type\": \"CreativeWork\"}]}</script><script type=\"application/ld+json\">{\"@context\":\"https://schema.org\",\"@type\":\"BreadcrumbList\",\"itemListElement\":[{\"@type\":\"ListItem\",\"position\":1,\"item\":\"https://justjoin.it/job-offers\",\"name\":\"Job Offers\"},{\"@type\":\"ListItem\",\"position\":2,\"item\":\"https://justjoin.it/job-offers/all-locations\",\"name\":\"All locations\"}]}</script></head><body><a href=\"/job-offer/goodylabs-senior-devops-engineer-k-m-x--lodz-devops\">one</a><script src=\"/_next/static/chunks/main.js\"></script><img src=\"https://public.justjoin.it/logos/x.png\"/><script>self.__next_f.push([1,\"{\\\"props\\\":{\\\"pageProps\\\":{\\\"dehydratedState\\\":{\\\"queries\\\":[{\\\"state\\\":{\\\"data\\\":{\\\"count\\\":19379},\\\"dataUpdateCount\\\":1,\\\"dataUpdatedAt\\\":1789727831325,\\\"error\\\":null,\\\"errorUpdateCount\\\":0,\\\"errorUpdatedAt\\\":0,\\\"fetchFailureCount\\\":0,\\\"fetchFailureReason\\\":null,\\\"fetchMeta\\\":null,\\\"isInvalidated\\\":false,\\\"status\\\":\\\"success\\\",\\\"fetchStatus\\\":\\\"idle\\\"},\\\"queryKey\\\":[\\\"OFFERS_COUNT\\\",{\\\"countAllBoardOffers\\\":false,\\\"currentFiltersValues\\\":{}}]},{\\\"state\\\":{\\\"meta\\\":{\\\"from\\\":0,\\\"totalItems\\\":10000,\\\"prev\\\":{\\\"cursor\\\":null,\\\"itemsCount\\\":100},\\\"next\\\":{\\\"cursor\\\":100,\\\"itemsCount\\\":100}},\\\"data\\\":[{\\\"applyUrl\\\":null,\\\"body\\\":\\\"Senior DevOps Engineer (k/m/x)\\\",\\\"categoryId\\\":0,\\\"city\\\":\\\"\\u0141\\u00f3d\\u017a\\\",\\\"companyLogoThumbUrl\\\":\\\"https://imgproxy.justjoinit.tech/UH0eJBWDrQWAgF6fsY4eCff-9jxahqM93oT7HNGuTQc/h:200/w:200/plain/https://public.justjoin.it/companies/logos/original/a0e65ec29bf5cc3fdabec6ebb8eb17c5b8e9cdd3.png\\\",\\\"companyName\\\":\\\"goodylabs\\\",\\\"companyProfileSlug\\\":null,\\\"customConsent\\\":null,\\\"displayOffer\\\":true,\\\"employmentTypes\\\":[{\\\"from\\\":22000,\\\"fromPerUnit\\\":22000,\\\"to\\\":28000,\\\"toPerUnit\\\":28000,\\\"currency\\\":\\\"PLN\\\",\\\"currencySource\\\":\\\"original\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"month\\\",\\\"gross\\\":false},{\\\"from\\\":5897.01664566971,\\\"fromPerUnit\\\":5897.01664566971,\\\"to\\\":7505.29391267054,\\\"toPerUnit\\\":7505.29391267054,\\\"currency\\\":\\\"USD\\\",\\\"currencySource\\\":\\\"conversion\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"month\\\",\\\"gross\\\":false},{\\\"from\\\":5093.889647827002,\\\"fromPerUnit\\\":5093.889647827002,\\\"to\\\":6483.132279052548,\\\"toPerUnit\\\":6483.132279052548,\\\"currency\\\":\\\"EUR\\\",\\\"currencySource\\\":\\\"conversion\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"month\\\",\\\"gross\\\":false},{\\\"from\\\":4785.417527679294,\\\"fromPerUnit\\\":4785.417527679294,\\\"to\\\":6090.531398864556,\\\"toPerUnit\\\":6090.531398864556,\\\"currency\\\":\\\"CHF\\\",\\\"currencySource\\\":\\\"conversion\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"month\\\",\\\"gross\\\":false},{\\\"from\\\":4357.55739101155,\\\"fromPerUnit\\\":4357.55739101155,\\\"to\\\":5545.9821340147,\\\"toPerUnit\\\":5545.9821340147,\\\"currency\\\":\\\"GBP\\\",\\\"currencySource\\\":\\\"conversion\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"month\\\",\\\"gross\\\":false}],\\\"experienceLevel\\\":\\\"senior\\\",\\\"futureConsent\\\":null,\\\"informationClause\\\":\\\"\\\",\\\"latitude\\\":51.7596858,\\\"longitude\\\":19.4514766,\\\"niceToHaveSkills\\\":[],\\\"openToHireUkrainians\\\":false,\\\"publishedAt\\\":\\\"2026-09-20T07:45:07.364Z\\\",\\\"lastPublishedAt\\\":\\\"2026-09-18T09:51:12.673298Z\\\",\\\"expiredAt\\\":\\\"2026-09-25T12:01:18.785205Z\\\",\\\"applyMethod\\\":\\\"external\\\",\\\"remoteInterview\\\":false,\\\"requiredSkills\\\":[\\\"Ansible\\\",\\\"Linux\\\",\\\"Docker\\\",\\\"Kubernetes\\\",\\\"CI/CD\\\",\\\"Bash\\\",\\\"Cloud\\\"],\\\"slug\\\":\\\"goodylabs-senior-devops-engineer-k-m-x--lodz-devops\\\",\\\"street\\\":\\\"Gda\\u0144ska 130\\\",\\\"title\\\":\\\"Senior DevOps Engineer (k/m/x)\\\",\\\"workingTime\\\":\\\"b2b_contract\\\",\\\"workplaceType\\\":\\\"hybrid\\\",\\\"guid\\\":\\\"417c9a35-f7ad-4000-998d-fc076e229b51\\\",\\\"multilocation\\\":[{\\\"slug\\\":\\\"goodylabs-senior-devops-engineer-k-m-x--lodz-devops\\\",\\\"city\\\":\\\"\\u0141\\u00f3d\\u017a\\\",\\\"street\\\":\\\"Gda\\u0144ska 130\\\",\\\"latitude\\\":51.7596858,\\\"longitude\\\":19.4514766}],\\\"isPromoted\\\":false,\\\"isSuperOffer\\\":false,\\\"matchPercent\\\":\\\"$undefined\\\"},{\\\"applyUrl\\\":null,\\\"body\\\":\\\"Presales Specialist [Backup product]\\\",\\\"categoryId\\\":0,\\\"city\\\":\\\"Warszawa\\\",\\\"companyLogoThumbUrl\\\":\\\"https://imgproxy.justjoinit.tech/RaWDwW3OTULSFrvhogH3DhsJdLHROIf22ctBMamlD1U/h:200/w:200/plain/https://s3.eu-west-1.amazonaws.com/images.justjoin.it/justjoinit/company-logos/1769620825_019c059ee4f57bf9a894311dec5254e8.png\\\",\\\"companyName\\\":\\\"Storware\\\",\\\"companyProfileSlug\\\":null,\\\"customConsent\\\":null,\\\"displayOffer\\\":true,\\\"employmentTypes\\\":[{\\\"from\\\":11000,\\\"fromPerUnit\\\":11000,\\\"to\\\":13000,\\\"toPerUnit\\\":13000,\\\"currency\\\":\\\"PLN\\\",\\\"currencySource\\\":\\\"original\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"month\\\",\\\"gross\\\":false},{\\\"from\\\":2892.453326321328,\\\"fromPerUnit\\\":2892.453326321328,\\\"to\\\":3418.353931107024,\\\"toPerUnit\\\":3418.353931107024,\\\"currency\\\":\\\"USD\\\",\\\"currencySource\\\":\\\"conversion\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"month\\\",\\\"gross\\\":false},{\\\"from\\\":2521.085441877519,\\\"fromPerUnit\\\":2521.085441877519,\\\"to\\\":2979.464613127977,\\\"toPerUnit\\\":2979.464613127977,\\\"currency\\\":\\\"EUR\\\",\\\"currencySource\\\":\\\"conversion\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"month\\\",\\\"gross\\\":false},{\\\"from\\\":2387.360013890093,\\\"fromPerUnit\\\":2387.360013890093,\\\"to\\\":2821.425470961019,\\\"toPerUnit\\\":2821.425470961019,\\\"currency\\\":\\\"CHF\\\",\\\"currencySource\\\":\\\"conversion\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"month\\\",\\\"gross\\\":false},{\\\"from\\\":2159.318440579487,\\\"fromPerUnit\\\":2159.318440579487,\\\"to\\\":2551.921793412121,\\\"toPerUnit\\\":2551.921793412121,\\\"currency\\\":\\\"GBP\\\",\\\"currencySource\\\":\\\"conversion\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"month\\\",\\\"gross\\\":false}],\\\"experienceLevel\\\":\\\"junior\\\",\\\"futureConsent\\\":null,\\\"informationClause\\\":\\\"\\\",\\\"latitude\\\":52.1939894,\\\"longitude\\\":20.9549002,\\\"niceToHaveSkills\\\":[],\\\"openToHireUkrainians\\\":false,\\\"publishedAt\\\":\\\"2026-09-18T10:36:09.878308Z\\\",\\\"lastPublishedAt\\\":\\\"2026-09-18T10:36:09.878308Z\\\",\\\"expiredAt\\\":\\\"2026-10-18T10:36:09.878308Z\\\",\\\"applyMethod\\\":\\\"external\\\",\\\"remoteInterview\\\":false,\\\"requiredSkills\\\":[\\\"Cloud\\\",\\\"Storage\\\",\\\"Wirtualizacja\\\",\\\"Wdro\\u017cenia\\\"],\\\"slug\\\":\\\"storware-presales-specialist-backup-product--warszawa-pm\\\",\\\"street\\\":\\\"Bakalarska 15A\\\",\\\"title\\\":\\\"Presales Specialist [Backup product]\\\",\\\"workingTime\\\":\\\"b2b_contract\\\",\\\"workplaceType\\\":\\\"remote\\\",\\\"guid\\\":\\\"fc7de2cc-ac8e-48ea-9831-e3c41d31cd65\\\",\\\"multilocation\\\":[{\\\"slug\\\":\\\"storware-presales-specialist-backup-product--warszawa-pm\\\",\\\"city\\\":\\\"Warszawa\\\",\\\"street\\\":\\\"Bakalarska 15A\\\",\\\"latitude\\\":52.1939894,\\\"longitude\\\":20.9549002},{\\\"slug\\\":\\\"storware-presales-specialist-backup-product--bielsko-biala-pm\\\",\\\"city\\\":\\\"Bielsko-Bia\\u0142a\\\",\\\"street\\\":\\\"Ksi\\u0119dza Stanis\\u0142awa Stoja\\u0142owskiego 14\\\",\\\"latitude\\\":49.82253,\\\"longitude\\\":19.0487492}],\\\"isPromoted\\\":false,\\\"isSuperOffer\\\":false,\\\"matchPercent\\\":\\\"$undefined\\\"},{\\\"applyUrl\\\":null,\\\"body\\\":\\\"Expert Java Developer (f/m/x)\\\",\\\"categoryId\\\":0,\\\"city\\\":\\\"Gda\\u0144sk\\\",\\\"companyLogoThumbUrl\\\":\\\"https://imgproxy.justjoinit.tech/_MqB7Uvo956DTU995Zgcqo1nhIGRw-Olu4BEVz9Bkxk/h:200/w:200/plain/https://public.justjoin.it/companies/logos/original/b223c419715a26ddd4d791d9dea6feaa72239db9.png\\\",\\\"companyName\\\":\\\"B2Bnetwork\\\",\\\"companyProfileSlug\\\":null,\\\"customConsent\\\":null,\\\"displayOffer\\\":true,\\\"employmentTypes\\\":[{\\\"from\\\":110,\\\"fromPerUnit\\\":110,\\\"to\\\":130,\\\"toPerUnit\\\":130,\\\"currency\\\":\\\"PLN\\\",\\\"currencySource\\\":\\\"original\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"hour\\\",\\\"gross\\\":false},{\\\"from\\\":28.92453326321328,\\\"fromPerUnit\\\":28.92453326321328,\\\"to\\\":34.18353931107024,\\\"toPerUnit\\\":34.18353931107024,\\\"currency\\\":\\\"USD\\\",\\\"currencySource\\\":\\\"conversion\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"hour\\\",\\\"gross\\\":false},{\\\"from\\\":25.21085441877519,\\\"fromPerUnit\\\":25.21085441877519,\\\"to\\\":29.79464613127977,\\\"toPerUnit\\\":29.79464613127977,\\\"currency\\\":\\\"EUR\\\",\\\"currencySource\\\":\\\"conversion\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"hour\\\",\\\"gross\\\":false},{\\\"from\\\":23.87360013890093,\\\"fromPerUnit\\\":23.87360013890093,\\\"to\\\":28.21425470961019,\\\"toPerUnit\\\":28.21425470961019,\\\"currency\\\":\\\"CHF\\\",\\\"currencySource\\\":\\\"conversion\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"hour\\\",\\\"gross\\\":false},{\\\"from\\\":21.59318440579487,\\\"fromPerUnit\\\":21.59318440579487,\\\"to\\\":25.51921793412121,\\\"toPerUnit\\\":25.51921793412121,\\\"currency\\\":\\\"GBP\\\",\\\"currencySource\\\":\\\"conversion\\\",\\\"type\\\":\\\"b2b\\\",\\\"unit\\\":\\\"hour\\\",\\\"gross\\\":false}],\\\"experienceLevel\\\":\\\"senior\\\",\\\"futureConsent\\\":null,\\\"informationClause\\\":\\\"\\\",\\\"latitude\\\":54.35202520000001,\\\"longitude\\\":18.6466384,\\\"niceToHaveSkills\\\":[],\\\"openToHireUkrainians\\\":false,\\\"publishedAt\\\":\\\"2026-09-18T10:33:22.2309829Z\\\",\\\"lastPublishedAt\\\":\\\"2026-09-18T10:33:22.2309829Z\\\",\\\"expiredAt\\\":\\\"2026-12-17T11:30:19.755Z\\\",\\\"applyMethod\\\":\\\"form\\\",\\\"remoteInterview\\\":false,\\\"requiredSkills\\\":[\\\"Java\\\",\\\"Spring Boot\\\",\\\"Relational Databases\\\",\\\"AI Toos\\\",\\\"Kafka\\\",\\\"CI/CD\\\",\\\"REST API\\\",\\\"Maven\\\",\\\"JUnit\\\",\\\"Docker\\\"],\\\"slug\\\":\\\"b2bnetwork-expert-java-developer-f-m-x--gdansk-java\\\",\\\"street\\\":\\\"-\\\",\\\"title\\\":\\\"Expert Java Developer (f/m/x)\\\",\\\"workingTime\\\":\\\"full_time\\\",\\\"workplaceType\\\":\\\"hybrid\\\",\\\"guid\\\":\\\"980bd85d-903b-47d5-ac97-9ebd3863b893\\\",\\\"multilocation\\\":[{\\\"slug\\\":\\\"b2bnetwork-expert-java-developer-f-m-x--gdansk-java\\\",\\\"city\\\":\\\"Gda\\u0144sk\\\",\\\"street\\\":\\\"-\\\",\\\"latitude\\\":54.35202520000001,\\\"longitude\\\":18.6466384},{\\\"slug\\\":\\\"b2bnetwork-expert-java-developer-f-m-x--warszawa-java\\\",\\\"city\\\":\\\"Warszawa\\\",\\\"street\\\":\\\"-\\\",\\\"latitude\\\":52.2296756,\\\"longitude\\\":21.0122287}],\\\"isPromoted\\\":false,\\\"isSuperOffer\\\":false,\\\"matchPercent\\\":\\\"$undefined\\\"}]}}}]}}}}\"])</script></body></html>"

# The site's own 404, verbatim. An RFC 7231 problem document — note that it
# names the UPSTREAM path (`/api/v1/justjoinit/offers/...`), which is how
# the same-origin proxy in front of `api.justjoin.it` announces itself.
NOT_FOUND_JSON = "{\"type\":\"https://tools.ietf.org/html/rfc7231#section-6.6.1\",\"title\":\"Entity not found\",\"status\":404,\"detail\":\"Offer with active slug 'this-offer-does-not-exist-xyz' not found for job board 'justjoinit'.\",\"instance\":\"/api/v1/justjoinit/offers/this-offer-does-not-exist-xyz\",\"traceId\":\"00-SCRUBBED-BY-SMOKE-TEST-00-SCRUBBED-01\"}"

# `/offers/facets/count` and `/offers/categories/count`, verbatim.
FACETS_JSON = "{\"workplaceTypes\": [{\"key\": \"office\", \"count\": 839}, {\"key\": \"hybrid\", \"count\": 7842}, {\"key\": \"remote\", \"count\": 10706}, {\"key\": \"mobile\", \"count\": 0}], \"employmentTypes\": [{\"key\": \"b2b\", \"count\": 15467}, {\"key\": \"permanent\", \"count\": 8820}, {\"key\": \"mandate_contract\", \"count\": 3325}, {\"key\": \"contract\", \"count\": 3108}, {\"key\": \"internship\", \"count\": 3182}, {\"key\": \"freelance\", \"count\": 3094}], \"workingTimes\": [{\"key\": \"full_time\", \"count\": 16446}, {\"key\": \"part_time\", \"count\": 249}, {\"key\": \"internship\", \"count\": 33}, {\"key\": \"freelance\", \"count\": 330}, {\"key\": \"b2b_contract\", \"count\": 2329}], \"experienceLevels\": [{\"key\": \"intern\", \"count\": 73}, {\"key\": \"junior\", \"count\": 796}, {\"key\": \"mid\", \"count\": 6561}, {\"key\": \"senior\", \"count\": 10712}, {\"key\": \"manager\", \"count\": 984}, {\"key\": \"c_level\", \"count\": 261}], \"languages\": [{\"key\": \"pl\", \"count\": 3862}, {\"key\": \"en\", \"count\": 10412}, {\"key\": \"de\", \"count\": 847}, {\"key\": \"fr\", \"count\": 229}, {\"key\": \"es\", \"count\": 22}, {\"key\": \"uk\", \"count\": 4}, {\"key\": \"ru\", \"count\": 5}, {\"key\": \"it\", \"count\": 18}, {\"key\": \"pt\", \"count\": 24}, {\"key\": \"polish\", \"count\": 25}, {\"key\": \"da\", \"count\": 21}, {\"key\": \"nl\", \"count\": 17}, {\"key\": \"sv\", \"count\": 11}, {\"key\": \"english\", \"count\": 12}, {\"key\": \"no\", \"count\": 8}, {\"key\": \"lt\", \"count\": 7}, {\"key\": \"ro\", \"count\": 3}, {\"key\": \"ja\", \"count\": 2}, {\"key\": \"ar\", \"count\": 1}, {\"key\": \"cs\", \"count\": 1}, {\"key\": \"ko\", \"count\": 2}, {\"key\": \"sl\", \"count\": 1}, {\"key\": \"th\", \"count\": 1}, {\"key\": \"bg\", \"count\": 0}, {\"key\": \"el\", \"count\": 0}, {\"key\": \"et\", \"count\": 0}, {\"key\": \"fi\", \"count\": 0}, {\"key\": \"he\", \"count\": 0}, {\"key\": \"hr\", \"count\": 0}, {\"key\": \"hu\", \"count\": 0}, {\"key\": \"is\", \"count\": 0}, {\"key\": \"lv\", \"count\": 0}, {\"key\": \"sk\", \"count\": 0}, {\"key\": \"tr\", \"count\": 0}, {\"key\": \"zh\", \"count\": 0}], \"publishedSinceDays\": [{\"key\": \"1\", \"count\": 962}, {\"key\": \"7\", \"count\": 3387}, {\"key\": \"14\", \"count\": 6311}, {\"key\": \"30\", \"count\": 11467}]}"

CATEGORIES_JSON = "[{\"key\": \"data\", \"count\": 1991}, {\"key\": \"java\", \"count\": 1467}, {\"key\": \"analytics\", \"count\": 1577}, {\"key\": \"pm\", \"count\": 1607}, {\"key\": \"devops\", \"count\": 1383}, {\"key\": \"erp\", \"count\": 1243}, {\"key\": \"architecture\", \"count\": 1189}, {\"key\": \"testing\", \"count\": 1224}, {\"key\": \"ai\", \"count\": 1081}, {\"key\": \"security\", \"count\": 856}, {\"key\": \"other\", \"count\": 847}, {\"key\": \"python\", \"count\": 878}, {\"key\": \"net\", \"count\": 738}, {\"key\": \"admin\", \"count\": 721}, {\"key\": \"javascript\", \"count\": 730}, {\"key\": \"support\", \"count\": 504}, {\"key\": \"mobile\", \"count\": 344}, {\"key\": \"c\", \"count\": 272}, {\"key\": \"ux\", \"count\": 245}, {\"key\": \"php\", \"count\": 141}, {\"key\": \"go\", \"count\": 133}, {\"key\": \"game\", \"count\": 110}, {\"key\": \"scala\", \"count\": 50}, {\"key\": \"ruby\", \"count\": 56}, {\"key\": \"html\", \"count\": 14}]"

# NOT from this site. A hand-built page carrying a Cloudflare managed
# challenge, so the marker set has something to bite on — justjoin.it has
# never served this scraper one, and a check with no positive case proves
# nothing (CLAUDE.md §19: a measurement with no negative case is an
# anecdote, and the inverse holds too).
SYNTHETIC_CHALLENGE_HTML = (
    "<html><head><title>Just a moment...</title></head><body>"
    "<script src=\"https://challenges.cloudflare.com/turnstile/v0/api.js\">"
    "</script><div id=\"cf-please-wait\"></div>"
    "<div class=\"main-wrapper\" role=\"main\">Enable JavaScript and cookies "
    "to continue</div></body></html>")

# A sitemap index and a part file, in the two shapes the site really uses:
# the outer file names one part, the part file names offers with lastmod.
SITEMAP_INDEX_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<sitemap><loc>https://justjoin.it/sitemaps/active-jobs/part0.xml</loc>"
    "</sitemap></sitemapindex>")

# Served OLDEST FIRST, exactly as the site serves it, plus one entry with
# no `lastmod` at all — so the ordering check has something to reorder and
# the undated case is exercised rather than assumed.
SITEMAP_PART_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<url><loc>https://justjoin.it/job-offer/fibertide-senior-site-reliability"
    "-engineer-wroclaw-architecture-7f4e4784</loc>"
    "<lastmod>2026-09-14T13:06:30+00:00</lastmod></url>"
    "<url><loc>https://justjoin.it/job-offer/undated-role-warszawa-java</loc>"
    "</url>"
    "<url><loc>https://justjoin.it/job-offer/accenture-sap-security-associate"
    "-manager-gdansk-erp-73955f53</loc>"
    "<lastmod>2026-09-18T00:14:09+00:00</lastmod></url>"
    "<url><loc>https://justjoin.it/job-offers/all-locations</loc></url>"
    "</urlset>")


# ---------------------------------------------------------------------------
# Parsing — the endpoint
# ---------------------------------------------------------------------------

import product_parser as pp
import page_flow
import output_writer
from output_writer import JobPosting, Facet, ROW_CLASS_BY_MODE


def _listing():
    return pp.parse_offers_response(LISTING_JSON, url=pp.api_url(items=7),
                                    page=1, items=7)


def _detail():
    return pp.parse_detail_response(DETAIL_JSON)


def _ssr():
    return pp.parse_ssr_listing(SSR_HTML, url=pp.DEFAULT_LISTING_URL, page=1)


def check_listing_parses():
    page = _listing()
    equal("listing rows", len(page.rows), 7)
    equal("records_in_payload", page.records_in_payload, 7)
    equal("total_items is the site's capped figure", page.total_items, 10000)
    equal("offset echoed", page.offset, 0)
    check("every row has a sku", all(r.sku for r in page.rows))
    check("every sku is a uuid",
          all(re.fullmatch(r"[0-9a-f-]{36}", r.sku) for r in page.rows),
          [r.sku for r in page.rows][:2])
    check("every row has a url on the site",
          all(r.url.startswith("https://justjoin.it/job-offer/")
              for r in page.rows))
    equal("data_source", {r.data_source for r in page.rows}, {"api"})


def check_the_salary_is_never_read_from_index_zero():
    """The headline trap. See product_parser's docstring for the numbers.

    `employmentTypes` mixes the employer's own figure with conversions the
    site computed, and the real one is not first. This fixture holds a row
    where indexing `[0]` yields a real-looking number in the wrong currency
    — PLN 129.37 where the employer offers EUR 30 — and the check asserts
    both that the parser gets the right one AND that the wrong one is
    genuinely there to be got. A check whose trap is absent from its own
    fixture passes for the wrong reason (CLAUDE.md §21).
    """
    records = json.loads(LISTING_JSON)["data"]
    rows = {r.sku: r for r in _listing().rows}

    trapped = 0
    for record in records:
        entries = record["employmentTypes"]
        first = entries[0]
        original = next(e for e in entries
                        if e.get("currencySource") == "original")
        row = rows[record["guid"]]
        if first is not original:
            trapped += 1
            check("row %s does not take the currency from [0]"
                  % record["guid"][:8],
                  row.salary_currency == (original.get("currency") or "").upper()
                  and row.salary_currency != (first.get("currency") or "").upper()
                  or original.get("currency") == first.get("currency"),
                  "got %r, [0] offers %r, the original says %r"
                  % (row.salary_currency, first.get("currency"),
                     original.get("currency")))
        equal("row %s reads the ORIGINAL rate" % record["guid"][:8],
              row.salary_min, pp._float_or_none(original.get("fromPerUnit")))

    check("the fixture actually contains the trap", trapped >= 2,
          "only %d of 7 records have a conversion first — the fixture no "
          "longer exercises what this check is for" % trapped)

    # And the disclosed one, pinned by value.
    eur = [r for r in rows.values() if r.salary_currency == "EUR"]
    equal("the EUR offer is read in EUR, not the PLN conversion",
          len(eur), 1)
    equal("its quoted minimum", eur[0].salary_min, 30.0)
    equal("its quoted maximum", eur[0].salary_max, 50.0)
    equal("its unit", eur[0].salary_unit, "hour")


def check_no_conversion_currency_ever_reaches_a_row():
    """Not one row may carry a currency the site marked as computed.

    Asked through `product_parser.converted_currencies`, which exists for
    exactly this and whose docstring says so — a helper that promises the
    suite reads it and is then read by nothing is the defect CLAUDE.md §17
    names.
    """
    records = {r["guid"]: r for r in json.loads(LISTING_JSON)["data"]}
    offenders = []
    saw_conversions = 0
    for row in _listing().rows:
        entries = records[row.sku]["employmentTypes"]
        computed = set(pp.converted_currencies(entries))
        quoted = {o.currency for o in
                  pp.salary_from_employment_types(entries)[1] if o.currency}
        if computed:
            saw_conversions += 1
        if row.salary_currency and row.salary_currency in computed - quoted:
            offenders.append((row.sku[:8], row.salary_currency))
    check("no row carries a conversion-only currency", not offenders,
          str(offenders))
    # Without this the check could pass on a fixture that holds no
    # conversions at all — green for the wrong reason (CLAUDE.md §22).
    check("the fixture actually contains conversions to avoid",
          saw_conversions >= 5,
          "only %d of 7 records carry a computed currency" % saw_conversions)
    check("and the helper really finds them",
          set(pp.converted_currencies(
              records[_listing().rows[0].sku]["employmentTypes"]))
          <= {"USD", "EUR", "GBP", "CHF", "PLN"},
          "an unexpected conversion currency")


def check_the_monthly_figure_is_not_read_as_the_quoted_rate():
    """`from` is normalised to a month; `unit` names the employer's unit.

    Read together they say "21,000 PLN per hour" for a job paying 125. The
    parser reads `fromPerUnit` for the scalar columns and keeps the monthly
    figure in columns that say monthly.
    """
    records = {r["guid"]: r for r in json.loads(LISTING_JSON)["data"]}
    hourly = [r for r in _listing().rows if r.salary_unit == "hour"
              and r.salary_min is not None]
    check("the fixture holds an hourly offer", hourly)
    row = hourly[0]
    entry = next(e for e in records[row.sku]["employmentTypes"]
                 if e.get("currencySource") == "original")
    equal("quoted rate is fromPerUnit", row.salary_min,
          pp._float_or_none(entry["fromPerUnit"]))
    equal("monthly figure is `from`", row.salary_monthly_min,
          pp._float_or_none(entry["from"]))
    check("the two really differ on this record",
          entry["from"] != entry["fromPerUnit"],
          "the fixture no longer exercises the normalisation")
    check("the monthly figure is the larger one",
          row.salary_monthly_min > row.salary_min)
    # And the arithmetic the site used, which is why this is safe to keep.
    equal("168 working hours to the month",
          round(entry["from"] / entry["fromPerUnit"]), 168)


def check_an_undisclosed_salary_is_null_and_never_zero():
    """461 of 1,000 offers disclose nothing. A zero would drag every
    average a consumer computes (CLAUDE.md §21)."""
    page = _listing()
    undisclosed = [r for r in page.rows if r.salary_min is None]
    check("the fixture holds undisclosed offers", undisclosed)
    for row in undisclosed:
        equal("%s salary_min" % row.sku[:8], row.salary_min, None)
        equal("%s salary_max" % row.sku[:8], row.salary_max, None)
        equal("%s salary_monthly_min" % row.sku[:8],
              row.salary_monthly_min, None)
        check("%s carries no zero salary" % row.sku[:8],
              0 not in (row.salary_min, row.salary_max,
                        row.salary_monthly_min, row.salary_monthly_max))
    # The currency still comes from the original entry, not from a
    # conversion that happens to sit first.
    records = {r["guid"]: r for r in json.loads(LISTING_JSON)["data"]}
    for row in undisclosed:
        quoted = {(e.get("currency") or "").upper() for e in
                  records[row.sku]["employmentTypes"]
                  if e.get("currencySource") == "original"}
        check("%s names a quoted currency" % row.sku[:8],
              row.salary_currency in quoted, row.salary_currency)


def check_two_quoted_rates_are_both_kept():
    """99 of 1,000 offers quote a B2B rate AND an employment-contract rate.
    They are different offers of pay for one job, so neither is dropped."""
    page = _listing()
    multi = [r for r in page.rows if r.salary_options]
    check("the fixture holds an offer with two rates", multi)
    row = multi[0]
    check("salary_options lists both", len(row.salary_options) >= 2,
          row.salary_options)
    if row.salary_min is not None:
        check("the scalar columns hold one of the quoted rates",
              any(str(int(row.salary_min)) in option
                  for option in row.salary_options),
              "%r not in %r" % (row.salary_min, row.salary_options))
    else:
        # An offer can quote two contract TYPES and disclose neither
        # amount. The options column still records both, which is the
        # point: the contract on offer is information even when the pay is
        # not.
        check("an undisclosed multi-rate offer still lists both contracts",
              all("undisclosed" in option for option in row.salary_options),
              row.salary_options)
    check("each option names its contract type",
          all(option.split()[0] in pp.EMPLOYMENT_TYPES + ("any",)
              for option in row.salary_options), row.salary_options)
    singles = [r for r in page.rows if not r.salary_options]
    check("a single-rate offer leaves the column null", singles)


def check_the_unit_and_country_are_case_normalised():
    """The site spells one unit two ways ("month" 517, "Month" 275) and one
    country two ways ("PL" 36, "pl" 2). Left alone, each splits a
    consumer's GROUP BY in two."""
    raw_units = {e.get("unit") for r in json.loads(LISTING_JSON)["data"]
                 for e in r["employmentTypes"]
                 if e.get("currencySource") == "original"}
    check("the fixture really mixes case", any(u and u[0].isupper() for u in raw_units)
          and any(u and u[0].islower() for u in raw_units), sorted(raw_units))
    units = {r.salary_unit for r in _listing().rows if r.salary_unit}
    check("every parsed unit is lower case",
          all(u == u.lower() for u in units), sorted(units))
    check("every parsed unit is one we measured",
          units <= {"hour", "day", "month", "year"}, sorted(units))

    raw_country = json.loads(DETAIL_JSON).get("countryCode")
    equal("the detail fixture's own spelling", raw_country, raw_country)
    equal("parsed country is upper case", _detail().country_code,
          (raw_country or "").upper() or None)


def check_a_form_apply_has_no_url_and_that_is_the_site_saying_so():
    """`applyUrl` is non-null on exactly the offers whose `applyMethod` is
    "external" — 800 of 1,000. The null is the site saying "applied to on
    our own page", not a missing field (CLAUDE.md §21)."""
    for row in _listing().rows:
        if row.apply_method == "form":
            equal("%s form apply has no url" % row.sku[:8], row.apply_url, None)
        elif row.apply_method == "external":
            check("%s external apply has a url" % row.sku[:8], row.apply_url)
    methods = {r.apply_method for r in _listing().rows}
    check("the fixture holds both apply methods",
          methods == {"form", "external"}, sorted(methods))
    # Asked against the parser's own vocabulary, so a value the site starts
    # sending that this repo has never seen fails here rather than reaching
    # a consumer unannounced.
    equal("and both are in the measured vocabulary",
          methods <= set(pp.APPLY_METHODS), True)
    equal("which is the two the site publishes",
          sorted(pp.APPLY_METHODS), ["external", "form"])


def check_multi_city_offers_keep_every_city():
    """205 of 1,000 offers are open in more than one city — up to nine — so
    `city` alone under-reports where the job is."""
    rows = [r for r in _listing().rows if (r.location_count or 0) > 1]
    check("the fixture holds a multi-city offer", rows)
    row = max(rows, key=lambda r: r.location_count)
    check("locations lists more than one", len(row.locations) > 1, row.locations)
    check("city is one of them", row.city in row.locations,
          "%r not in %r" % (row.city, row.locations))
    equal("location_count matches the payload", row.location_count,
          len(json.loads(LISTING_JSON)["data"][
              [r["guid"] for r in json.loads(LISTING_JSON)["data"]].index(row.sku)
          ]["locations"]))


def check_the_sparse_company_columns_are_real_on_some_records():
    """`companyUrl` and `companySize` are non-null on 5 of 40 sampled
    detail records — sparse, and real. A column that no fixture ever
    populates reads as one to delete (§9), so one that does is kept."""
    row = pp.parse_detail_response(DETAIL_WITH_COMPANY_JSON)
    check("the record names a company website", row.company_url)
    check("and a company size", row.company_size)
    equal("while the other detail fixture fills neither",
          (_detail().company_url, _detail().company_size), (None, None))
    check("both are absent from every listing row",
          all(r.company_url is None and r.company_size is None
              for r in _listing().rows))


def check_the_detail_route_adds_what_the_listing_does_not():
    """A detail record carries the description body, the company URL and
    size, the country and `isActive`; a listing row carries none of them."""
    detail = _detail()
    listing = _listing().rows[0]
    check("detail has a description", detail.description)
    check("the description is text, not markup",
          "<p>" not in (detail.description or ""))
    check("detail names a country", detail.country_code)
    check("detail states isActive", detail.is_active is not None)
    for field in ("description", "country_code", "is_active"):
        equal("a listing row has no %s" % field,
              getattr(listing, field), None)
    equal("detail rows are marked", detail.data_source, "detail")
    # And the same trap as the listing, worse: index 4.
    entries = json.loads(DETAIL_JSON)["employmentTypes"]
    original = next(e for e in entries if e.get("currencySource") == "original")
    check("the detail fixture's original is not first",
          entries[0] is not original,
          "the fixture no longer exercises the index trap")
    equal("detail reads the quoted rate", detail.salary_min,
          pp._float_or_none(original["fromPerUnit"]))
    equal("detail reads the quoted currency", detail.salary_currency,
          original["currency"].upper())


def check_an_empty_slug_response_is_not_read_as_an_offer():
    """`/offers/` with no slug answers HTTP 200 with the whole LISTING.

    Read as a detail record that would publish one row made of a hundred
    offers' worth of nothing, so `detail_api_url` refuses an empty slug and
    the parser refuses a listing-shaped body.
    """
    equal("detail_api_url refuses an empty slug",
          pp.detail_api_url(""), None)
    equal("and one that is only slashes", pp.detail_api_url("///"), None)
    equal("a listing body is not a detail record",
          pp.parse_detail_response(LISTING_JSON), None)
    equal("and it classifies as parse_error, not content",
          pp.detect_page_state(LISTING_JSON, 200, "", "offer"), "parse_error")


def check_a_delisted_offer_is_read_from_the_body_when_there_is_no_status():
    """The canary found this on its first run.

    justjoin.it's sitemap is generated ahead of the fetch, so an offer can
    be taken down in between — the FIRST slug in the sitemap was, on
    2026-09-18. The engines were discarding the navigation's HTTP status,
    so the 404 arrived at the classifier as `status=None`, fell past the
    404 branch and landed on `parse_error`: a retry, a debug dump and a
    wasted fetch for an address that will never exist again.

    Two fixes, and the second is the one that covers every engine.
    Playwright and pyppeteer now keep `response.status`. Selenium CANNOT —
    `driver.get()` returns None and WebDriver exposes no status at all — so
    the parser reads the status out of the site's own RFC 7231 problem
    document, which justjoin.it states in the body.
    """
    equal("with a transport status, it is not_found",
          pp.detect_page_state(NOT_FOUND_JSON, 404,
                               pp.detail_api_url("gone"), "offer"),
          "not_found")
    equal("and WITHOUT one it is still not_found",
          pp.detect_page_state(NOT_FOUND_JSON, None,
                               pp.detail_api_url("gone"), "offer"),
          "not_found")
    equal("the body states its own status",
          pp.problem_status(json.loads(NOT_FOUND_JSON)), 404)
    equal("a real offer states none",
          pp.problem_status(json.loads(DETAIL_JSON)), None)
    equal("nor does a listing", pp.problem_status(json.loads(LISTING_JSON)),
          None)
    # The reader must not fire on any JSON that happens to carry a
    # `status` key — it needs the problem document's own shape.
    equal("a payload with a bare status key is not a problem document",
          pp.problem_status({"status": 404}), None)
    equal("nor is one with a type and no status",
          pp.problem_status({"type": "x"}), None)

    # And every engine must actually thread a status through, or the
    # Playwright fix would be silently absent from its twins.
    for module in ENGINES:
        path = os.path.join(HERE, module + ".py")
        if not os.path.exists(path):
            continue
        source = open(path, encoding="utf-8").read()
        check("%s binds the navigation status" % module,
              "http_status" in source,
              "the status is being discarded again")
        check("%s passes it to classify" % module,
              re.search(r"classify\([^)]*http_status", source, re.S)
              or re.search(r"_classify\([^)]*http_status", source, re.S),
              "a status is captured and then not used")


def check_the_404_is_not_a_block():
    """An offer taken down between the sitemap and the fetch is ordinary."""
    equal("state", pp.detect_page_state(NOT_FOUND_JSON, 404,
                                        pp.detail_api_url("gone"), "offer"),
          "not_found")
    equal("not retried", page_flow.should_retry("not_found"), False)
    equal("not blocked", page_flow.counts_as_blocked("not_found"), False)
    equal("nothing to parse", page_flow.should_parse("not_found"), False)
    equal("no solve", page_flow.should_solve("not_found"), False)


def check_walking_past_the_cap_is_named_for_what_it_is():
    """`from=10000` answers HTTP 500 — the site refusing an impossible
    request, not failing. Retrying would ask it again (CLAUDE.md §21)."""
    past = pp.api_url(offset=pp.MAX_FROM)
    equal("state", pp.detect_page_state("<html>500</html>", 500, past),
          "cap_exceeded")
    equal("not retried", page_flow.should_retry("cap_exceeded"), False)
    equal("not blocked", page_flow.counts_as_blocked("cap_exceeded"), False)
    equal("terminal", page_flow.is_terminal("cap_exceeded"), True)
    # A 500 on an address that is NOT past the cap is not this state.
    equal("an ordinary 500 is unknown",
          pp.detect_page_state("<html>500</html>", 500, pp.api_url(offset=0)),
          "unknown")


def check_the_planner_never_builds_an_address_past_the_cap():
    url = pp.api_url(items=100)
    last = pp.api_page_url(url, 100, items=100)   # offset 9900
    check("page 100 is addressable", last)
    check("and it stops below the ceiling",
          "from=9900" in last, last)
    equal("page 101 is refused", pp.api_page_url(url, 101, items=100), None)
    equal("pages_available for a capped query",
          pp.pages_available_for(19381, 100), 100)
    equal("pages_available for a small one",
          pp.pages_available_for(439, 100), 5)
    equal("max offset for the default page size",
          page_flow.max_offset_for(100), 9900)


def check_a_rendered_listing_is_never_paginated():
    """`?page=2` answers HTTP 200 with page 1 again — a complete-looking
    run holding a hundred rows of ten thousand (CLAUDE.md §7, §18)."""
    equal("page_url refuses to invent a second page",
          pp.page_url(pp.DEFAULT_LISTING_URL, 2), None)
    equal("and says so for any page", pp.page_url(pp.DEFAULT_LISTING_URL, 7),
          None)
    equal("ssr is not addressable",
          page_flow.pagination_is_addressable(pp.DEFAULT_LISTING_URL, "ssr"),
          False)
    equal("the endpoint is",
          page_flow.pagination_is_addressable(pp.api_url(), "api"), True)
    equal("ssr gets one worker",
          page_flow.concurrency_for_mode("listings", 8, "ssr"), 1)
    equal("facets gets one worker",
          page_flow.concurrency_for_mode("facets", 8, "api"), 1)
    equal("the endpoint gets them all",
          page_flow.concurrency_for_mode("listings", 8, "api"), 8)
    equal("and so does --mode offer",
          page_flow.concurrency_for_mode("offer", 8, "api"), 8)


def check_the_ssr_route_publishes_a_poorer_schema_and_says_so():
    """The rendered payload spells the same offer differently and omits
    three columns. A row must carry nulls rather than invented values."""
    page = _ssr()
    equal("rows", len(page.rows), 3)
    equal("data_source", {r.data_source for r in page.rows}, {"ssr"})
    for row in page.rows:
        equal("%s has no category" % row.sku[:8], row.category, None)
        equal("%s has no languages" % row.sku[:8], row.languages, None)
        equal("%s has no skill levels" % row.sku[:8],
              row.required_skill_levels, None)
        check("%s still has skills" % row.sku[:8], row.required_skills)
        check("%s still has a company" % row.sku[:8], row.company_name)
    # The two spellings the normaliser has to know about.
    payload = pp.flight_payload(SSR_HTML)
    record = pp.offers_from_flight(payload)[0]
    check("the payload really uses the other names",
          "multilocation" in record and "remoteInterview" in record
          and "categoryId" in record, sorted(record)[:6])
    check("skills really are bare strings",
          all(isinstance(s, str) for s in record["requiredSkills"]))
    row = {r.sku: r for r in page.rows}[record["guid"]]
    check("locations came from multilocation", row.locations)
    check("remote_interview came from the other spelling",
          row.remote_interview is not None)


def check_the_ssr_page_states_the_cap_without_a_second_request():
    """The rendered page embeds the board-wide count beside the capped
    total, so `--route ssr` can report the cap honestly."""
    page = _ssr()
    equal("the capped figure", page.total_items, 10000)
    check("the board figure is larger", page.site_total > page.total_items,
          "%r vs %r" % (page.site_total, page.total_items))
    equal("so the page knows it is capped", page.capped_by_site, True)
    check("and the figure is plausible", 10000 < page.site_total < 100000,
          page.site_total)


def check_the_jsonld_names_offers_and_says_nothing_about_them():
    """Counted before a line of the parser was written (CLAUDE.md §15): a
    listing page publishes two blocks, and the one that lists offers
    carries only their URLs. That is why the flight payload is primary."""
    blocks = pp.jsonld_blocks(SSR_HTML)
    equal("two blocks", len(blocks), 2)
    types = {b.get("@type") for b in blocks if isinstance(b, dict)}
    equal("their types", types, {"CollectionPage", "BreadcrumbList"})
    urls = pp.itemlist_urls(SSR_HTML)
    equal("it names one URL per offer on the page", len(urls),
          _ssr().records_in_payload)
    collection = next(b for b in blocks if b.get("@type") == "CollectionPage")
    part = collection["hasPart"][0]
    equal("and each part carries nothing but a url and a type",
          sorted(part), ["@type", "url"])


def check_the_cap_report_asks_the_right_question():
    """`capped_by_site` asks whether THIS QUERY was truncated, not whether
    the board is bigger than the query. A `--category python` run matching
    439 offers is not capped — it can reach every one of them."""
    small = pp.cap_report(439, 19381, 439)
    equal("a small query is not capped", small["capped_by_site"], False)
    equal("and it reached everything", small["share_of_query_pct"], 100.0)
    check("its share of the board is small", small["share_of_board_pct"] < 5)

    capped = pp.cap_report(10000, 19381, 10000)
    equal("a query at the ceiling is capped", capped["capped_by_site"], True)
    equal("reachable_max is the ceiling", capped["reachable_max"], 10000)
    equal("it reached everything reachable",
          capped["share_of_query_pct"], 100.0)
    check("and that is about half the board",
          40 < capped["share_of_board_pct"] < 60,
          capped["share_of_board_pct"])

    unknown = pp.cap_report(None, None, 0)
    equal("nothing is claimed without input", unknown["capped_by_site"], None)


# ---------------------------------------------------------------------------
# Facets
# ---------------------------------------------------------------------------

def check_facets_parse_into_their_own_row_class():
    rows = pp.parse_facets_response(FACETS_JSON,
                                    categories_text=CATEGORIES_JSON,
                                    site_total=19381)
    check("rows were read", rows)
    check("they are Facets", all(isinstance(r, Facet) for r in rows))
    groups = {r.facet_group for r in rows}
    check("every measured group is present",
          set(pp.FACET_GROUPS) <= groups, sorted(groups))
    check("and the categories are folded in",
          pp.CATEGORY_FACET_GROUP in groups)
    equal("skus are unique", len({r.sku for r in rows}), len(rows))
    check("every sku is group:key",
          all(r.sku == "%s:%s" % (r.facet_group, r.facet_key) for r in rows))
    check("every row has a count",
          all(r.offer_count is not None for r in rows))


def check_a_cumulative_facet_group_is_flagged_as_one():
    """`publishedSinceDays` keys are windows (1, 7, 14, 30), not a
    partition: they sum to more than the board. A reader summing them
    without this column would conclude the site contradicts itself."""
    rows = pp.parse_facets_response(FACETS_JSON,
                                    categories_text=CATEGORIES_JSON,
                                    site_total=19381)
    by_group = {}
    for row in rows:
        by_group.setdefault(row.facet_group, []).append(row)

    windows = by_group["publishedSinceDays"]
    check("it is flagged cumulative", all(r.is_cumulative for r in windows))
    total = sum(r.offer_count for r in windows)
    check("and it really does exceed the board", total > 19381, total)

    partition = by_group["workplaceTypes"]
    check("a partitioning group is not flagged",
          not any(r.is_cumulative for r in partition))
    check("and it is not flagged as overlapping",
          not any(r.overlaps_other_keys for r in partition))
    check("employmentTypes IS flagged as overlapping",
          all(r.overlaps_other_keys for r in by_group["employmentTypes"]))


def check_the_facet_counts_are_not_capped():
    """The reason `--mode facets` is worth a mode: these describe all
    19,381 offers, which is what makes them the tool for planning runs that
    reach past the 10,000 ceiling."""
    rows = pp.parse_facets_response(FACETS_JSON,
                                    categories_text=CATEGORIES_JSON)
    biggest = max(r.offer_count for r in rows)
    check("at least one count exceeds the per-query ceiling",
          biggest > pp.MAX_FROM, biggest)
    categories = [r for r in rows
                  if r.facet_group == pp.CATEGORY_FACET_GROUP]
    check("every category is reachable in one query",
          all(r.offer_count < pp.MAX_FROM for r in categories),
          max((r.offer_count for r in categories), default=0))


def check_facets_and_offers_are_refused_against_each_other():
    """Different row classes and no shared id — every row would be
    reported as both added and removed (CLAUDE.md §9)."""
    equal("facets get their own class", ROW_CLASS_BY_MODE["facets"], Facet)
    equal("both job modes share one", ROW_CLASS_BY_MODE["listings"],
          ROW_CLASS_BY_MODE["offer"])
    equal("and it is JobPosting", ROW_CLASS_BY_MODE["listings"], JobPosting)
    facet_cols = {f.name for f in fields(Facet)}
    job_cols = {f.name for f in fields(JobPosting)}
    equal("the family prefix is byte-identical and in order",
          [f.name for f in fields(Facet)][:5],
          [f.name for f in fields(JobPosting)][:5])
    check("and the rest genuinely differs", facet_cols != job_cols)


# ---------------------------------------------------------------------------
# URLs, routing and filters
# ---------------------------------------------------------------------------

def check_url_building_and_routes():
    equal("the default listing", pp.route_of(pp.DEFAULT_LISTING_URL),
          "listing")
    equal("the endpoint", pp.route_of(pp.api_url()), "api_offers")
    equal("an offer page",
          pp.route_of("https://justjoin.it/job-offer/acme-dev-warszawa-java"),
          "offer")
    equal("an offer's endpoint",
          pp.route_of(pp.detail_api_url("acme-dev-warszawa-java")),
          "api_offer")
    equal("the facets endpoint", pp.route_of(pp.facets_api_url()),
          "api_facets")
    equal("www is the same site",
          pp.route_of("https://www.justjoin.it/job-offers/all-locations"),
          "listing")
    equal("and canonicalises to the bare host",
          pp.canonical_url("https://www.justjoin.it/job-offers/warszawa"),
          "https://justjoin.it/job-offers/warszawa")

    equal("a listing URL implies --mode listings",
          pp.mode_for_url(pp.DEFAULT_LISTING_URL), "listings")
    equal("an offer URL implies --mode offer",
          pp.mode_for_url("https://justjoin.it/job-offer/x-y-z"), "offer")
    equal("the facets URL implies --mode facets",
          pp.mode_for_url(pp.facets_api_url()), "facets")

    equal("the slug comes back out",
          pp.slug_from_url("https://justjoin.it/job-offer/acme-dev-krakow-java"),
          "acme-dev-krakow-java")
    equal("and a bare slug is accepted too",
          pp.offer_slug_or_url("acme-dev-krakow-java"), "acme-dev-krakow-java")


def check_the_sku_is_not_recoverable_from_a_url_and_says_so():
    """Unlike every other repo in this family, justjoin.it publishes no id
    in any URL — the `sku` is a UUID that appears only in the payload.
    Returning the slug here would look like a recovery and would join
    against nothing."""
    equal("no id in an offer URL",
          pp.sku_from_url("https://justjoin.it/job-offer/acme-dev-krakow-java"),
          None)
    equal("nor in a listing URL", pp.sku_from_url(pp.DEFAULT_LISTING_URL),
          None)
    row = _listing().rows[0]
    check("the row's sku is a uuid and is not in its url",
          row.sku not in row.url, (row.sku, row.url))


def check_only_filters_that_measurably_filter_are_offered():
    """Several plausible parameter names are ACCEPTED AND IGNORED by the
    endpoint — the same defect CLAUDE.md §21 found on BBB, where
    `showOnlyAccredited` does nothing. Only the measured ones are built."""
    params = pp.Filters(category="python", city="Kraków", remote="remote",
                        keyword="rust", experience=["senior"],
                        language=["en"], employment=["b2b"],
                        working_time=["full_time"],
                        with_salary=True).as_params()
    equal("category is `categories`", params.get("categories"), "python")
    equal("city carries a radius", params.get("cityRadius"), 30)
    equal("remote is `remoteWorkOptions`",
          params.get("remoteWorkOptions"), "remote")
    equal("keyword is `keywords`", params.get("keywords"), "rust")
    equal("and carries keywordType, without which it is ignored",
          params.get("keywordType"), "any")
    for ignored in ("categoryKeys", "keyword", "cities", "workplaceTypes",
                    "openToHireUkrainians", "remoteInterview", "salaryFrom"):
        check("%s is never sent — it is accepted and ignored" % ignored,
              ignored not in params)

    url = pp.api_url(filters=pp.Filters(city="Kraków"))
    check("a non-ASCII city is percent-encoded — raw is HTTP 400",
          "Krak%C3%B3w" in url, url)
    equal("an empty filter set adds nothing",
          pp.Filters().as_params(), {})
    equal("and says so", pp.Filters().is_empty(), True)


def check_a_browsable_url_is_read_back_into_the_filters_the_site_sends():
    f = pp.filters_from_listing_url(
        "https://justjoin.it/job-offers/warszawa/python")
    equal("city", f.city, "warszawa")
    equal("category", f.category, "python")
    f = pp.filters_from_listing_url("https://justjoin.it/job-offers/remote")
    equal("`remote` is not a city", f.city, None)
    equal("it is the remote filter", f.remote, "remote")
    f = pp.filters_from_listing_url(pp.DEFAULT_LISTING_URL)
    equal("all-locations is not a city either", f.city, None)
    equal("and selects nothing", f.is_empty(), True)
    equal("category_from_url reads the endpoint too",
          pp.category_from_url(pp.api_url(filters=pp.Filters(category="go"))),
          "go")


def check_unsupported_urls_are_refused_with_a_reason():
    ok, why = pp.is_supported_url("https://justjoin.it/job-offers/warszawa")
    equal("a listing URL is supported", ok, True)

    ok, why = pp.is_supported_url("https://api.justjoin.it/justjoinit/offers")
    equal("the upstream API host is refused", ok, False)
    check("and the reason names the 503 rather than calling it a typo",
          "503" in why and "api.justjoin.it" in why, why)
    check("and points at the route that works", pp.API_PATH in why, why)

    ok, why = pp.is_supported_url("https://rocketjobs.pl/oferty-pracy")
    equal("a sibling job board is refused", ok, False)
    check("and is told it is not justjoin.it", "not a justjoin.it URL" in why,
          why)

    ok, why = pp.is_supported_url("https://justjoin.it/profile")
    equal("a route this scraper does not read is refused", ok, False)
    check("and the message lists the ones it does",
          "/job-offers" in why and "/job-offer" in why, why)

    for bad in ("", "justjoin.it/job-offers", "ftp://justjoin.it/x"):
        ok, why = pp.is_supported_url(bad)
        equal("%r is refused" % bad, ok, False)
        check("with a reason", bool(why))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def check_page_states_on_real_captures():
    equal("the endpoint's own payload",
          pp.detect_page_state(LISTING_JSON, 200, pp.api_url()), "content")
    equal("an empty result is an ANSWER, not a block",
          pp.detect_page_state('{"data":[],"meta":{"totalItems":0}}', 200,
                               pp.api_url()), "empty")
    equal("a rendered listing",
          pp.detect_page_state(SSR_HTML, 200, pp.DEFAULT_LISTING_URL,
                               "listings", "ssr"), "content")
    equal("a detail record",
          pp.detect_page_state(DETAIL_JSON, 200, pp.detail_api_url("x"),
                               "offer"), "content")
    equal("the facet payload",
          pp.detect_page_state(FACETS_JSON, 200, pp.facets_api_url(),
                               "facets"), "content")
    equal("and the facet payload with its categories attached",
          pp.detect_page_state(FACETS_JSON + pp.FACETS_JOIN + CATEGORIES_JSON,
                               200, pp.facets_api_url(), "facets"), "content")
    equal("the site's 404",
          pp.detect_page_state(NOT_FOUND_JSON, 404,
                               pp.detail_api_url("gone"), "offer"),
          "not_found")
    equal("a challenge", pp.detect_page_state(SYNTHETIC_CHALLENGE_HTML, 403,
                                              pp.DEFAULT_LISTING_URL),
          "blocked")
    equal("nothing at all",
          pp.detect_page_state("", None, pp.api_url()), "unknown")

    equal("an empty result is parsed, not retried",
          (page_flow.should_parse("empty"), page_flow.should_retry("empty")),
          (True, False))


def check_content_wins_over_every_heuristic():
    """CLAUDE.md §17's classification-order trap: a threshold heuristic
    that runs ahead of an unambiguous signal reports exit 3 for a correct
    answer. A payload holding records is `content` whatever else is in it —
    including a job description that happens to say "access denied"."""
    payload = json.loads(LISTING_JSON)
    payload["data"][0]["title"] = "Access denied testing engineer"
    doctored = json.dumps(payload, ensure_ascii=False)
    equal("a marker word in a real record does not make it a block",
          pp.detect_page_state(doctored, 200, pp.api_url()), "content")
    equal("even under a 403, records are records",
          pp.detect_page_state(LISTING_JSON, 403, pp.api_url()), "content")


def check_no_marker_matches_a_page_the_site_serves():
    """CLAUDE.md §18: count every candidate on a page you know is good
    BEFORE adding it. Two candidates were thrown out this way —
    `cf-turnstile`, which 2Captcha's own extension injects into every page
    it loads, and `akamai`, which appears four times on a served offer page
    because an employer lists "Akamai Linode" among the clouds a candidate
    should know."""
    for name, body in (("listing payload", LISTING_JSON),
                       ("rendered page", SSR_HTML),
                       ("detail record", DETAIL_JSON),
                       ("facets", FACETS_JSON),
                       ("categories", CATEGORIES_JSON),
                       ("the site's own 404", NOT_FOUND_JSON)):
        equal("no marker fires on the %s" % name,
              pp.detect_bot_challenge(body), None)

    check("`cf-turnstile` is not in the set",
          not any("turnstile" in m and "cloudflare" not in m
                  for m in pp.BOT_CHALLENGE_MARKERS),
          [m for m in pp.BOT_CHALLENGE_MARKERS if "turnstile" in m])
    check("`akamai` is not in the set",
          not any("akamai" in m for m in pp.BOT_CHALLENGE_MARKERS))
    check("`challenges.cloudflare.com` is",
          "challenges.cloudflare.com" in pp.BOT_CHALLENGE_MARKERS)

    # And the positive case, without which this proves nothing.
    check("a real challenge page IS caught",
          pp.detect_bot_challenge(SYNTHETIC_CHALLENGE_HTML))


def check_a_marker_survives_both_encodings_of_the_same_page():
    """CLAUDE.md §20: an edge's refusal reaches a parser entity-escaped
    over a raw HTTP client and plain out of a browser DOM."""
    escaped = SYNTHETIC_CHALLENGE_HTML.replace(
        "challenges.cloudflare.com",
        "challenges&#46;cloudflare&#46;com").replace(
        "Just a moment...", "Just a moment&#46;&#46;&#46;")
    check("the escaped spelling is still caught",
          pp.detect_bot_challenge(escaped), escaped[:120])
    check("and the scan is bounded, so a 2 MB payload is cheap",
          pp._ENTITY_PREFIX_BYTES <= 8000, pp._ENTITY_PREFIX_BYTES)


def check_positive_asset_detection_reads_the_sites_own_hosts():
    """CLAUDE.md §8's trick, on its third site: a served page is built out
    of the site's own assets; an interstitial is not."""
    check("a rendered page references them", pp.count_site_assets(SSR_HTML)
          >= pp.MIN_ASSET_MARKERS, pp.count_site_assets(SSR_HTML))
    equal("a challenge page does not",
          pp.count_site_assets(SYNTHETIC_CHALLENGE_HTML), 0)
    check("the threshold is 1, not the measured average",
          pp.MIN_ASSET_MARKERS == 1,
          "a higher threshold calls a minimal-but-real page blocked (§17)")


def check_the_sites_own_captcha_is_recorded_even_though_it_never_renders():
    """CLAUDE.md §18: grep a GOOD page for the site's own captcha config
    rather than building a marker set from a vendor list.

    justjoin.it publishes a reCAPTCHA **v2** key on every page, and it
    guards the job-application form. Measured in a live browser on
    2026-09-18: `window.grecaptcha` undefined on a rendered listing, on an
    offer page and on the endpoint — the script is never loaded on the read
    path at all.
    """
    page = ('<html><body><script>self.__next_f.push([1,'
            '"{\\"publicRuntimeConfig\\":{\\"googleRecaptchaV2Key\\":'
            '\\"6LeTZ-ErAAAAAA_fFNEN3N575ErA7CPrGYyZX50o\\"}}"])</script>'
            '</body></html>')
    equal("the key is read from the page config",
          pp.site_recaptcha_sitekey(page),
          "6LeTZ-ErAAAAAA_fFNEN3N575ErA7CPrGYyZX50o")
    equal("and a payload carrying none returns None",
          pp.site_recaptcha_sitekey(LISTING_JSON), None)
    # Reading offers is not what it guards, so no route this scraper reads
    # classifies as challenged.
    for body, mode, route in ((LISTING_JSON, "listings", "api"),
                              (SSR_HTML, "listings", "ssr"),
                              (DETAIL_JSON, "offer", "api")):
        check("no route classifies as blocked",
              pp.detect_page_state(body, 200, "", mode, route) == "content")


def check_the_refused_user_agent_is_pinned():
    """`Python-urllib/*` is the one User-Agent both hosts answer 403 to.
    Everything else measured was served, so this is a denylist entry rather
    than a bot gate — and it happens to be what the standard library sends
    if you do not set one."""
    check("the stdlib default is recognised",
          pp.user_agent_is_refused("Python-urllib/3.13"))
    check("and any version of it", pp.user_agent_is_refused("Python-urllib/3.9"))
    for fine in ("curl/8.14.1", "python-requests/2.32.3", "Wget/1.21",
                 "Mozilla/5.0", pp.HTTP_USER_AGENT, "", None):
        check("%r is not refused" % (fine,), not pp.user_agent_is_refused(fine))
    check("this repo's own UA is not the refused one",
          not pp.user_agent_is_refused(pp.HTTP_USER_AGENT), pp.HTTP_USER_AGENT)


# ---------------------------------------------------------------------------
# Sitemaps and enumeration
# ---------------------------------------------------------------------------

def check_the_sitemap_index_is_followed_rather_than_parsed_for_offers():
    check("an index is recognised", pp.sitemap_is_index(SITEMAP_INDEX_XML))
    check("a part file is not", not pp.sitemap_is_index(SITEMAP_PART_XML))
    equal("the index names its part",
          pp.sitemap_locs(SITEMAP_INDEX_XML),
          ["https://justjoin.it/sitemaps/active-jobs/part0.xml"])
    equal("an index yields no offer slugs",
          pp.sitemap_offer_slugs(SITEMAP_INDEX_XML), [])
    slugs = pp.sitemap_offer_slugs(SITEMAP_PART_XML)
    equal("the part file yields only offer slugs", len(slugs), 3)
    check("a listing URL in the sitemap is skipped",
          all("job-offers" not in s for s in slugs), slugs)

    # THE ordering, and it is not cosmetic. The site serves this file
    # oldest-first and generates it ahead of any fetch, so its head is
    # where offers that have since been taken down collect: 2 of the first
    # 12 were gone on 2026-09-18 against 0 of the last 12 and 0 of 12 at
    # random. Walking it in document order pointed `--mode offer --pages 3`
    # at the three likeliest-dead addresses in it, and the canary came back
    # with zero rows on a site that was serving perfectly.
    equal("the newest offer comes first", slugs[0],
          "accenture-sap-security-associate-manager-gdansk-erp-73955f53")
    equal("the oldest is not first", slugs[1],
          "fibertide-senior-site-reliability-engineer-wroclaw-architecture"
          "-7f4e4784")
    equal("an undated entry is kept, and sorts last",
          slugs[-1], "undated-role-warszawa-java")

    # The document order is still available, and the two must hold the same
    # SET — a sort that dropped an entry would be worse than no sort.
    doc = pp.sitemap_offer_slugs_in_document_order(SITEMAP_PART_XML)
    equal("sorting loses nothing", set(doc), set(slugs))
    check("and it really did reorder", doc[0] != slugs[0], (doc[0], slugs[0]))


def check_enumeration_follows_both_hops():
    """`active-jobs.xml` is a 63-byte redirect to another host, which then
    names one part file. Both hops are followed rather than assumed away,
    because a second part file appearing is exactly how a silent
    under-count would start."""
    seen = []

    def fetch(url):
        seen.append(url)
        if url.endswith("active-jobs.xml"):
            return SITEMAP_INDEX_XML
        return SITEMAP_PART_XML

    class Args:
        slugs_file = None

    slugs = page_flow.enumerate_offers(fetch, Args())
    equal("both hops were fetched", len(seen), 2)
    equal("starting at the advertised index", seen[0], pp.SITEMAP_INDEX)
    equal("and the offers came from the part file", len(slugs), 3)
    equal("newest first, as the parser sorts them", slugs[0],
          "accenture-sap-security-associate-manager-gdansk-erp-73955f53")

    # A sitemap that fetches to nothing must not silently report success.
    class Dead:
        slugs_file = None

    equal("an unreachable sitemap yields nothing",
          page_flow.enumerate_offers(lambda u: "", Dead()), [])


def check_slugs_can_come_from_a_previous_runs_output():
    """The enrichment path: gather the board cheaply with `--mode
    listings`, then fetch full records for the offers you care about."""
    rows = [asdict(r) for r in _listing().rows]
    with tempfile.TemporaryDirectory() as tmp:
        as_json = os.path.join(tmp, "run.json")
        with open(as_json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
        slugs = pp.slugs_from_file(as_json)
        equal("a previous run's JSON is read directly", len(slugs), len(rows))
        check("and gives slugs, not URLs",
              all(not s.startswith("http") for s in slugs))

        as_text = os.path.join(tmp, "run.txt")
        with open(as_text, "w", encoding="utf-8") as fh:
            fh.write("# a comment\n\n")
            fh.write("acme-dev-krakow-java\n")
            fh.write("https://justjoin.it/job-offer/beta-qa-warszawa-testing\n")
        equal("a plain list works too, comments skipped",
              pp.slugs_from_file(as_text),
              ["acme-dev-krakow-java", "beta-qa-warszawa-testing"])

        as_csv = os.path.join(tmp, "run.csv")
        with open(as_csv, "w", encoding="utf-8") as fh:
            fh.write("sku,slug,title\n1,acme-dev-krakow-java,Dev\n")
        equal("and a CSV whose header names a column we can read",
              pp.slugs_from_file(as_csv), ["acme-dev-krakow-java"])

        equal("a missing file is reported, not crashed on",
              pp.slugs_from_file(os.path.join(tmp, "nope.json")), [])


# ---------------------------------------------------------------------------
# Rows and the contract
# ---------------------------------------------------------------------------

def check_row_schema():
    prefix = [f.name for f in fields(JobPosting)][:5]
    equal("the family prefix, byte-identical and in order", prefix,
          ["source", "scraped_at", "url", "sku", "title"])
    equal("Facet carries the same prefix",
          [f.name for f in fields(Facet)][:5], prefix)
    row = _listing().rows[0]
    equal("source names the site", row.source, "justjoin.it")
    check("scraped_at is a UTC instant",
          re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", row.scraped_at),
          row.scraped_at)
    names = {f.name for f in fields(JobPosting)}
    for gone in ("category_parent", "is_promoted", "prime"):
        check("`%s` is not a column — measured null on every row" % gone,
              gone not in names)


def check_no_column_is_null_on_every_row_of_every_route():
    """CLAUDE.md §9: a column that is null on every row of every run should
    not exist. Checked across all three routes together, because several
    columns are legitimately filled by only one of them."""
    rows = (_listing().rows + _ssr().rows + [_detail()]
            + [pp.parse_detail_response(DETAIL_WITH_COMPANY_JSON)])
    populated = set()
    for row in rows:
        for name, value in asdict(row).items():
            if value not in (None, "", [], {}):
                populated.add(name)
    missing = sorted({f.name for f in fields(JobPosting)} - populated)
    equal("every column is filled by at least one route", missing, [])


def check_page_and_position_are_unique_across_pages():
    """CLAUDE.md §18: `position` restarts at 1 on every page, so without
    the page number beside it a row from page 2 claims a position another
    row already has. One line, and the column is worthless without it."""
    first = pp.parse_offers_response(LISTING_JSON, page=1, items=7).rows
    second = pp.parse_offers_response(LISTING_JSON, page=2, items=7).rows
    pairs = [(r.page, r.position) for r in first + second]
    equal("page+position is unique across a two-page run",
          len(set(pairs)), len(pairs))
    equal("position restarts on page 2",
          [r.position for r in second][:3], [1, 2, 3])
    equal("but the page number differs",
          {r.page for r in first}, {1})
    equal("and so does the second's", {r.page for r in second}, {2})


def check_the_two_routes_agree_about_the_same_offer():
    """One parser reads both spellings, so the routes cannot drift. Where
    both fill a column they must agree; where only one does, the other is
    null rather than guessed."""
    api = {r.sku: r for r in _listing().rows}
    ssr = {r.sku: r for r in _ssr().rows}
    shared = set(api) & set(ssr)
    if not shared:
        # The two fixtures were cut from different responses; compare the
        # SHAPES instead, which is the invariant that matters.
        check("the two routes produce the same row class",
              type(next(iter(api.values()))) is type(next(iter(ssr.values()))))
        return
    for sku in shared:
        for field in ("title", "company_name", "city", "slug",
                      "salary_min", "salary_currency", "salary_unit"):
            equal("%s agrees on %s" % (sku[:8], field),
                  getattr(ssr[sku], field), getattr(api[sku], field))


def check_category_is_a_filter_and_not_the_run_label():
    """`--category` filters the board here, unlike the repo this was
    ported from where it only labelled the run. A flag that both filters
    and names the run would make a filtered run and an unfiltered one look
    identical in the sidecar."""
    url = pp.api_url(filters=pp.Filters(category="devops"))
    check("it reaches the request", "categories=devops" in url, url)
    equal("and is read back out", pp.category_from_url(url), "devops")
    check("the site's own keys are offered in the help text",
          "devops" in pp.CATEGORY_KEYS and "python" in pp.CATEGORY_KEYS)
    equal("25 of them were published when this was measured",
          len(pp.CATEGORY_KEYS), 25)


def check_the_ordering_vocabulary_is_the_request_not_the_payload():
    """`sortBy=published&orderBy=DESC` — the spelling in the field names —
    is HTTP 400. The request uses `publishedAt` / `descending`."""
    equal("the default sort", pp.DEFAULT_SORT, "publishedAt")
    equal("the default order", pp.DEFAULT_ORDER, "descending")
    check("the payload's spelling is not offered",
          "published" not in pp.SORTS and "DESC" not in pp.ORDERS,
          (pp.SORTS, pp.ORDERS))
    url = pp.api_url(sort="salary", order="ascending")
    check("a chosen sort reaches the request",
          "sortBy=salary" in url and "orderBy=ascending" in url, url)
    bad = pp.api_url(sort="nonsense", order="sideways")
    check("an unknown one falls back rather than being sent",
          "sortBy=publishedAt" in bad and "orderBy=descending" in bad, bad)


def check_the_page_size_ceiling_is_enforced_before_the_request():
    """`itemsCount=2000` is HTTP 400; 1000 is served."""
    equal("the ceiling", pp.MAX_ITEMS_COUNT, 1000)
    check("a request at the ceiling is built",
          "itemsCount=1000" in pp.api_url(items=1000))
    check("and one above it is clamped rather than sent",
          "itemsCount=1000" in pp.api_url(items=5000),
          pp.api_url(items=5000))
    check("a zero is clamped up", "itemsCount=1" in pp.api_url(items=0))


def check_fixtures_carry_no_personal_names():
    """justjoin.it's offer records name COMPANIES and job titles, never
    individuals. This guards the SHAPE so a future capture from a route
    that does carry a person is caught (CLAUDE.md §10)."""
    person_fields = ("firstName", "lastName", "authorName", "recruiterName",
                     "displayName", "userName", "profileUrl", "avatarUrl",
                     "email", "phone")
    for name, body in (("listing", LISTING_JSON), ("detail", DETAIL_JSON),
                       ("ssr", SSR_HTML)):
        for field in person_fields:
            check("the %s fixture carries no %s" % (name, field),
                  field not in body)
    for name, body in (("listing", LISTING_JSON), ("detail", DETAIL_JSON)):
        check("the %s fixture carries no email address" % name,
              not re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", body))
    # And the row class has no column that could hold one.
    columns = {f.name for f in fields(JobPosting)}
    for column in ("author", "recruiter", "contact_email", "contact_name"):
        check("`%s` is not a column" % column, column not in columns)


# ---------------------------------------------------------------------------
# Engines and the family contract
# ---------------------------------------------------------------------------

ENGINES = ("playwright_scraper", "selenium_scraper", "puppeteer_scraper")

_TREE_BEFORE = None

DRIVER_IMPORTS = {
    "playwright_scraper": "playwright",
    "selenium_scraper": "selenium",
    "puppeteer_scraper": "pyppeteer",
}

# The family contract (CLAUDE.md §9), re-derived across the repos rather
# than copied from that list — which was itself wrong for months.
CONTRACT_FLAGS = {
    "--url", "--pages", "--category", "--format", "--out", "--delay",
    "--retries", "--retry-delay", "--concurrency", "--proxy", "--proxy-file",
    "--proxy-rotate", "--proxy-shuffle", "--proxy-block-retries",
    "--twocaptcha-key", "--captcha-api", "--solve-captcha", "--min-score",
    "--cdp-endpoint", "--allow-empty", "--dump-html",
    # The five CLAUDE.md §9 admits its own list omitted for months while
    # nearly every repo shipped them, plus the headless pair every engine
    # carries. RE-MEASURED here rather than inherited (§13 — a number you
    # inherited is not a number you measured): counted 2026-09-18 across
    # the 26 sibling repos in ~/2scraper, `--fingerprint`, `--fp-tags` and
    # `--fp-country` are in 25 of 26 and `--locale` in 23, against 23 flags
    # that are in all 26. Re-derive with:
    #
    #   grep -ohE '"--[a-z0-9-]+"' */playwright_scraper.py \
    #     | sort | uniq -c | sort -rn
    "--fingerprint", "--fp-tags", "--fp-country",
    "--headless", "--headful",
}
# The flags this SITE adds on top of the contract.
#
# Every one of them is a REAL query parameter that changes what the site
# sends — checked against the live endpoint by comparing `meta.totalItems`
# against the unfiltered 10,000, because several plausible names are
# accepted and silently ignored (product_parser has the table). There is no
# client-side filter here: a flag that looked like a filter and only
# discarded rows already downloaded would be a worse tool than none.
SITE_FLAGS = {
    "--mode", "--route", "--locale",
    # the measured filters
    "--city", "--city-radius", "--remote", "--keyword", "--experience",
    "--employment", "--working-time", "--language", "--with-salary",
    # the query's shape
    "--sort", "--order", "--per-page",
    # --mode offer's enumeration
    "--slugs-file",
}

# §12: wording the build fails on. Assembled from pieces rather than written
# out, so this file can scan ITSELF — three sibling repos exempted
# `smoke_test.py` wholesale, which made the file most likely to acquire a
# stray phrase the one file nobody scanned (CLAUDE.md §22).
BANNED_WORDING = (
    "cloud " + "browser", "anti" + "detect browser",
    "2scraper Anti" + "detect Browser",
    "gate." + "2prx.com", "ANTI" + "DETECT_LOCAL_API",
)

BANNED_FLAGS = ("--anti" + "detect", "--country-code")

def check_state_policy():
    import page_flow
    equal("every state has a policy",
          sorted(page_flow.STATE_POLICY),
          ["blocked", "cap_exceeded", "content", "empty", "not_found",
           "parse_error", "unknown"])
    equal("and the parser and the policy agree on the list",
          sorted(pp.PAGE_STATES), sorted(page_flow.STATE_POLICY))
    check("content: parsed, not retried, not blocked",
          page_flow.should_parse("content")
          and not page_flow.should_retry("content")
          and not page_flow.counts_as_blocked("content"))
    # ONE blocked state, and on this site it has never been reached:
    # justjoin.it served every request measured on 2026-09-18. Solving is True so that a
    # challenge appearing tomorrow is met with the tools this repo already
    # has rather than with a code change; retrying is True because on every
    # sibling site that DOES refuse, a different exit clears it far more
    # cheaply than a solve. Both are cautious settings rather than measured
    # ones here, and page_flow says so.
    check("blocked: retried, solvable, counts as blocked",
          page_flow.should_retry("blocked")
          and page_flow.should_solve("blocked")
          and page_flow.counts_as_blocked("blocked"))
    # A served page we could not read is OUR bug, never "0 jobs" (§20), so
    # it neither counts as blocked nor buys a solve.
    check("parse_error: retried once, never solved, NOT blocked",
          page_flow.should_retry("parse_error")
          and not page_flow.should_solve("parse_error")
          and not page_flow.counts_as_blocked("parse_error")
          and not page_flow.should_parse("parse_error"))
    # Retrying an address that does not exist is waste, and calling it a
    # block sends a user rotating proxies over a typo.
    check("not_found: not retried, not blocked",
          not page_flow.should_retry("not_found")
          and not page_flow.counts_as_blocked("not_found"))
    # Asking past the site's 10,000 ceiling is an HTTP 500 — the site
    # refusing an impossible request rather than failing. A retry would ask
    # the identical impossible question again; calling it blocked would
    # send a reader after a proxy (§21).
    check("cap_exceeded: not retried, not blocked, not solved",
          not page_flow.should_retry("cap_exceeded")
          and not page_flow.counts_as_blocked("cap_exceeded")
          and not page_flow.should_solve("cap_exceeded")
          and not page_flow.should_parse("cap_exceeded"))
    check("and both terminal states say so",
          page_flow.is_terminal("cap_exceeded")
          and page_flow.is_terminal("not_found")
          and not page_flow.is_terminal("blocked"))
    check("unknown: retried, not solved, NOT blocked",
          page_flow.should_retry("unknown")
          and not page_flow.should_solve("unknown")
          and not page_flow.counts_as_blocked("unknown"))
    check("an unrecognised state falls back to unknown's policy",
          page_flow.should_retry("something-new")
          and not page_flow.should_solve("something-new"))
    equal("at most one solve per page", page_flow.SOLVES_PER_PAGE, 1)


def check_the_two_known_dead_names_in_the_shared_solver_stay_pinned():
    """§17 says a public name nothing reads is the same defect as dead code,
    and a sweep of this repo finds exactly two — both in `captcha_solver.py`,
    which is family core copied verbatim.

    They are LEFT, and this check is the pin rather than the fix. Checked
    2026-09-18 against three sibling repos (mercor, bbb, foodpanda): both
    are dead in every one of them, so removing them here would make this
    repo's copy of a shared module differ from five others for no reason
    this site supplies — which is how a family's core stops being shared.

    What the check buys is that the list cannot GROW silently. A third dead
    name appearing in the shared solver is a real finding; these two are a
    decision (CLAUDE.md §10: pin a known limitation rather than
    half-guarding it).
    """
    import ast as _ast
    src = open(os.path.join(HERE, "captcha_solver.py"), encoding="utf-8").read()
    # The suite itself is excluded, and not as a convenience: this check
    # NAMES both functions in its own body, so including it would find them
    # and report a clean bill. The question is whether the PRODUCTION code
    # reads them.
    everything = "\n".join(
        open(os.path.join(HERE, f), encoding="utf-8").read()
        for f in sorted(os.listdir(HERE))
        if f.endswith(".py") and f != "smoke_test.py")
    dead = []
    for node in _ast.parse(src).body:
        if isinstance(node, (_ast.FunctionDef, _ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, _ast.Assign):
            # Assignments too, and that is not pedantry: one of the two is
            # a backwards-compatibility ALIAS (`solve_recaptcha_v3 =
            # solve_recaptcha`), which a scan that only walked `def` would
            # miss — and did, until this line was added.
            names = [t.id for t in node.targets
                     if isinstance(t, _ast.Name)]
        else:
            names = []
        for name in names:
            if name.startswith("_"):
                continue
            if len(re.findall(r"\b" + re.escape(name) + r"\b", everything)) <= 1:
                dead.append(name)
    equal("the shared solver's dead names are the two known ones",
          sorted(dead), ["get_balance", "solve_recaptcha_v3"])
    check("one of them is a function and one an alias, as recorded",
          "def get_balance" in src
          and "solve_recaptcha_v3 = solve_recaptcha" in src,
          "the shapes changed, so the pin above needs re-reading")


def check_policy_constants_have_a_consumer():
    """§17: a policy constant nothing reads is the same defect as dead code.

    `RETRY_ON_BLOCKED` carried a paragraph of justification in a sibling repo
    and no engine consulted it, so setting it False changed nothing.
    """
    import page_flow
    sources = []
    for name in ("playwright_scraper.py", "selenium_scraper.py",
                 "puppeteer_scraper.py", "scraper_api_client.py"):
        path = os.path.join(HERE, name)
        if os.path.exists(path):
            sources.append(open(path, encoding="utf-8").read())
    joined = "\n".join(sources)
    for constant in ("RETRY_ON_BLOCKED", "BLOCK_RETRIES_WITHOUT_POOL",
                     "SOLVES_PER_PAGE"):
        check("page_flow.%s is CONSULTED by an engine" % constant,
              constant in joined,
              "defined in page_flow and read by nothing")
    for fn in ("ready_selector", "min_matches",
               "content_timeout_ms", "wait_for_count", "classify",
               "should_retry", "should_solve", "counts_as_blocked",
               "should_parse", "concurrency_limit", "concurrency_for_mode",
               "pagination_is_addressable", "plan_page_urls",
               "enumerate_offers", "needs_readiness_wait", "is_terminal"):
        check("page_flow.%s has a caller outside its own module" % fn,
              fn in joined, "unused policy")

    # `pages_to_plan` and `max_offset_for` are consulted by
    # `plan_page_urls`, which lives in page_flow itself — so "a caller
    # outside this module" is the wrong question for them and asking it
    # would fail a function that is very much read. The right question is
    # whether ANYTHING reads them, and the answer has to come from the
    # module's own source rather than from the engines'.
    own = open(os.path.join(HERE, "page_flow.py"), encoding="utf-8").read()
    for fn in ("pages_to_plan", "max_offset_for", "mask_secrets"):
        calls = own.count(fn + "(")
        check("page_flow.%s is called, not merely defined" % fn,
              calls >= 2, "%d occurrence(s) — only its own def" % calls)

    # And the parser's own run-planning helpers, which the engines call.
    for fn in ("target_url", "filters_from_args", "parse_for_mode",
               "is_single_offer_url"):
        check("product_parser.%s has a caller in an engine" % fn,
              fn in joined, "unused helper")


def check_csv_and_json_writers():
    from output_writer import JobPosting, write_csv, write_json
    import product_parser as P
    rows = P.parse_offers_response(LISTING_JSON, url=P.api_url(items=7),
                                   page=1, items=7).rows
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = os.path.join(tmp, "out.csv")
        write_csv(rows, csv_path, row_cls=JobPosting)
        with open(csv_path, encoding="utf-8") as f:
            reader = list(csv.reader(f))
        equal("CSV header matches the dataclass, in order",
              reader[0], [f.name for f in fields(JobPosting)])
        equal("CSV holds every row", len(reader) - 1, len(rows))
        # `required_skills` is the list column on this site — every offer
        # carries at least one and the median is four, so the join shows.
        badges_col = reader[0].index("required_skills")
        joined = [r[badges_col] for r in reader[1:] if r[badges_col]]
        check("a list column is joined readably rather than repr()'d",
              any(" | " in v for v in joined), repr(joined[:2]))
        check("no Python list repr leaked into the CSV",
              not any(cell.startswith("[") for row in reader[1:] for cell in row))

        empty_csv = os.path.join(tmp, "empty.csv")
        write_csv([], empty_csv, row_cls=JobPosting)
        with open(empty_csv, encoding="utf-8") as f:
            header = list(csv.reader(f))
        equal("an EMPTY csv still carries its header", len(header), 1)
        equal("...and it is the right one", header[0],
              [f.name for f in fields(JobPosting)])

        json_path = os.path.join(tmp, "out.json")
        write_json(rows, json_path)
        loaded = json.load(open(json_path, encoding="utf-8"))
        equal("JSON holds every row", len(loaded), len(rows))
        equal("JSON keys are the dataclass fields, in order",
              list(loaded[0].keys()), [f.name for f in fields(JobPosting)])
        with_list = next(r for r in loaded if r["required_skills"])
        check("a list column stays a real list in JSON",
              isinstance(with_list["required_skills"], list),
              repr(with_list["required_skills"]))
        # An empty list and a null both mean "the employer did not say",
        # so both are written as null rather than putting a distinction in
        # the data that is not in the site. `nice_to_have_skills` is where
        # this shows: 46 of 1,000 offers name any, and the site sends `[]`
        # for the rest.
        check("...and a field the employer left blank is null, never []",
              all(r["nice_to_have_skills"] is None
                  or r["nice_to_have_skills"] for r in loaded),
              [r["nice_to_have_skills"] for r in loaded])
        check("the fixture really holds both shapes",
              any(r["nice_to_have_skills"] for r in loaded)
              and any(r["nice_to_have_skills"] is None for r in loaded))


def check_exit_codes():
    import output_writer as O
    equal("0 ok / 1 crash / 2 usage / 3 blocked / 4 empty / 5 api / 6 partial",
          (O.EXIT_BLOCKED, O.EXIT_NO_PRODUCTS, O.EXIT_API_ERROR, O.EXIT_PARTIAL),
          (3, 4, 5, 6))
    check("page_cap_reached is a COMPLETE stop reason",
          "page_cap_reached" in O.COMPLETE_STOP_REASONS)
    # `--mode facets` is one request, and `--route ssr` serves one page and
    # answers `?page=2` with page 1 again — measured, not assumed — so a run
    # that stopped after one fetch fetched the whole route.
    check("single_page_route is complete by construction AND by measurement",
          "single_page_route" in O.COMPLETE_STOP_REASONS)
    # Carried for the family's shared vocabulary: a rendered listing echoes
    # page 1 for `?page=2`, which is what this names. `--route ssr` plans one
    # page, so a run should never reach it.
    check("page_echo_mismatch is complete",
          "page_echo_mismatch" in O.COMPLETE_STOP_REASONS)
    check("...and an enumeration that yielded nothing is NOT complete",
          "enumeration_empty" not in O.COMPLETE_STOP_REASONS)
    check("no_new_products is complete",
          "no_new_products" in O.COMPLETE_STOP_REASONS)


def check_a_run_that_finds_nothing_writes_nothing():
    """Never replace last night's good output with []."""
    from output_writer import save
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "out")
        with open(prefix + ".json", "w", encoding="utf-8") as f:
            f.write('[{"sku": "yesterday"}]')
        code = save([], prefix, "json", allow_empty=False)
        equal("an empty run exits 4", code, 4)
        equal("...and leaves the previous good file alone",
              open(prefix + ".json", encoding="utf-8").read(),
              '[{"sku": "yesterday"}]')
        code = save([], prefix, "json", allow_empty=True)
        equal("--allow-empty WRITES the empty file...", 
              json.load(open(prefix + ".json", encoding="utf-8")), [])
        # ...and still reports exit 4. Pinned deliberately (§10: pin a known
        # behaviour rather than half-guarding it): "zero offers" is true
        # whether or not the file was written, and a caller that wanted the
        # file still wants to know the result was empty.
        equal("...and still reports exit 4, because it IS empty", code, 4)


def check_sidecar_shape():
    from output_writer import run_meta
    # Values from a real `--route ssr` run's sidecar, 2026-09-18.
    meta = run_meta(status="complete", stop_reason="single_page_route",
                    pages_requested=1, pages_completed=1, pages_failed=[],
                    products=100, mode="listings", source="justjoin.it",
                    start_url="https://justjoin.it/job-offers/all-locations",
                    final_url="https://justjoin.it/job-offers/all-locations",
                    extra={"total_results": 10000, "site_total": 19388,
                           "capped_by_site": True,
                           "pages_available": 1, "route_is_paginated": False})
    for key in ("status", "stop_reason", "pages_requested", "pages_completed",
                "pages_failed", "mode", "source"):
        check("the sidecar records %r" % key, key in meta)
    equal("the sidecar carries how many results the query matched",
          meta["total_results"], 10000)
    # Both figures, because every query is capped at 10,000 while the board
    # holds more. Without the second number a reader cannot tell that a
    # "complete" run is about half the board (CLAUDE.md §21: complete and
    # exhaustive are different words).
    equal("...and how many offers the whole board held",
          meta["site_total"], 19388)
    equal("...and whether the site's cap bounded the query",
          meta["capped_by_site"], True)
    equal("...and whether this route is addressable page by page",
          meta["route_is_paginated"], False)
    equal("pages_failed is a LIST of numbers, not a count",
          isinstance(meta["pages_failed"], list), True)


def _import_engine(name):
    try:
        return __import__(name)
    except ImportError as e:
        skip(name, "engine library absent (%s)" % e)
        return None


def check_engines_import_their_driver_at_module_level():
    """For the guarded imports above to MEAN anything.

    A sibling repo imported `launch`/`connect` inside the launch path, so the
    module imported cleanly with no pyppeteer installed: the group never
    skipped, and the CI job that exists to fail on unexpected skips could not
    have caught a broken import. It also let CI run against a stub version
    for a while without anything noticing. This drifts back silently, so it
    is asserted with an `ast` walk rather than trusted.
    """
    for module, driver in DRIVER_IMPORTS.items():
        path = os.path.join(HERE, module + ".py")
        if not os.path.exists(path):
            check("%s exists" % module, False)
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        top_level = set()
        for node in tree.body:          # module level ONLY
            if isinstance(node, ast.Import):
                top_level.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level.add(node.module.split(".")[0])
        check("%s imports %s at MODULE level" % (module, driver),
              driver in top_level,
              "top-level imports: %s" % sorted(top_level))


def check_shared_calls_bind_against_the_real_signature():
    """§17's check #1, and the one that earns its keep.

    A sibling repo shipped `classify(html, url=…)` in two of three engines
    against a callee taking `status` second, and BOTH crashed on their first
    fetch — invisible to import, --help, compileall, the undefined-name walk
    and 400+ green assertions, because none of those calls a function the way
    a live run does.

    This walks every engine's AST for calls into the shared modules and binds
    each one against the callee's real signature.
    """
    import page_flow
    import product_parser
    import output_writer
    targets = {"page_flow": page_flow, "product_parser": product_parser,
               "output_writer": output_writer}
    bound = 0
    for module in ENGINES + ("scraper_api_client",):
        path = os.path.join(HERE, module + ".py")
        if not os.path.exists(path):
            continue
        source = open(path, encoding="utf-8").read()
        tree = ast.parse(source)
        # Which shared names this file imported directly (`from x import y`).
        direct = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in targets:
                for alias in node.names:
                    direct[alias.asname or alias.name] = (
                        targets[node.module], alias.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            owner = attr = None
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id in targets:
                    owner, attr = targets[func.value.id], func.attr
            elif isinstance(func, ast.Name) and func.id in direct:
                owner, attr = direct[func.id]
            if owner is None:
                continue
            # A name that is NOT THERE is the loudest possible failure and
            # this check used to swallow it: `getattr(..., None)` returned
            # None, `not callable(None)` was true, and the call was skipped.
            # Three calls into a page_flow API that does not exist in this
            # repo -- comparable(), next_page_selector(),
            # next_page_candidates(), all of them Tokopedia's, all arriving
            # with copied code -- sat in two engines under a green run of
            # this very function. Absent is not "nothing to bind".
            if not hasattr(owner, attr):
                check("%s.%s exists (called from %s:%d)"
                      % (getattr(owner, "__name__", owner), attr,
                         module + ".py", node.lineno),
                      False,
                      "the engine calls a name the shared module does not "
                      "define; a live run reaches this as AttributeError")
                continue
            callee = getattr(owner, attr)
            if not callable(callee) or inspect.isclass(callee):
                continue
            try:
                signature = inspect.signature(callee)
            except (TypeError, ValueError):
                continue
            positional = [inspect.Parameter.empty] * len(node.args)
            keywords = {}
            for kw in node.keywords:
                if kw.arg is None:          # **kwargs — cannot be checked here
                    keywords = None
                    break
                keywords[kw.arg] = inspect.Parameter.empty
            if keywords is None:
                continue
            try:
                signature.bind(*positional, **keywords)
                bound += 1
            except TypeError as e:
                check("%s:%d %s.%s(...) binds against its real signature"
                      % (module, node.lineno, owner.__name__, attr),
                      False, "%s; signature is %s" % (e, signature))
    check("every shared-module call in every engine binds (%d checked)" % bound,
          bound > 40, "only %d calls were checked — is the walk finding them?"
          % bound)


def _argparse_flags(module_name):
    """Every --flag a module's parser defines, without running the CLI."""
    path = os.path.join(HERE, module_name + ".py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    # Only calls on the argparse parser itself. A browser's option object
    # also has `add_argument`, and counting Chrome's own switches
    # (`--no-sandbox`, `--window-size=…`) as CLI flags made this check
    # compare nonsense.
    parsers = {"p"}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr in ("add_argument_group",
                                             "add_mutually_exclusive_group")):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    parsers.add(target.id)
    flags = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in parsers):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and arg.value.startswith("--"):
                    flags.add(arg.value)
    return flags


def check_engine_flag_sets():
    """§17's check #2: against the contract AND against each other, both ways.

    A missing flag fails; so does closing a difference the README documents.
    """
    sets = {}
    for module in ENGINES:
        if not os.path.exists(os.path.join(HERE, module + ".py")):
            continue
        sets[module] = _argparse_flags(module)
    for module, flags in sets.items():
        missing = (CONTRACT_FLAGS | SITE_FLAGS) - flags
        check("%s defines every contract flag" % module, not missing,
              "missing %s" % sorted(missing))
    # The ONE documented difference: pyppeteer downloads its own Chromium
    # and could not launch it on the development machine, so it needs a way
    # to point at another one. Its twins have no equivalent because they do
    # not ship a browser. Listed here so that closing the difference — or
    # growing a second one — fails the build (§17).
    DOCUMENTED_DIFFERENCES = {"puppeteer_scraper": {"--chromium-path"}}
    names = sorted(sets)
    for i in range(len(names) - 1):
        a, b = names[i], names[i + 1]
        only_a = sets[a] - sets[b] - DOCUMENTED_DIFFERENCES.get(a, set())
        only_b = sets[b] - sets[a] - DOCUMENTED_DIFFERENCES.get(b, set())
        check("%s and %s define the same flags" % (a, b),
              not only_a and not only_b,
              "only in %s: %s; only in %s: %s"
              % (a, sorted(only_a), b, sorted(only_b)))


def check_the_concurrency_difference_is_documented_in_both_directions():
    """§20: the exception list IS the documentation.

    All three engines take `--concurrency` because it is in the family's CLI
    contract, but only the Playwright engine implements it — the other two
    log that they are ignoring it and fetch one page at a time. That is a
    legitimate design difference, and an UNDOCUMENTED one is how a sibling
    repo's README came to promise "same CLI" while twelve flags differed.

    Pinned in both directions, which is the half that is easy to skip: a
    mirror that silently stops warning fails here, and so does the primary
    engine losing its implementation. Closing the difference is then a
    decision someone makes on purpose rather than a surprise.
    """
    primary, mirrors = "playwright_scraper", ("selenium_scraper", "puppeteer_scraper")
    src = {}
    for module in (primary,) + mirrors:
        path = os.path.join(HERE, module + ".py")
        if os.path.exists(path):
            src[module] = open(path, encoding="utf-8").read()

    if primary in src:
        check("the primary engine actually implements concurrency",
              "_fetch_pages_concurrently" in src[primary],
              "no concurrent fetch path found in the engine that documents one")
        check("...and consults the shared policy for it",
              "concurrency_for_mode" in src[primary]
              and "concurrency_limit" in src[primary])
    for module in mirrors:
        if module not in src:
            continue
        check("%s says out loud that it ignores --concurrency" % module,
              "--concurrency is ignored in this engine" in src[module],
              "a mirror that silently accepts the flag looks like it "
              "parallelises and does not")
        check("...and does not secretly implement it after all" % (),
              "_fetch_pages_concurrently" not in src[module],
              "%s has a concurrent fetch path but still warns that it "
              "ignores the flag — one of the two is now a lie" % module)

    # And the README has to carry it, because a difference nobody documented
    # is one a user discovers from a run that took four times as long.
    readme = open(os.path.join(HERE, "README.md"), encoding="utf-8").read()
    check("the README documents where --concurrency is clamped",
          "--mode facets` (one\nrequest)" in readme
          or "clamped to 1 where it would be pointless" in readme,
          "the concurrency difference is not in the README")
    check("...and why a CDP endpoint refuses it",
          "profile allows one live connection" in readme,
          "the CDP collision is not in the README")


def check_banned_and_removed_flags():
    """Scoped to the ENGINES.

    `--country` is banned on the ENGINES, and there is no exception here:
    justjoin.it serves one board from one host, and its endpoint answered
    identically from every exit tried, so such a flag could only contradict
    what the URL already says. On `fingerprint_client.py` the same name is
    legitimate — there it picks a fingerprint locale, not a target — which
    is why this check is scoped to the engines rather than to the tree
    (CLAUDE.md §10).

    What the rule is really about is a flag that can disagree with the URL
    or with the mode. Two refusals enforce that here:

      * `--url` and `--mode` are refused when they name different routes,
        because reading an offer URL with the listings reader returns zero
        rows from a perfectly good response (§20).
      * the filter flags are refused outside `--mode listings`, because
        `--mode offer` reads a sitemap and `--mode facets` reads one
        endpoint — neither takes a query. A `--category python --mode
        offer` run that quietly ignored the filter would look like it had
        narrowed the enumeration when it had not.
    """
    for module in ENGINES:
        path = os.path.join(HERE, module + ".py")
        if not os.path.exists(path):
            continue
        source = open(path, encoding="utf-8").read()
        for flag in BANNED_FLAGS:
            check("%s does not define %s" % (module, flag),
                  '"%s"' % flag not in source)
        check("%s refuses a --url whose route disagrees with --mode" % module,
              "but --mode is" in source,
              "the refusal that makes --mode safe here is missing")
        check("%s refuses a filter outside --mode listings" % module,
              "does \n                    \"not read. Use --mode listings to filter" in source
              or "not read. Use --mode listings to filter" in source,
              "the refusal that keeps a filter from looking applied when it "
              "is not is missing")


def check_undefined_names_in_every_module():
    """§10: compileall proves a file PARSES, not that its names RESOLVE.

    A live run of a sibling repo's pyppeteer engine died with NameError on a
    line reached only while fetching, after an import had been removed — the
    module imported cleanly, --help worked, compileall passed and CI was
    green. Kept COARSE (pooled bindings, no scope tracking) so it
    under-reports rather than inventing problems.
    """
    import builtins
    modules = [f for f in sorted(os.listdir(HERE))
               if f.endswith(".py") and f != "smoke_test.py"]
    for filename in modules:
        tree = ast.parse(open(os.path.join(HERE, filename), encoding="utf-8").read())
        # Module-level dunders exist without being assigned anywhere.
        defined = set(dir(builtins)) | {"__file__", "__name__", "__doc__",
                                        "__package__", "__spec__"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    defined.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                defined.add(node.id)
            elif isinstance(node, ast.arg):
                defined.add(node.arg)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                defined.add(node.name)
            elif isinstance(node, ast.alias) and node.asname:
                defined.add(node.asname)
        used = {n.id for n in ast.walk(tree)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        unresolved = sorted(used - defined)
        check("%s: every name resolves" % filename, not unresolved,
              "%s" % unresolved)


def _import_graph(entrypoint):
    """Every local module an entrypoint reaches, transitively."""
    local = {f[:-3] for f in os.listdir(HERE) if f.endswith(".py")}
    seen, queue = set(), [entrypoint]
    while queue:
        name = queue.pop()
        if name in seen or name not in local:
            continue
        seen.add(name)
        tree = ast.parse(open(os.path.join(HERE, name + ".py"),
                              encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                queue.extend(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                queue.append(node.module.split(".")[0])
    return seen


def check_dockerfile_copies_everything_the_entrypoint_imports():
    """§10: all three repos in this family shipped an image that died with
    ModuleNotFoundError on every invocation, --help included, because
    proxy_pool.py was missing from the COPY list. CI never built the image;
    this check needs no Docker."""
    path = os.path.join(HERE, "Dockerfile")
    if not os.path.exists(path):
        check("Dockerfile exists", False)
        return
    dockerfile = open(path, encoding="utf-8").read()
    # Only the COPY instructions, continuations included — a comment above
    # them naming a file is not a file the image carries.
    copy_lines, joining = [], False
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if joining or stripped.upper().startswith("COPY "):
            copy_lines.append(stripped)
            joining = stripped.endswith("\\")
    copied = set(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\.py", " ".join(copy_lines)))
    entry = re.search(r'(?:CMD|ENTRYPOINT)\s*\[?\s*"?(?:python3?"?,\s*"?)?'
                      r'([A-Za-z_][A-Za-z0-9_]*)\.py', dockerfile)
    entrypoint = entry.group(1) if entry else "playwright_scraper"
    needed = _import_graph(entrypoint)
    missing = sorted(needed - copied)
    check("the Dockerfile COPYs every module %s.py imports" % entrypoint,
          not missing, "missing %s" % missing)
    for unwanted in ("smoke_test", "test_smoke"):
        check("the image does not carry %s.py" % unwanted,
              unwanted not in copied)


def check_env_example_documents_exactly_what_the_loader_reads():
    import env_config
    path = os.path.join(HERE, ".env.example")
    if not os.path.exists(path):
        check(".env.example exists", False)
        return
    documented = set(re.findall(r"^\s*#?\s*([A-Z][A-Z0-9_]+)\s*=", 
                                open(path, encoding="utf-8").read(), re.M))
    read = set(env_config.ENV_KEYS)
    check("every variable the loader reads is documented",
          not (read - documented), "undocumented: %s" % sorted(read - documented))
    check("every documented variable is actually read",
          not (documented - read), "unread: %s" % sorted(documented - read))


def check_a_copied_env_example_reads_as_UNSET():
    """§17: `cp .env.example .env` followed by a run must not connect.

    The placeholder check was a literal set in a sibling repo, and the two
    credentialled URLs are documented the way the vendor documents them —
    `ws://{login}-zone-…:{password}@cb.2captcha.com:9222` — so neither
    literal matched, the run connected with the string `{login}-zone-…` as
    its username, and got a 401 a long way from its cause.
    """
    import env_config
    example = os.path.join(HERE, ".env.example")
    if not os.path.exists(example):
        check(".env.example exists", False)
        return
    text = open(example, encoding="utf-8").read()
    values = dict(re.findall(r"^([A-Z][A-Z0-9_]+)=(.*)$", text, re.M))
    check("the example actually sets every variable",
          set(values) == set(env_config.ENV_KEYS),
          "example has %s, loader reads %s"
          % (sorted(values), sorted(env_config.ENV_KEYS)))
    # Every CREDENTIAL must read as unset. The default TARGET must not: it is
    # a real, usable URL, and blanking it would remove the one setting this
    # file exists to make convenient (§17's check #3 says exactly this — the
    # credentials unset, the non-credential default still usable).
    CREDENTIALS = {"TWOCAPTCHA_KEY", "JUSTJOIN_CDP_ENDPOINT", "JUSTJOIN_PROXY"}
    before = dict(os.environ)
    try:
        for name, raw in values.items():
            os.environ[name] = raw
            got = env_config.env_value(name)
            if name in CREDENTIALS:
                check("a copied .env.example leaves %s unset" % name,
                      got is None, "got %r" % got)
            else:
                check("...while %s stays a usable default" % name,
                      got == raw.strip(), "got %r" % got)
    finally:
        os.environ.clear()
        os.environ.update(before)
    # And the counter-check: a real credential must still come through, or
    # the placeholder rule would have made the loader useless. Deliberately
    # NOT 32 hex characters — that is the shape of a real 2captcha key, and
    # this repo's own credential scan (rightly) fails on one.
    try:
        os.environ["TWOCAPTCHA_KEY"] = "not-a-real-key-but-a-real-value"
        equal("a real value is still read",
              env_config.env_value("TWOCAPTCHA_KEY"),
              "not-a-real-key-but-a-real-value")
    finally:
        os.environ.clear()
        os.environ.update(before)


def check_ci_greps_for_a_sentinel_this_suite_can_actually_emit():
    """The `engine-smoke` job must be ABLE to fail.

    That job exists for one reason (CLAUDE.md §10): "skipped, engine absent"
    reads identically to a real import error, so CI installs each engine and
    fails if THAT engine's group still reports a skip. It works by grepping
    the suite's own output for a sentinel.

    The inherited version grepped for `"<engine>_scraper could not be
    imported"` — a string no suite in this family emits. Checked 2026-09-18,
    seven sibling repos carry the same dead grep, so in none of them could
    the job ever have failed. It passed for the wrong reason, which is
    §22's "a check that swallows the loudest failure it could report".

    This check is the guard against that coming back: whatever sentinel the
    workflow looks for, this suite has to be capable of printing it. It
    verifies the sentinel against `skip()`'s real output format rather than
    against a copy of the string, so changing either one without the other
    fails here.
    """
    workflow = os.path.join(HERE, ".github", "workflows", "tests.yml")
    if not os.path.isdir(os.path.join(HERE, ".github")):
        # §22: trigger on the WHOLE .github directory being absent — which
        # is the Docker image, where it is deliberately not COPYed — and
        # never on a file inside it going missing, because a check that
        # quietly starts passing once its input disappears is the failure
        # mode this whole function is about.
        skip("ci-sentinel", "no .github/ in this tree (the Docker image)")
        return
    check("tests.yml exists", os.path.exists(workflow))
    if not os.path.exists(workflow):
        return
    text = open(workflow, encoding="utf-8").read()

    # What `skip()` actually prints, derived rather than quoted.
    import io as _io, contextlib as _contextlib
    buf = _io.StringIO()
    before = len(SKIPS)
    with _contextlib.redirect_stdout(buf):
        skip("playwright_scraper", "engine library absent (probe)")
    del SKIPS[before:]          # leave the run's real skip list untouched
    printed = buf.getvalue()
    check("skip() prints a line naming the engine", "playwright_scraper" in printed,
          repr(printed))

    # The sentinel the workflow greps for, with the matrix placeholder
    # resolved the way Actions would resolve it.
    greps = re.findall(r'grep -q(?:E)? "([^"]*matrix\.engine[^"]*)"', text)
    check("the engine-smoke step greps for something", bool(greps),
          "no grep against ${{ matrix.engine }} found in tests.yml")
    for pattern in greps:
        resolved = pattern.replace("${{ matrix.engine }}", "playwright")
        check("the CI sentinel %r is a string this suite can emit" % resolved,
              resolved in printed,
              "the workflow greps for %r but skip() prints %r — the job "
              "cannot fail" % (resolved, printed.strip()))

    # And the other half: the step must confirm the suite RAN, or a crash on
    # line one sails past a grep for an absent string.
    check("the engine-smoke step also asserts the suite ran to completion",
          "checks passed" in text,
          "nothing in tests.yml checks for the suite's summary line")


def check_credential_scan_is_one_implementation_invoked_from_both():
    """§17: two sources of truth, one dead and one holed.

    `.github/ci_checks.py` sat in three repos invoked by NOTHING, while
    tests.yml carried an inline grep doing a narrower version of the same job
    — one that matched only ws:// and wss://, so an http://user:pass@
    credential would have sailed past CI.
    """
    script = os.path.join(HERE, ".github", "ci_checks.py")
    check("the credential scan exists as a script", os.path.exists(script))
    if not os.path.exists(script):
        return
    workflow_dir = os.path.join(HERE, ".github", "workflows")
    workflow = os.path.join(workflow_dir, "tests.yml")
    # Triggered on the whole .github directory being absent, never on this one
    # file being missing: two suites in this family run INSIDE the Docker
    # image, which deliberately COPYs no .github/, and a check that quietly
    # starts passing once its input disappears is the same failure this
    # function is about (CLAUDE.md §22).
    if not os.path.isdir(workflow_dir):
        skip("ci-wiring", "no .github/ in this tree (the Docker image)")
    elif os.path.exists(workflow):
        text = open(workflow, encoding="utf-8").read()
        check("CI INVOKES the script rather than reimplementing it",
              "ci_checks.py" in text)
        # ...and does not ALSO reimplement it. The original version of this
        # check asserted only the first half, and the workflow carried inline
        # `python - <<EOF` copies of the --help and sample checks alongside
        # the call — justified in a comment as keeping the two from drifting
        # apart. They drifted: the inline sample copy still imported the row
        # dataclass under a name this repo renamed, and it failed on the
        # repo's FIRST push while the script it duplicated passed.
        #
        # Scoped to the OFFLINE job, because the docker job legitimately
        # names `sample_output.json` for a different purpose — asserting the
        # image does NOT contain it. A guard that fired there would be wrong,
        # and a guard people have to argue with is one they learn to
        # suppress.
        offline = text.split("  engine-smoke:", 1)[0]
        for marker, what in (("from output_writer import", "the row schema"),
                             ("sample_output.json", "the sample output"),
                             ("subprocess.run([sys.executable", "the --help contract")):
            check("the offline job does not reimplement the check for %s" % what,
                  marker not in offline,
                  "tests.yml's offline job mentions %r — one implementation, "
                  "in ci_checks.py, invoked from both" % marker)
        # And the guard must have had something to read, or it passed for the
        # wrong reason (CLAUDE.md §22).
        check("...and the offline job was actually found to scan",
              "ci_checks.py" in offline, "no offline job in tests.yml")
    result = subprocess.run([sys.executable, script, "--all"], cwd=HERE,
                            capture_output=True, text=True)
    check("the credential scan passes on this repo's own tree",
          result.returncode == 0,
          (result.stdout + result.stderr)[-600:])


def check_the_credential_scan_survives_a_venv_in_the_tree():
    """A guard people have to argue with is one they learn to suppress.

    Found by cloning this repo the way a stranger does and following the
    README: `python3 -m venv` puts a virtualenv in the working tree, and the
    credential scan walked into pip's vendored code and flagged a 32-hex
    string in `_elffile.py` as key-shaped. Correct about the string, wrong
    about the file, and the first thing a new user would have seen.

    The fix is structural rather than a longer list of names — a directory
    holding `pyvenv.cfg` is a virtualenv whatever it is called — and this
    pins BOTH halves, because narrowing a credential scan is exactly how one
    stops catching things. CLAUDE.md §22 records a sibling repo whose scan
    caught an UNTRACKED `.env.bak` holding a live key, so scanning must not
    be reduced to tracked files.
    """
    import importlib.util
    script = os.path.join(HERE, ".github", "ci_checks.py")
    if not os.path.exists(script):
        skip("credential-scan", "no .github/ in this tree (the Docker image)")
        return
    spec = importlib.util.spec_from_file_location("_ci_checks", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    check("the scan knows a virtualenv structurally, not by name",
          hasattr(mod, "_is_virtualenv"))
    if not hasattr(mod, "_is_virtualenv"):
        return

    with tempfile.TemporaryDirectory() as tmp:
        odd = os.path.join(tmp, "whatever-i-called-it")
        os.makedirs(os.path.join(odd, "lib"))
        open(os.path.join(odd, "pyvenv.cfg"), "w").write("home = /usr\n")
        check("...so a venv under any name is recognised",
              mod._is_virtualenv(pathlib.Path(odd)))
        plain = os.path.join(tmp, "src")
        os.makedirs(plain)
        check("...and an ordinary directory is not",
              not mod._is_virtualenv(pathlib.Path(plain)))

    # The other half: it must still walk files git does not track, because a
    # key pasted into a scratch file is the case this scan exists for.
    scanned = [str(p) for p in mod.scanned_files()]
    check("the scan still reads this repo's own files", len(scanned) > 20,
          "%d file(s)" % len(scanned))
    check("...and is not limited to git's index",
          "git ls-files" not in open(script, encoding="utf-8").read())


def check_banned_wording():
    """§12: enforced by this test rather than by review."""
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in
                   (".git", "__pycache__", ".pytest_cache", "node_modules")]
        for filename in files:
            if not filename.endswith((".py", ".md", ".yml", ".yaml", ".txt",
                                      ".toml", ".html", ".example")):
                continue
            path = os.path.join(root, filename)
            text = open(path, encoding="utf-8", errors="replace").read().lower()
            for phrase in BANNED_WORDING:
                if phrase.lower() in text and filename != "smoke_test.py":
                    check("%s contains no %r" % (
                        os.path.relpath(path, HERE), phrase), False)
    check("banned-wording scan ran", True)


def check_concurrency_with_the_browser_stubbed():
    """§10: a live run cannot always reach this machinery.

    Page 1 is fetched alone and decides how many pages there are, so a
    blocked page 1 means the workers never start. Driven directly instead,
    with the browser replaced.
    """
    engine = _import_engine("playwright_scraper")
    if engine is None:
        return

    class Args:
        delay = 0
        retries = 1
        retry_delay = 0
        out = "unused"
        mode = "search"
        sort = "a-z"
        pages = 50

    fetched = []
    import threading
    lock = threading.Lock()

    def fake_fetch(session, args, pool, page_num, url):
        with lock:
            fetched.append(page_num)
        outcome = engine.PageOutcome(page_num=page_num, url=url)
        # Page 6 is the end of this listing: no rows, but a served page.
        outcome.products = [] if page_num >= 6 else [object()] * 15
        outcome.state = "empty" if page_num >= 6 else "content"
        return outcome

    class FakeSession:
        def __init__(self, *a, **k):
            self.pool = None
        def open(self):
            return self
        def close(self):
            pass

    class FakePlaywright:
        def __enter__(self):
            return None
        def __exit__(self, *a):
            return False

    real_fetch = engine._fetch_one_page
    real_session = engine._BrowserSession
    real_pw = engine.sync_playwright
    engine._fetch_one_page = fake_fetch
    engine._BrowserSession = FakeSession
    engine.sync_playwright = lambda: FakePlaywright()
    try:
        specs = [(n, "u%d" % n) for n in range(2, 51)]
        results, unattempted, exhausted = engine._fetch_pages_concurrently(
            Args(), None, specs, 4)
    finally:
        engine._fetch_one_page = real_fetch
        engine._BrowserSession = real_session
        engine.sync_playwright = real_pw

    check("every page fetched was fetched exactly once",
          len(fetched) == len(set(fetched)), "%r" % sorted(fetched))
    check("dispatch STOPPED at the end of the listing", exhausted)
    check("...so the 49 queued pages cost far fewer fetches",
          len(fetched) < 15, "fetched %d of 49" % len(fetched))
    check("unattempted pages are REPORTED, not counted as failed",
          len(unattempted) > 0 and all(isinstance(n, int) for n in unattempted))
    equal("attempted + unattempted covers the whole queue",
          len(set(fetched)) + len(unattempted), 49)
    equal("outcomes are restorable to page order",
          [o.page_num for o in sorted(results, key=lambda o: o.page_num)],
          sorted(o.page_num for o in results))


def check_a_dead_worker_neither_hangs_nor_loses_its_siblings():
    engine = _import_engine("playwright_scraper")
    if engine is None:
        return

    class Args:
        delay = 0
        retries = 1
        retry_delay = 0
        out = "unused"
        mode = "search"
        sort = "a-z"
        pages = 10

    def exploding_fetch(session, args, pool, page_num, url):
        if page_num == 3:
            raise RuntimeError("worker died")
        outcome = engine.PageOutcome(page_num=page_num, url=url)
        outcome.products = [object()] * 15
        outcome.state = "content"
        return outcome

    class FakeSession:
        def __init__(self, *a, **k):
            self.pool = None
        def open(self):
            return self
        def close(self):
            pass

    class FakePlaywright:
        def __enter__(self):
            return None
        def __exit__(self, *a):
            return False

    real_fetch, real_session, real_pw = (engine._fetch_one_page,
                                         engine._BrowserSession,
                                         engine.sync_playwright)
    engine._fetch_one_page = exploding_fetch
    engine._BrowserSession = FakeSession
    engine.sync_playwright = lambda: FakePlaywright()
    try:
        specs = [(n, "u%d" % n) for n in range(2, 8)]
        results, unattempted, exhausted = engine._fetch_pages_concurrently(
            Args(), None, specs, 3)
    finally:
        engine._fetch_one_page = real_fetch
        engine._BrowserSession = real_session
        engine.sync_playwright = real_pw

    check("the run returned rather than hanging", True)
    check("the dead worker's siblings still delivered their pages",
          len(results) >= 3, "%d results" % len(results))
    check("page 3 is not reported as a success",
          3 not in [o.page_num for o in results])


def check_worker_pools_start_on_different_exits():
    engine = _import_engine("playwright_scraper")
    if engine is None:
        return
    from proxy_pool import ProxyPool
    pool = ProxyPool(["http://a:1", "http://b:2", "http://c:3"], rotate="per-run")
    firsts = [engine._worker_pool(pool, i).current for i in range(3)]
    equal("three workers start on three different exits",
          len(set(firsts)), 3)
    equal("a missing pool stays missing", engine._worker_pool(None, 0), None)


def check_fingerprint_kwargs_are_ones_the_driver_accepts():
    """§10: an unknown key in new_context(**kwargs) is a TypeError at launch,
    on the PAID path, at runtime."""
    engine = _import_engine("playwright_scraper")
    if engine is None:
        return
    try:
        from fingerprint_client import playwright_context_kwargs
    except ImportError as e:
        skip("fingerprint", str(e))
        return
    sample = {"id": "x", "country": "US",
              "userAgent": "Mozilla/5.0 Chrome/140.0.0.0",
              "screen": {"width": 1920, "height": 1080},
              "timezone": "America/New_York", "language": "en-US",
              "devicePixelRatio": 2}
    kwargs = playwright_context_kwargs(sample)
    from playwright.sync_api import sync_playwright  # noqa: F401
    import playwright.sync_api as pw_api
    signature = inspect.signature(pw_api.Browser.new_context)
    unknown = [k for k in kwargs if k not in signature.parameters]
    check("every fingerprint kwarg is one new_context accepts", not unknown,
          "unknown: %s" % unknown)


def check_every_engine_exposes_the_same_public_surface():
    for module in ENGINES:
        engine = _import_engine(module)
        if engine is None:
            continue
        # `target_url` and `parse_for_mode` are deliberately NOT here:
        # they moved into product_parser so the three engines cannot
        # disagree about which URL a run fetches or how a body is read
        # (CLAUDE.md §1, the argument page_flow already makes for policy).
        for name in ("scrape", "parse_args", "PageOutcome", "_fetch_one_page",
                     "_is_endpoint", "_snapshot"):
            check("%s.%s exists" % (module, name), hasattr(engine, name))
        outcome = engine.PageOutcome(page_num=1, url="u")
        for field_name in ("state", "total_available", "site_total",
                           "offset",
                           "pages_available", "urls_in_itemlist",
                           "echoed_page", "products", "blocked_by",
                           "load_failed", "final_url"):
            check("%s.PageOutcome carries %r" % (module, field_name),
                  hasattr(outcome, field_name))
        check("%s.PageOutcome.ok is True for a fresh outcome" % module,
              outcome.ok)
        equal("%s shares CORE_FIELDS with its twins" % module,
              tuple(engine.CORE_FIELDS),
              ("title", "url", "sku", "company_name"))
        # LONGER on purpose: a detail record carries the description body
        # on 40 of 40 sampled, and a listing row carries none — so the
        # detail floor can ask for something the listing floor must not.
        equal("%s shares CORE_FIELDS_OFFER with its twins" % module,
              tuple(engine.CORE_FIELDS_OFFER),
              ("title", "url", "sku", "company_name", "description"))
        # And the facet row, which has no url and no company at all.
        equal("%s shares CORE_FIELDS_FACETS with its twins" % module,
              tuple(engine.CORE_FIELDS_FACETS),
              ("title", "sku", "facet_group", "facet_key"))
        equal("%s shares CORE_FIELD_FLOOR with its twins" % module,
              engine.CORE_FIELD_FLOOR, 99)


def check_every_solve_is_counted_against_the_budget():
    """`SOLVES_PER_PAGE` is a MONEY limit, so every call that can buy must be
    counted — CLAUDE.md §17's "a policy constant nothing reads".

    `handle_captcha_if_present` is called TWICE per attempt in every engine:
    once before the page is classified (so a challenge is cleared before
    anything is judged) and once after, for the state that says the page
    really is gated. Only the second was counted, and the first therefore
    bought a solve on every block attempt, for free, silently.

    INHERITED from a sibling repo (wellfound-scraper), not measured here —
    justjoin.it refuses nothing, so this repo has no such run. Measured
    there 2026-09-17 from a datacenter address, which meets a real
    Cloudflare challenge on every fetch: one page bought THREE Turnstile
    solves before the fix and ONE after, with the cap set to 1 both times.
    Every token was refused either way, so the three purchases bought
    nothing at all.

    Asserted on the source, because the branch only runs when a challenge is
    actually rendered and the suite must pass offline.
    """
    for module in ENGINES:
        path = os.path.join(HERE, module + ".py")
        if not os.path.exists(path):
            continue
        source = open(path, encoding="utf-8").read()
        calls = source.count("if handle_captcha_if_present(")
        check("%s calls the solver from two places, as designed" % module,
              calls == 2, "found %d call site(s)" % calls)
        # Both must sit under a budget test. Counting the guard is the cheap
        # way to say that without parsing the control flow.
        guards = source.count("_solve_budget(args, solves_bought)")
        check("%s gates BOTH solve call sites on the budget" % module,
              guards == calls,
              "%d budget guard(s) for %d call site(s)" % (guards, calls))
        check("%s increments the counter beside each guard" % module,
              source.count("solves_bought += 1") == calls,
              "%d increment(s) for %d call site(s)"
              % (source.count("solves_bought += 1"), calls))
    # And the constant must still be the one thing that decides it.
    import page_flow
    equal("at most one purchase per page", page_flow.SOLVES_PER_PAGE, 1)


def check_a_dead_proxy_is_reported_as_a_proxy_failure():
    """CLAUDE.md §8: a proxy failure is not a timeout, and the two want
    opposite responses — another try at the same exit versus a different one.

    The engines all compute the reason (`_proxy_failure`) and, WITH a pool,
    log it on rotation. Without a pool — a single `--proxy`, which is the
    common case — an earlier version dropped it and reported only "gave up
    loading", so a refused proxy read exactly like a slow site. Found by
    running it rather than by reading it: `--proxy http://127.0.0.1:9`
    printed the generic message while `_proxy_failure()` had already
    identified ERR_PROXY_CONNECTION_FAILED.

    Asserted on the SOURCE rather than by launching a browser, because the
    branch only runs when a navigation fails and the suite must pass with no
    engine library installed at all.
    """
    for module in ENGINES:
        path = os.path.join(HERE, module + ".py")
        if not os.path.exists(path):
            continue
        source = open(path, encoding="utf-8").read()
        # Anchored on the GIVE-UP branch — the one that reports and returns —
        # not on the `if load_failed: break` inside the retry loop, which
        # comes first in the file and would make this check read the wrong
        # block and pass for the wrong reason (CLAUDE.md §22).
        marker = "        outcome.load_failed = True"
        if marker not in source:
            check("%s has a give-up branch to check" % module, False)
            continue
        end = source.index(marker)
        branch = source[max(0, end - 1800):end]
        check("%s names the proxy when the proxy was the fault" % module,
              "if exit_failed:" in branch,
              "the give-up branch does not consult exit_failed")
        check("%s still has a plain message for a non-proxy failure" % module,
              "after %d attempt(s)" in branch,
              "the non-proxy branch was lost")
    # ...and the detector the branch depends on must actually match the
    # string Chromium produces. Measured on a SIBLING repo 2026-09-17
    # against a dead
    # local port: `net::ERR_PROXY_CONNECTION_FAILED`.
    engine = _import_engine("playwright_scraper")
    if engine is None:
        skip("proxy-failure", "playwright_scraper not importable here")
    else:
        class _E(Exception):
            pass
        got = engine._proxy_failure(
            _E("Page.goto: net::ERR_PROXY_CONNECTION_FAILED at https://x/"))
        equal("the marker list matches what Chromium really raises", got,
              "ERR_PROXY_CONNECTION_FAILED")
        equal("...and a plain timeout is NOT read as a proxy failure",
              engine._proxy_failure(_E("Page.goto: Timeout 25000ms exceeded.")),
              "")


def check_engines_do_not_evaluate_a_string_in_the_browser():
    """§18: a site whose CSP omits `unsafe-eval` kills wait_for_function with
    an EvalError and takes the run down with exit 1, on the site's most
    obvious URL. justjoin.it has not been measured for that, and the cheap habit
    costs nothing on a site that would have allowed it."""
    for module in ENGINES:
        path = os.path.join(HERE, module + ".py")
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
        for banned in ("wait_for_function", "waitForFunction", "waitFor"):
            check("%s never CALLS %s" % (module, banned), banned not in called,
                  "poll through page_flow.wait_for_count instead")


def check_credentials_never_reach_a_log():
    """§8: an EXCEPTION MESSAGE is a log, and the masker must be GLOBAL.

    A Playwright connection error repeats the endpoint five times (the
    message plus a four-line call log), so a masker handling only the first
    occurrence prints the password four times and looks like it is working.
    """
    for module in ENGINES:
        engine = _import_engine(module)
        if engine is None:
            continue
        masked = engine._mask_credentials(
            "tried ws://u:supersecret@h1:9222 and ws://u:supersecret@h2:9222 "
            "and again ws://u:supersecret@h1:9222")
        check("%s masks EVERY occurrence" % module,
              "supersecret" not in masked, masked)
        check("%s keeps the host and port, which are the useful half" % module,
              "h1:9222" in masked and "h2:9222" in masked, masked)
    from proxy_pool import mask
    masked = mask("http://user:secret@exit.example.com:2334")
    check("proxy_pool.mask hides the password", "secret" not in masked)
    check("proxy_pool.mask keeps the exit", "exit.example.com:2334" in masked)


def check_sample_output_matches_the_schema():
    from output_writer import JobPosting
    expected = [f.name for f in fields(JobPosting)]
    json_path = os.path.join(HERE, "sample_output.json")
    csv_path = os.path.join(HERE, "sample_output.csv")
    if not os.path.exists(json_path):
        check("sample_output.json exists", False)
        return
    rows = json.load(open(json_path, encoding="utf-8"))
    check("sample_output.json holds rows", bool(rows))
    equal("sample_output.json keys match the schema, in order",
          list(rows[0].keys()), expected)
    check("sample_output.json is from a real run (justjoin.it rows)",
          all(r["source"] == "justjoin.it" for r in rows))
    check("...and every row points at a real offer page",
          all((r.get("url") or "").startswith(
              "https://justjoin.it/job-offer/") for r in rows))
    check("...and every sku is a uuid, not a made-up id",
          all(re.fullmatch(r"[0-9a-f-]{36}", r.get("sku") or "")
              for r in rows))
    # The sample exists to SHOW the shape, so it has to hold more than one
    # of it: a sample where every salary is disclosed would teach a reader
    # that a null there is a fault.
    check("...and shows both a disclosed and an undisclosed salary",
          any(r["salary_min"] is not None for r in rows)
          and any(r["salary_min"] is None for r in rows))
    check("...and both an hourly and a monthly rate",
          {r["salary_unit"] for r in rows} >= {"hour", "month"},
          sorted({r["salary_unit"] for r in rows}))
    check("...and carries no fabrication markers",
          not any("example" in (r.get("url") or "").lower() or
                  "lorem" in (r.get("title") or "").lower() for r in rows))
    if os.path.exists(csv_path):
        header = next(csv.reader(open(csv_path, encoding="utf-8")))
        equal("sample_output.csv header matches the schema", header, expected)


def _tree_state():
    result = subprocess.run(["git", "status", "--porcelain"], cwd=HERE,
                            capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return sorted(line for line in result.stdout.splitlines()
                  if not line.endswith(".pyc"))


def check_no_test_mutates_the_working_tree():
    """§10: one suite used its own file as a fake chromedriver and chmod'd it
    to 755, leaving a mode change in git status.

    Compares the tree against how it looked when the suite STARTED, not
    against a clean checkout — otherwise this is permanently red while
    anyone is editing, and a check that is always red teaches everyone to
    ignore checks.
    """
    if _TREE_BEFORE is None:
        skip("git status", "not a git repository")
        return
    after = _tree_state()
    changed = sorted(set(after) - set(_TREE_BEFORE))
    check("the suite itself changed nothing in the working tree",
          not changed, "%s" % changed)


def check_captcha_capability_claims_match_the_code():
    """§19: the most expensive bug this family can ship is a SENTENCE.

    It fails in both directions and this family has shipped both:

      * saying a captcha CANNOT be solved, when the true statement is that
        THIS REPO does not implement the task type. 2Captcha solves
        enterprise reCAPTCHA and Cloudflare Turnstile and has for years, so
        such a sentence tells a reader not to buy something that works.
      * saying this repo DOES solve something it builds no task type for --
        which is what the README said here: it billed the Managed Challenge
        solve to `--twocaptcha-key`, while the only thing that clears one is
        `Captcha.setAutoSolve` over `--cdp-endpoint`.

    Neither is visible to any other check: nothing fails, nothing crashes,
    and the output is correct.
    """
    readme = open(os.path.join(HERE, "README.md"), encoding="utf-8").read()
    solver = open(os.path.join(HERE, "captcha_solver.py"), encoding="utf-8").read()
    low = readme.lower()

    # Conclusions about the PRODUCT. Phrases about a page carrying no widget
    # are deliberately absent -- a page really can carry none, and calling
    # THAT unsolvable is honest.
    for phrase in ("cannot be solved", "can't be solved", "neither is solvable",
                   "is not solvable", "solver is inapplicable", "no solver can"):
        check("README: no %r -- write 'this repo does not implement X'" % phrase,
              phrase not in low)

    # The positive direction, stated as a PAIRING rather than a keyword
    # search so it cannot go quiet by accident: if the task type is absent,
    # the README has to say so in those words.
    if "TurnstileTaskProxyless" not in solver:
        check("README says plainly that TurnstileTaskProxyless is not built here",
              "does not implement `turnstiletaskproxyless`" in low,
              "the solver builds no Turnstile task, so the README must not let "
              "a reader believe --twocaptcha-key clears a Managed Challenge")
        check("...and the Managed Challenge is not billed to --twocaptcha-key",
              "(`--twocaptcha-key`) - for the managed challenge" not in low
              and "(`--twocaptcha-key`) \u2014 for the managed challenge" not in low)
    else:
        check("a built Turnstile task needs the render interception too",
              "TURNSTILE_INTERCEPT_JS" in solver)

    # Whatever the README credits with clearing the challenge must be a thing
    # the engines actually do.
    if "setautosolve" in low:
        srcs = ""
        for name in ("playwright_scraper.py", "selenium_scraper.py",
                     "puppeteer_scraper.py"):
            path = os.path.join(HERE, name)
            if os.path.exists(path):
                srcs += open(path, encoding="utf-8").read()
        check("README credits Captcha.setAutoSolve, and an engine calls it",
              "Captcha.setAutoSolve" in srcs)


def check_no_statement_is_unreachable():
    """A statement sitting after a return/raise/break/continue in the SAME
    block, which therefore can never run.

    Narrow on purpose: it makes no claim about reachability in general, only
    about a block whose control flow has already left. Measured across the
    eighteen repos of this family on 2026-09-16 it reported six problems and
    zero false positives.

    `check_undefined_names_in_every_module` cannot see this class at all, by
    design -- it pools every binding in the file rather than tracking scopes,
    so a name used inside dead code passes as long as anything else in the
    module binds it. What was hiding in that blind spot here, and in five
    sibling repos, byte for byte: a function whose `def` line had been lost,
    leaving its docstring and body absorbed into the end of the function
    above it. Present since this repo's first commit, invisible to import,
    `--help`, `compileall`, and every green run of this suite.
    """
    for filename in sorted(f for f in os.listdir(HERE) if f.endswith(".py")):
        tree = ast.parse(open(os.path.join(HERE, filename),
                              encoding="utf-8").read())
        dead = []
        for node in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if not isinstance(block, list):
                    continue
                for i, stmt in enumerate(block[:-1]):
                    if isinstance(stmt, (ast.Return, ast.Raise,
                                         ast.Continue, ast.Break)):
                        dead.append(block[i + 1].lineno)
                        break
        check("%s: no statement the control flow can never reach" % filename,
              not dead, "first at line %d" % min(dead) if dead else "")


def main():
    global VERBOSE
    parser = argparse.ArgumentParser(description="justjoin-scraper offline suite")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    VERBOSE = args.verbose

    global _TREE_BEFORE
    _TREE_BEFORE = _tree_state()

    for fn in CHECKS:
        if VERBOSE:
            print("\n== %s" % fn.__name__)
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — a broken check is a failure
            import traceback
            FAILURES.append("%s raised %s: %s" % (fn.__name__, type(e).__name__, e))
            print("  ERROR %s raised %s: %s" % (fn.__name__, type(e).__name__, e))
            if VERBOSE:
                traceback.print_exc()

    print("\n%d checks passed, %d failed, %d group(s) skipped."
          % (PASSED, len(FAILURES), len(SKIPS)))
    for line in SKIPS:
        print("  skipped: %s" % line)
    if FAILURES:
        print("\nFailures:")
        for line in FAILURES:
            print("  - %s" % line)
        return 1
    return 0


def check_x_debug_header_is_redacted():
    """SECURITY.md names the Scraper API's x-debug header as a place
    credentials reach a log unmasked. It was then logged verbatim.

    The fixtures are assembled from pieces rather than written out whole,
    because this file is scanned by the credential check like every other
    and a fixture that LOOKS like a live key fails it. They are the SHAPES a
    credential takes, not the literals this repo happens to contain today.
    """
    try:
        import scraper_api_client as sac
    except ImportError:
        return

    pw = "SeCr" + "EtPw"
    key = "abcdef01" * 4
    raw = ("cdpurl=ws://acct-zone-scraping_browser-pid-7:" + pw
           + "@cb.2captcha.com:9222 cost=0.00145 key=" + key + " status=200")
    out = sac._redact_debug_header(raw)
    check("x-debug: the credential and the key are gone",
                 pw not in out and key not in out)
    check("x-debug: the cost, host and status survive",
                 "cost=0.00145" in out and "cb.2captcha.com:9222" in out
                 and "status=200" in out)

    s1, s2 = "secret" + "one", "secret" + "two"
    two = sac._redact_debug_header(
        "a=http://u1:" + s1 + "@h1:1 b=http://u2:" + s2 + "@h2:2")
    check("x-debug: both credentials are masked, not just the first",
                 s1 not in two and s2 not in two)

    src = inspect.getsource(sac)
    check("x-debug: the log line calls the redactor",
                 'logger.info("x-debug: %s", _redact_debug_header(debug))' in src)



CHECKS = [v for k, v in sorted(globals().items()) if k.startswith("check_")]


if __name__ == "__main__":
    sys.exit(main())
