# result_all_today.py  (fieldnote-lab-bot)  ★PREDICT完全一致版（推奨）
# 目的：
# - 「result の指数（上位5頭）」を predict と 100% 同じにする
# やり方：
# - result 側では指数の再計算をやめて、predict が出力した JSON（predict_YYYYMMDD_XX.json）を読み込んで使う
#
# 出力：
# - output/result_YYYYMMDD_<track>.json / .html
# - output/pnl_total.json（収支まとめ：全開催場の合算）

import os
import re
import json
import math
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "ja,en;q=0.8"}
MARKS5 = ["◎", "〇", "▲", "△", "☆"]

# ===== 混戦度（predictに合わせる：JSON内の値をそのまま表示）=====
KONSEN_NAME = os.environ.get("KONSEN_NAME", "混戦度")

# ===== ベット関連（任意）=====
BET_ENABLED = os.environ.get("BET_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
BET_UNIT = int(os.environ.get("BET_UNIT", "100"))

# ===== 予想JSONの置き場所 =====
PRED_DIR = Path(os.environ.get("PRED_DIR", "output"))

# ===== 結果出力先 =====
OUT_DIR = Path(os.environ.get("OUT_DIR", "output"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ===== NAR（nar.k-ba.net）track code（= keiba.go.jp k_babaCode）※帯広(3)除外 =====
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
EXCLUDE_BABA = {3}  # 帯広ばんえい


# ===== keiba.go.jp から結果を取る =====
def fetch(url: str, debug=False, params=None) -> str:
    try:
        r = requests.get(url, headers=UA, params=params, timeout=25)
    except Exception as e:
        if debug:
            print(f"[GET] {url}  ERROR={e}")
        return ""
    if debug:
        ct = r.headers.get("Content-Type", "")
        print(f"[GET] {r.url}  status={r.status_code}  ct={ct}  bytes={len(r.content)}")
    if r.status_code != 200:
        return ""
    r.encoding = r.apparent_encoding
    return r.text


def keibago_racelist_has_race(html: str) -> bool:
    if not html:
        return False
    return ("1R" in html) or ("２Ｒ" in html) or ("出馬表" in html)


def detect_active_tracks(yyyymmdd: str, debug=False):
    active = []
    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"
    for track, baba in BABA_CODE.items():
        if baba in EXCLUDE_BABA:
            continue
        url = f"https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceList?k_babaCode={baba}&k_raceDate={date_slash}"
        html = fetch(url, debug=debug)
        if keibago_racelist_has_race(html):
            active.append(track)
        time.sleep(0.08)
    return active


def find_result_table(soup: BeautifulSoup):
    # もっともそれっぽいテーブルを探す（払い戻し/着順の表）
    best = None
    best_score = -1
    for t in soup.find_all("table"):
        txt = t.get_text(" ", strip=True)
        score = 0
        if "着順" in txt:
            score += 3
        if "馬番" in txt:
            score += 2
        if "払戻" in txt or "払戻金" in txt:
            score += 2
        if "人気" in txt:
            score += 1
        if score > best_score:
            best_score = score
            best = t
    return best if best_score >= 3 else None


def parse_result_race(html: str):
    """
    keiba.go.jp の結果ページから
    - 着順（上位3）
    - 単勝/複勝/馬連/ワイド/馬単/三連複/三連単（取れる範囲）
    を抽出する（ページ構造の揺れがあるので、多少雑に）
    """
    if not html:
        return {"top3": [], "payouts": {}}

    soup = BeautifulSoup(html, "lxml")

    # 着順テーブルっぽいもの
    t = find_result_table(soup)
    top3 = []
    if t:
        trs = t.find_all("tr")

        # ヘッダ行から列を推測
        head = [c.get_text(" ", strip=True) for c in trs[0].find_all(["th", "td"])]

        def find_col(keys):
            for i, h in enumerate(head):
                for k in keys:
                    if k in h:
                        return i
            return None

        c_rank = find_col(["着順", "着"])
        c_umaban = find_col(["馬番", "馬"])
        c_name = find_col(["馬名"])
        c_pop = find_col(["人気"])

        if c_rank is None:
            c_rank = 0

        for tr in trs[1:]:
            tds = tr.find_all(["td", "th"])
            if not tds:
                continue
            vals = [c.get_text(" ", strip=True) for c in tds]
            if len(vals) <= max(c_rank, c_umaban or 0):
                continue

            rk = re.search(r"\d+", vals[c_rank] or "")
            if not rk:
                continue
            rk = int(rk.group())
            if rk > 3:
                continue

            um = None
            if c_umaban is not None:
                m = re.search(r"\d+", vals[c_umaban] or "")
                um = int(m.group()) if m else None

            nm = vals[c_name] if (c_name is not None and c_name < len(vals)) else ""

            pp = None
            if c_pop is not None and c_pop < len(vals):
                mp = re.search(r"\d+", vals[c_pop] or "")
                pp = int(mp.group()) if mp else None

            top3.append({"rank": rk, "umaban": um, "name": nm, "pop": pp})

        top3.sort(key=lambda x: x["rank"])

    # 払戻金（ページ上のテキストからざっくり拾う）
    payouts = {}
    text = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    def pick_money(line):
        m = re.search(r"(\d[\d,]*)\s*円", line)
        return int(m.group(1).replace(",", "")) if m else None

    # キー候補（出る順序も揺れるので雑に）
    keys = [
        ("単勝", "tansho"),
        ("複勝", "fukusho"),
        ("枠連", "wakuren"),
        ("馬連", "umaren"),
        ("馬単", "umatan"),
        ("ワイド", "wide"),
        ("三連複", "sanrenpuku"),
        ("三連単", "sanrentan"),
    ]

    for ln in lines:
        for jp, k in keys:
            if jp in ln and k not in payouts:
                v = pick_money(ln)
                if v is not None:
                    payouts[k] = {"label": jp, "yen": v, "raw": ln}

    return {"top3": top3, "payouts": payouts}


def build_result_url(yyyymmdd: str, track_id: int, race_no: int) -> str:
    # keiba.go.jp の「結果」ページ（RaceResult）URL
    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"
    return (
        "https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceMarkTable"
        f"?k_raceDate={date_slash}&k_babaCode={track_id}&k_raceNo={race_no}"
    )


def html_escape(s: str) -> str:
    import html
    return html.escape(str(s))


def render_html(title: str, data):
    """
    result HTML（predictと似た見た目にする）
    data: {"track":..., "races":[...], "pnl":...}
    """
    parts = []
    parts.append("<div style='max-width:980px;margin:0 auto;line-height:1.7;'>")
    parts.append(f"<h2 style='margin:12px 0 8px;font-size:20px;font-weight:900;'>{html_escape(title)}</h2>")

    for r in data.get("races", []):
        rno = r.get("race_no")
        race_name = r.get("race_name", "")
        head = f"{rno}R" + (f" {race_name}" if race_name else "")

        parts.append(
            "<div style='margin:16px 0 18px;padding:12px 12px;"
            "border:1px solid #e5e7eb;border-radius:14px;background:#ffffff;'>"
        )
        parts.append(f"<div style='font-size:18px;font-weight:900;color:#111827;'>{html_escape(head)}</div>")

        # 予想（predictのtop5）
        picks = r.get("picks", [])
        if picks:
            parts.append("<div style='margin:8px 0 6px;font-weight:900;'>予想（上位5）</div>")
            parts.append("<div style='overflow-x:auto;'><table style='width:100%;border-collapse:collapse;'>")
            parts.append(
                "<thead><tr>"
                "<th style='border-bottom:2px solid #1d4ed8;padding:8px;text-align:center;white-space:nowrap;'>印</th>"
                "<th style='border-bottom:2px solid #1d4ed8;padding:8px;text-align:center;white-space:nowrap;'>馬番</th>"
                "<th style='border-bottom:2px solid #1d4ed8;padding:8px;text-align:left;'>馬名</th>"
                "<th style='border-bottom:2px solid #1d4ed8;padding:8px;text-align:right;white-space:nowrap;'>指数</th>"
                "</tr></thead><tbody>"
            )
            for i, p in enumerate(picks):
                bgrow = "#ffffff" if i % 2 == 0 else "#f8fafc"
                parts.append(
                    f"<tr style='background:{bgrow};'>"
                    f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;font-weight:900;'>{html_escape(p.get('mark',''))}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;'>{int(p.get('umaban',0))}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:left;font-weight:750;'>{html_escape(p.get('name',''))}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:right;font-variant-numeric:tabular-nums;'>"
                    f"{float(p.get('score',0.0)):.2f}"
                    f"</td>"
                    f"</tr>"
                )
            parts.append("</tbody></table></div>")

        # 結果（top3）
        top3 = r.get("result", {}).get("top3", [])
        if top3:
            parts.append("<div style='margin:10px 0 6px;font-weight:900;'>結果（上位3）</div>")
            parts.append("<div style='overflow-x:auto;'><table style='width:100%;border-collapse:collapse;'>")
            parts.append(
                "<thead><tr>"
                "<th style='border-bottom:2px solid #10b981;padding:8px;text-align:center;white-space:nowrap;'>着</th>"
                "<th style='border-bottom:2px solid #10b981;padding:8px;text-align:center;white-space:nowrap;'>馬番</th>"
                "<th style='border-bottom:2px solid #10b981;padding:8px;text-align:left;'>馬名</th>"
                "<th style='border-bottom:2px solid #10b981;padding:8px;text-align:center;white-space:nowrap;'>人気</th>"
                "</tr></thead><tbody>"
            )
            for i, h in enumerate(top3):
                bgrow = "#ffffff" if i % 2 == 0 else "#f8fafc"
                parts.append(
                    f"<tr style='background:{bgrow};'>"
                    f"<td style='padding:8px;border-bottom:1px solid #d1fae5;text-align:center;font-weight:900;'>{int(h.get('rank',0))}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #d1fae5;text-align:center;'>{html_escape(h.get('umaban',''))}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #d1fae5;text-align:left;font-weight:750;'>{html_escape(h.get('name',''))}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #d1fae5;text-align:center;'>{html_escape(h.get('pop',''))}</td>"
                    f"</tr>"
                )
            parts.append("</tbody></table></div>")

        # 払戻（雑表示）
        payouts = r.get("result", {}).get("payouts", {})
        if payouts:
            parts.append("<div style='margin:10px 0 6px;font-weight:900;'>払戻（拾えた範囲）</div>")
            parts.append("<ul style='margin:0 0 0 18px;'>")
            for k, v in payouts.items():
                parts.append(f"<li>{html_escape(v.get('label'))}: {html_escape(v.get('yen'))} 円</li>")
            parts.append("</ul>")

        # 収支（任意）
        if BET_ENABLED:
            pnl = r.get("pnl", None)
            if pnl is not None:
                parts.append(f"<div style='margin-top:10px;font-weight:900;'>収支: {int(pnl):+d} 円</div>")

        parts.append("</div>")

    # 合計
    if BET_ENABLED:
        total = data.get("pnl_total", None)
        if total is not None:
            parts.append(
                "<div style='margin:20px 0 10px;padding:12px;border-radius:14px;"
                "border:1px solid #e5e7eb;background:#f8fafc;font-weight:900;'>"
                f"合計収支: {int(total):+d} 円（unit={BET_UNIT}）"
                "</div>"
            )

    parts.append("</div>")
    return "\n".join(parts)


def calc_pnl_simple(picks, top3, unit=100):
    """
    超簡易：◎（1位予想）が1着なら +unit、それ以外 -unit
    本格的にやるなら馬券種別/オッズが必要
    """
    if not picks or not top3:
        return 0
    pred1 = picks[0].get("umaban")
    win = top3[0].get("umaban")
    if pred1 is None or win is None:
        return 0
    return unit if int(pred1) == int(win) else -unit


def load_predict_json(yyyymmdd: str, track_id: int):
    # output/predict_YYYYMMDD_<trackId>.json を読む
    p = PRED_DIR / f"predict_{yyyymmdd}_{track_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def main():
    yyyymmdd = os.environ.get("DATE") or datetime.now().strftime("%Y%m%d")
    debug = os.environ.get("DEBUG", "").strip() == "1"

    print(f"[INFO] DATE={yyyymmdd}")
    print(f"[INFO] BET enabled={BET_ENABLED} unit={BET_UNIT}")

    active = detect_active_tracks(yyyymmdd, debug=debug)
    print(f"[INFO] active_tracks = {active}")

    total_pnl = 0
    out_total = {
        "date": yyyymmdd,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "bet_enabled": BET_ENABLED,
        "bet_unit": BET_UNIT,
        "tracks": [],
    }

    for track in active:
        track_id = BABA_CODE.get(track)
        if not track_id or track_id in EXCLUDE_BABA:
            continue

        pred = load_predict_json(yyyymmdd, track_id)
        if not pred:
            print(f"[SKIP] {track}: predict json not found -> NO OUTPUT")
            continue

        races = []
        track_pnl = 0

        for r in pred.get("predictions", []):
            rno = int(r.get("race_no", 0))
            race_name = r.get("race_name", "")
            picks = r.get("picks", [])

            # 結果取得
            url = build_result_url(yyyymmdd, track_id, rno)
            html = fetch(url, debug=False)
            rr = parse_result_race(html)

            pnl = None
            if BET_ENABLED:
                pnl = calc_pnl_simple(picks, rr.get("top3", []), unit=BET_UNIT)
                track_pnl += pnl

            races.append({
                "race_no": rno,
                "race_name": race_name,
                "picks": picks,
                "result": rr,
                "result_url": url,
                "pnl": pnl,
            })
            time.sleep(0.05)

        if BET_ENABLED:
            total_pnl += track_pnl

        title = f"{yyyymmdd[0:4]}.{yyyymmdd[4:6]}.{yyyymmdd[6:8]} {track}競馬 結果"
        out = {
            "date": yyyymmdd,
            "place": track,
            "place_code": str(track_id),
            "title": title,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "bet_enabled": BET_ENABLED,
            "bet_unit": BET_UNIT,
            "pnl_total": track_pnl if BET_ENABLED else None,
            "races": races,
        }

        json_path = OUT_DIR / f"result_{yyyymmdd}_{track}.json"
        html_path = OUT_DIR / f"result_{yyyymmdd}_{track}.html"
        json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path.write_text(render_html(title, out), encoding="utf-8")

        print(f"[OK] wrote {json_path.as_posix()} / {html_path.as_posix()}")

        out_total["tracks"].append({
            "place": track,
            "place_code": str(track_id),
            "pnl_total": track_pnl if BET_ENABLED else None,
            "result_json": json_path.name,
            "result_html": html_path.name,
        })

    # 合計収支
    if BET_ENABLED:
        out_total["pnl_total"] = total_pnl

    (OUT_DIR / "pnl_total.json").write_text(json.dumps(out_total, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] wrote {OUT_DIR / 'pnl_total.json'}")


if __name__ == "__main__":
    main()
