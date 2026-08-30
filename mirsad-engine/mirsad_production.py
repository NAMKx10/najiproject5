#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path

import requests

import mirsad_engine as base

OUTPUT = Path("mirsad-engine/news.json")
TMP_OUTPUT = Path("mirsad-engine/live-news-test.json")
AR_RE = re.compile(r"[\u0600-\u06ff]")

# Keep a richer backend inventory while the app itself still shows only highlights.
base.MAX_FINAL_ITEMS = 30
base.MAX_AGE_DAYS = 35
base.FRESHNESS_GUARD_DAYS = 10

# Avoid the old substring bug where the letters "ai" inside an unrelated word
# could make a general technology article look like an AI story.
STRICT_TERMS = [x for x in base.AI_TERMS if x != "ai"]

def strict_ai_relevant(title: str, summary: str) -> bool:
    hay = (title + " " + summary).lower()
    if re.search(r"(?<![a-z0-9])ai(?![a-z0-9])", hay):
        return True
    return any(term in hay for term in STRICT_TERMS)

base.is_ai_relevant = strict_ai_relevant


def has_arabic(text: str) -> bool:
    return bool(AR_RE.search(text or ""))


def load_translation_cache() -> dict[str, str]:
    cache: dict[str, str] = {}
    if not OUTPUT.exists():
        return cache
    try:
        old = json.loads(OUTPUT.read_text(encoding="utf-8"))
        for item in old.get("items", []):
            title_original = str(item.get("titleOriginal") or "").strip()
            title_ar = str(item.get("title") or "").strip()
            summary_original = str(item.get("summaryOriginal") or "").strip()
            summary_ar = str(item.get("summary") or "").strip()
            if title_original and title_ar and has_arabic(title_ar):
                cache[title_original] = title_ar
            if summary_original and summary_ar and has_arabic(summary_ar):
                cache[summary_original] = summary_ar
    except Exception as exc:
        print(f"translation cache ignored: {type(exc).__name__}: {exc}", flush=True)
    return cache

TRANSLATION_CACHE = load_translation_cache()
TRANSLATION_SESSION = requests.Session()
TRANSLATION_SESSION.headers.update({
    "User-Agent": "MirsadTrend/2.0",
    "Accept": "application/json",
})


def translate_ar(text: str, limit: int = 320) -> str:
    text = base.clean_text(text)
    if not text:
        return ""
    if has_arabic(text):
        return text
    if text in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[text]

    query = text[:limit].strip()
    try:
        response = TRANSLATION_SESSION.get(
            "https://api.mymemory.translated.net/get",
            params={"q": query, "langpair": "en|ar"},
            timeout=22,
        )
        response.raise_for_status()
        data = response.json()
        translated = base.clean_text((data.get("responseData") or {}).get("translatedText"))
        if translated and has_arabic(translated):
            TRANSLATION_CACHE[text] = translated
            time.sleep(0.28)
            return translated
    except Exception as exc:
        print(f"translation fallback: {type(exc).__name__}: {exc}", flush=True)
    return ""


def make_item(raw: dict) -> dict:
    title_original = base.clean_text(raw.get("titleOriginal"))
    summary_original = base.clean_text(raw.get("summaryOriginal"))
    company = str(raw.get("company") or "مصدر رسمي")
    source = str(raw.get("url") or "")
    d = raw.get("published")
    ident = hashlib.sha256((source + "|" + title_original).encode("utf-8")).hexdigest()[:20]
    typ, category, importance, why = base.classify(title_original, summary_original)

    title_ar = translate_ar(title_original, 260)
    if not has_arabic(title_ar):
        title_ar = f"مستجد جديد من {company}"

    # Translate a concise source summary to stay within the public translator quota.
    summary_seed = summary_original[:170].strip()
    summary_ar = translate_ar(summary_seed, 170) if summary_seed else ""
    if not has_arabic(summary_ar):
        summary_ar = f"أعلنت {company} عن مستجد جديد مرتبط بالذكاء الاصطناعي. يمكن فتح المصدر الرسمي للاطلاع على التفاصيل الكاملة."

    return {
        "id": ident,
        "company": company,
        "logoText": base.LOGO.get(company, company[:2]),
        "type": typ,
        "title": title_ar,
        "titleOriginal": title_original,
        "dateLabel": base.arabic_date(d),
        "dateISO": base.iso_day(d),
        "category": category,
        "importance": importance,
        "summary": summary_ar,
        "summaryOriginal": summary_original,
        "why": why,
        "source": source,
        "sourceLabel": base.SOURCE_LABEL.get(company, company),
        "badge": "موثق من المصدر الرسمي",
        "verified": True,
        "isNew": False,
        "searchAliases": f"{company} {title_original}",
    }

base.make_item = make_item


def validate(payload: dict) -> None:
    items = payload.get("items") or []
    if len(items) < 8:
        raise RuntimeError("production feed contains fewer than 8 items")
    for item in items:
        if item.get("verified") is not True:
            raise RuntimeError("unverified item reached production feed")
        if not str(item.get("source") or "").startswith("http"):
            raise RuntimeError("item without official source URL reached production feed")
        if not has_arabic(str(item.get("title") or "")):
            raise RuntimeError("non-Arabic title reached production feed")
        if not has_arabic(str(item.get("summary") or "")):
            raise RuntimeError("non-Arabic summary reached production feed")


def main() -> int:
    rc = base.main()
    if rc != 0:
        return rc
    payload = json.loads(TMP_OUTPUT.read_text(encoding="utf-8"))
    payload["engine"] = "mirsad-github-v2"
    payload["schemaVersion"] = max(4, int(payload.get("schemaVersion") or 0))
    validate(payload)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"MIRSAD_PRODUCTION_OK items={len(payload['items'])} newest={payload.get('newestPublishedAt')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
