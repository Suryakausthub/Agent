"""Job-description fetching (spec section 12).

Workday, Greenhouse, Lever and Ashby postings are fetched through each
system's public JSON API, which is complete and reliable. Anything else falls
back to best-effort HTML scraping, which works only on server-rendered pages.

LinkedIn and several other boards render in JavaScript and block non-browser
clients; for those the caller is told to paste, not given a half-scraped page
-- a JD that is silently missing its requirements section would quietly
corrupt the score.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlparse

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


# ----------------------------------------------- Greenhouse / Lever / Ashby
#
# All three applicant-tracking systems publish postings through free, public,
# unauthenticated JSON APIs. Like Workday, that beats scraping: the data is
# complete and structured, and it works even though their pages render in
# JavaScript.

def _get_json(url: str, timeout: float) -> tuple[int, object]:
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": _UA,
                                   "Accept": "application/json"}) as c:
            resp = c.get(url)
    except httpx.HTTPError as e:
        raise FetchError("request to %s failed: %s"
                         % (urlparse(url).hostname, e)) from e
    if resp.status_code >= 400:
        return resp.status_code, None
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, None


def _html_text(fragment: str) -> str:
    return _clean(HTMLParser(fragment or "").text(separator="\n"))


def _compose(title: str, company: str, location: str, extra: list[str],
             body: str) -> str:
    if len(body) < MIN_CHARS:
        raise FetchError("the posting's description was only %d characters"
                         % len(body))
    header = [(title or "").strip(), company, location, *extra]
    return "\n".join(h for h in header if h) + "\n\n" + body


def _slug_name(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def _guess_tokens(url: str, html_patterns: list[str],
                  timeout: float) -> list[str]:
    """Board names to try for a posting hosted on a company's own site.

    Read from the page itself (embed scripts name the board) and, failing
    that, from the domain: careers.airbnb.com -> airbnb.
    """
    tokens: list[str] = []
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": _UA}) as c:
            page = c.get(url).text
        for pat in html_patterns:
            tokens += re.findall(pat, page, re.I)
    except httpx.HTTPError:
        pass
    host = (urlparse(url).hostname or "").lower().split(".")
    skip = {"www", "careers", "jobs", "job", "boards", "apply", "work", "join"}
    for part in host[:-1]:
        if part not in skip and len(part) > 1:
            tokens.append(part)
            break
    return list(dict.fromkeys(t.lower() for t in tokens if t))


_GH_HOSTS = ("boards.greenhouse.io", "job-boards.greenhouse.io",
             "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io")


def greenhouse_target(url: str) -> tuple[list[str] | None, str] | None:
    """(board tokens or None-to-discover, job id) for a Greenhouse posting."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    q = parse_qs(p.query)
    if host in _GH_HOSTS:
        parts = [s for s in p.path.split("/") if s]
        if "jobs" in parts and parts.index("jobs") >= 1:
            i = parts.index("jobs")
            if i + 1 < len(parts) and parts[i + 1].isdigit():
                return [parts[i - 1]], parts[i + 1]
        if q.get("for") and q.get("token"):           # /embed/job_app?for=&token=
            return [q["for"][0]], q["token"][0]
    if q.get("gh_jid") and q["gh_jid"][0].isdigit():  # company-hosted board
        return None, q["gh_jid"][0]
    return None


def fetch_greenhouse(url: str, timeout: float = 25.0) -> tuple[str, str]:
    target = greenhouse_target(url)
    if not target:
        raise FetchError("not a Greenhouse posting URL")
    tokens, job_id = target
    if tokens is None:
        tokens = _guess_tokens(url, [
            r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([a-z0-9_-]+)",
            r"boards-api(?:\.eu)?\.greenhouse\.io/v1/boards/([a-z0-9_-]+)",
        ], timeout)
    data = None
    for token in tokens:
        if token in ("embed", "v1"):
            continue
        # One API host serves every board, EU-hosted boards included.
        status, data = _get_json(
            "https://boards-api.greenhouse.io/v1/boards/%s/jobs/%s"
            % (token, job_id), timeout)
        if data:
            break
    if not data:
        raise FetchError("couldn't find this job on Greenhouse (tried board%s "
                         "%s) - it may have closed; paste the description"
                         % ("s" if len(tokens) != 1 else "",
                            ", ".join(tokens) or "none found"))
    body = _html_text(html.unescape(data.get("content") or ""))
    company = data.get("company_name") or _slug_name(tokens[0])
    return _compose(data.get("title", "").strip(), company,
                    (data.get("location") or {}).get("name", ""),
                    ["Job ID: %s" % data["id"] if data.get("id") else ""],
                    body), data.get("absolute_url") or url


