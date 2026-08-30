#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

OUTPUT = Path("mirsad-engine/trends.json")
MAX_ITEMS = 30
MAX_PER_PLATFORM = 12
GOOGLE_GEOS = ["SA", "AE", "EG", "US"]
AR_RE = re.compile(r"[\u0600-\u06ff]")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; MirsadTrendRadar/1.0; +https://github.com/NAMKx10/najiproject5)",
    "Accept-Language": "en-US,en;q=0.8,ar;q=0.7",
})

REGION_LABELS = {
    "SA": "السعودية",
    "AE": "الإمارات",
    "EG": "مصر",
    "US": "الولايات المتحدة",
}


def clean_text(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def has_arabic(text: str) -> bool:
    return bool(AR_RE.search(text or ""))


def norm_title(text: str) -> str:
    text = clean_text(text).lower()
    text = re.sub(r"[^\w\u0600-\u06ff]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def stable_id(platform: str, title: str) -> str:
    key = f"{platform}|{norm_title(title)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def parse_human_number(value: object) -> int:
    text = clean_text(value).upper().replace(",", "")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMB]?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    suffix = match.group(2)
    factor = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
    return int(number * factor)


def clamp_score(value: float) -> int:
    return max(0, min(100, int(round(value))))


def strength_label(score: int) -> str:
    if score >= 88:
        return "انفجاري"
    if score >= 74:
        return "قوي"
    if score >= 58:
        return "صاعد"
    return "ضعيف"


def google_score(traffic: int, rank: int) -> int:
    base = 45.0 + min(49.0, math.log10(max(1, traffic)) * 8.0)
    rank_bonus = max(0.0, 6.0 - (rank - 1) * 0.35)
    return clamp_score(base + rank_bonus)


def github_score(stars_today: int, rank: int) -> int:
    base = 44.0 + min(50.0, math.log10(max(1, stars_today) + 1) * 18.0)
    rank_bonus = max(0.0, 5.0 - (rank - 1) * 0.3)
    return clamp_score(base + rank_bonus)


def hn_score(points: int, comments: int, age_hours: float, rank: int) -> int:
    activity = 43.0
    activity += math.log10(max(1, points) + 1) * 14.0
    activity += math.log10(max(1, comments) + 1) * 7.0
    activity += max(0.0, 4.0 - (rank - 1) * 0.12)
    activity -= min(18.0, max(0.0, age_hours) * 0.45)
    return clamp_score(activity)


def load_previous() -> tuple[dict[str, dict], dict[str, str]]:
    previous: dict[str, dict] = {}
    translations: dict[str, str] = {}
    if not OUTPUT.exists():
        return previous, translations
    try:
        payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
        for item in payload.get("items") or []:
            ident = str(item.get("id") or "")
            if ident:
                previous[ident] = item
            original = clean_text(item.get("titleOriginal"))
            translated = clean_text(item.get("title"))
            if original and translated and has_arabic(translated):
                translations[original] = translated
    except Exception as exc:
        print(f"previous trend cache ignored: {type(exc).__name__}: {exc}", flush=True)
    return previous, translations


PREVIOUS, TRANSLATIONS = load_previous()


def translate_title(text: str) -> str:
    text = clean_text(text)
    if not text or has_arabic(text):
        return text
    cached = TRANSLATIONS.get(text)
    if cached:
        return cached
    try:
        response = SESSION.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:260], "langpair": "en|ar"},
            timeout=12,
        )
        response.raise_for_status()
        translated = clean_text((response.json().get("responseData") or {}).get("translatedText"))
        if translated and has_arabic(translated):
            TRANSLATIONS[text] = translated
            time.sleep(0.12)
            return translated
    except Exception as exc:
        print(f"trend translation fallback: {type(exc).__name__}: {exc}", flush=True)
    # Freshness is more important than delaying publication for translation.
    return text


def xml_child_text(node: ET.Element, suffix: str) -> str:
    for child in list(node):
        if child.tag.split("}")[-1] == suffix:
            return clean_text(child.text)
    return ""


