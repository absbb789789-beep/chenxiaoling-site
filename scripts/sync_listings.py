#!/usr/bin/env python3
"""Weekly public Taiwan House listing sync. Uses rendered Chromium pages only."""
from __future__ import annotations
import json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

TZ = timezone(timedelta(hours=8))
TODAY = datetime.now(TZ).date().isoformat()
NOW = datetime.now(TZ).isoformat(timespec="seconds")
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "listings.json"
BACKUPS = ROOT / "backups"
REPORTS = ROOT / "reports"
LOGS = ROOT / "logs"
STORES = {
    "TBD9": "https://store.twhg.com.tw/TBD9",
    "TB73": "https://store.twhg.com.tw/TB73",
    "TB75": "https://store.twhg.com.tw/TB75",
}
DETAIL_BASE = "https://www.twhg.com.tw/buy/"
ID_RE = re.compile(r"/buy/([A-Za-z0-9_-]+)", re.I)
CITIES = r"(?:台北市|新北市|桃園市|台中市|臺中市|台南市|臺南市|高雄市|基隆市|新竹市|嘉義市|新竹縣|苗栗縣|彰化縣|南投縣|雲林縣|嘉義縣|屏東縣|宜蘭縣|花蓮縣|台東縣|臺東縣|澎湖縣|金門縣|連江縣)"

