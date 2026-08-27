#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

UA = "MirsadTrend/2.0 (+official-source-monitor)"
TIMEOUT = 25
MAX_ITEMS_PER_SOURCE = 10
MAX_FINAL_ITEMS = 45
MAX_AGE_DAYS = 60
FRESHNESS_GUARD_DAYS = 21

FEEDS = [
    {"company":"OpenAI","url":"https://openai.com/news/rss.xml","all_ai":True},
    {"company":"Microsoft","url":"https://blogs.microsoft.com/feed/","all_ai":False},
    {"company":"NVIDIA","url":"https://blogs.nvidia.com/feed/","all_ai":False},
    {"company":"Amazon","url":"https://aws.amazon.com/blogs/machine-learning/feed/","all_ai":True},
    {"company":"Apple","url":"https://www.apple.com/newsroom/rss-feed.rss","all_ai":False},
    {"company":"Hugging Face","url":"https://huggingface.co/blog/feed.xml","all_ai":True},
]

HTML_SOURCES = [
    {"company":"Google","url":"https://blog.google/","patterns":["blog.google/"],"all_ai":False},
    {"company":"Anthropic","url":"https://www.anthropic.com/news","patterns":["/news/"],"all_ai":True},
    {"company":"Meta AI","url":"https://ai.meta.com/blog/","patterns":["/blog/"],"all_ai":True},
    {"company":"Mistral AI","url":"https://mistral.ai/news/","patterns":["/news/"],"all_ai":True},
    {"company":"Cohere","url":"https://cohere.com/blog","patterns":["/blog/"],"all_ai":True},
    {"company":"xAI","url":"https://x.ai/news","patterns":["/news/"],"all_ai":True},
]

LOGO = {
    "OpenAI":"AI","Google":"G","Anthropic":"A","Meta AI":"M","Microsoft":"MS",
    "NVIDIA":"NV","xAI":"xAI","Amazon":"AWS","Apple":"","Hugging Face":"HF",
    "Mistral AI":"MI","Cohere":"CO",
}

SOURCE_LABEL = {
    "OpenAI":"OpenAI","Google":"Google","Anthropic":"Anthropic Newsroom","Meta AI":"AI at Meta",
    "Microsoft":"Microsoft","NVIDIA":"NVIDIA Blog","xAI":"xAI News","Amazon":"AWS Machine Learning Blog",
    "Apple":"Apple Newsroom","Hugging Face":"Hugging Face Blog","Mistral AI":"Mistral News","Cohere":"Cohere Blog",
}

AI_TERMS = [
    "ai","artificial intelligence","machine learning","deep learning","llm","model","models","gemini","claude",
    "chatgpt","gpt","copilot","agent","agents","agentic","neural","inference","training","transformer","multimodal",
    "generative","foundation model","computer vision","reasoning","robotics","bedrock","sagemaker","nvidia ai","openai",
]

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept": "text/html,application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.8"})


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(html.unescape(str(value)), "html.parser")
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def fetch(url: str) -> requests.Response:
    r = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r


def parse_date(value) -> datetime | None:
    if not value:
        return None
    try:
        d = dateparser.parse(str(value))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None


def iso_day(d: datetime | None) -> str:
    return d.date().isoformat() if d else ""


def arabic_date(d: datetime | None) -> str:
    if not d:
        return "تاريخ غير محدد"
    months = ["يناير","فبراير","مارس","أبريل","مايو","يونيو","يوليو","أغسطس","سبتمبر","أكتوبر","نوفمبر","ديسمبر"]
    return f"{d.day} {months[d.month-1]} {d.year}"


def is_ai_relevant(title: str, summary: str) -> bool:
    hay = (title + " " + summary).lower()
    return any(term in hay for term in AI_TERMS)