def lever_target(url: str) -> tuple[str, str, str] | None:
    """(api host, company, posting id) for a Lever posting."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host not in ("jobs.lever.co", "jobs.eu.lever.co"):
        return None
    parts = [s for s in p.path.split("/") if s]
    if len(parts) < 2:
        return None
    api = "https://api.eu.lever.co" if ".eu." in host else "https://api.lever.co"
    return api, parts[0], parts[1]


def fetch_lever(url: str, timeout: float = 25.0) -> tuple[str, str]:
    target = lever_target(url)
    if not target:
        raise FetchError("not a Lever posting URL")
    api, company, posting = target
    status, data = _get_json("%s/v0/postings/%s/%s" % (api, company, posting),
                             timeout)
    if not isinstance(data, dict) or not data.get("text"):
        raise FetchError("Lever returned HTTP %d for this posting - it may "
                         "have closed; paste the description" % status)
    parts = [data.get("descriptionPlain") or _html_text(data.get("description") or "")]
    for block in data.get("lists") or []:
        items = _html_text(block.get("content") or "")
        parts.append("%s\n%s" % (block.get("text", ""), items))
    parts.append(data.get("additionalPlain") or "")
    cats = data.get("categories") or {}
    return _compose(data.get("text", ""), _slug_name(company),
                    cats.get("location", ""),
                    [cats.get("commitment", ""), cats.get("team", "")],
                    _clean("\n\n".join(x for x in parts if x))), \
        data.get("hostedUrl") or url


def ashby_target(url: str) -> tuple[list[str] | None, str] | None:
    """(org names or None-to-discover, job id) for an Ashby posting."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    q = parse_qs(p.query)
    if host == "jobs.ashbyhq.com":
        parts = [s for s in p.path.split("/") if s]
        if len(parts) >= 2:
            return [parts[0]], parts[1]
    if q.get("ashby_jid"):                           # company-hosted board
        return None, q["ashby_jid"][0]
    return None


def fetch_ashby(url: str, timeout: float = 40.0) -> tuple[str, str]:
    """Ashby's public API serves a whole job board, not single postings, so
    the board is fetched and the job picked out by id (large boards are a few
    MB - slower than the others, still seconds)."""
    target = ashby_target(url)
    if not target:
        raise FetchError("not an Ashby posting URL")
    orgs, job_id = target
    if orgs is None:
        orgs = _guess_tokens(url, [r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)"],
                             timeout)
    for org in orgs:
        status, data = _get_json(
            "https://api.ashbyhq.com/posting-api/job-board/%s" % org, timeout)
        if not isinstance(data, dict):
            continue
        job = next((j for j in data.get("jobs") or [] if j.get("id") == job_id),
                   None)
        if job is None:
            raise FetchError("this job is no longer on %s's Ashby board - it "
                             "may have closed; paste the description" % org)
        body = job.get("descriptionPlain") or _html_text(job.get("descriptionHtml") or "")
        return _compose(job.get("title", ""), _slug_name(org),
                        job.get("location", ""),
                        [job.get("employmentType", ""), job.get("department", "")],
                        _clean(body)), job.get("jobUrl") or url
    raise FetchError("couldn't find this company's Ashby job board (tried %s)"
                     % (", ".join(orgs) or "none found"))


def fetch_jd(url: str, timeout: float = 20.0) -> tuple[str, str]:
    """Return (description_text, final_url). Raises FetchError on failure."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError("only http(s) URLs are supported")

    if workday_api_url(url):
        return fetch_workday(url, timeout=max(timeout, 25.0))
    if greenhouse_target(url):
        return fetch_greenhouse(url, timeout=max(timeout, 25.0))
    if lever_target(url):
        return fetch_lever(url, timeout=max(timeout, 25.0))
    if ashby_target(url):
        return fetch_ashby(url, timeout=max(timeout, 40.0))

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
