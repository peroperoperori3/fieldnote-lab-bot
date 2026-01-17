import os, re, json
from datetime import datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup

UA = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "ja,en;q=0.8"
}

SIGNS = ["◎", "○", "▲", "△", "☆"]

# 開催場コード（keibablood / nar.k-ba.net 用）
PLACE_CODE = {
  "帯広": "03",
  "門別": "36",
  "盛岡": "10",
  "水沢": "11",
  "浦和": "18",
  "船橋": "19",
  "大井": "20",
  "川崎": "21",
  "金沢": "22",
  "笠松": "23",
  "名古屋": "24",
  "園田": "27",
  "姫路": "28",
  "高知": "31",
  "佐賀": "32",
}

def fetch(url):
    r = requests.get(url, headers=UA, timeout=30)
    if r.status_code != 200:
        return ""
    r.encoding = r.apparent_encoding
    return r.text

def parse_keibablood(html):
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    races = {}

    for t in tables:
        head = t.find("tr")
        if not head:
            continue
        headers = [c.get_text(strip=True) for c in head.find_all(["th","td"])]
        if not ("指数" in "".join(headers) and "馬名" in "".join(headers)):
            continue

        def idx(key):
            for i,h in enumerate(headers):
                if key in h:
                    return i
            return None

        i_ban = idx("番")
        i_name = idx("馬名")
        i_idx = idx("指数")

        if None in (i_ban, i_name, i_idx):
            continue

        rno = len(races) + 1
        rows = []

        for tr in t.find_all("tr")[1:]:
            tds = [td.get_text(strip=True) for td in tr.find_all(["td","th"])]
            if len(tds) <= max(i_ban, i_name, i_idx):
                continue
            if not re.search(r"\d+", tds[i_ban]):
                continue

            rows.append({
                "umaban": int(re.search(r"\d+", tds[i_ban]).group()),
                "name": tds[i_name],
                "score": float(re.search(r"\d+", tds[i_idx]).group())
            })

        if rows:
            rows.sort(key=lambda x: -x["score"])
            races[rno] = rows[:5]

    return races

def main():
    today = datetime.now().strftime("%Y%m%d")
    os.makedirs("output", exist_ok=True)

    for place, code in PLACE_CODE.items():
        # keibablood URL を総当たり（-1 ～ -12）
        found = False
        for race_idx in range(1, 13):
            url = f"https://keibablood.com/{today}{code}-{race_idx}/"
            html = fetch(url)
            if not html:
                continue

            races = parse_keibablood(html)
            if not races:
                continue

            found = True
            preds = []
            for rno, horses in races.items():
                picks = []
                for i,h in enumerate(horses):
                    picks.append({
                        "mark": SIGNS[i],
                        "umaban": h["umaban"],
                        "name": h["name"],
                        "score": h["score"]
                    })
                preds.append({"race_no": rno, "picks": picks})

            title = f"{today[0:4]}.{today[4:6]}.{today[6:8]} {place}競馬 予想"
            out = {
                "date": today,
                "place": place,
                "place_code": code,
                "title": title,
                "predictions": preds,
                "generated_at": datetime.now().isoformat(timespec="seconds")
            }

            out_path = Path("output") / f"predict_{today}_{code}.json"
            out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] {place} -> {out_path.name}")
            break

        if not found:
            print(f"[SKIP] {place}: keibablood 未開催")

if __name__ == "__main__":
    main()