def collect_google_trends() -> tuple[list[dict], list[dict]]:
    merged: dict[str, dict] = {}
    health: list[dict] = []

    for geo in GOOGLE_GEOS:
        url = f"https://trends.google.com/trending/rss?geo={geo}"
        try:
            response = SESSION.get(url, timeout=18)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            rows = root.findall(".//item")
            health.append({
                "source": "Google Trends",
                "region": geo,
                "ok": bool(rows),
                "items": len(rows),
                "error": None,
            })
            for rank, node in enumerate(rows[:20], start=1):
                title = clean_text(node.findtext("title"))
                if not title:
                    continue
                traffic_label = xml_child_text(node, "approx_traffic") or ""
                traffic = parse_human_number(traffic_label)
                key = norm_title(title)
                if not key:
                    continue
                score = google_score(traffic, rank)
                search_url = f"https://trends.google.com/trends/explore?q={quote(title)}&geo={geo}"
                current = merged.get(key)
                if current:
                    regions = current.setdefault("regions", [])
                    if geo not in regions:
                        regions.append(geo)
                    if traffic > int(current.get("signalValue") or 0):
                        current["signalValue"] = traffic
                        current["signalLabel"] = traffic_label or str(traffic)
                        current["strengthScore"] = score
                        current["url"] = search_url
                        current["region"] = geo
                    current["sourceRank"] = min(int(current.get("sourceRank") or rank), rank)
                    continue

                merged[key] = {
                    "id": stable_id("google-trends", title),
                    "titleOriginal": title,
                    "sourcePlatform": "google-trends",
                    "sourceLabel": "Google Trends",
                    "trendType": "search",
                    "url": search_url,
                    "region": geo,
                    "regions": [geo],
                    "sourceRank": rank,
                    "signalValue": traffic,
                    "signalLabel": traffic_label or (str(traffic) if traffic else "إشارة بحث صاعدة"),
                    "strengthScore": score,
                }
        except Exception as exc:
            health.append({
                "source": "Google Trends",
                "region": geo,
                "ok": False,
                "items": 0,
                "error": f"{type(exc).__name__}: {exc}",
            })

    return list(merged.values()), health


