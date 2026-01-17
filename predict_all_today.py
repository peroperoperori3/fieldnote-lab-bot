import os, re, json, time
from datetime import datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent":"Mozilla/5.0", "Accept-Language":"ja,en;q=0.8"}
SIGNS = ["◎","○","▲","△","☆"]

# keiba.go.jp babaCode（開催判定用）※帯広は除外
BABA_CODE = {
  "門別": 36,
  "盛岡": 10,
  "水沢": 11,
  "浦和": 18,
  "船橋": 19,
  "大井": 20,
  "川崎": 21,
  "金沢": 22,
  "笠松": 23,
  "名古屋": 24,
  "園田": 27,
  "姫路": 28,
  "高知": 31,
  "佐賀": 32,
}

# keibablood 開催場コード（実績ベース）
KEIBABLOOD_CODE = {
  "門別": "30",
  "盛岡": "35",
  "水沢": "36",
  "浦和": "42",
  "船橋": "43",
  "大井": "44",
  "川崎": "45",
  "金沢": "46",
  "笠松": "47",
  "名古屋": "48",
  "園田": "50",
  "姫路": "51",
  "高知": "54",
  "佐賀": "55",
}

def fetch(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=25)
    if r.status_code != 200:
        return ""
    r.encoding = r.apparent_encoding
    return r.text

def detect_active_tracks_keibago(yyyymmdd: str):
    active = []
    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"
    for track, baba in BABA_CODE.items():
        url = f"https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceList?k_babaCode={baba}&k_raceDate={date_slash}"
        html = fetch(url)
        if html and ("1R" in html):
            active.append(track)
        time.sleep(0.08)
    return active

def parse_keibablood_tables(html: str):
    soup = BeautifulSoup(html, "lxml")
    races = {}
    for t in soup.find_all("table"):
        head = t.find("tr")
        if not head:
            continue
        headers = [c.get_text(" ", strip=True) for c in head.find_all(["th","td"])]
        if not ("指数" in " ".join(headers) and "馬名" in " ".join(headers)):
            continue

        def idx(k):
            for i,h in enumerate(headers):
                if k in h:
                    return i
            return None

        i_ban, i_name, i_idx = idx("番"), idx("馬名"), idx("指数")
        if None in (i_ban, i_name, i_idx):
            continue

        rno = len(races) + 1
        rows = []
        for tr in t.find_all("tr")[1:]:
            cells = tr.find_all(["td","th"])
            if not cells:
                continue
            vals = [c.get_text(" ", strip=True) for c in cells]
            if len(vals) <= max(i_ban, i_name, i_idx):
                continue
            mban = re.search(r"\d+", vals[i_ban])
            midx = re.search(r"[\d.]+", vals[i_idx])
            if not (mban and midx):
                continue
            rows.append({
                "umaban": int(mban.group()),
                "name": vals[i_name].strip(),
                "score": float(midx.group())
            })
        if rows:
            rows.sort(key=lambda x:(-x["score"], x["umaban"]))
            races[rno] = rows[:5]
    return races

def main():
    yyyymmdd = os.environ.get("DATE") or datetime.now().strftime("%Y%m%d")
    os.makedirs("output", exist_ok=True)

    active = detect_active_tracks_keibago(yyyymmdd)
    print(f"[INFO] active_tracks = {active}")

    for track in active:
        code = KEIBABLOOD_CODE.get(track)
        if not code:
            continue

        for i in range(1, 13):
            html = fetch(f"https://keibablood.com/{yyyymmdd}{code}-{i}/")
            if not html:
                continue
            races = parse_keibablood_tables(html)
            if not races:
                continue

            preds = []
            for rno, hs in races.items():
                picks = []
                for j,h in enumerate(hs):
                    picks.append({
                        "mark": SIGNS[j],
                        "umaban": h["umaban"],
                        "name": h["name"],
                        "score": h["score"],
                    })
                preds.append({"race_no": rno, "picks": picks})

            out = {
                "date": yyyymmdd,
                "place": track,
                "place_code": code,
                "title": f"{yyyymmdd[:4]}.{yyyymmdd[4:6]}.{yyyymmdd[6:]} {track}競馬 予想",
                "predictions": preds,
                "generated_at": datetime.now().isoformat(timespec="seconds")
            }
            p = Path("output") / f"predict_{yyyymmdd}_{code}.json"
            p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] {track} -> {p.name}")
            break

if __name__ == "__main__":
    main()
