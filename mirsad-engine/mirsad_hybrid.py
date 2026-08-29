#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

import mirsad_engine as base
import mirsad_production as production

OUTPUT = Path("mirsad-engine/news.json")
MAX_FINAL_ITEMS = 15
MAX_GAS_CANDIDATES = 40
MAX_ACCEPTED_GAS_ITEMS = 12
GAS_ENDPOINT = "https://script.google.com/macros/s/AKfycbxki3NpmfgEYFGEFPtbGmgkaIONegqw6ng5l2SNMQVs_5qWXlD-JbNz6FUkJoL5IOwBAw/exec"

# Apps Script is only a discovery accelerator. The final URL must still belong
# to an official company domain before an item can enter the production feed.
OFFICIAL_HOSTS = [
    ("openai.com", "OpenAI"),
    ("blog.google", "Google"),
    ("developers.googleblog.com", "Google"),
    ("googleblog.com", "Google"),
    ("anthropic.com", "Anthropic"),
    ("ai.meta.com", "Meta AI"),
    ("about.fb.com", "Meta AI"),
    ("microsoft.com", "Microsoft"),
    ("nvidia.com", "NVIDIA"),
    ("aws.amazon.com", "Amazon"),
    ("amazon.com", "Amazon"),
    ("apple.com", "Apple"),
    ("huggingface.co", "Hugging Face"),
    ("mistral.ai", "Mistral AI"),
    ("cohere.com", "Cohere"),
    ("x.ai", "xAI"),
]

TITLE_KEYS = ("titleOriginal", "title", "headline", "name")
SUMMARY_KEYS = ("summaryOriginal", "summary", "description", "snippet", "excerpt")
URL_KEYS = ("source", "url", "link", "sourceUrl", "source_url", "originalUrl", "articleUrl", "href")
DATE_KEYS = ("publishedAt", "published_at", "published", "datePublished", "date", "dateISO", "createdAt", "updatedAt", "time")
CONTAINER_KEYS = ("items", "news", "articles", "data", "results", "entries", "feed")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "MirsadTrend/3.0 (+hybrid-collector)",
    "Accept": "application/json,text/plain,*/*",
})


def first_text(row: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return base.clean_text(value)
    return ""


def first_url(row: dict) -> str:
    for key in URL_KEYS:
        value = row.get(key)
        if isinstance(value, str) and re.match(r"^https?://", value.strip(), re.I):
            return value.strip()
    return ""


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
        kept = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            low = key.lower()
            if low.startswith("utm_") or low in {"fbclid", "gclid", "mc_cid", "mc_eid"}:
                continue
            kept.append((key, value))
        path = re.sub(r"/{2,}", "/", parts.path or "/")
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(kept), ""))
    except Exception:
        return url.split("#", 1)[0].rstrip("/")


def official_company(url: str) -> str | None:
    try:
        host = (urlsplit(url).hostname or "").lower().lstrip("www.")
    except Exception:
        return None
    for domain, company in OFFICIAL_HOSTS:
        if host == domain or host.endswith("." + domain):
            return company
    return None


def looks_like_item(row: dict) -> bool:
    return bool(first_url(row) and first_text(row, TITLE_KEYS))


def iter_rows(node, depth: int = 0):
    if depth > 6:
        return
    if isinstance(node, list):
        for child in node:
            yield from iter_rows(child, depth + 1)
        return
    if not isinstance(node, dict):
        return
    if looks_like_item(node):
        yield node
        return
    used = False
    for key in CONTAINER_KEYS:
        child = node.get(key)
        if isinstance(child, (list, dict)):
            used = True
            yield from iter_rows(child, depth + 1)
    if not used:
        for child in node.values():
            if isinstance(child, (list, dict)):
                yield from iter_rows(child, depth + 1)


def parse_gas_candidate(row: dict) -> dict | None:
    url = first_url(row)
    company = official_company(url)
    if not company:
        return None
    title = first_text(row, TITLE_KEYS)
    summary = first_text(row, SUMMARY_KEYS)
    if not title:
        return None
    if not base.is_ai_relevant(title, summary):
        return None
    date_value = ""
    for key in DATE_KEYS:
        value = row.get(key)
        if value not in (None, ""):
            date_value = value
            break
    published = base.parse_date(date_value)
    return {
        "company": company,
        "titleOriginal": title,
        "summaryOriginal": summary,
        "url": canonical_url(url),
        "published": published,
        "collector": "google-apps-script",
        "collectors": ["google-apps-script"],
    }


