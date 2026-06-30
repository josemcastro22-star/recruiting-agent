#!/usr/bin/env python3
"""
South FL Job Search Agent
Scans career pages of local South FL companies for relevant openings.
Outputs a clean markdown report of matches.

Run locally:  python3 job_searcher.py
Scheduled:    GitHub Actions runs this every Monday at 8am ET
"""

import requests
import time
import sys
import json
from datetime import datetime

# ─── CONFIG ──────────────────────────────────────────────────────────────────

KEYWORDS = [
    "cybersecurity", "cyber security", "security analyst", "soc analyst",
    "information security", "it analyst", "it specialist", "automation",
    "ai engineer", "ai analyst", "machine learning", "python developer",
    "cloud security", "grc", "compliance analyst", "network security",
    "devops", "devsecops", "prompt engineer", "data analyst",
    "workflow automation", "security operations", "helpdesk", "it support",
]

LOCATION_KEYWORDS = [
    "florida", "fort lauderdale", "boca raton", "west palm beach",
    "broward", "palm beach", "dania", "deerfield", "pompano",
    "delray", "boynton", "coral springs", "plantation", "weston",
    "remote", "work from home", "wfh",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

REQUEST_TIMEOUT = 12   # seconds per request
MAX_RETRIES     = 3    # retries on 429 or 5xx
RETRY_DELAY     = 5    # seconds between retries

# ─── COMPANY REGISTRY ────────────────────────────────────────────────────────

COMPANIES = [
    # ── Greenhouse (verified slugs) ───────────────────────────────────────
    {
        "name": "Chewy",                 "hq": "Dania Beach, FL",
        "type": "greenhouse",            "slug": "chewycom",
        "career_url": "https://careers.chewy.com",
    },
    {
        "name": "Modernizing Medicine",  "hq": "Boca Raton, FL",
        "type": "greenhouse",            "slug": "modernizingmedicineinc",
        "career_url": "https://www.modmed.com/company/careers/",
    },
    # ── Workday (POST API) ────────────────────────────────────────────────
    {
        "name": "AutoNation",            "hq": "Fort Lauderdale, FL",
        "type": "workday",
        "workday_tenant": "autonation",  "workday_instance": "wd5",
        "workday_board": "Careers",
        "career_url": "https://autonation.wd5.myworkdayjobs.com/Careers",
    },
    {
        "name": "JM Family Enterprises", "hq": "Deerfield Beach, FL",
        "type": "workday",
        "workday_tenant": "jm",          "workday_instance": "wd103",
        "workday_board": "External",
        "career_url": "https://jm.wd103.myworkdayjobs.com/External",
    },
    # ── SmartRecruiters ───────────────────────────────────────────────────
    {
        "name": "Citrix / Cloud Software Group", "hq": "Fort Lauderdale, FL",
        "type": "smartrecruiters",       "slug": "Citrix1",
        "career_url": "https://careers.cloud.com/",
    },
    # ── Scrape only ───────────────────────────────────────────────────────
    {
        "name": "ADT Security",          "hq": "Boca Raton, FL",
        "type": "scrape",
        "career_url": "https://jobs.adt.com",
        "search_url": "https://jobs.adt.com/job-search-results/?keyword=cybersecurity+IT+analyst",
    },
    {
        "name": "UKG",                   "hq": "Weston, FL",
        "type": "scrape",
        "career_url": "https://www.ukg.com/about-us/careers",
        "search_url": "https://www.ukg.com/about-us/careers?q=cybersecurity+IT+analyst",
    },
    {
        "name": "Carnival Corporation",  "hq": "Doral, FL",
        "type": "scrape",
        "career_url": "https://jobs.carnivalcorp.com",
        "search_url": "https://jobs.carnivalcorp.com/search-jobs?q=cybersecurity+IT+analyst",
    },
    {
        "name": "Office Depot / ODP",    "hq": "Boca Raton, FL",
        "type": "scrape",
        "career_url": "https://jobs.officedepot.com",
        "search_url": "https://jobs.officedepot.com/search-jobs/cybersecurity%20IT%20analyst/Florida/0",
    },
    {
        "name": "Broward County Gov",    "hq": "Fort Lauderdale, FL",
        "type": "scrape",
        "career_url": "https://www.governmentjobs.com/careers/broward",
        "search_url": "https://www.governmentjobs.com/careers/broward?keywords=cybersecurity+IT+analyst",
    },
    {
        "name": "City of Fort Lauderdale", "hq": "Fort Lauderdale, FL",
        "type": "scrape",
        "career_url": "https://www.governmentjobs.com/careers/ftlauderdale",
        "search_url": "https://www.governmentjobs.com/careers/ftlauderdale?keywords=cybersecurity+IT",
    },
    {
        "name": "City of Boca Raton",    "hq": "Boca Raton, FL",
        "type": "scrape",
        "career_url": "https://www.governmentjobs.com/careers/bocaraton",
        "search_url": "https://www.governmentjobs.com/careers/bocaraton?keywords=IT+analyst+cybersecurity",
    },
    {
        "name": "Palm Beach County Gov", "hq": "West Palm Beach, FL",
        "type": "scrape",
        "career_url": "https://www.governmentjobs.com/careers/palmbeach",
        "search_url": "https://www.governmentjobs.com/careers/palmbeach?keywords=cybersecurity+IT+analyst",
    },
]


# ─── FETCH WITH RETRY ────────────────────────────────────────────────────────

class FetchError(Exception):
    """Raised when a URL can't be fetched after all retries."""
    def __init__(self, url, reason):
        self.url    = url
        self.reason = reason
        super().__init__(f"Failed to fetch {url}: {reason}")


def fetch(url: str) -> requests.Response:
    """
    GET a URL with retry logic.
    Retries on 429 (rate limit) and 5xx (server errors).
    Raises FetchError on permanent failures (4xx, SSL, timeout, etc.)
    """
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)

            # Rate limited — wait and retry
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", RETRY_DELAY * attempt))
                print(f"    ⏳ Rate limited. Waiting {wait}s (attempt {attempt}/{MAX_RETRIES})...")
                time.sleep(wait)
                last_error = f"HTTP 429 rate limit"
                continue

            # Server error — retry with backoff
            if r.status_code >= 500:
                print(f"    ⚠ HTTP {r.status_code}. Retrying in {RETRY_DELAY * attempt}s...")
                time.sleep(RETRY_DELAY * attempt)
                last_error = f"HTTP {r.status_code} server error"
                continue

            # Not found — slug is probably wrong, don't retry
            if r.status_code == 404:
                raise FetchError(url, "HTTP 404 — company may have changed their ATS or slug")

            # Other 4xx — don't retry
            if r.status_code >= 400:
                raise FetchError(url, f"HTTP {r.status_code} client error")

            return r  # ✅ success

        except requests.exceptions.SSLError as e:
            raise FetchError(url, f"SSL certificate error — {e}")
        except requests.exceptions.ConnectionError as e:
            last_error = f"Connection error — {e}"
            print(f"    ⚠ Connection failed (attempt {attempt}/{MAX_RETRIES}): {e}")
            time.sleep(RETRY_DELAY * attempt)
        except requests.exceptions.Timeout:
            last_error = f"Timed out after {REQUEST_TIMEOUT}s"
            print(f"    ⚠ Timeout (attempt {attempt}/{MAX_RETRIES})")
            time.sleep(RETRY_DELAY)
        except FetchError:
            raise  # pass 404s and 4xx straight through
        except Exception as e:
            raise FetchError(url, f"Unexpected error — {e}")

    raise FetchError(url, f"All {MAX_RETRIES} retries failed. Last error: {last_error}")


