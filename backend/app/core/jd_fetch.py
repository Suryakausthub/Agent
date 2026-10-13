"""Best-effort job-description fetching (spec section 12).

Pasting is the reliable path and the UI leads with it. This exists to save a
copy-paste when the posting happens to be server-rendered.

It will fail on a lot of real postings, and that is expected rather than a bug:
LinkedIn, Workday and most Greenhouse-embedded boards render the description in
JavaScript, and several actively block non-browser clients. When that happens
the caller is told to paste, not given a half-scraped page -- a JD that is
silently missing its requirements section would quietly corrupt the score.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# Stripped before text extraction: never part of a job description.
_CHROME = ("script", "style", "nav", "header", "footer", "noscript", "svg",
           "form", "iframe", "aside", "button")

# Containers job boards commonly use for the description body.
_CANDIDATES = (
    '[data-testid="jobDescriptionText"]',
    "#jobDescriptionText",
    ".jobsearch-JobComponent-description",
    '[class*="job-description"]',
    '[class*="jobDescription"]',
    '[id*="job-description"]',
    '[class*="posting-description"]',
    ".content-intro",
    "#content",
    "main",
    "article",
)

# Boards known to render client-side or block bots. Failing fast with a clear
# message beats returning a page of navigation chrome.
#
# Workday is deliberately NOT here: its pages are client-rendered, but every
# tenant exposes the posting as JSON behind /wday/cxs/..., which gives cleaner
# and more complete data than scraping ever would. See fetch_workday.
_JS_ONLY = ("linkedin.com", "icims.com", "taleo.net", "successfactors.com",
            "smartrecruiters.com", "eightfold.ai", "naukri.com", "glassdoor.")

MIN_CHARS = 400


class FetchError(RuntimeError):
    pass


def _clean(text: str) -> str:
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return "\n".join(l.rstrip() for l in text.splitlines()).strip()


def _extract_text(html: str) -> str:
    tree = HTMLParser(html)
    for tag in _CHROME:
        for node in tree.css(tag):
            node.decompose()

    best = ""
    for sel in _CANDIDATES:
        try:
            nodes = tree.css(sel)
        except Exception:
            continue
        for node in nodes:
            txt = _clean(node.text(separator="\n"))
            if len(txt) > len(best):
                best = txt
        if len(best) >= MIN_CHARS:
            return best

    body = tree.css_first("body")
    whole = _clean(body.text(separator="\n")) if body else ""
    return best if len(best) > len(whole) * 0.5 else whole


def workday_api_url(url: str) -> str | None:
    """Map a Workday posting URL to its JSON endpoint.

        https://TENANT.wdNN.myworkdayjobs.com[/LOCALE]/SITE/job/PATH
     -> https://TENANT.wdNN.myworkdayjobs.com/wday/cxs/TENANT/SITE/job/PATH

    Returns None if the URL is not a Workday posting.
    """
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if "myworkdayjobs.com" not in host and "workday.com" not in host:
        return None

    tenant = host.split(".")[0]
    parts = [seg for seg in p.path.split("/") if seg]
    if "job" not in parts:
        return None

    job_at = parts.index("job")
    if job_at == 0:
        return None
    # The segment before /job/ is the site id; anything before that is a locale.
    site = parts[job_at - 1]
    tail = "/".join(parts[job_at:])
    return "https://%s/wday/cxs/%s/%s/%s" % (p.netloc, tenant, site, tail)


def fetch_workday(url: str, timeout: float = 25.0) -> tuple[str, str]:
    """Fetch a Workday posting through its JSON API.

    Returns text with the structured fields as a header followed by the
    description body, so the extractor gets verified company/title/location/
    req-id rather than having to infer them from prose.
    """
    api = workday_api_url(url)
    if not api:
        raise FetchError("not a Workday posting URL")

    try:
        with httpx.Client(follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": _UA,
                                   "Accept": "application/json"}) as c:
            resp = c.get(api)
    except httpx.HTTPError as e:
        raise FetchError("Workday API request failed: %s" % e) from e

    if resp.status_code >= 400:
        raise FetchError("Workday API returned HTTP %d" % resp.status_code)

    try:
        data = resp.json()
    except ValueError as e:
        raise FetchError("Workday API did not return JSON: %s" % e) from e

    info = data.get("jobPostingInfo") or {}
    if not info:
        raise FetchError("Workday response contained no jobPostingInfo")

    body = _clean(HTMLParser(info.get("jobDescription") or "").text(separator="\n"))
    if len(body) < MIN_CHARS:
        raise FetchError("Workday description was only %d characters" % len(body))

    # hiringOrganization.name is the legal entity ("Postdot Technologies India
    # Private Ltd"), which is not what anyone calls the company or what the
    # tracker should show. The Workday tenant is the brand ("postman"), so
    # offer both and let extraction prefer the recognisable one.
    legal = (data.get("hiringOrganization") or {}).get("name") or ""
    tenant = (urlparse(api).hostname or "").split(".")[0]
    brand = tenant.replace("-", " ").title() if tenant else ""
    if brand and legal and brand.lower() not in legal.lower():
        company = "%s (%s)" % (brand, legal)
    else:
        company = legal or brand

    location = (info.get("location")
                or (info.get("jobRequisitionLocation") or {}).get("descriptor") or "")

    header = [
        info.get("title", ""),
        company,
        location,
        info.get("timeType", ""),
        "Job ID: %s" % info.get("jobReqId", "") if info.get("jobReqId") else "",
        "Posted: %s" % info.get("startDate", "") if info.get("startDate") else "",
    ]
    text = "\n".join(h for h in header if h) + "\n\n" + body
    return text, info.get("externalUrl") or url


def fetch_jd(url: str, timeout: float = 20.0) -> tuple[str, str]:
    """Return (description_text, final_url). Raises FetchError on failure."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError("only http(s) URLs are supported")

    if workday_api_url(url):
        return fetch_workday(url, timeout=max(timeout, 25.0))

    host = (parsed.hostname or "").lower()
    if any(d in host for d in _JS_ONLY):
        raise FetchError(
            "%s renders its postings in JavaScript and blocks plain HTTP "
            "clients; paste the description text instead" % host)

    try:
        with httpx.Client(follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": _UA,
                                   "Accept-Language": "en-US,en;q=0.9"}) as c:
            resp = c.get(url)
    except httpx.HTTPError as e:
        raise FetchError("request failed: %s" % e) from e

    if resp.status_code >= 400:
        raise FetchError("server returned HTTP %d" % resp.status_code)

    text = _extract_text(resp.text)
    if len(text) < MIN_CHARS:
        raise FetchError(
            "only %d characters of readable text found - the posting is "
            "probably rendered client-side" % len(text))
    return text, str(resp.url)
