import requests
from bs4 import BeautifulSoup
import csv
import json
from datetime import datetime, timezone

URL = "https://benchmarks.ul.com/zh-hans/compare/best-gpus"
PARAMS = {
    "amount": "0",
    "sortBy": "SCORE",
    "reverseOrder": "true",
    "types": "MOBILE,DESKTOP,MAC",
    "minRating": "0",
}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
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


def scrape():
    print("Fetching GPU benchmark data...")
    resp = requests.get(URL, params=PARAMS, headers=HEADERS, timeout=30)
    resp.raise_for_status()

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