def safe_parse_json(response: requests.Response, source: str) -> dict | list | None:
    """Parse JSON from a response, returning None on failure."""
    try:
        return response.json()
    except ValueError:
        # Response isn't JSON — could be HTML error page or empty body
        preview = response.text[:120].replace("\n", " ").strip()
        print(f"    ⚠ JSON parse failed for {source}. Response preview: '{preview}'")
        return None


# ─── RELEVANCE CHECK ─────────────────────────────────────────────────────────

def is_relevant(title: str, location: str = "") -> bool:
    title_lower    = (title or "").lower()
    location_lower = (location or "").lower()

    if not any(kw in title_lower for kw in KEYWORDS):
        return False

    # No location = remote/unspecified → accept it
    if not location_lower:
        return True

    return any(kw in location_lower for kw in LOCATION_KEYWORDS)


def make_job(company: dict, title: str, location: str, link: str, note: str = "") -> dict:
    """Safely build a job dict, stripping None/empty values."""
    return {
        "company":  company["name"],
        "hq":       company["hq"],
        "title":    (title    or "Untitled").strip(),
        "location": (location or "").strip(),
        "link":     (link     or "").strip(),
        "note":     note,
    }


# ─── SCRAPERS ────────────────────────────────────────────────────────────────

def check_greenhouse(company: dict) -> tuple[list[dict], str | None]:
    """
    Returns (matches, error_message).
    error_message is None on success.
    """
    slug = company["slug"]
    url  = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    print(f"  → Greenhouse API ({slug})")

    try:
        r    = fetch(url)
        data = safe_parse_json(r, url)
    except FetchError as e:
        hint = " — Check the slug in COMPANIES list." if "404" in str(e) else ""
        return [], f"{e}{hint}"

    if data is None:
        return [], "Response was not valid JSON"

    if not isinstance(data, dict):
        return [], f"Unexpected response shape: {type(data).__name__}"

    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        return [], f"'jobs' key missing or not a list (got {type(jobs).__name__})"

    matches, seen = [], set()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        title    = job.get("title", "")
        location = job.get("location", {})
        location = location.get("name", "") if isinstance(location, dict) else ""
        link     = job.get("absolute_url", "")

        if not title:
            continue  # skip malformed entries

        dedup_key = (title.lower(), location.lower())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        if is_relevant(title, location):
            matches.append(make_job(company, title, location, link))

    return matches, None


