#!/usr/bin/env python3
import json
import sys
import time
import requests

SAMPLES = [
    "Better answers, broader thinking: What students gain from ChatGPT and critical-thinking training",
    "Delivering Vera: NVIDIA’s First CPU Built for Agents Is Shipping Now",
    "Introducing OpenAI models on Amazon Bedrock for in-country inferencing in India",
]

HEADERS={"User-Agent":"MirsadTrend/2.0","Accept":"application/json"}

def has_arabic(text: str) -> bool:
    return any('\u0600' <= c <= '\u06ff' for c in text)

def translate_mymemory(text: str) -> str:
    r=requests.get(
        "https://api.mymemory.translated.net/get",
        params={"q":text,"langpair":"en|ar"},
        headers=HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    data=r.json()
    ar=str((data.get("responseData") or {}).get("translatedText") or "").strip()
    if not ar or not has_arabic(ar):
        raise RuntimeError(f"no Arabic translation: {ar[:120]!r}; responseStatus={data.get('responseStatus')}")
    return ar

def main():
    rows=[]
    for text in SAMPLES:
        try:
            ar=translate_mymemory(text)
            row={"original":text,"arabic":ar,"provider":"mymemory","hasArabic":True}
        except Exception as e:
            row={"original":text,"error":f"{type(e).__name__}: {e}","hasArabic":False}
        print(json.dumps(row,ensure_ascii=False),flush=True)
        rows.append(row)
        time.sleep(.5)
    return 0 if all(x.get("hasArabic") for x in rows) else 2

if __name__ == "__main__":
    sys.exit(main())
