#!/usr/bin/env python3
"""London paralegal job tracker.

Fetches every paralegal role within 10 miles of London from the Reed and
Adzuna APIs, merges them into data/jobs.json (deduplicating across sources and
against earlier runs), scores each job as a preference signal and regenerates
docs/index.html.

API keys are read from the environment only:
    REED_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY

Uses only the Python standard library.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_dashboard import render_dashboard  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "jobs.json"
PAGE_FILE = ROOT / "docs" / "index.html"

KEYWORDS = "paralegal"
LOCATION = "London"
RADIUS_MILES = 10
MAX_AGE_DAYS = 30

# Request budgets per run. The workflow runs 8 times a day, so these keep us
# far below the free tiers (Adzuna: 250 calls/day, 1,000/week, 2,500/month).
REED_PAGE_SIZE = 100          # Reed maximum
REED_MAX_PAGES = 10           # up to 1,000 results -> <= 80 search calls/day
REED_MAX_DETAIL_CALLS = 40    # details (contract type, full text) for new jobs only
ADZUNA_PAGE_SIZE = 50         # Adzuna maximum
ADZUNA_MAX_PAGES = 5          # up to 250 results -> <= 40 calls/day, ~1,200/month
ADZUNA_DELAY_SECONDS = 3      # stay well under the 25 calls/minute limit

HTTP_TIMEOUT = 30
USER_AGENT = "london-paralegal-job-tracker/1.0 (+GitHub Actions)"

log = logging.getLogger("jobs")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _secrets() -> list[str]:
    return [v for v in (os.environ.get(k) for k in ("REED_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY")) if v]


def redact(text: str) -> str:
    """Strip any API credentials from text before it is logged or stored."""
    for secret in _secrets():
        text = text.replace(secret, "***")
    return text


def http_get_json(url: str, headers: dict | None = None, retries: int = 1) -> dict:
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    req_headers.update(headers or {})
    attempt = 0
    while True:
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if attempt < retries and (exc.code == 429 or exc.code >= 500):
                attempt += 1
                time.sleep(5 * attempt)
                continue
            raise RuntimeError(f"HTTP {exc.code} {exc.reason}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < retries:
                attempt += 1
                time.sleep(5 * attempt)
                continue
            raise RuntimeError(redact(f"network error: {getattr(exc, 'reason', exc)}")) from None


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean_text(value) -> str:
    if not value:
        return ""
    text = html.unescape(TAG_RE.sub(" ", str(value)))
    return WS_RE.sub(" ", text).strip()


def parse_date(value) -> str | None:
    """Return an ISO date (YYYY-MM-DD) from the formats the APIs use."""
    if not value:
        return None
    value = str(value).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def to_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def format_salary(lo, hi, estimated: bool = False) -> str:
    lo, hi = to_number(lo), to_number(hi)
    if not lo and not hi:
        return "Not specified"
    ref = hi or lo
    if ref < 200:
        unit = "/hr"
    elif ref < 1500:
        unit = "/day"
    else:
        unit = ""

    def money(n: float) -> str:
        return f"£{n:,.2f}".replace(".00", "") if n < 200 else f"£{n:,.0f}"

    if lo and hi and round(lo) != round(hi):
        text = f"{money(lo)} – {money(hi)}{unit}"
    else:
        text = f"{money(lo or hi)}{unit}"
    return f"{text} (est.)" if estimated else text


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

def fetch_reed(known_ids: set[str]) -> list[dict]:
    key = os.environ.get("REED_API_KEY")
    if not key:
        raise RuntimeError("REED_API_KEY is not set")
    auth = base64.b64encode(f"{key}:".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}

    raw: list[dict] = []
    for page in range(REED_MAX_PAGES):
        params = urllib.parse.urlencode({
            "keywords": KEYWORDS,
            "locationName": LOCATION,
            "distanceFromLocation": RADIUS_MILES,
            "resultsToTake": REED_PAGE_SIZE,
            "resultsToSkip": page * REED_PAGE_SIZE,
        })
        try:
            data = http_get_json(f"https://www.reed.co.uk/api/1.0/search?{params}", headers)
        except RuntimeError:
            if page == 0:
                raise
            log.exception("Reed: page %d failed; keeping %d results already fetched", page + 1, len(raw))
            break
        results = data.get("results") or []
        raw.extend(results)
        total = data.get("totalResults") or 0
        if len(results) < REED_PAGE_SIZE or len(raw) >= total:
            break

    jobs = []
    detail_calls = 0
    for item in raw:
        job_id = item.get("jobId")
        if not job_id:
            continue
        uid = f"reed:{job_id}"
        details = {}
        if uid not in known_ids and detail_calls < REED_MAX_DETAIL_CALLS:
            detail_calls += 1
            try:
                details = http_get_json(f"https://www.reed.co.uk/api/1.0/jobs/{job_id}", headers)
            except RuntimeError as exc:
                log.warning("Reed: details for job %s failed: %s", job_id, exc)
        hours = ""
        if details.get("partTime"):
            hours = "Part-time"
        elif details.get("fullTime"):
            hours = "Full-time"
        lo = item.get("minimumSalary") or details.get("yearlyMinimumSalary")
        hi = item.get("maximumSalary") or details.get("yearlyMaximumSalary")
        jobs.append({
            "id": uid,
            "source": "Reed",
            "title": clean_text(item.get("jobTitle")),
            "company": clean_text(item.get("employerName")),
            "location": clean_text(item.get("locationName")),
            "salary_min": to_number(lo),
            "salary_max": to_number(hi),
            "salary_estimated": False,
            "contract_raw": clean_text(details.get("contractType")),
            "hours_raw": hours,
            "url": item.get("jobUrl") or details.get("jobUrl") or f"https://www.reed.co.uk/jobs/{job_id}",
            "posted": parse_date(item.get("date") or details.get("datePosted")),
            "expires": parse_date(item.get("expirationDate") or details.get("expirationDate")),
            "description": clean_text(details.get("jobDescription") or item.get("jobDescription"))[:3000],
            "category": "",
        })
    log.info("Reed: %d jobs (%d detail lookups)", len(jobs), detail_calls)
    return jobs


def fetch_adzuna() -> list[dict]:
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError("ADZUNA_APP_ID / ADZUNA_APP_KEY are not set")

    raw: list[dict] = []
    for page in range(1, ADZUNA_MAX_PAGES + 1):
        if page > 1:
            time.sleep(ADZUNA_DELAY_SECONDS)
        params = urllib.parse.urlencode({
            "app_id": app_id,
            "app_key": app_key,
            "what": KEYWORDS,
            "where": LOCATION,
            "distance": round(RADIUS_MILES * 1.609),  # Adzuna uses kilometres
            "max_days_old": MAX_AGE_DAYS,
            "results_per_page": ADZUNA_PAGE_SIZE,
            "sort_by": "date",
            "content-type": "application/json",
        })
        try:
            data = http_get_json(f"https://api.adzuna.com/v1/api/jobs/gb/search/{page}?{params}")
        except RuntimeError:
            if page == 1:
                raise
            log.exception("Adzuna: page %d failed; keeping %d results already fetched", page, len(raw))
            break
        results = data.get("results") or []
        raw.extend(results)
        if len(results) < ADZUNA_PAGE_SIZE or len(raw) >= (data.get("count") or 0):
            break

    jobs = []
    for item in raw:
        job_id = item.get("id")
        if not job_id:
            continue
        hours = {"full_time": "Full-time", "part_time": "Part-time"}.get(item.get("contract_time") or "", "")
        jobs.append({
            "id": f"adzuna:{job_id}",
            "source": "Adzuna",
            "title": clean_text(item.get("title")),
            "company": clean_text((item.get("company") or {}).get("display_name")),
            "location": clean_text((item.get("location") or {}).get("display_name")),
            "salary_min": to_number(item.get("salary_min")),
            "salary_max": to_number(item.get("salary_max")),
            "salary_estimated": str(item.get("salary_is_predicted")) == "1",
            "contract_raw": clean_text(item.get("contract_type")),
            "hours_raw": hours,
            "url": item.get("redirect_url") or "",
            "posted": parse_date(item.get("created")),
            "expires": None,
            "description": clean_text(item.get("description"))[:3000],
            "category": clean_text((item.get("category") or {}).get("label")),
        })
    log.info("Adzuna: %d jobs", len(jobs))
    return jobs


# --------------------------------------------------------------------------
# Classification and scoring
# --------------------------------------------------------------------------

def _rx(*terms: str) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(terms) + r")\b", re.IGNORECASE)


# label -> pattern; order matters for practice areas (first title match wins)
POLICY_TERMS = {
    "public law": _rx(r"public law"),
    "public inquiry": _rx(r"public inquir(?:y|ies)", r"statutory inquir(?:y|ies)"),
    "regulatory": _rx(r"regulatory", r"regulator"),
    "government": _rx(r"government", r"civil service", r"whitehall", r"government legal"),
    "parliament": _rx(r"parliament(?:ary)?"),
    "local authority": _rx(r"local authorit(?:y|ies)", r"borough council", r"city council", r"county council", r"local government"),
    "public sector": _rx(r"public[- ]sector", r"public bod(?:y|ies)"),
    # Deliberately narrow: bare "policy" matches HR boilerplate ("equal opportunities policy").
    "policy": _rx(r"public policy", r"social policy", r"law and policy", r"(?:legal|government|regulatory) policy",
                  r"policy (?:team|unit|work|development|advis[eo]rs?|officers?|research|analysis|matters)",
                  r"policy and (?:public affairs|regulatory|campaigns|research)"),
    "judicial review": _rx(r"judicial reviews?"),
    "housing": _rx(r"housing"),
    "legal aid": _rx(r"legal aid"),
    "human rights": _rx(r"human rights", r"civil liberties"),
}

CAREER_TERMS = {
    "future trainee": _rx(r"future trainees?", r"training contract", r"trainee solicitor", r"pathway to (?:qualification|training)"),
    "SQE": _rx(r"SQE\d?", r"qualifying work experience", r"QWE"),
    "graduate": _rx(r"graduates?", r"entry[- ]level", r"junior paralegal"),
    "part-time": _rx(r"part[- ]time"),
    "temporary": _rx(r"temporary", r"temp", r"interim", r"fixed[- ]term", r"maternity cover"),
    "hybrid": _rx(r"hybrid", r"remote working", r"work from home"),
}

PRACTICE_AREAS = [
    ("Public Law", _rx(r"public law", r"judicial review", r"public inquir(?:y|ies)", r"administrative law", r"government legal")),
    ("Regulatory", _rx(r"regulatory", r"compliance", r"financial services regulation")),
    ("Human Rights", _rx(r"human rights", r"civil liberties", r"actions against the police")),
    ("Housing", _rx(r"housing", r"landlord (?:and|&) tenant", r"disrepair")),
    ("Immigration", _rx(r"immigration", r"asylum", r"visas?", r"business immigration")),
    ("Family", _rx(r"family", r"divorce", r"matrimonial", r"children law", r"care proceedings")),
    ("Crime", _rx(r"criminal", r"crime", r"police station", r"white[- ]collar", r"fraud")),
    ("Employment", _rx(r"employment", r"hr law", r"tribunal")),
    ("Personal Injury", _rx(r"personal injury", r"\bPI\b", r"serious injury", r"road traffic")),
    ("Clinical Negligence", _rx(r"clinical negligence", r"medical negligence", r"clin neg")),
    ("Property / Real Estate", _rx(r"real estate", r"property", r"conveyancing", r"conveyancer", r"leasehold", r"planning")),
    ("Private Client / Probate", _rx(r"private client", r"probate", r"wills", r"trusts", r"estates", r"court of protection")),
    ("Banking & Finance", _rx(r"banking", r"finance", r"funds?", r"capital markets", r"restructuring", r"insolvency")),
    ("Corporate / M&A", _rx(r"corporate", r"m&a", r"mergers", r"private equity", r"company secretar(?:y|ial)")),
    ("Commercial", _rx(r"commercial", r"contracts?", r"technology", r"data protection", r"privacy")),
    ("Intellectual Property", _rx(r"intellectual property", r"\bIP\b", r"patents?", r"trade ?marks?", r"copyright")),
    ("Construction", _rx(r"construction", r"projects", r"infrastructure", r"energy")),
    ("Insurance", _rx(r"insurance", r"reinsurance", r"professional negligence", r"defendant")),
    ("Competition", _rx(r"competition", r"antitrust")),
    ("Tax", _rx(r"tax")),
    ("Litigation / Disputes", _rx(r"litigation", r"disputes?", r"dispute resolution", r"arbitration", r"commercial court", r"investigations?", r"e-?disclosure", r"disclosure")),
    ("In-house", _rx(r"in[- ]house")),
]

NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_NUM = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
YEARS_RE = re.compile(
    _NUM + r"\s*\+?\s*(?:(?:-|–|to)\s*" + _NUM + r"\s*\+?\s*)?(?:years?|yrs?)\b",
    re.IGNORECASE,
)
EXPERIENCE_CONTEXT_RE = re.compile(r"experience|pqe|paralegal|background|in a (?:legal|law)|working (?:in|as|within)", re.IGNORECASE)
REQUIREMENT_PREFIX_RE = re.compile(r"(?:minimum|at least|min\.?|over|more than|plus)\s*(?:of\s*)?$", re.IGNORECASE)


def requires_experience(text: str, min_years: int = 2) -> str | None:
    """Return the matching phrase if the text asks for >= min_years experience."""
    for m in YEARS_RE.finditer(text):
        lower = m.group(1).lower()
        years = NUM_WORDS.get(lower) or int(lower)
        if years < min_years or years > 15:
            continue
        before = text[max(0, m.start() - 25):m.start()]
        after = text[m.end():m.end() + 50]
        if EXPERIENCE_CONTEXT_RE.search(after) or REQUIREMENT_PREFIX_RE.search(before.rstrip()):
            return m.group(0)
    return None


def matched_labels(terms: dict[str, re.Pattern], text: str) -> list[str]:
    return [label for label, pattern in terms.items() if pattern.search(text)]


def practice_area(job: dict) -> str:
    for field in ("title", "description"):
        text = job.get(field) or ""
        for label, pattern in PRACTICE_AREAS:
            if pattern.search(text):
                return label
    return "General / Not specified"


CONTRACT_INFER = [
    ("Fixed-term", _rx(r"fixed[- ]term", r"FTC", r"maternity cover", r"maternity leave cover", r"\d+[- ]months? contract")),
    ("Temporary", _rx(r"temporary", r"temp", r"temp to perm", r"interim", r"locum")),
    ("Contract", _rx(r"contractor", r"contract role", r"day rate")),
    ("Permanent", _rx(r"permanent", r"perm")),
]


def contract_type(job: dict) -> str:
    raw = (job.get("contract_raw") or "").lower()
    if raw:
        if "perm" in raw:
            return "Permanent"
        if "temp" in raw:
            return "Temporary"
        if "contract" in raw:
            return "Contract"
    for field in ("title", "description"):
        for label, pattern in CONTRACT_INFER:
            if pattern.search(job.get(field) or ""):
                return label
    return "Not specified"


def working_hours(job: dict) -> str:
    if job.get("hours_raw"):
        return job["hours_raw"]
    text = f"{job.get('title', '')} {job.get('description', '')}"
    if re.search(r"\bpart[- ]time\b", text, re.IGNORECASE):
        return "Part-time"
    if re.search(r"\bfull[- ]time\b", text, re.IGNORECASE):
        return "Full-time"
    return ""


def score_job(job: dict) -> dict:
    """Score 1-5 as a preference signal. Never used to exclude a job."""
    text = f"{job.get('title', '')}. {job.get('description', '')}"
    score = 3
    parts = ["Base 3"]

    policy = matched_labels(POLICY_TERMS, text)
    if policy:
        bonus = 2 if len(policy) >= 2 else 1
        score += bonus
        parts.append(f"+{bonus} public/policy ({', '.join(policy[:4])})")

    career = matched_labels(CAREER_TERMS, text)
    if job.get("contract_type") in ("Temporary", "Fixed-term") and "temporary" not in career:
        career.append("temporary")
    if job.get("hours") == "Part-time" and "part-time" not in career:
        career.append("part-time")
    if career:
        score += 1
        parts.append(f"+1 career-friendly ({', '.join(career[:4])})")

    experience = requires_experience(text)
    if experience:
        score -= 1
        parts.append(f"−1 asks for {experience} experience")

    score = max(1, min(5, score))
    return {
        "score": score,
        "score_reason": "; ".join(parts) if len(parts) > 1 else "Base 3; no strong signals either way",
        "policy_focus": bool(policy),
    }


def enrich(job: dict) -> dict:
    job["contract_type"] = contract_type(job)
    job["hours"] = working_hours(job)
    job["practice_area"] = practice_area(job)
    job["salary"] = format_salary(job.get("salary_min"), job.get("salary_max"), job.get("salary_estimated", False))
    job.update(score_job(job))
    return job


# --------------------------------------------------------------------------
# Deduplication and storage
# --------------------------------------------------------------------------

COMPANY_NOISE = re.compile(
    r"\b(?:ltd|limited|llp|plc|inc|uk|the|group|international|recruitment|solicitors|law firm|legal|lawyers|&|and|co)\b",
    re.IGNORECASE,
)
NON_ALNUM = re.compile(r"[^a-z0-9]+")


def fingerprint(job: dict) -> str:
    title = NON_ALNUM.sub(" ", (job.get("title") or "").lower()).strip()
    company = NON_ALNUM.sub(" ", COMPANY_NOISE.sub(" ", (job.get("company") or "").lower())).strip()
    return f"{WS_RE.sub(' ', title)}|{WS_RE.sub(' ', company)}"


def load_store() -> dict:
    if DATA_FILE.exists():
        try:
            with DATA_FILE.open(encoding="utf-8") as fh:
                store = json.load(fh)
            store.setdefault("jobs", [])
            store.setdefault("meta", {})
            return store
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read %s; starting fresh", DATA_FILE)
    return {"meta": {}, "jobs": []}


MUTABLE_FIELDS = ("title", "company", "location", "salary_min", "salary_max", "salary_estimated",
                  "posted", "expires", "category")


def merge(existing: list[dict], fetched: list[dict], run_at: str) -> tuple[list[dict], int]:
    by_id: dict[str, dict] = {}
    by_fp: dict[str, dict] = {}
    for job in existing:
        for uid in job.get("ids") or [job["id"]]:
            by_id[uid] = job
        by_fp[job.get("fingerprint") or fingerprint(job)] = job

    new_count = 0
    for item in fetched:
        fp = fingerprint(item)
        match = by_id.get(item["id"]) or by_fp.get(fp)
        if match is None:
            item.update({
                "ids": [item["id"]],
                "sources": [item["source"]],
                "links": {item["source"]: item["url"]},
                "fingerprint": fp,
                "first_seen": run_at,
                "last_seen": run_at,
            })
            existing.append(item)
            by_id[item["id"]] = item
            by_fp[fp] = item
            new_count += 1
            continue

        match["last_seen"] = run_at
        if item["id"] not in match.setdefault("ids", [match["id"]]):
            match["ids"].append(item["id"])
        if item["source"] not in match.setdefault("sources", [match["source"]]):
            match["sources"].append(item["source"])
        match.setdefault("links", {})[item["source"]] = item["url"]
        by_id[item["id"]] = match
        same_source = match["source"] == item["source"]
        if same_source:
            match["url"] = item["url"] or match.get("url")
            for field in MUTABLE_FIELDS:
                if item.get(field) not in (None, ""):
                    match[field] = item[field]
        else:
            # Fill gaps (e.g. salary or contract info) from the other source.
            for field in MUTABLE_FIELDS:
                if match.get(field) in (None, "") and item.get(field) not in (None, ""):
                    match[field] = item[field]
            if match.get("salary_estimated") and item.get("salary_min") and not item.get("salary_estimated"):
                match.update(salary_min=item["salary_min"], salary_max=item["salary_max"], salary_estimated=False)
        for field in ("contract_raw", "hours_raw"):
            if item.get(field):
                match[field] = item[field] if same_source or not match.get(field) else match[field]
        if len(item.get("description") or "") > len(match.get("description") or ""):
            match["description"] = item["description"]
    return existing, new_count


def prune(jobs: list[dict], today: date) -> list[dict]:
    cutoff = today - timedelta(days=MAX_AGE_DAYS)
    kept = []
    for job in jobs:
        first_seen = job["first_seen"][:10]
        reference = min(d for d in (job.get("posted"), first_seen) if d)
        if date.fromisoformat(reference) < cutoff:
            continue
        if job.get("expires") and date.fromisoformat(job["expires"]) < today:
            continue
        kept.append(job)
    return kept


def save_store(store: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=1, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    tmp.replace(DATA_FILE)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    run_at = now.isoformat().replace("+00:00", "Z")

    store = load_store()
    existing = store["jobs"]
    previous_run = store["meta"].get("last_run")
    known_ids = {uid for job in existing for uid in (job.get("ids") or [job["id"]])}

    fetched: list[dict] = []
    status: dict[str, str] = {}
    for name, fetch in (("Reed", lambda: fetch_reed(known_ids)), ("Adzuna", fetch_adzuna)):
        try:
            jobs = fetch()
            fetched.extend(jobs)
            status[name] = f"ok ({len(jobs)} results)"
        except Exception as exc:  # one source failing must not stop the other
            message = redact(str(exc))
            log.error("%s failed: %s", name, message)
            status[name] = f"error: {message}"

    if not any(s.startswith("ok") for s in status.values()):
        log.error("All sources failed; leaving data and page unchanged")
        return 1

    jobs, new_count = merge(existing, fetched, run_at)
    jobs = prune(jobs, now.date())
    for job in jobs:
        enrich(job)
    jobs.sort(key=lambda j: (j.get("posted") or j["first_seen"][:10], j["first_seen"]), reverse=True)

    store = {
        "meta": {
            "last_run": run_at,
            "previous_run": previous_run,
            "new_this_run": new_count,
            "total": len(jobs),
            "sources": status,
            "search": {"keywords": KEYWORDS, "location": LOCATION, "radius_miles": RADIUS_MILES,
                       "max_age_days": MAX_AGE_DAYS},
        },
        "jobs": jobs,
    }
    save_store(store)
    PAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PAGE_FILE.write_text(render_dashboard(store), encoding="utf-8")
    log.info("Saved %d jobs (%d new this run) -> %s, %s", len(jobs), new_count,
             DATA_FILE.relative_to(ROOT), PAGE_FILE.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
