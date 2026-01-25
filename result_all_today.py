# result_all_today.py  (fieldnote-lab-bot)  ★PREDICT完全一致版（推奨）
# 目的：
# - result の「上位5頭（指数）」は predict JSON をそのまま使い、再計算しない（predict と100%一致）
# - 予想の的中率（全体 + 開催場別）を集計して蓄積する
#
# 的中定義：
# - 1,2,3着が「指数上位5頭」にすべて含まれていれば「的中」
# - 結果の top3 が揃ってない場合は判定しない（集計しない）
#
# 出力：
# - output/result_YYYYMMDD_<track>.json / .html
# - output/pnl_total.json（収支まとめ：全開催場の合算）※BET_ENABLED時
# - output/pred_hit_history.json（的中率の日次履歴：全体 + 場別）
# - output/pred_hit_cumulative.json（累計：全体 + 場別）←トップページはこれを読むのがラク

import os
import re
import json
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
    - 払戻（拾える範囲）
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
        if trs:
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
                if c_umaban is not None and c_umaban < len(vals):
                    m = re.search(r"\d+", vals[c_umaban] or "")
                    um = int(m.group()) if m else None

                nm = vals[c_name] if (c_name is not None and c_name < len(vals)) else ""

                pp = None
                if c_pop is not None and c_pop < len(vals):
                    mp = re.search(r"\d+", vals[c_pop] or "")
                    pp = int(mp.group()) if mp else None

                top3.append({"rank": rk, "umaban": um, "name": nm, "pop": pp})

            top3.sort(key=lambda x: x["rank"])

    # 払戻（ページ上のテキストからざっくり拾う）
    payouts = {}
    text = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    def pick_money(line):
        m = re.search(r"(\d[\d,]*)\s*円", line)
        return int(m.group(1).replace(",", "")) if m else None

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
    # keiba.go.jp の「結果」ページURL
    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"
    return (
        "https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceMarkTable"
        f"?k_raceDate={date_slash}&k_babaCode={track_id}&k_raceNo={race_no}"
    )


def html_escape(s: str) -> str:
    import html

    return html.escape(str(s))


def _badge(text: str, bg: str, fg: str = "#ffffff") -> str:
    return (
        "<span style='display:inline-block;padding:4px 10px;border-radius:999px;"
        f"background:{bg};color:{fg};font-weight:900;font-size:12px;letter-spacing:.02em;"
        "line-height:1;white-space:nowrap;'>"
        f"{html_escape(text)}</span>"
    )


def render_html(title: str, data):
    """
    result HTML（predictと似た見た目 + 的中バッジ）
    data: {"track":..., "races":[...], "pnl":...}
    """
    parts = []
    parts.append("<div style='max-width:980px;margin:0 auto;line-height:1.7;'>")
    parts.append(f"<h2 style='margin:12px 0 8px;font-size:20px;font-weight:900;'>{html_escape(title)}</h2>")

    # トップサマリ（開催場別）
    tr = data.get("pred_races", 0) or 0
    th = data.get("pred_hits", 0) or 0
    rate = (th / tr * 100.0) if tr else 0.0
    parts.append(
        "<div style='margin:10px 0 14px;padding:12px;border-radius:14px;"
        "border:1px solid #e5e7eb;background:#f8fafc;'>"
        f"<div style='font-weight:900;'>場別成績：予想レース数 {int(tr)} / 的中 {int(th)} / 的中率 {rate:.1f}%</div>"
        "</div>"
    )

    for r in data.get("races", []):
        rno = r.get("race_no")
        race_name = r.get("race_name", "")
        head = f"{rno}R" + (f" {race_name}" if race_name else "")

        # 的中バッジ
        hit = r.get("hit", None)
        hit_badge = ""
        if hit is True:
            hit_badge = _badge("的中", "#10b981")
        elif hit is False:
            hit_badge = _badge("不適中", "#6b7280")
        else:
            hit_badge = _badge("判定不可", "#9ca3af")

        parts.append(
            "<div style='margin:16px 0 18px;padding:12px 12px;"
            "border:1px solid #e5e7eb;border-radius:14px;background:#ffffff;'>"
        )

        parts.append(
            "<div style='display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;'>"
            f"<div style='font-size:18px;font-weight:900;color:#111827;'>{html_escape(head)}</div>"
            f"<div style='display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap;'>{hit_badge}</div>"
            "</div>"
        )

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


