"""Job-link fetching against live job boards (network, no model calls).

Postings close, so the live samples are discovered from each board's current
listing at run time rather than hard-coded.
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
from app.core.jd_fetch import (FetchError, ashby_target, fetch_jd,
                               greenhouse_target, lever_target)

fails = 0


def check(cond, label):
    global fails
    print("  %s %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        fails += 1


print("URL recognition (offline)")
check(greenhouse_target("https://job-boards.greenhouse.io/airbnb/jobs/123") == (["airbnb"], "123"),
      "Greenhouse board URL")
check(greenhouse_target("https://boards.greenhouse.io/embed/job_app?for=acme&token=456") == (["acme"], "456"),
      "Greenhouse embed URL")
check(greenhouse_target("https://careers.airbnb.com/positions/9?gh_jid=9") == (None, "9"),
      "company-hosted Greenhouse (gh_jid)")
check(lever_target("https://jobs.lever.co/palantir/abc-123/apply") == ("https://api.lever.co", "palantir", "abc-123"),
      "Lever URL (with /apply)")
check(lever_target("https://jobs.eu.lever.co/acme/x1")[0] == "https://api.eu.lever.co", "Lever EU URL")
check(ashby_target("https://jobs.ashbyhq.com/openai/u-1/application") == (["openai"], "u-1"),
      "Ashby URL (with /application)")
check(greenhouse_target("https://example.com/jobs/1") is None and lever_target("https://example.com/x/y") is None,
      "other sites are not misrecognised")

c = httpx.Client(timeout=40, follow_redirects=True)


def live(label, url, want_company=None):
    try:
        text, final = fetch_jd(url)
    except FetchError as e:
        check(False, "%s: %s" % (label, e))
        return
    head = text.split("\n\n")[0].replace("\n", " | ")
    ok = len(text) > 400 and (want_company is None or want_company.lower() in text.lower())
    check(ok, "%s -> %d chars  [%s]" % (label, len(text), head[:90]))


print("\nlive fetches")
gh = c.get("https://boards-api.greenhouse.io/v1/boards/airbnb/jobs").json()["jobs"][0]
live("Greenhouse board link", "https://job-boards.greenhouse.io/airbnb/jobs/%s" % gh["id"], "airbnb")
live("Greenhouse company-hosted link", gh["absolute_url"], "airbnb")

lv = c.get("https://api.lever.co/v0/postings/palantir?limit=1&mode=json").json()[0]
live("Lever link", lv["hostedUrl"], "palantir")

ab = c.get("https://api.ashbyhq.com/posting-api/job-board/ramp").json()["jobs"][0]
live("Ashby link", ab["jobUrl"], "ramp")

print("\nclosed postings give a clear message")
for label, url in [
    ("Greenhouse", "https://job-boards.greenhouse.io/airbnb/jobs/1"),
    ("Lever", "https://jobs.lever.co/palantir/00000000-0000-0000-0000-000000000000"),
    ("Ashby", "https://jobs.ashbyhq.com/ramp/00000000-0000-0000-0000-000000000000"),
]:
    try:
        fetch_jd(url)
        check(False, "%s closed posting should fail" % label)
    except FetchError as e:
        check("paste" in str(e) or "closed" in str(e), "%s: %s" % (label, str(e)[:90]))

print("\nFAILED: %d" % fails if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
