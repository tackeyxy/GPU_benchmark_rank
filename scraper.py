import time
import random

import requests
from bs4 import BeautifulSoup
import csv
import json
from datetime import datetime, timezone

URL = "https://benchmarks.ul.com/zh-hans/compare/best-gpus"
# 实测结论，改动前请先确认：
# 1) 不能带查询参数。一旦带上 ?amount=0&sortBy=... 之类，源站一律 500。
# 2) 不能自己设 Accept。只要 Accept 不是 */*（哪怕只写 text/html），
#    源站也是一律 500。requests 默认发的就是 */*，这里显式写出来，
#    免得以后又被"补全成浏览器请求头"改坏。
MAX_ATTEMPTS = 6
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

# 同一个页面换个等价写法，对 CloudFront 就是另一个缓存键。源站抽风时
# 某些键会被缓存成一张 521 字节的静态 500 错误页，对着同一个键重试
# 只会一直拿到同一份缓存，换键才能立刻绕开。
URL_VARIANTS = [
    URL,
    "https://benchmarks.ul.com/zh-hans/./compare/best-gpus",
]

# 源站整体挂掉时（2026-10-26 起这次故障，compare/best-gpus、best-cpus、
# hardware/gpu-list 全部命中一张 521 字节的静态 500 页，而首页、
# compare/best-smartphones 仍然正常），换缓存键也没用。这种情况重试
# 是白费，交给 workflow 里的轮次重试，别把整轮时间耗在必然失败的请求上。
# 注意：平时零星 500 多半只是单个缓存键被污染，换键重试仍然有效，
# 所以下面要连续 len(URL_VARIANTS) 次 5xx 才判定源站故障。
FAIL_FAST_STATUSES = {500, 502, 503, 504}

CSV_FILE = "gpu_benchmarks.csv"
JSON_FILE = "gpu_benchmarks.json"


def parse_stars(star_div):
    if not star_div:
        return 0
    return star_div.select(".icon-starConverted.full").__len__()


def parse_score(score_div):
    if not score_div:
        return None
    span = score_div.select_one(".bar-score")
    if span:
        text = span.get_text(strip=True)
        try:
            return float(text.replace(",", ""))
        except ValueError:
            return None
    return None


def parse_price(price_div):
    if not price_div:
        return None
    not_avail = price_div.select_one(".not-available")
    if not_avail:
        return None
    text = price_div.get_text(strip=True)
    text = text.replace("$", "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def parse_value_for_money(vfm_div):
    if not vfm_div:
        return None
    center_div = vfm_div.select_one("div.center div")
    if center_div:
        text = center_div.get_text(strip=True)
        try:
            return int(text)
        except ValueError:
            return None
    return None


def parse_popularity(pop_div):
    if not pop_div:
        return None
    span = pop_div.select_one(".bar-score")
    if span:
        text = span.get_text(strip=True)
        try:
            return float(text)
        except ValueError:
            return None
    return None


def determine_gpu_type(link):
    href = link.get("href", "")
    if "/hardware/gpu/" in href:
        return "Desktop/Mobile"
    elif "/hardware/mac/" in href:
        return "Mac"
    return "Unknown"


def fetch_page(max_attempts=MAX_ATTEMPTS):
    """抓取页面，按响应正文校验，而不是只看状态码。

    UL 源站会间歇性抽风，CloudFront 会返回一张 521 字节的静态 500
    错误页；正常页面约 426KB 且含 productTable。每次重试换一个
    CloudFront 缓存键，并做退避，通常几秒内就能拿到正常页面。

    但如果所有缓存键都返回 5xx，说明是源站整体故障，而不是某个缓存
    键被污染，换键重试没有意义，直接抛错交给上层轮次重试。
    """
    last_error = "unknown"
    server_errors = 0
    for attempt in range(1, max_attempts + 1):
        url = URL_VARIANTS[(attempt - 1) % len(URL_VARIANTS)]
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as e:
            last_error = f"network error: {e}"
        else:
            if resp.status_code == 200 and "productTable" in resp.text:
                return resp
            last_error = (
                f"HTTP {resp.status_code}, no product table "
                f"({len(resp.text)} bytes, X-Cache: "
                f"{resp.headers.get('X-Cache', '?')})"
            )
            if resp.status_code in FAIL_FAST_STATUSES:
                server_errors += 1
                if server_errors >= len(URL_VARIANTS):
                    raise RuntimeError(
                        f"Origin appears to be down, all {server_errors} "
                        f"cache keys returned 5xx: {last_error}"
                    )
                # 换一个键立即重试，不值得为源站故障等退避
                print(f"Attempt {attempt}/{max_attempts} failed "
                      f"({last_error}), trying another cache key...")
                continue
            server_errors = 0
        if attempt == max_attempts:
            break
        wait = min(20, 3 * 2 ** (attempt - 1)) + random.uniform(0, 2)
        print(f"Attempt {attempt}/{max_attempts} failed ({last_error}), "
              f"retrying in {wait:.0f}s...")
        time.sleep(wait)
    raise RuntimeError(f"Giving up after {max_attempts} attempts: {last_error}")


def scrape():
    print("Fetching GPU benchmark data...")
    resp = fetch_page()

    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.select_one("table#productTable")
    if not table:
        raise RuntimeError("Could not find product table on the page")

    rows = table.select("tbody tr")
    print(f"Found {len(rows)} GPU entries")

    gpus = []
    for row in rows:
        rank_td = row.select_one("td.order-cell")
        name_td = row.find_all("td")
        if not rank_td or not name_td:
            continue

        rank = rank_td.get_text(strip=True)

        link = row.select_one("td:nth-child(2) a.OneLinkNoTx")
        name = link.get_text(strip=True) if link else None
        gpu_type = determine_gpu_type(link) if link else "Unknown"

        price_td = row.select_one("td.list-tiny-none")
        price = parse_price(price_td)

        score_td = row.select_one("td.small-pr1")
        score = parse_score(score_td)

        vfm_td = row.select_one("td.list-small-none.medium-pr1")
        value_for_money = parse_value_for_money(vfm_td)

        pop_td = row.select_one("td.list-medium-none")
        popularity = parse_popularity(pop_td)

        star_div = row.select_one("td:nth-child(2) .starRating")
        stars = parse_stars(star_div)

        gpus.append({
            "rank": int(rank) if rank.isdigit() else rank,
            "name": name,
            "type": gpu_type,
            "price_usd": price,
            "score": score,
            "value_for_money": value_for_money,
            "popularity": popularity,
            "stars": stars,
        })

    gpus.sort(key=lambda x: x["rank"])

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    data = {
        "source": URL,
        "fetched_at": timestamp,
        "total_gpus": len(gpus),
        "gpus": gpus,
    }

    with open(CSV_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "rank", "name", "type", "price_usd", "score",
            "value_for_money", "popularity", "stars"
        ])
        writer.writeheader()
        writer.writerows(gpus)

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(gpus)} GPUs to {CSV_FILE} and {JSON_FILE}")


if __name__ == "__main__":
    scrape()
