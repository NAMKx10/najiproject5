#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone

import feedparser
import requests
from bs4 import BeautifulSoup

UA = "MirsadTrend/2.0 (+official-source-monitor)"
TIMEOUT = 25

SOURCES = [
    {"company":"OpenAI","kind":"feed","url":"https://openai.com/news/rss.xml"},
    {"company":"Google","kind":"feed","url":"https://blog.google/feed/"},
    {"company":"Microsoft","kind":"feed","url":"https://blogs.microsoft.com/feed/"},
    {"company":"NVIDIA","kind":"feed","url":"https://blogs.nvidia.com/feed/"},
    {"company":"Amazon","kind":"feed","url":"https://aws.amazon.com/blogs/machine-learning/feed/"},
    {"company":"Apple","kind":"feed","url":"https://www.apple.com/newsroom/rss-feed.rss"},
    {"company":"Hugging Face","kind":"feed","url":"https://huggingface.co/blog/feed.xml"},
    {"company":"Anthropic","kind":"html","url":"https://www.anthropic.com/news"},
    {"company":"Meta AI","kind":"html","url":"https://ai.meta.com/blog/"},
    {"company":"Mistral AI","kind":"html","url":"https://mistral.ai/news"},
    {"company":"Cohere","kind":"html","url":"https://cohere.com/blog"},
    {"company":"xAI","kind":"html","url":"https://x.ai/news"},
]


def fetch(url: str) -> requests.Response:
    return requests.get(url, headers={"User-Agent": UA, "Accept": "text/html,application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.8"}, timeout=TIMEOUT, allow_redirects=True)


def probe_feed(source: dict) -> dict:
    r = fetch(source["url"])
    r.raise_for_status()
    parsed = feedparser.parse(r.content)
    entries = parsed.entries or []
    if not entries:
        raise RuntimeError("feed parsed but contains 0 entries")
    first = entries[0]
    return {
        "ok": True,
        "status": r.status_code,
        "finalUrl": r.url,
        "bytes": len(r.content),
        "entries": len(entries),
        "sampleTitle": str(first.get("title", ""))[:180],
        "sampleLink": str(first.get("link", ""))[:300],
    }


def probe_html(source: dict) -> dict:
    r = fetch(source["url"])
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    links = [a.get("href") for a in soup.find_all("a", href=True)]
    meaningful = [x for x in links if x and not x.startswith("#") and not x.lower().startswith("javascript:")]
    if len(r.text) < 500 or not meaningful:
        raise RuntimeError("HTML response is too small or has no usable links")
    return {
        "ok": True,
        "status": r.status_code,
        "finalUrl": r.url,
        "bytes": len(r.content),
        "links": len(meaningful),
        "pageTitle": title[:180],
    }


def main() -> int:
    started = time.time()
    results = []
    for source in SOURCES:
        row = {"company": source["company"], "kind": source["kind"], "url": source["url"]}
        try:
            details = probe_feed(source) if source["kind"] == "feed" else probe_html(source)
            row.update(details)
        except Exception as exc:
            row.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    success = sum(1 for r in results if r.get("ok"))
    report = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "elapsedSeconds": round(time.time() - started, 2),
        "success": success,
        "failed": len(results) - success,
        "results": results,
    }
    with open("mirsad-engine/probe-result.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"MIRSAD_PROBE_SUMMARY success={success} failed={len(results)-success}")
    # The probe is useful even if some companies reject automation.
    # Fail only when fewer than four independent official sources are reachable.
    return 0 if success >= 4 else 2


if __name__ == "__main__":
    sys.exit(main())
