#!/usr/bin/env python3
import json
import sys

import argostranslate.package
import argostranslate.translate

SAMPLES = [
    "Better answers, broader thinking: What students gain from ChatGPT and critical-thinking training",
    "Delivering Vera: NVIDIA’s First CPU Built for Agents Is Shipping Now",
    "Introducing OpenAI models on Amazon Bedrock for in-country inferencing in India",
]


def ensure_en_ar():
    argostranslate.package.update_package_index()
    packages = argostranslate.package.get_available_packages()
    matches = [p for p in packages if p.from_code == "en" and p.to_code == "ar"]
    if not matches:
        raise RuntimeError("No Argos en→ar model is available")
    argostranslate.package.install_from_path(matches[0].download())


def translate(text: str) -> str:
    return argostranslate.translate.translate(text, "en", "ar")


def main():
    ensure_en_ar()
    out=[]
    for text in SAMPLES:
        ar=translate(text)
        row={"original":text,"arabic":ar,"hasArabic":any('\u0600' <= c <= '\u06ff' for c in ar)}
        print(json.dumps(row,ensure_ascii=False),flush=True)
        out.append(row)
    if not all(x["hasArabic"] for x in out):
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