def check_lever(company: dict) -> tuple[list[dict], str | None]:
    slug = company["slug"]
    url  = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    print(f"  → Lever API ({slug})")

    try:
        r    = fetch(url)
        data = safe_parse_json(r, url)
    except FetchError as e:
        hint = " — Check the slug in COMPANIES list." if "404" in str(e) else ""
        return [], f"{e}{hint}"

    if data is None:
        return [], "Response was not valid JSON"

    if not isinstance(data, list):
        return [], f"Expected list from Lever, got {type(data).__name__}"

    matches, seen = [], set()
    for job in data:
        if not isinstance(job, dict):
            continue
        title    = job.get("text", "")
        cats     = job.get("categories", {})
        location = cats.get("location", "") if isinstance(cats, dict) else ""
        link     = job.get("hostedUrl", "")

        if not title:
            continue

        dedup_key = (title.lower(), location.lower())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        if is_relevant(title, location):
            matches.append(make_job(company, title, location, link))

    return matches, None


def check_workday(company: dict) -> tuple[list[dict], str | None]:
    """
    Query Workday's internal jobs API (POST).
    URL pattern: https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs
    """
    tenant   = company["workday_tenant"]
    instance = company["workday_instance"]
    board    = company["workday_board"]
    url      = f"https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
    print(f"  → Workday API ({tenant}.{instance})")

    all_matches, seen, offset = [], set(), 0
    limit = 20

    while True:
        payload = {"limit": limit, "offset": offset, "searchText": ""}
        try:
            r = requests.post(
                url,
                headers={**HEADERS, "Content-Type": "application/json"},
                data=json.dumps(payload),
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 404:
                return [], f"HTTP 404 — check workday_tenant/board in COMPANIES list"
            if r.status_code == 403:
                return [], "HTTP 403 — Workday is blocking automated requests for this company"
            if r.status_code >= 400:
                return [], f"HTTP {r.status_code}"
            data = safe_parse_json(r, url)
        except requests.exceptions.Timeout:
            return [], f"Timed out after {REQUEST_TIMEOUT}s"
        except requests.exceptions.ConnectionError as e:
            return [], f"Connection error: {e}"
        except Exception as e:
            return [], f"Unexpected error: {e}"

        if data is None:
            return [], "Response was not valid JSON"

        job_postings = data.get("jobPostings", [])
        if not isinstance(job_postings, list):
            break

        for job in job_postings:
            if not isinstance(job, dict):
                continue
            title       = job.get("title", "")
            location    = job.get("locationsText", "")
            external_id = job.get("externalPath", "")
            link        = f"{company['career_url'].rstrip('/')}{external_id}" if external_id else company["career_url"]

            if not title:
                continue
            dedup_key = (title.lower(), location.lower())
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            if is_relevant(title, location):
                all_matches.append(make_job(company, title, location, link))

        # Paginate if there are more results
        total = data.get("total", 0)
        offset += limit
        if offset >= total or not job_postings:
            break

    return all_matches, None


def check_smartrecruiters(company: dict) -> tuple[list[dict], str | None]:
    """Query SmartRecruiters public postings API."""
    slug = company["slug"]
    url  = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"
    print(f"  → SmartRecruiters API ({slug})")

    try:
        r    = fetch(url)
        data = safe_parse_json(r, url)
    except FetchError as e:
        hint = " — Check slug in COMPANIES list." if "404" in str(e) else ""
        return [], f"{e}{hint}"

    if data is None:
        return [], "Response was not valid JSON"

    postings = data.get("content", [])
    if not isinstance(postings, list):
        return [], f"Unexpected response shape from SmartRecruiters"

    matches, seen = [], set()
    for job in postings:
        if not isinstance(job, dict):
            continue
        title    = job.get("name", "")
        location = job.get("location", {})
        city     = location.get("city", "") if isinstance(location, dict) else ""
        country  = location.get("country", "") if isinstance(location, dict) else ""
        loc_str  = f"{city}, {country}".strip(", ")
        link     = f"https://jobs.smartrecruiters.com/{slug}/{job.get('id', '')}"

        if not title:
            continue
        dedup_key = (title.lower(), loc_str.lower())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        if is_relevant(title, loc_str):
            matches.append(make_job(company, title, loc_str, link))

    return matches, None


def check_scrape(company: dict) -> tuple[list[dict], str | None]:
    """
    Fetches search URL, looks for keyword hits in raw HTML.
    Returns a single pointer entry if keywords found (JS pages need manual verify).
    """
    search_url = company.get("search_url", company["career_url"])
    print(f"  → Scraping {search_url}")

    try:
        r = fetch(search_url)
    except FetchError as e:
        return [], str(e)

    if not r.text:
        return [], "Response body was empty"

    page_text     = r.text.lower()
    found_keywords = [kw for kw in KEYWORDS if kw in page_text]

    if not found_keywords:
        return [], None  # No error, just no matches

    kw_preview = ", ".join(found_keywords[:4]) + ("..." if len(found_keywords) > 4 else "")
    return [make_job(
        company,
        title    = f"Possible matches — keywords found: {kw_preview}",
        location = company["hq"],
        link     = search_url,
        note     = "⚠ Page scraped (not parsed) — click link to verify real openings. JS-heavy pages may need manual check.",
    )], None


# ─── MAIN RUNNER ─────────────────────────────────────────────────────────────

def run_search() -> tuple[list[dict], list[dict]]:
    """Returns (all_matches, errors) where errors = [{company, reason}]."""
    all_matches, errors = [], []
    print(f"\n🔍 South FL Job Search — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    for company in COMPANIES:
        print(f"[{company['name']}]")
        try:
            if company["type"] == "greenhouse":
                matches, err = check_greenhouse(company)
            elif company["type"] == "lever":
                matches, err = check_lever(company)
            elif company["type"] == "workday":
                matches, err = check_workday(company)
            elif company["type"] == "smartrecruiters":
                matches, err = check_smartrecruiters(company)
            elif company["type"] == "scrape":
                matches, err = check_scrape(company)
            else:
                matches, err = [], f"Unknown type '{company['type']}'"

            if err:
                print(f"  ❌ Error: {err}")
                errors.append({"company": company["name"], "reason": err})
            elif matches:
                print(f"  ✅ {len(matches)} match(es) found")
                all_matches.extend(matches)
            else:
                print(f"  — No relevant openings")

        except Exception as e:
            # Catch-all: one company failing should never kill the whole run
            msg = f"Unhandled exception: {type(e).__name__}: {e}"
            print(f"  💥 {msg}")
            errors.append({"company": company["name"], "reason": msg})

        time.sleep(1)  # small delay between companies to be polite

    return all_matches, errors


# ─── REPORT BUILDER ──────────────────────────────────────────────────────────

def build_report(matches: list[dict], errors: list[dict]) -> str:
    now   = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    lines = [
        "# South FL Job Search Report",
        f"*Generated: {now}*",
        f"*Keywords: cybersecurity · IT analyst · automation · AI · GRC · SOC · Python*",
        "",
    ]

    # ── Matches ──────────────────────────────────────────────────────────
    if matches:
        lines.append(f"## ✅ {len(matches)} Relevant Opening(s) Found\n")
        by_company: dict[str, list] = {}
        for m in matches:
            by_company.setdefault(m["company"], []).append(m)

        for company_name, jobs in by_company.items():
            hq = jobs[0]["hq"]
            lines.append(f"### {company_name} — {hq}")
            for job in jobs:
                loc  = f" · *{job['location']}*" if job.get("location") else ""
                link = job.get("link", "")
                note = f"\n  > {job['note']}" if job.get("note") else ""
                entry = f"- [{job['title']}]({link}){loc}" if link else f"- {job['title']}{loc}"
                lines.append(entry + note)
            lines.append("")
    else:
        lines += ["## No relevant openings found this run.", ""]

    # ── Errors ───────────────────────────────────────────────────────────
    if errors:
        lines += [
            "## ⚠ Companies That Had Errors",
            "*These need attention — check slugs or career page URLs.*",
            "",
        ]
        for e in errors:
            lines.append(f"- **{e['company']}**: {e['reason']}")
        lines.append("")

    # ── Manual check list ────────────────────────────────────────────────
    lines += [
        "---",
        "## Career Pages to Check Manually",
        "*(JS-heavy pages that can't be fully scraped)*",
        "",
    ]
    for c in COMPANIES:
        lines.append(f"- [{c['name']}]({c['career_url']}) — {c['hq']}")

    return "\n".join(lines)


def save_report(report: str) -> str:
    filename = f"job_report_{datetime.now().strftime('%Y-%m-%d')}.md"
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n📄 Report saved: {filename}")
    except OSError as e:
        print(f"\n❌ Could not save report to file: {e}")
        print("Printing to stdout instead:\n")
        print(report)
    return filename


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    matches, errors = run_search()
    report          = build_report(matches, errors)
    save_report(report)

    # Exit with error code if every company failed (useful for GitHub Actions alerts)
    if errors and not matches:
        print("\n⚠ All companies errored and no matches found. Check errors above.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print(report)
