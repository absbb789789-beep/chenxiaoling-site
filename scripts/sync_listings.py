#!/usr/bin/env python3
"""Fetch publicly accessible Taiwan House storefront listings. Never bypass access controls."""
import json, re, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

STORES = {
    "TBD9": "https://store.twhg.com.tw/house/TBD9",
    "TB73": "https://store.twhg.com.tw/house/TB73",
    "TB75": "https://store.twhg.com.tw/house/TB75",
}
OUT = Path("data/listings.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ChenXiaoLingPropertySite/1.0; +https://absbb789789-beep.github.io/chenxiaoling-site/)", "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"}
session = requests.Session()
session.headers.update(HEADERS)

def clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def money(text):
    m = re.search(r"([0-9,]+(?:\.[0-9]+)?)\s*萬", text)
    return m.group(1).replace(",", "") if m else ""

def extract_store(code, base):
    r = session.get(base, timeout=35)
    r.raise_for_status()
    if len(r.text) < 500:
        raise RuntimeError(f"{code}: response too short")
    soup = BeautifulSoup(r.text, "html.parser")
    found = {}
    # Structured data is the least layout-dependent source.
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.string or node.get_text())
        except Exception:
            continue
        stack = payload if isinstance(payload, list) else [payload]
        while stack:
            obj = stack.pop()
            if isinstance(obj, dict):
                if isinstance(obj.get("@graph"), list): stack.extend(obj["@graph"])
                typ = obj.get("@type", [])
                if isinstance(typ, str): typ = [typ]
                if any(t in ("Product", "Offer", "RealEstateListing", "Residence") for t in typ):
                    name = clean(obj.get("name") or obj.get("headline"))
                    url = obj.get("url") or obj.get("@id")
                    if name and url:
                        found[urljoin(base, url)] = {"title": name, "url": urljoin(base, url), "image": obj.get("image", ""), "description": clean(obj.get("description", ""))}
    # Listing cards commonly link to a property detail route; collect only links with meaningful text.
    for a in soup.select("a[href]"):
        href = urljoin(base, a.get("href", ""))
        if urlparse(href).netloc != urlparse(base).netloc: continue
        text = clean(a.get_text(" ", strip=True))
        if len(text) < 5: continue
        parent = a
        for _ in range(4):
            if parent.parent: parent = parent.parent
        block = clean(parent.get_text(" ", strip=True))
        if not re.search(r"\d[\d,.]*\s*萬|總坪|建坪|地坪|房\s*廳", block): continue
        if not any(k in href.lower() for k in ("businessinfo", "property", "house", "detail", "item", "sale", "buy")) and href.rstrip("/") == base.rstrip("/"): continue
        image = ""
        img = parent.select_one("img")
        if img:
            image = img.get("data-src") or img.get("data-original") or img.get("src") or ""
            if image: image = urljoin(base, image)
        title = text if len(text) > 6 else clean(a.get("title"))
        found.setdefault(href, {"title": title, "url": href, "image": image, "description": block})
    items = []
    for url, obj in found.items():
        desc = clean(obj.get("description", ""))
        title = clean(obj.get("title"))
        if not title: continue
        image = obj.get("image", "")
        if isinstance(image, list): image = image[0] if image else ""
        items.append({
            "store": code, "title": title[:100], "url": url,
            "image": image if isinstance(image, str) and image.startswith(("http://","https://")) else "",
            "price": money(desc),
            "area": (re.search(r"(?:總坪|建坪|地坪)\s*[:：]?\s*([0-9.]+)", desc).group(1) if re.search(r"(?:總坪|建坪|地坪)\s*[:：]?\s*([0-9.]+)", desc) else ""),
            "layout": (re.search(r"([0-9]+\s*房[^|｜]{0,18})", desc).group(1) if re.search(r"([0-9]+\s*房[^|｜]{0,18})", desc) else ""),
            "district": (re.search(r"台中市.{0,8}?(?:區|鄉|鎮)", desc).group(0) if re.search(r"台中市.{0,8}?(?:區|鄉|鎮)", desc) else ""),
        })
    return items

def main():
    all_items, errors = [], []
    for code, url in STORES.items():
        try:
            rows = extract_store(code, url)
            print(f"{code}: {len(rows)} items")
            all_items.extend(rows)
        except Exception as e:
            errors.append(f"{code}: {type(e).__name__}: {e}")
            print(errors[-1], file=sys.stderr)
    # Avoid overwriting a good feed with an empty/partial response.
    if not all_items:
        raise SystemExit("No listing records retrieved; previous JSON preserved. " + "; ".join(errors))
    # Deduplicate on canonical URL.
    unique = {x["url"]: x for x in all_items}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "stores": list(STORES), "count": len(unique), "items": list(unique.values()), "warnings": errors}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(unique)} listing cards.")

if __name__ == "__main__":
    main()