def extract_jsonld(soup: BeautifulSoup) -> list[dict]:
    found = []
    for tag in soup.find_all("script", attrs={"type":"application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text() or "{}")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                found.append(node)
                graph = node.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(node, list):
                stack.extend(node)
    return found


def article_meta(url: str, company: str) -> dict | None:
    try:
        r = fetch(url)
    except Exception:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    title = ""
    summary = ""
    date_value = None

    for key in ["og:title", "twitter:title"]:
        tag = soup.find("meta", attrs={"property":key}) or soup.find("meta", attrs={"name":key})
        if tag and tag.get("content"):
            title = clean_text(tag["content"])
            break
    if not title and soup.find("h1"):
        title = clean_text(soup.find("h1").get_text(" ", strip=True))
    if not title and soup.title:
        title = clean_text(soup.title.get_text(" ", strip=True))

    for key in ["og:description", "twitter:description", "description"]:
        tag = soup.find("meta", attrs={"property":key}) or soup.find("meta", attrs={"name":key})
        if tag and tag.get("content"):
            summary = clean_text(tag["content"])
            break

    for key in ["article:published_time", "datePublished", "date", "pubdate"]:
        tag = soup.find("meta", attrs={"property":key}) or soup.find("meta", attrs={"name":key})
        if tag and tag.get("content"):
            date_value = tag["content"]
            break

    if not date_value:
        t = soup.find("time")
        if t:
            date_value = t.get("datetime") or t.get_text(" ", strip=True)

    for node in extract_jsonld(soup):
        typ = str(node.get("@type", "")).lower()
        if any(x in typ for x in ["article","newsarticle","blogposting"]):
            if not title:
                title = clean_text(node.get("headline") or node.get("name"))
            if not summary:
                summary = clean_text(node.get("description"))
            if not date_value:
                date_value = node.get("datePublished") or node.get("dateCreated")
            break

    d = parse_date(date_value)
    if not title or len(title) < 4:
        return None
    return {"company":company,"titleOriginal":title,"summaryOriginal":summary,"url":r.url,"published":d}


def collect_feed(cfg: dict) -> tuple[list[dict], str | None]:
    try:
        r = fetch(cfg["url"])
        parsed = feedparser.parse(r.content)
        if not parsed.entries:
            return [], "0 entries"
        out = []
        for e in parsed.entries[:MAX_ITEMS_PER_SOURCE*2]:
            title = clean_text(e.get("title"))
            summary = clean_text(e.get("summary") or e.get("description"))
            link = str(e.get("link") or "").strip()
            d = parse_date(e.get("published") or e.get("updated"))
            if not title or not link:
                continue
            if not cfg.get("all_ai") and not is_ai_relevant(title, summary):
                continue
            out.append({"company":cfg["company"],"titleOriginal":title,"summaryOriginal":summary,"url":link,"published":d})
            if len(out) >= MAX_ITEMS_PER_SOURCE:
                break
        return out, None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"


def discover_links(cfg: dict) -> tuple[list[str], str | None]:
    try:
        r = fetch(cfg["url"])
        soup = BeautifulSoup(r.text, "html.parser")
        base_host = urlparse(r.url).netloc.lower()
        links = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = urljoin(r.url, a["href"])
            p = urlparse(href)
            if p.scheme not in {"http","https"}:
                continue
            if p.netloc.lower() != base_host and not (cfg["company"] == "Google" and p.netloc.lower().endswith("google")):
                continue
            path_lower = (p.path or "").lower()
            if cfg["company"] == "Google":
                # Skip navigation/category pages and keep article-like dated/product paths.
                if path_lower in {"/","/feed/","/about/"} or len(path_lower.strip("/").split("/")) < 2:
                    continue
            elif not any(pattern.lower() in path_lower for pattern in cfg["patterns"]):
                continue
            href = href.split("#",1)[0]
            if href in seen or href.rstrip("/") == r.url.rstrip("/"):
                continue
            seen.add(href)
            links.append(href)
            if len(links) >= MAX_ITEMS_PER_SOURCE*3:
                break
        return links, None
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"


def collect_html(cfg: dict) -> tuple[list[dict], str | None]:
    links, err = discover_links(cfg)
    if err:
        return [], err
    out = []
    for link in links:
        item = article_meta(link, cfg["company"])
        if not item:
            continue
        if not cfg.get("all_ai") and not is_ai_relevant(item["titleOriginal"], item["summaryOriginal"]):
            continue
        out.append(item)
        if len(out) >= MAX_ITEMS_PER_SOURCE:
            break
    return out, None if out else "no usable article pages"


def translate_ar(text: str) -> str:
    text = clean_text(text)
    if not text:
        return ""
    if re.search(r"[\u0600-\u06ff]", text):
        return text
    # Public Google translation endpoint; if it rejects automation we keep original text.
    try:
        r = SESSION.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client":"gtx","sl":"auto","tl":"ar","dt":"t","q":text[:3500]},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        translated = "".join(part[0] for part in (data[0] or []) if part and part[0])
        return clean_text(translated) or text
    except Exception:
        return text


def classify(title: str, summary: str) -> tuple[str,str,int,str]:
    s = (title + " " + summary).lower()
    if any(k in s for k in ["launch","introduc","release","model","gpt","gemini","claude","llama","mistral"]):
        return "إطلاق أو تحديث", "models", 92, "قد يغيّر قدرات الأدوات أو طريقة استخدامها، لذلك يستحق المتابعة من المصدر الرسمي."
    if any(k in s for k in ["research","paper","study","science","benchmark"]):
        return "بحث", "research", 86, "يوضح اتجاهًا بحثيًا أو نتيجة قد تؤثر في تطور تقنيات الذكاء الاصطناعي."
    if any(k in s for k in ["security","safety","policy","regulation","privacy"]):
        return "أمان وسياسات", "policy", 88, "قد يؤثر في الأمان أو الخصوصية أو القواعد المنظمة لاستخدام الذكاء الاصطناعي."
    if any(k in s for k in ["enterprise","business","partner","partnership","customer"]):
        return "أعمال وشراكات", "business", 82, "يعكس توسع استخدام الذكاء الاصطناعي في الشركات والخدمات العملية."
    return "مستجد تقني", "tools", 78, "مستجد رسمي قد يكون مفيدًا لفهم اتجاهات منتجات وتقنيات الذكاء الاصطناعي."


def make_item(raw: dict) -> dict:
    title_original = raw["titleOriginal"]
    summary_original = raw.get("summaryOriginal") or ""
    title_ar = translate_ar(title_original)
    summary_ar = translate_ar(summary_original) if summary_original else ""
    typ, category, importance, why = classify(title_original, summary_original)
    d = raw.get("published")
    source = raw["url"]
    ident = hashlib.sha256((source + "|" + title_original).encode("utf-8")).hexdigest()[:20]
    company = raw["company"]
    summary = summary_ar or f"إعلان رسمي جديد من {company}. افتح المصدر للاطلاع على التفاصيل الكاملة."
    return {
        "id": ident,
        "company": company,
        "logoText": LOGO.get(company, company[:2]),
        "type": typ,
        "title": title_ar,
        "titleOriginal": title_original,
        "dateLabel": arabic_date(d),
        "dateISO": iso_day(d),
        "category": category,
        "importance": importance,
        "summary": summary,
        "summaryOriginal": summary_original,
        "why": why,
        "source": source,
        "sourceLabel": SOURCE_LABEL.get(company, company),
        "badge": "موثق من المصدر الرسمي",
        "verified": True,
        "isNew": False,
        "searchAliases": f"{company} {title_original}",
    }


def main() -> int:
    started = time.time()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    raw_items = []
    source_report = []

    for cfg in FEEDS:
        items, err = collect_feed(cfg)
        raw_items.extend(items)
        source_report.append({"company":cfg["company"],"kind":"feed","ok":bool(items),"items":len(items),"error":err})
        print(json.dumps(source_report[-1], ensure_ascii=False), flush=True)

    for cfg in HTML_SOURCES:
        items, err = collect_html(cfg)
        raw_items.extend(items)
        source_report.append({"company":cfg["company"],"kind":"html","ok":bool(items),"items":len(items),"error":err})
        print(json.dumps(source_report[-1], ensure_ascii=False), flush=True)

    # Remove old, duplicate, and malformed entries.
    unique = {}
    for raw in raw_items:
        d = raw.get("published")
        if d and d < cutoff:
            continue
        key = (raw.get("url","").split("#",1)[0].rstrip("/"), raw.get("titleOriginal","").lower())
        if not key[0] or not key[1]:
            continue
        unique[key] = raw

    ordered = sorted(unique.values(), key=lambda x: x.get("published") or datetime(1970,1,1,tzinfo=timezone.utc), reverse=True)
    ordered = ordered[:MAX_FINAL_ITEMS]

    successful_companies = {r["company"] for r in source_report if r["ok"]}
    dated = [x["published"] for x in ordered if x.get("published")]
    newest = max(dated) if dated else None

    # Safety guard: do not publish a falsely fresh empty/stale snapshot.
    if len(successful_companies) < 4:
        print("GUARD_FAIL: fewer than 4 official companies produced usable items", file=sys.stderr)
        return 3
    if not newest or newest < now - timedelta(days=FRESHNESS_GUARD_DAYS):
        print(f"GUARD_FAIL: newest item is too old: {newest}", file=sys.stderr)
        return 4
    if len(ordered) < 8:
        print("GUARD_FAIL: fewer than 8 usable items", file=sys.stderr)
        return 5

    print(f"Translating {len(ordered)} items...", flush=True)
    items = []
    for i, raw in enumerate(ordered, start=1):
        items.append(make_item(raw))
        print(f"translated {i}/{len(ordered)} {raw['company']}: {raw['titleOriginal'][:90]}", flush=True)

    payload = {
        "schemaVersion": 3,
        "updatedAt": now.isoformat(),
        "checkedAt": now.isoformat(),
        "newestPublishedAt": newest.isoformat() if newest else None,
        "sourcePolicy": "official-only",
        "engine": "mirsad-github-v2-test",
        "sourceHealth": source_report,
        "items": items,
    }
    with open("mirsad-engine/live-news-test.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"MIRSAD_ENGINE_OK companies={len(successful_companies)} items={len(items)} newest={newest.isoformat()} elapsed={time.time()-started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
