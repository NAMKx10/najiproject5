#!/usr/bin/env python3
from __future__ import annotations

import math

import trends_engine as base

# Keep the collector implementation stable, but apply a live-ranking layer that
# prevents one platform from monopolising the radar.
PLATFORM_ORDER = ["google-trends", "github-trending", "hacker-news"]
GOOGLE_REGION_ORDER = ["SA", "AE", "EG", "US"]
PER_PLATFORM_TARGET = 10
HN_MAX_AGE_HOURS = 48.0


def recalibrated_github_score(stars_today: int, rank: int) -> int:
    # GitHub daily stars are strong signals, but the old curve saturated near 100
    # too quickly and pushed almost every project above public-search trends.
    score = 40.0 + min(48.0, math.log10(max(1, stars_today) + 1) * 14.0)
    score += max(0.0, 5.0 - (rank - 1) * 0.28)
    return base.clamp_score(score)


def recalibrated_hn_score(points: int, comments: int, age_hours: float, rank: int) -> int:
    # Reward interaction and recency without letting old high-score threads look
    # more urgent than fresh search signals.
    score = 38.0
    score += math.log10(max(1, points) + 1) * 12.0
    score += math.log10(max(1, comments) + 1) * 6.0
    score += max(0.0, 4.0 - (rank - 1) * 0.10)
    score -= min(20.0, max(0.0, age_hours) * 0.48)
    return base.clamp_score(score)


base.github_score = recalibrated_github_score
base.hn_score = recalibrated_hn_score


def sort_key(item: dict) -> tuple[int, int]:
    return (
        int(item.get("strengthScore") or 0),
        -int(item.get("sourceRank") or 999),
    )


def unique_sorted(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for item in sorted(items, key=sort_key, reverse=True):
        key = base.norm_title(item.get("titleOriginal") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def balanced_google(items: list[dict]) -> list[dict]:
    # Round-robin across Arabic-region signals first, then the US bucket.
    buckets: dict[str, list[dict]] = {geo: [] for geo in GOOGLE_REGION_ORDER}
    extra: list[dict] = []
    for item in items:
        regions = list(item.get("regions") or [])
        placed = False
        for geo in GOOGLE_REGION_ORDER:
            if geo in regions:
                buckets[geo].append(item)
                placed = True
                break
        if not placed:
            extra.append(item)

    for geo in GOOGLE_REGION_ORDER:
        buckets[geo] = unique_sorted(buckets[geo])
    extra = unique_sorted(extra)

    selected: list[dict] = []
    seen_ids: set[str] = set()
    while len(selected) < PER_PLATFORM_TARGET:
        progressed = False
        for geo in GOOGLE_REGION_ORDER:
            while buckets[geo] and str(buckets[geo][0].get("id")) in seen_ids:
                buckets[geo].pop(0)
            if buckets[geo]:
                item = buckets[geo].pop(0)
                selected.append(item)
                seen_ids.add(str(item.get("id")))
                progressed = True
                if len(selected) >= PER_PLATFORM_TARGET:
                    break
        if not progressed:
            break

    if len(selected) < PER_PLATFORM_TARGET:
        remaining = unique_sorted(
            [item for geo in GOOGLE_REGION_ORDER for item in buckets[geo]] + extra
        )
        for item in remaining:
            if str(item.get("id")) in seen_ids:
                continue
            selected.append(item)
            seen_ids.add(str(item.get("id")))
            if len(selected) >= PER_PLATFORM_TARGET:
                break
    return selected


def balanced_diversify(items: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {platform: [] for platform in PLATFORM_ORDER}
    leftovers: list[dict] = []

    for item in items:
        platform = str(item.get("sourcePlatform") or "")
        if platform == "hacker-news" and float(item.get("ageHours") or 0.0) > HN_MAX_AGE_HOURS:
            continue
        if platform in groups:
            groups[platform].append(item)
        else:
            leftovers.append(item)

    groups["google-trends"] = balanced_google(groups["google-trends"])
    groups["github-trending"] = unique_sorted(groups["github-trending"])[:PER_PLATFORM_TARGET]
    groups["hacker-news"] = unique_sorted(groups["hacker-news"])[:PER_PLATFORM_TARGET]

    selected: list[dict] = []
    seen: set[str] = set()

    # Interleave the three independent signal families. This makes the first
    # screen representative instead of showing three projects from one site.
    for index in range(PER_PLATFORM_TARGET):
        for platform in PLATFORM_ORDER:
            group = groups[platform]
            if index >= len(group):
                continue
            item = group[index]
            key = base.norm_title(item.get("titleOriginal") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            selected.append(item)
            if len(selected) >= base.MAX_ITEMS:
                return selected

    if len(selected) < base.MAX_ITEMS:
        fallback = unique_sorted(items + leftovers)
        for item in fallback:
            if item.get("sourcePlatform") == "hacker-news" and float(item.get("ageHours") or 0.0) > HN_MAX_AGE_HOURS:
                continue
            key = base.norm_title(item.get("titleOriginal") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            selected.append(item)
            if len(selected) >= base.MAX_ITEMS:
                break
    return selected


base.diversify = balanced_diversify


def live_finalize_item(item: dict, now, final_rank: int) -> dict:
    ident = str(
        item.get("id")
        or base.stable_id(str(item.get("sourcePlatform")), str(item.get("titleOriginal")))
    )
    old = base.PREVIOUS.get(ident) or {}
    title_original = base.clean_text(item.get("titleOriginal"))
    platform = str(item.get("sourcePlatform") or "")

    # Repository slugs are proper names/identifiers. Translating them can produce
    # misleading labels such as literal translations of package names.
    if platform == "github-trending":
        title = title_original
        translation_ready = False
    else:
        title = base.translate_title(title_original)
        translation_ready = base.has_arabic(title)

    score = base.clamp_score(float(item.get("strengthScore") or 0))
    first_seen = base.clean_text(old.get("firstSeenAt")) or now.isoformat()
    result = dict(item)
    result.update({
        "id": ident,
        "title": title,
        "titleOriginal": title_original,
        "translationReady": translation_ready,
        "strengthScore": score,
        "strengthLabel": base.strength_label(score),
        "summary": base.arabic_summary(item),
        "why": base.arabic_why(item),
        "firstSeenAt": first_seen,
        "collectedAt": now.isoformat(),
        "rank": final_rank,
        "isNew": ident not in base.PREVIOUS,
        "rankingMode": "balanced-live-radar",
    })
    return result


base.finalize_item = live_finalize_item


if __name__ == "__main__":
    raise SystemExit(base.main())