def collect_from_apps_script() -> tuple[list[dict], dict]:
    report = {
        "company": "Google Apps Script",
        "kind": "collector",
        "ok": False,
        "items": 0,
        "accepted": 0,
        "duplicates": 0,
        "rejectedNonOfficial": 0,
        "error": None,
    }
    try:
        response = SESSION.get(GAS_ENDPOINT, timeout=28, allow_redirects=True)
        response.raise_for_status()
        try:
            payload = response.json()
        except Exception:
            text = response.text.strip()
            payload = json.loads(text)

        rows = list(iter_rows(payload))[:MAX_GAS_CANDIDATES]
        report["items"] = len(rows)
        accepted: list[dict] = []
        for row in rows:
            url = first_url(row)
            if url and not official_company(url):
                report["rejectedNonOfficial"] += 1
                continue
            candidate = parse_gas_candidate(row)
            if candidate:
                accepted.append(candidate)
            if len(accepted) >= MAX_ACCEPTED_GAS_ITEMS:
                break

        report["accepted"] = len(accepted)
        report["ok"] = True
        return accepted, report
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        return [], report


def enrich_candidate(candidate: dict) -> dict | None:
    # Re-read the official article when possible. This verifies the final URL and
    # usually gives a better date/summary than the discovery payload.
    company = candidate["company"]
    url = candidate["url"]
    meta = base.article_meta(url, company)
    if meta:
        meta["collector"] = "google-apps-script"
        meta["collectors"] = ["google-apps-script"]
        if not meta.get("published"):
            meta["published"] = candidate.get("published")
        if not meta.get("summaryOriginal"):
            meta["summaryOriginal"] = candidate.get("summaryOriginal") or ""
        if base.is_ai_relevant(meta.get("titleOriginal", ""), meta.get("summaryOriginal", "")):
            return meta

    # If the official page blocks the runner, we may still use the Apps Script
    # candidate, but only with an official URL, an AI-relevant title and a date.
    published = candidate.get("published")
    if not published:
        return None
    if published < datetime.now(timezone.utc) - timedelta(days=production.base.MAX_AGE_DAYS):
        return None
    return candidate


def item_sort_time(item: dict) -> datetime:
    value = item.get("publishedAt") or item.get("dateISO")
    parsed = base.parse_date(value)
    return parsed or datetime(1970, 1, 1, tzinfo=timezone.utc)


def main() -> int:
    # First build the known-good direct feed. If the direct engine fails, keep its
    # existing guard behavior and do not let the auxiliary collector mask the failure.
    rc = production.main()
    if rc != 0:
        return rc

    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    direct_items = list(payload.get("items") or [])
    for item in direct_items:
        item.setdefault("collectors", ["github-direct"])
        item.setdefault("collector", "github-direct")

    gas_candidates, gas_report = collect_from_apps_script()
    by_url = {canonical_url(str(item.get("source") or "")): item for item in direct_items if item.get("source")}
    additions: list[dict] = []

    for candidate in gas_candidates:
        key = canonical_url(candidate["url"])
        existing = by_url.get(key)
        if existing is not None:
            collectors = list(existing.get("collectors") or [existing.get("collector") or "github-direct"])
            if "google-apps-script" not in collectors:
                collectors.append("google-apps-script")
            existing["collectors"] = collectors
            existing["collector"] = "both" if len(collectors) > 1 else collectors[0]
            gas_report["duplicates"] += 1
            continue

        raw = enrich_candidate(candidate)
        if not raw:
            continue
        item = production.make_item(raw)
        item["collectors"] = ["google-apps-script"]
        item["collector"] = "google-apps-script"
        if raw.get("published"):
            item["publishedAt"] = raw["published"].isoformat()
        additions.append(item)
        by_url[key] = item

    combined = direct_items + additions
    combined.sort(key=item_sort_time, reverse=True)
    combined = combined[:MAX_FINAL_ITEMS]

    now = datetime.now(timezone.utc)
    payload["updatedAt"] = now.isoformat()
    payload["checkedAt"] = now.isoformat()
    payload["collectorMode"] = "hybrid-direct+google-apps-script"
    payload["sourcePolicy"] = "official-only"
    payload["engine"] = "mirsad-github-v2"
    payload["sourceHealth"] = [r for r in (payload.get("sourceHealth") or []) if r.get("kind") != "collector"] + [gas_report]
    payload["items"] = combined

    newest = base.parse_date(payload.get("newestPublishedAt"))
    for item in additions:
        d = base.parse_date(item.get("publishedAt") or item.get("dateISO"))
        if d and (newest is None or d > newest):
            newest = d
    if newest:
        payload["newestPublishedAt"] = newest.isoformat()

    production.validate(payload)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "MIRSAD_HYBRID_OK "
        f"direct={len(direct_items)} gas_rows={gas_report['items']} "
        f"gas_accepted={gas_report['accepted']} gas_duplicates={gas_report['duplicates']} "
        f"gas_added={len(additions)} final={len(combined)}",
        flush=True,
    )
    if gas_report.get("error"):
        print(f"MIRSAD_GAS_DEGRADED {gas_report['error']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
