#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import mirsad_engine as base
import mirsad_production as production

OUTPUT = Path("mirsad-engine/news.json")
MAX_FINAL_ITEMS = 15
MAX_AGE_DAYS = 35
MAX_NEW_CANDIDATES = 6


def canonical_url(url: str) -> str:
    try:
        p = urlsplit((url or "").strip())
        path = p.path.rstrip("/") or "/"
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, "", ""))
    except Exception:
        return (url or "").split("#", 1)[0].rstrip("/")


def item_time(item: dict) -> datetime:
    value = item.get("publishedAt") or item.get("dateISO")
    parsed = base.parse_date(value)
    return parsed or datetime(1970, 1, 1, tzinfo=timezone.utc)


def raw_time(raw: dict) -> datetime:
    return raw.get("published") or datetime(1970, 1, 1, tzinfo=timezone.utc)


def main() -> int:
    if not OUTPUT.exists():
        # First run must build a complete, guarded snapshot.
        return production.main()

    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    existing = list(payload.get("items") or [])
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)

    existing_by_url = {
        canonical_url(str(item.get("source") or "")): item
        for item in existing
        if item.get("source")
    }

    feed_reports = []
    raw_candidates = []
    duplicates = 0

    # Fetch all lightweight official feeds first. Do not translate anything yet.
    for cfg in base.FEEDS:
        rows, err = base.collect_feed(cfg)
        feed_reports.append({
            "company": cfg["company"],
            "kind": "feed-fast",
            "ok": bool(rows),
            "items": len(rows),
            "error": err,
        })
        for raw in rows:
            published = raw.get("published")
            if published and published < cutoff:
                continue
            url = canonical_url(str(raw.get("url") or ""))
            if not url:
                continue
            if url in existing_by_url:
                duplicates += 1
                continue
            raw["url"] = url
            raw_candidates.append(raw)

    # Only the newest unseen candidates can possibly enter the compact 15-item feed.
    raw_candidates.sort(key=raw_time, reverse=True)
    raw_candidates = raw_candidates[:MAX_NEW_CANDIDATES]

    additions = []
    for raw in raw_candidates:
        item = production.make_item(raw)
        item["collector"] = "github-fast"
        item["collectors"] = ["github-fast"]
        published = raw.get("published")
        if published:
            item["publishedAt"] = published.isoformat()
        additions.append(item)

    # Keep the last deep-scan health for HTML sources, replace only feed status.
    old_health = [
        row for row in (payload.get("sourceHealth") or [])
        if str(row.get("kind") or "").startswith("html")
    ]
    payload["sourceHealth"] = feed_reports + old_health

    combined = existing + additions
    combined.sort(key=item_time, reverse=True)
    combined = combined[:MAX_FINAL_ITEMS]

    newest = base.parse_date(payload.get("newestPublishedAt"))
    for item in additions:
        d = item_time(item)
        if d.year > 1970 and (newest is None or d > newest):
            newest = d

    payload["items"] = combined
    payload["engine"] = "mirsad-github-v2"
    payload["sourcePolicy"] = "official-only"
    payload["collectorMode"] = "github-fast+deep"
    payload["lastFastCheckAt"] = now.isoformat()
    payload["checkedAt"] = now.isoformat()
    payload["updatedAt"] = now.isoformat()
    payload["schemaVersion"] = max(4, int(payload.get("schemaVersion") or 0))
    if newest:
        payload["newestPublishedAt"] = newest.isoformat()

    production.validate(payload)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"MIRSAD_FAST_OK sources={sum(1 for r in feed_reports if r['ok'])} "
        f"candidates={len(raw_candidates)} added={len(additions)} "
        f"duplicates={duplicates} final={len(combined)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