def judge_hit_top3_in_top5(picks, top3):
    """
    的中定義：
    - 結果 top3 が「指数上位5頭」にすべて含まれていれば True
    - top3 が3頭揃っていない場合は None（判定不可＝集計しない）
    """
    if not picks or not top3:
        return None

    pred_set = set()
    for p in picks:
        um = p.get("umaban")
        if um is not None and str(um).isdigit():
            pred_set.add(int(um))

    res_set = set()
    for h in top3:
        um = h.get("umaban")
        if um is not None and str(um).isdigit():
            res_set.add(int(um))

    if len(res_set) != 3:
        return None
    if len(pred_set) < 5:
        return None

    return res_set.issubset(pred_set)


def main():
    yyyymmdd = os.environ.get("DATE") or datetime.now().strftime("%Y%m%d")
    debug = os.environ.get("DEBUG", "").strip() == "1"

    print(f"[INFO] DATE={yyyymmdd}")
    print(f"[INFO] BET enabled={BET_ENABLED} unit={BET_UNIT}")

    active = detect_active_tracks(yyyymmdd, debug=debug)
    print(f"[INFO] active_tracks = {active}")

    # ===== 全体集計（当日）=====
    day_pred_races = 0
    day_pred_hits = 0

    # ===== 場別集計（当日）=====
    day_by_track = {}  # track -> {"races":int,"hits":int}

    # ===== 収支（任意）=====
    total_pnl = 0

    # pnl_total.json など用（従来の枠）
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

        # 場別（当日）カウンタ
        track_pred_races = 0
        track_pred_hits = 0

        for r in pred.get("predictions", []):
            rno = int(r.get("race_no", 0))
            race_name = r.get("race_name", "")
            picks = r.get("picks", [])

            # 結果取得
            url = build_result_url(yyyymmdd, track_id, rno)
            html = fetch(url, debug=False)
            rr = parse_result_race(html)

            # 的中判定（top3が揃うレースのみ集計）
            hit_j = judge_hit_top3_in_top5(picks, rr.get("top3", []))
            if hit_j is True:
                day_pred_races += 1
                day_pred_hits += 1
                track_pred_races += 1
                track_pred_hits += 1
            elif hit_j is False:
                day_pred_races += 1
                track_pred_races += 1
            # None は判定不可（集計しない）

            pnl = None
            if BET_ENABLED:
                pnl = calc_pnl_simple(picks, rr.get("top3", []), unit=BET_UNIT)
                track_pnl += pnl

            races.append(
                {
                    "race_no": rno,
                    "race_name": race_name,
                    "picks": picks,
                    "result": rr,
                    "result_url": url,
                    "hit": hit_j,  # True/False/None
                    "pnl": pnl,
                }
            )
            time.sleep(0.05)

        # 場別集計（当日）を保存
        day_by_track[track] = {"races": track_pred_races, "hits": track_pred_hits}

        if BET_ENABLED:
            total_pnl += track_pnl

        title = f"{yyyymmdd[0:4]}.{yyyymmdd[4:6]}.{yyyymmdd[6:8]} {track}競馬 結果"
        track_hit_rate = (track_pred_hits / track_pred_races * 100.0) if track_pred_races else 0.0

        out = {
            "date": yyyymmdd,
            "place": track,
            "place_code": str(track_id),
            "title": title,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "bet_enabled": BET_ENABLED,
            "bet_unit": BET_UNIT,
            "pnl_total": track_pnl if BET_ENABLED else None,
            "pred_races": track_pred_races,
            "pred_hits": track_pred_hits,
            "pred_hit_rate": round(track_hit_rate, 1),
            "races": races,
        }

        json_path = OUT_DIR / f"result_{yyyymmdd}_{track}.json"
        html_path = OUT_DIR / f"result_{yyyymmdd}_{track}.html"
        json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path.write_text(render_html(title, out), encoding="utf-8")

        print(f"[OK] wrote {json_path.as_posix()} / {html_path.as_posix()}")

        out_total["tracks"].append(
            {
                "place": track,
                "place_code": str(track_id),
                "pnl_total": track_pnl if BET_ENABLED else None,
                "pred_races": track_pred_races,
                "pred_hits": track_pred_hits,
                "pred_hit_rate": round(track_hit_rate, 1),
                "result_json": json_path.name,
                "result_html": html_path.name,
            }
        )

    # ===== pnl_total.json（既存系）=====
    if BET_ENABLED:
        out_total["pnl_total"] = total_pnl

    # 当日全体の的中率も入れておく（トップページで使ってもOK）
    day_hit_rate = (day_pred_hits / day_pred_races * 100.0) if day_pred_races else 0.0
    out_total["pred_races"] = day_pred_races
    out_total["pred_hits"] = day_pred_hits
    out_total["pred_hit_rate"] = round(day_hit_rate, 1)

    (OUT_DIR / "pnl_total.json").write_text(json.dumps(out_total, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] wrote {OUT_DIR / 'pnl_total.json'}")

    # ===== 的中率：日次履歴を蓄積（再実行は同日上書き）=====
    history_path = OUT_DIR / "pred_hit_history.json"
    today = {
        "date": yyyymmdd,
        "races": day_pred_races,
        "hits": day_pred_hits,
        "hit_rate": round(day_hit_rate, 1),
        "tracks": [
            {
                "place": t,
                "races": int(v.get("races", 0) or 0),
                "hits": int(v.get("hits", 0) or 0),
                "hit_rate": round((v.get("hits", 0) or 0) / (v.get("races", 0) or 1) * 100.0, 1)
                if (v.get("races", 0) or 0) > 0
                else 0.0,
            }
            for t, v in sorted(day_by_track.items(), key=lambda x: x[0])
        ],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []
    else:
        history = []

    # 同日を除いて差し替え
    history = [h for h in history if h.get("date") != yyyymmdd]
    history.append(today)
    history.sort(key=lambda x: x.get("date", ""))

    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    # ===== 累計（全体 + 場別）を作成（トップページはこれを見る）=====
    total_races = sum(int(h.get("races", 0) or 0) for h in history)
    total_hits = sum(int(h.get("hits", 0) or 0) for h in history)
    total_hit_rate = (total_hits / total_races * 100.0) if total_races else 0.0

    # 場別累計
    cum_by_track = {}  # place -> {"races":..,"hits":..}
    for h in history:
        for tr in (h.get("tracks") or []):
            place = tr.get("place")
            if not place:
                continue
            cum_by_track.setdefault(place, {"races": 0, "hits": 0})
            cum_by_track[place]["races"] += int(tr.get("races", 0) or 0)
            cum_by_track[place]["hits"] += int(tr.get("hits", 0) or 0)

    tracks_list = []
    for place, v in sorted(cum_by_track.items(), key=lambda x: x[0]):
        r = int(v.get("races", 0) or 0)
        hi = int(v.get("hits", 0) or 0)
        hr = (hi / r * 100.0) if r else 0.0
        tracks_list.append(
            {
                "place": place,
                "races": r,
                "hits": hi,
                "hit_rate": round(hr, 1),
            }
        )

    cumulative = {
        "races": total_races,
        "hits": total_hits,
        "hit_rate": round(total_hit_rate, 1),
        "history_days": len(history),
        "tracks": tracks_list,  # ←開催場別の累計（トップページでそのまま使える）
        "last_updated": datetime.now().isoformat(timespec="seconds"),
    }

    cum_path = OUT_DIR / "pred_hit_cumulative.json"
    cum_path.write_text(json.dumps(cumulative, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] wrote {history_path}")
    print(f"[OK] wrote {cum_path}")


if __name__ == "__main__":
    main()