def collect_github_trending() -> tuple[list[dict], dict]:
    url = "https://github.com/trending?since=daily"
    items: list[dict] = []
    try:
        response = SESSION.get(url, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.select("article.Box-row")
        for rank, card in enumerate(cards[:25], start=1):
            link = card.select_one("h2 a")
            if not link:
                continue
            href = clean_text(link.get("href"))
            repo = clean_text(link.get_text(" ", strip=True)).replace(" / ", "/").replace("/ ", "/").replace(" /", "/")
            if not href or not repo:
                continue
            description_node = card.select_one("p")
            description = clean_text(description_node.get_text(" ", strip=True) if description_node else "")
            card_text = clean_text(card.get_text(" ", strip=True))
            stars_match = re.search(r"([0-9,.]+(?:[kKmM])?)\s+stars?\s+today", card_text, flags=re.I)
            stars_label = stars_match.group(1) if stars_match else "0"
            stars_today = parse_human_number(stars_label)
            score = github_score(stars_today, rank)
            items.append({
                "id": stable_id("github-trending", repo),
                "titleOriginal": repo,
                "sourcePlatform": "github-trending",
                "sourceLabel": "GitHub Trending",
                "trendType": "project",
                "url": "https://github.com" + href,
                "region": "global",
                "regions": ["global"],
                "sourceRank": rank,
                "signalValue": stars_today,
                "signalLabel": f"{stars_label} نجمة اليوم" if stars_match else "ضمن المشاريع الرائجة اليوم",
                "strengthScore": score,
                "descriptionOriginal": description,
            })
        return items, {
            "source": "GitHub Trending",
            "ok": bool(items),
            "items": len(items),
            "error": None,
        }
    except Exception as exc:
        return [], {
            "source": "GitHub Trending",
            "ok": False,
            "items": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


def collect_hacker_news() -> tuple[list[dict], dict]:
    items: list[dict] = []
    try:
        response = SESSION.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=18)
        response.raise_for_status()
        ids = response.json()[:35]
        now_ts = datetime.now(timezone.utc).timestamp()
        for rank, story_id in enumerate(ids, start=1):
            try:
                row_response = SESSION.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                    timeout=12,
                )
                row_response.raise_for_status()
                row = row_response.json() or {}
            except Exception:
                continue
            if row.get("type") != "story":
                continue
            title = clean_text(row.get("title"))
            if not title:
                continue
            points = int(row.get("score") or 0)
            comments = int(row.get("descendants") or 0)
            created = float(row.get("time") or now_ts)
            age_hours = max(0.0, (now_ts - created) / 3600.0)
            score = hn_score(points, comments, age_hours, rank)
            discussion_url = f"https://news.ycombinator.com/item?id={story_id}"
            items.append({
                "id": stable_id("hacker-news", title),
                "titleOriginal": title,
                "sourcePlatform": "hacker-news",
                "sourceLabel": "Hacker News",
                "trendType": "discussion",
                "url": discussion_url,
                "targetUrl": clean_text(row.get("url")) or discussion_url,
                "region": "global",
                "regions": ["global"],
                "sourceRank": rank,
                "signalValue": points,
                "signalLabel": f"{points} نقطة · {comments} تعليق",
                "points": points,
                "comments": comments,
                "ageHours": round(age_hours, 1),
                "strengthScore": score,
            })
        return items, {
            "source": "Hacker News",
            "ok": bool(items),
            "items": len(items),
            "error": None,
        }
    except Exception as exc:
        return [], {
            "source": "Hacker News",
            "ok": False,
            "items": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


def arabic_summary(item: dict) -> str:
    platform = item.get("sourcePlatform")
    signal = clean_text(item.get("signalLabel"))
    if platform == "google-trends":
        regions = [REGION_LABELS.get(x, x) for x in item.get("regions") or []]
        region_text = "، ".join(regions) if regions else "المنطقة المرصودة"
        return f"إشارة بحث صاعدة على Google Trends في {region_text}. حجم الاهتمام المرصود: {signal}."
    if platform == "github-trending":
        return f"مشروع صاعد ضمن GitHub Trending. الإشارة الحالية: {signal}."
    if platform == "hacker-news":
        return f"نقاش تقني نشط على Hacker News. مستوى التفاعل الحالي: {signal}."
    return f"إشارة ترند مرصودة من {clean_text(item.get('sourceLabel'))}."


def arabic_why(item: dict) -> str:
    platform = item.get("sourcePlatform")
    if platform == "google-trends":
        return "ارتفاع البحث مؤشر مباشر على اهتمام جماهيري لحظي، لذلك يُستخدم لرصد الموضوعات الصاعدة مبكرًا."
    if platform == "github-trending":
        return "النجوم اليومية وظهور المشروع في قائمة GitHub Trending يعطيان إشارة على تسارع اهتمام المطورين."
    if platform == "hacker-news":
        return "النقاط والتعليقات وسرعة التفاعل تعكس نشاط النقاش التقني حول الموضوع."
    return "إشارة نشاط حديثة من منصة ترند مستقلة."


def diversify(items: list[dict]) -> list[dict]:
    items = sorted(items, key=lambda x: (int(x.get("strengthScore") or 0), -int(x.get("sourceRank") or 999)), reverse=True)
    selected: list[dict] = []
    counts: dict[str, int] = {}
    seen: set[str] = set()

    for item in items:
        key = norm_title(item.get("titleOriginal") or "")
        if not key or key in seen:
            continue
        platform = str(item.get("sourcePlatform") or "unknown")
        if counts.get(platform, 0) >= MAX_PER_PLATFORM:
            continue
        selected.append(item)
        seen.add(key)
        counts[platform] = counts.get(platform, 0) + 1
        if len(selected) >= MAX_ITEMS:
            break

    if len(selected) < MAX_ITEMS:
        for item in items:
            key = norm_title(item.get("titleOriginal") or "")
            if not key or key in seen:
                continue
            selected.append(item)
            seen.add(key)
            if len(selected) >= MAX_ITEMS:
                break
    return selected


def finalize_item(item: dict, now: datetime, final_rank: int) -> dict:
    ident = str(item.get("id") or stable_id(str(item.get("sourcePlatform")), str(item.get("titleOriginal"))))
    old = PREVIOUS.get(ident) or {}
    title_original = clean_text(item.get("titleOriginal"))
    title = translate_title(title_original)
    score = clamp_score(float(item.get("strengthScore") or 0))
    first_seen = clean_text(old.get("firstSeenAt")) or now.isoformat()
    result = dict(item)
    result.update({
        "id": ident,
        "title": title,
        "titleOriginal": title_original,
        "translationReady": has_arabic(title),
        "strengthScore": score,
        "strengthLabel": strength_label(score),
        "summary": arabic_summary(item),
        "why": arabic_why(item),
        "firstSeenAt": first_seen,
        "collectedAt": now.isoformat(),
        "rank": final_rank,
        "isNew": ident not in PREVIOUS,
    })
    return result


def validate(payload: dict) -> None:
    items = payload.get("items") or []
    if len(items) < 12:
        raise RuntimeError("trend feed contains fewer than 12 items")
    platforms = {str(x.get("sourcePlatform") or "") for x in items}
    if len(platforms) < 2:
        raise RuntimeError("trend feed needs at least two independent platforms")
    for item in items:
        score = int(item.get("strengthScore") or -1)
        if score < 0 or score > 100:
            raise RuntimeError("invalid trend strength score")
        if not str(item.get("url") or "").startswith("http"):
            raise RuntimeError("trend item has no source URL")


def main() -> int:
    now = datetime.now(timezone.utc)
    google_items, google_health = collect_google_trends()
    github_items, github_health = collect_github_trending()
    hn_items, hn_health = collect_hacker_news()

    candidates = google_items + github_items + hn_items
    selected = diversify(candidates)
    final_items = [finalize_item(item, now, rank) for rank, item in enumerate(selected, start=1)]

    payload = {
        "schemaVersion": 1,
        "engine": "mirsad-trends-v1",
        "sourcePolicy": "independent-trend-signals",
        "updatedAt": now.isoformat(),
        "checkedAt": now.isoformat(),
        "sourceHealth": google_health + [github_health, hn_health],
        "items": final_items,
    }
    validate(payload)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    distribution: dict[str, int] = {}
    for item in final_items:
        platform = str(item.get("sourcePlatform") or "unknown")
        distribution[platform] = distribution.get(platform, 0) + 1
    print(
        f"MIRSAD_TRENDS_OK items={len(final_items)} distribution={distribution}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