def log(event, **kwargs):
    LOGS.mkdir(parents=True, exist_ok=True)
    row = {"timestamp": NOW, "event": event, **kwargs}
    with (LOGS / "execution_history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False))

def read_previous():
    if not DATA.exists():
        return {"items": [], "stores": list(STORES), "updated_at": None}
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Existing listings.json is unreadable; refusing to overwrite: {exc}")

def text_or_empty(value):
    return re.sub(r"\s+", " ", value or "").strip()

def parse_price(text):
    for pattern in [r"(?:總價|售價|開價|價格)\s*[:：]?\s*([\d,]+(?:\.\d+)?)\s*萬",
                    r"([\d,]+(?:\.\d+)?)\s*萬(?:元)?"]:
        m = re.search(pattern, text)
        if m:
            try: return float(m.group(1).replace(",", ""))
            except ValueError: pass
    return None

def parse_area(text):
    for pattern in [r"(?:總建坪|建坪|總坪數|總坪)\s*[:：]?\s*([\d.]+)", r"([\d.]+)\s*坪"]:
        m = re.search(pattern, text)
        if m:
            try: return float(m.group(1))
            except ValueError: pass
    return None

def parse_detail(page, property_id, store):
    url = DETAIL_BASE + property_id
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try: page.wait_for_load_state("networkidle", timeout=12000)
    except PlaywrightTimeoutError: pass
    page.wait_for_timeout(1800)
    body = text_or_empty(page.locator("body").inner_text(timeout=15000))
    if len(body) < 80: raise RuntimeError("詳情頁內容不足，可能尚未完成動態載入")
    title = ""
    for selector in ["h1", "meta[property='og:title']", "meta[name='twitter:title']", "title"]:
        loc = page.locator(selector).first
        try:
            value = loc.get_attribute("content", timeout=2500) if selector.startswith("meta") else loc.inner_text(timeout=2500)
            if value:
                title = text_or_empty(value)
                break
        except Exception: continue
    title = re.sub(r"\s*[-|｜]\s*台灣房屋.*$", "", title).strip() or f"台灣房屋物件 {property_id}"
    district = ""
    m = re.search(rf"({CITIES}\s*[^\s,，。；;｜|]{{1,12}}?(?:區|鄉|鎮))", body)
    if m:
        district = re.sub(r"\s+", "", m.group(1)).replace("臺中市", "台中市").replace("臺南市", "台南市").replace("臺東縣", "台東縣")
    price, area = parse_price(body), parse_area(body)
    layout = ""
    m = re.search(r"(\d+\s*房\s*\d+\s*廳\s*\d+\s*衛)", body)
    if m: layout = re.sub(r"\s+", "", m.group(1))
    else:
        m = re.search(r"(\d+\s*房(?:\s*\d+\s*廳)?(?:\s*\d+\s*衛)?)", body)
        if m: layout = re.sub(r"\s+", "", m.group(1))
    parking = ""
    for pattern in [r"(?:車位|停車位)\s*[:：]?\s*([^。；;｜|]{1,30})", r"(平面車位|機械車位|坡道平面|坡道機械|無車位)"]:
        m = re.search(pattern, body)
        if m:
            parking = text_or_empty(m.group(1))
            break
    image = ""
    for selector, attr in [("meta[property='og:image']", "content"), ("meta[name='twitter:image']", "content")]:
        try:
            image = page.locator(selector).first.get_attribute(attr, timeout=2500) or ""
            if image: break
        except Exception: pass
    if not image:
        try: image = page.locator("main img, article img, .swiper img, img").first.get_attribute("src", timeout=2500) or ""
        except Exception: pass
    if image: image = urljoin(page.url, image)
    return {"property_id": property_id, "title": title, "district": district,
            "price": price, "area": area, "layout": layout, "parking": parking,
            "image": image, "url": url, "store": store, "first_seen": None,
            "last_seen": TODAY, "status": "active", "fetch_status": "success"}

def collect_store_ids(page, code, url):
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try: page.wait_for_load_state("networkidle", timeout=12000)
    except PlaywrightTimeoutError: pass
    page.wait_for_timeout(2200)
    if not page.locator("body").inner_text(timeout=10000).strip():
        raise RuntimeError("店面頁沒有可讀取內容")
    ids = set()
    # Scroll through lazy-loaded sections and listing cards, then deduplicate by official ID.
    for _ in range(16):
        hrefs = page.locator("a[href*='/buy/']").evaluate_all("(els) => els.map(a => a.href).filter(Boolean)")
        for href in hrefs:
            m = ID_RE.search(href)
            if m: ids.add(m.group(1))
        before = page.evaluate("document.body.scrollHeight")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(600)
        after = page.evaluate("document.body.scrollHeight")
        if after == before: break
    hrefs = page.locator("a[href*='/buy/']").evaluate_all("(els) => els.map(a => a.href)")
    for href in hrefs:
        m = ID_RE.search(href or "")
        if m: ids.add(m.group(1))
    if not ids: raise RuntimeError("未找到任何 /buy/<物件編號> 連結；可能頁面結構改變或內容未載入")
    return ids

def compare(old_items, new_items, failed_stores):
    old = {str(x.get("property_id") or x.get("id") or ""): x for x in old_items if x.get("property_id") or x.get("id")}
    new = {str(x["property_id"]): x for x in new_items}
    added, removed, price_changes, changed = [], [], [], []
    for pid, item in new.items():
        prev = old.get(pid)
        if not prev:
            added.append(item); continue
        item["first_seen"] = prev.get("first_seen") or TODAY
        diffs = {}
        for key in ["title", "district", "area", "layout", "parking", "image", "url", "store"]:
            if prev.get(key) != item.get(key) and item.get(key) not in (None, ""):
                diffs[key] = {"before": prev.get(key), "after": item.get(key)}
        old_price, new_price = prev.get("price"), item.get("price")
        if old_price is not None and new_price is not None and old_price != new_price:
            price_changes.append({"property_id": pid, "title": item["title"], "url": item["url"],
                "before": old_price, "after": new_price, "difference": round(new_price-old_price, 2),
                "drop": round(old_price-new_price, 2) if new_price < old_price else 0, "store": item["store"]})
            diffs["price"] = {"before": old_price, "after": new_price}
        if diffs: changed.append({"property_id": pid, "title": item["title"], "url": item["url"], "changes": diffs})
    for pid, prev in old.items():
        if pid not in new and prev.get("store") not in failed_stores:
            removed.append({**prev, "status": "delisted"})
    return added, removed, price_changes, changed

def write_report(stats, added, removed, price_changes, changed, errors, failed_details):
    REPORTS.mkdir(parents=True, exist_ok=True)
    lines = ["# 每週房屋物件更新報告", "", f"- 執行時間：{NOW}（Asia/Taipei）",
        f"- 本週有效物件：{stats['count']} 筆",
        f"- 新增：{len(added)} 筆｜下架：{len(removed)} 筆｜價格變動：{len(price_changes)} 筆｜其他欄位異動：{len(changed)} 筆",
        "", "## 店面擷取狀態", ""]
    for code, result in stats["stores"].items():
        lines.append(f"- {code}：{'成功' if result['success'] else '失敗'}；本次找到 {result['found']} 筆")
    lines += ["", "## 新增物件", ""]
    lines += [f"- [{x['title']}]({x['url']})（{x['property_id']}，{x.get('price') if x.get('price') is not None else '價格未取得'} 萬）" for x in added] or ["- 無"]
    lines += ["", "## 下架物件", ""]
    lines += [f"- [{x.get('title','未命名')}]({x.get('url', DETAIL_BASE+x.get('property_id',''))})（{x.get('property_id')}）" for x in removed] or ["- 無"]
    drops = [x for x in price_changes if x["after"] < x["before"]]
    lines += ["", "## 降價物件", ""]
    lines += [f"- [{x['title']}]({x['url']})：{x['before']:g} 萬 → {x['after']:g} 萬（降 {x['drop']:g} 萬）" for x in drops] or ["- 無"]
    rises = [x for x in price_changes if x["after"] > x["before"]]
    lines += ["", "## 漲價物件", ""]
    lines += [f"- [{x['title']}]({x['url']})：{x['before']:g} 萬 → {x['after']:g} 萬" for x in rises] or ["- 無"]
    lines += ["", "## 其他欄位變動", ""]
    lines += [f"- [{x['title']}]({x['url']})（{x['property_id']}）：{', '.join(x['changes'].keys())}" for x in changed] or ["- 無"]
    lines += ["", "## 擷取失敗與保留資料", ""]
    lines += [f"- {e}" for e in errors] or ["- 無"]
    lines += [f"- 詳情頁 {x['property_id']}（{x['store']}）讀取失敗，已沿用上一版資料。" for x in failed_details] or ["- 無"]
    (REPORTS / "weekly_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

def main():
    previous = read_previous()
    old_items = previous.get("items", [])
    BACKUPS.mkdir(parents=True, exist_ok=True)
    if DATA.exists():
        backup = BACKUPS / f"listings_{TODAY}.json"
        if not backup.exists(): backup.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    errors, failed_details, successful_stores, all_ids = [], [], {}, {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="zh-TW", timezone_id="Asia/Taipei",
            user_agent="Mozilla/5.0 (compatible; ChenXiaoLingPropertySync/1.0)")
        page = context.new_page()
        page.set_default_timeout(15000)
        for code, url in STORES.items():
            try:
                ids = collect_store_ids(page, code, url)
                all_ids[code] = ids
                successful_stores[code] = {"success": True, "found": len(ids)}
                log("store_success", store=code, found=len(ids), url=url)
            except Exception as exc:
                successful_stores[code] = {"success": False, "found": 0}
                errors.append(f"{code} 店面頁擷取失敗：{type(exc).__name__}: {exc}")
                log("store_failure", store=code, error=str(exc))
        if not any(x["success"] for x in successful_stores.values()):
            browser.close()
            log("aborted", reason="all_storefronts_failed", preserved=True)
            raise SystemExit("三個店面頁全部無法讀取；已停止更新並保留現有 listings.json。")
        old_by_id = {str(x.get("property_id") or x.get("id") or ""): x for x in old_items}
        current = {}
        for code, ids in all_ids.items():
            for pid in sorted(ids):
                try:
                    item = parse_detail(page, pid, code)
                    item["first_seen"] = old_by_id.get(pid, {}).get("first_seen") or TODAY
                    current[pid] = item
                    log("detail_success", property_id=pid, store=code)
                except Exception as exc:
                    failed_details.append({"property_id": pid, "store": code})
                    errors.append(f"{code} 詳情頁 {pid} 擷取失敗：{type(exc).__name__}: {exc}")
                    if pid in old_by_id:
                        current[pid] = {**old_by_id[pid], "fetch_status": "retained_previous"}
                    log("detail_failure", property_id=pid, store=code, error=str(exc),
                         previous_preserved=pid in old_by_id)
        browser.close()
    failed_codes = {code for code, result in successful_stores.items() if not result["success"]}
    for old in old_items:
        pid = str(old.get("property_id") or old.get("id") or "")
        if old.get("store") in failed_codes and pid and pid not in current:
            current[pid] = {**old, "fetch_status": "store_retained_previous"}
    added, removed, price_changes, changed = compare(old_items, list(current.values()), failed_codes)
    final_items = sorted(current.values(), key=lambda x: (x.get("store",""), x.get("property_id","")))
    payload = {"updated_at": NOW, "timezone": "Asia/Taipei", "stores": list(STORES),
        "count": len(final_items), "items": final_items,
        "sync_status": "partial" if errors else "success",
        "store_results": successful_stores,
        "privacy": "Only property listing fields are stored; agent names and phone numbers are not collected."}
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report({"count": len(final_items), "stores": successful_stores},
        added, removed, price_changes, changed, errors, failed_details)
    log("completed", count=len(final_items), added=len(added), removed=len(removed),
        price_changes=len(price_changes), field_changes=len(changed), errors=len(errors),
        failed_details=len(failed_details))
    print(f"Completed: {len(final_items)} active listings.")

if __name__ == "__main__":
    main()
