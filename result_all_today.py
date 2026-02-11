# result_all_today.py  (fieldnote-lab-bot)
# ★PREDICT完全一致版 + latest_local_result.json 自動生成版

import os, re, json, time, glob
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "ja,en;q=0.8"}
MARKS5 = ["◎", "〇", "▲", "△", "☆"]

KONSEN_NAME = os.environ.get("KONSEN_NAME", "混戦度")

BET_ENABLED = os.environ.get("BET_ENABLED", "1").strip() != "0"
BET_UNIT = int(os.environ.get("BET_UNIT", "100"))
BET_BOX_N = int(os.environ.get("BET_BOX_N", "5"))

DEBUG = os.environ.get("DEBUG", "").strip() == "1"
REFUND_DEBUG = os.environ.get("REFUND_DEBUG", "").strip() == "1"

PNL_FILE = os.environ.get("PNL_FILE", "output/pnl_total.json")


# =========================
# PNLロード/保存
# =========================
def load_pnl_total(path: str):
    p = Path(path)
    if not p.exists():
        return {
            "invest": 0, "payout": 0, "profit": 0,
            "races": 0, "hits": 0,
            "last_updated": None,
            "pred_races": 0,
            "pred_hits": 0,
            "pred_hit_rate": None,
            "pred_by_place": {},
        }
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d
    except Exception:
        return {
            "invest": 0, "payout": 0, "profit": 0,
            "races": 0, "hits": 0,
            "last_updated": None,
            "pred_races": 0,
            "pred_hits": 0,
            "pred_hit_rate": None,
            "pred_by_place": {},
        }

def save_pnl_total(path: str, total: dict):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(total, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch(url: str, debug=False) -> str:
    try:
        r = requests.get(url, headers=UA, timeout=25)
    except Exception:
        return ""
    if r.status_code != 200:
        return ""
    r.encoding = r.apparent_encoding
    return r.text


BABA_CODE = {
  "門別": 36, "盛岡": 10, "水沢": 11, "浦和": 18, "船橋": 19, "大井": 20, "川崎": 21,
  "金沢": 22, "笠松": 23, "名古屋": 24, "園田": 27, "姫路": 28, "高知": 31, "佐賀": 32,
}

KEIBABLOOD_CODE = {
  "門別": "30","盛岡": "35","水沢": "36","浦和": "42","船橋": "43","大井": "44","川崎": "45",
  "金沢": "46","笠松": "47","名古屋": "48","園田": "50","姫路": "51","高知": "54","佐賀": "55",
}


def detect_active_tracks_keibago(yyyymmdd: str):
    active = []
    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"
    for track, baba in BABA_CODE.items():
        url = f"https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceList?k_babaCode={baba}&k_raceDate={date_slash}"
        html = fetch(url)
        if html and ("1R" in html):
            active.append(track)
        time.sleep(0.05)
    return active


# =========================
# predict JSON 読み込み
# =========================
def _find_predict_json(yyyymmdd: str, code: str):
    cand = []
    cand += glob.glob(f"output/predict_{yyyymmdd}_{code}.json")
    cand = [c for c in cand if Path(c).is_file()]
    return cand[0] if cand else None

def load_predict_for_track(yyyymmdd: str, baba: int, place_code: str):
    path = _find_predict_json(yyyymmdd, str(baba))
    if not path:
        return None, None
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    preds = d.get("predictions", [])
    race_map = {int(r["race_no"]): r for r in preds}
    return race_map, path


# =========================
# 結果取得
# =========================
def build_racemark_url(baba: int, yyyymmdd: str, rno: int):
    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"
    return (
        "https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceMarkTable"
        f"?k_babaCode={baba}&k_raceDate={date_slash}&k_raceNo={int(rno)}"
    )

def parse_top3_from_racemark(html_text: str):
    soup = BeautifulSoup(html_text, "lxml")
    top = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all(["th","td"])]
        if len(tds) >= 4 and re.fullmatch(r"\d+", tds[0]):
            top.append({
                "rank": int(tds[0]),
                "umaban": int(tds[2]),
                "name": tds[3],
            })
            if len(top) >= 3:
                break
    return top


# =========================
# MAIN
# =========================
def main():
    yyyymmdd = os.environ.get("DATE") or datetime.now().strftime("%Y%m%d")
    os.makedirs("output", exist_ok=True)

    print(f"[INFO] DATE={yyyymmdd}")

    active = detect_active_tracks_keibago(yyyymmdd)
    print(f"[INFO] active_tracks = {active}")

    pnl_total = load_pnl_total(PNL_FILE)

    wrote_any = False  # ★LATEST

    for track in active:
        baba = BABA_CODE.get(track)
        place_code = KEIBABLOOD_CODE.get(track)
        if not baba or not place_code:
            continue

        pred_map, pred_path = load_predict_for_track(yyyymmdd, baba, place_code)
        if not pred_map:
            print(f"[SKIP] {track}: predict json not found")
            continue

        races_out = []

        for rno in range(1, 13):
            pr = pred_map.get(int(rno))
            if not pr:
                continue

            rm_url = build_racemark_url(baba, yyyymmdd, rno)
            rm_html = fetch(rm_url)
            result_top3 = parse_top3_from_racemark(rm_html) if rm_html else []

            races_out.append({
                "race_no": rno,
                "race_name": pr.get("race_name", ""),
                "pred_top5": pr.get("picks", []),
                "result_top3": result_top3,
            })

        if not races_out:
            continue

        title = f"{yyyymmdd[0:4]}.{yyyymmdd[4:6]}.{yyyymmdd[6:8]} {track}競馬 結果"

        out = {
            "type": "fieldnote_result",
            "date": yyyymmdd,
            "place": track,
            "place_code": place_code,
            "title": title,
            "races": races_out,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

        json_path = Path("output") / f"result_{yyyymmdd}_{place_code}.json"
        json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"[OK] {track} -> {json_path.name}")

        wrote_any = True  # ★LATEST

    # =========================
    # ★LATEST 地方結果
    # =========================
    if wrote_any:
        Path("output/latest_local_result.json").write_text(
            json.dumps({"date": yyyymmdd}, ensure_ascii=False),
            encoding="utf-8"
        )
        print(f"[OK] wrote latest_local_result.json ({yyyymmdd})")


if __name__ == "__main__":
    main()