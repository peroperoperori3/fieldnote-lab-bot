import os, re, json, time
from datetime import datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent":"Mozilla/5.0", "Accept-Language":"ja,en;q=0.8"}
SIGNS = ["◎","○","▲","△","☆"]

# keiba.go.jp babaCode（開催判定に使う）
BABA_CODE = {
  "帯広": 3,
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

# ★keibablood の開催場コード（あなたが貼ってくれた実績ベース）
# 例: 佐賀=55 / 大井=44 / 名古屋=48 ...
KEIBABLOOD_CODE = {
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
  "水沢": "36",
  "門別": "30",
  "盛岡": "35",
  # "帯広": "??",  # 未確認。分かったら足す
}

def fetch(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=25)
    if r.status_code != 200:
        return ""
    r.encoding = r.apparent_encoding
    return r.text

def detect_active_tracks_keibago(yyyymmdd: str):
    """keiba.go.jp RaceList を叩いて今日開催してる場だけ返す"""
    active = []
    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"

    for track, baba in BABA_CODE.items():
        url = f"https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceList?k_babaCode={baba}&k_raceDate={date_slash}"
        html = fetch(url)
        # ゆるめ判定：'1R' が含まれてたら開催扱い
        if html and ("1R" in html):
            active.append(track)
        time.sleep(0.08)
    return active

def parse_keibablood_tables(html: str):
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    races = {}

    for t in tables:
        first = t.find("tr")
        if not first:
            continue

        headers = [c.get_text(" ", strip=True) for c in first.find_all(["th","td"])]
        hj = " ".join(headers)

        # ざっくり「指数」「馬名」「番」が揃ってる表だけ拾う
        if not (("指数" in hj) and ("馬名" in hj) and ("番" in hj)):
            continue

        def idx_of(key):
            for i, h in enumerate(headers):
                if key in h:
                    return i
            return None

        i_ban = idx_of("番")
        i_name = idx_of("馬名")
        i_idx = idx_of("指数")
        if None in (i_ban, i_name, i_idx):
            continue

        race_no = len(races) + 1
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
                "umaban": int(mban.group(0)),
                "name": vals[i_name].strip(),
                "score": float(midx.group(0)),
            })

        if rows:
            rows.sort(key=lambda x: (-x["score"], x["umaban"]))
            races[race_no] = rows[:5]

    return races

def main():
    yyyymmdd = os.environ.get("DATE") or datetime.now().strftime("%Y%m%d")
    os.makedirs("output", exist_ok=True)

    active_tracks = detect_active_tracks_keibago(yyyymmdd)
    print(f"[INFO] active_tracks = {active_tracks}")

    if not active_tracks:
        print("[INFO] active_tracks is empty -> nothing to do")
        return

    for track in active_tracks:
        code = KEIBABLOOD_CODE.get(track)
        if not code:
            print(f"[SKIP] {track}: keibablood code unknown")
            continue

        found = False
        for idx in range(1, 13):
            url = f"https://keibablood.com/{yyyymmdd}{code}-{idx}/"
            html = fetch(url)
            if not html:
                continue

            races = parse_keibablood_tables(html)
            if not races:
                continue

            found = True
            preds = []
            for rno in sorted(races.keys()):
                picks = []
                for i, h in enumerate(races[rno]):
                    picks.append({
                        "mark": SIGNS[i],
                        "umaban": h["umaban"],
                        "name": h["name"],
                        "score": h["score"],
                    })
                preds.append({"race_no": rno, "picks": picks})

            title = f"{yyyymmdd[0:4]}.{yyyymmdd[4:6]}.{yyyymmdd[6:8]} {track}競馬 予想"
            out = {
                "date": yyyymmdd,
                "place": track,
                "place_code": code,
                "title": title,
                "predictions": preds,
                "generated_at": datetime.now().isoformat(timespec="seconds")
            }

            out_path = Path("output") / f"predict_{yyyymmdd}_{code}.json"
            out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] {track} -> {out_path.name}")
            break

        if not found:
            print(f"[SKIP] {track}: keibablood 未発見（URL規則違い or 未掲載）")

if __name__ == "__main__":
    main()
