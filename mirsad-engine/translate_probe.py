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

ENDPOINTS = [
    "https://translate.googleapis.com/translate_a/single",
    "https://translate.google.com/translate_a/single",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/126 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}

def has_arabic(text: str) -> bool:
    return any('\u0600' <= c <= '\u06ff' for c in text)

def translate(text: str) -> tuple[str,str]:
    errors=[]
    for endpoint in ENDPOINTS:
        try:
            r=requests.get(endpoint, params={"client":"gtx","sl":"en","tl":"ar","dt":"t","q":text}, headers=HEADERS, timeout=20)
            if r.status_code != 200:
                errors.append(f"{endpoint} status={r.status_code} body={r.text[:120]!r}")
                continue
            data=r.json()
            ar="".join(part[0] for part in (data[0] or []) if part and part[0]).strip()
            if ar and has_arabic(ar):
                return ar, endpoint
            errors.append(f"{endpoint} no-arabic {ar[:120]!r}")
        except Exception as e:
            errors.append(f"{endpoint} {type(e).__name__}: {e}")
    raise RuntimeError(" | ".join(errors))

def main():
    out=[]
    for text in SAMPLES:
        try:
            ar, endpoint=translate(text)
            row={"original":text,"arabic":ar,"endpoint":endpoint,"hasArabic":has_arabic(ar)}
        except Exception as e:
            row={"original":text,"error":str(e),"hasArabic":False}
        print(json.dumps(row,ensure_ascii=False),flush=True)
        out.append(row)
        time.sleep(.4)
    return 0 if all(x.get("hasArabic") for x in out) else 2

if __name__ == "__main__":
    sys.exit(main())
