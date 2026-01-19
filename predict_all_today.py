import os, re, json, time
from datetime import datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent":"Mozilla/5.0", "Accept-Language":"ja,en;q=0.8"}
MARKS5 = ["◎","〇","▲","△","☆"]

# keiba.go.jp babaCode（開催判定用）※帯広除外
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

# kaisekisya（開催場別）
KAISEKISYA_JOCKEY_URL = {
  "門別": "https://www.kaisekisya.net/local/jockey/monbetsu.html",
  "盛岡": "https://www.kaisekisya.net/local/jockey/morioka.html",
  "水沢": "https://www.kaisekisya.net/local/jockey/mizusawa.html",
  "浦和": "https://www.kaisekisya.net/local/jockey/urawa.html",
  "船橋": "https://www.kaisekisya.net/local/jockey/funabashi.html",
  "大井": "https://www.kaisekisya.net/local/jockey/ooi.html",
  "川崎": "https://www.kaisekisya.net/local/jockey/kawasaki.html",
  "金沢": "https://www.kaisekisya.net/local/jockey/kanazawa.html",
  "笠松": "https://www.kaisekisya.net/local/jockey/kasamatsu.html",
  "名古屋": "https://www.kaisekisya.net/local/jockey/nagoya.html",
  "園田": "https://www.kaisekisya.net/local/jockey/sonoda.html",
  "姫路": "https://www.kaisekisya.net/local/jockey/himeji.html",
  "高知": "https://www.kaisekisya.net/local/jockey/kochi.html",
  "佐賀": "https://www.kaisekisya.net/local/jockey/saga.html",
}

def fetch(url: str, debug=False) -> str:
    try:
        r = requests.get(url, headers=UA, timeout=25)
    except Exception as e:
        if debug:
            print(f"[GET] {url}  ERROR={e}")
        return ""
    ct = r.headers.get("Content-Type","")
    if debug:
        print(f"[GET] {url}  status={r.status_code}  ct={ct}  bytes={len(r.content)}")
    if r.status_code != 200:
        return ""
    r.encoding = r.apparent_encoding
    return r.text

def detect_active_tracks_keibago(yyyymmdd: str, debug=False):
    active = []
    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"
    for track, baba in BABA_CODE.items():
        url = f"https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceList?k_babaCode={baba}&k_raceDate={date_slash}"
        html = fetch(url, debug=debug)
        if html and ("1R" in html):
            active.append(track)
            if debug:
                print(f"[ACTIVE] {track} (babaCode={baba})")
        else:
            if debug:
                print(f"[NO] {track} (babaCode={baba})")
        time.sleep(0.08)
    return active

# ====== kaisekisya 解析（result_all_today と同じ） ======
def parse_kaisekisya_jockey_table(html: str):
    soup = BeautifulSoup(html, "lxml")

    target = None
    for t in soup.find_all("table"):
        txt = t.get_text(" ", strip=True)
        if ("勝率" in txt) and ("連対率" in txt) and ("三連対率" in txt):
            target = t
            break
    if not target:
        return {}

    rows = target.find_all("tr")
    if len(rows) < 2:
        return {}

    header_cells = rows[0].find_all(["th","td"])
    headers = [c.get_text(" ", strip=True) for c in header_cells]

    def find_col(keys):
        for i,h in enumerate(headers):
            for k in keys:
                if k in h:
                    return i
        return None

    c_name = 0
    c_win  = find_col(["勝率"])
    c_quin = find_col(["連対率"])
    c_tri  = find_col(["三連対率"])
    if None in (c_win, c_quin, c_tri):
        return {}

    def pct(x):
        m = re.search(r"([\d.]+)\s*%", str(x))
        return float(m.group(1)) if m else None

    stats = {}
    for tr in rows[1:]:
        tds = tr.find_all(["td","th"])
        if not tds:
            continue
        vals = [td.get_text(" ", strip=True) for td in tds]
        mx = max(c_name, c_win, c_quin, c_tri)
        if len(vals) <= mx:
            continue

        name = re.sub(r"\s+", "", vals[c_name])
        win  = pct(vals[c_win])
        quin = pct(vals[c_quin])
        tri  = pct(vals[c_tri])
        if name and (win is not None) and (quin is not None) and (tri is not None):
            stats[name] = (win, quin, tri)

    return stats

def norm_jockey3(s: str) -> str:
    s = re.sub(r"\s+", "", str(s))
    s = re.sub(r"[◀◁▶▷]+", "", s)
    s = re.sub(r"[()（）]", "", s)
    return s[:3]

def match_jockey_by3(j3: str, stats: dict):
    for full, rates in stats.items():
        if full.startswith(j3):
            return rates
    return None

def jockey_add_points(win: float, quin: float, tri: float) -> float:
    # 内部は生floatで加算（丸めない）
    raw = win * 0.45 + quin * 0.35 + tri * 0.20
    return raw / 4.0

# ====== keibablood（指数表 + 騎手） ======
def parse_keibablood_tables(html: str):
    """
    返り値: { race_no: [ {umaban,name,base_index,jockey}, ... ] }
    ※ここではTOP5に絞らない（騎手補正後の並び替えがあるため）
    """
    soup = BeautifulSoup(html, "lxml")
    races = {}

    for t in soup.find_all("table"):
        head = t.find("tr")
        if not head:
            continue
        headers = [c.get_text(" ", strip=True) for c in head.find_all(["th","td"])]
        hj = " ".join(headers)

        # predictは騎手補正したいので「騎手」列も期待する（無い場合は後でfallback）
        if not (("指数" in hj) and ("馬名" in hj) and ("番" in hj)):
            continue

        def idx(k):
            for i,h in enumerate(headers):
                if k in h:
                    return i
            return None

        i_ban, i_name, i_idx, i_jok = idx("番"), idx("馬名"), idx("指数"), idx("騎手")
        if None in (i_ban, i_name, i_idx):
            continue

        rno = len(races) + 1
        rows = []
        for tr in t.find_all("tr")[1:]:
            cells = tr.find_all(["td","th"])
            if not cells:
                continue
            vals = [c.get_text(" ", strip=True) for c in cells]
            if len(vals) <= max(i_ban, i_name, i_idx, (i_jok or 0)):
                # i_jokがNoneのときは無視
                if len(vals) <= max(i_ban, i_name, i_idx):
                    continue

            mban = re.search(r"\d+", vals[i_ban])
            midx = re.search(r"[\d.]+", vals[i_idx])
            if not (mban and midx):
                continue

            jockey = ""
            if i_jok is not None and i_jok < len(vals):
                jockey = re.sub(r"[◀◁▶▷\s]+", "", vals[i_jok])

            rows.append({
                "umaban": int(mban.group()),
                "name": vals[i_name].strip(),
                "base_index": float(midx.group()),
                "jockey": jockey,
            })

        if rows:
            races[rno] = rows

    return races

# ====== 見た目（あなたの「完璧」版） ======
def render_html(title: str, preds) -> str:
    import html as _html

    def esc(s): return _html.escape(str(s))

    def idx_color(v: float) -> str:
        if v >= 75: return "#b91c1c"
        if v >= 68: return "#c2410c"
        if v >= 60: return "#1d4ed8"
        if v >= 55: return "#0f766e"
        return "#374151"

    def badge(text: str, bg: str, fg: str = "#111827") -> str:
        return (
            "<span style='display:inline-block;padding:4px 10px;border-radius:999px;"
            f"background:{bg};color:{fg};font-weight:900;font-size:12px;letter-spacing:.02em;'>"
            f"{esc(text)}</span>"
        )

    def section_title(left: str, right_badge: str, bg: str) -> str:
        return (
            "<div style='display:flex;align-items:center;justify-content:space-between;"
            f"padding:10px 12px;border-radius:12px;background:{bg};margin:10px 0 8px;'>"
            f"<strong style='font-size:14px;'>{esc(left)}</strong>"
            f"{right_badge}"
            "</div>"
        )

    parts = []
    parts.append("<div style='max-width:980px;margin:0 auto;line-height:1.7;'>")
    parts.append(f"<h2 style='margin:10px 0 10px;'>{esc(title)}</h2>")

    for race in preds:
        rno = int(race["race_no"])
        picks = race["picks"]

        parts.append(
            "<div style='margin:16px 0 18px;padding:12px 12px;"
            "border:1px solid #e5e7eb;border-radius:14px;background:#ffffff;'>"
        )

        head = f"{rno}R"
        parts.append(
            "<div style='display:flex;align-items:baseline;gap:10px;'>"
            f"<div style='font-size:18px;font-weight:900;color:#111827;'>{esc(head)}</div>"
            "</div>"
        )

        parts.append(section_title("指数上位5頭", badge("PRED", "#bfdbfe"), "#eff6ff"))
        parts.append("<div style='overflow-x:auto;'>")
        parts.append("<table style='width:100%;border-collapse:collapse;'>")
        parts.append(
            "<thead><tr>"
            "<th style='border-bottom:2px solid #1d4ed8;padding:8px;text-align:center;white-space:nowrap;'>印</th>"
            "<th style='border-bottom:2px solid #1d4ed8;padding:8px;text-align:center;white-space:nowrap;'>馬番</th>"
            "<th style='border-bottom:2px solid #1d4ed8;padding:8px;text-align:left;'>馬名</th>"
            "<th style='border-bottom:2px solid #1d4ed8;padding:8px;text-align:right;white-space:nowrap;'>指数</th>"
            "</tr></thead><tbody>"
        )

        for i, p in enumerate(picks):
            bg = "#ffffff" if i % 2 == 0 else "#f8fafc"
            sc = float(p.get("score", 0.0))
            parts.append(
                f"<tr style='background:{bg};'>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;font-weight:900;'>{esc(p.get('mark',''))}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;font-variant-numeric:tabular-nums;'>{int(p.get('umaban',0))}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:left;font-weight:750;'>{esc(p.get('name',''))}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:right;font-weight:900;color:{idx_color(sc)};font-variant-numeric:tabular-nums;'>{sc:.2f}</td>"
                f"</tr>"
            )

        parts.append("</tbody></table></div>")
        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)

def main():
    yyyymmdd = os.environ.get("DATE") or datetime.now().strftime("%Y%m%d")
    debug = os.environ.get("DEBUG", "").strip() == "1"
    os.makedirs("output", exist_ok=True)

    print(f"[INFO] DATE={yyyymmdd}")

    active = detect_active_tracks_keibago(yyyymmdd, debug=debug)
    print(f"[INFO] active_tracks = {active}")

    # keibablood は「-2」が多いので最優先
    SERIES_ORDER = [2, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]

    for track in active:
        code = KEIBABLOOD_CODE.get(track)
        if not code:
            print(f"[SKIP] {track}: keibablood code unknown")
            continue

        # 騎手成績（predictでも読む！）
        jockey_url = KAISEKISYA_JOCKEY_URL.get(track, "")
        jockey_stats = parse_kaisekisya_jockey_table(fetch(jockey_url, debug=False)) if jockey_url else {}
        if debug:
            print("========== JOCKEY DEBUG ==========")
            print(f"[DEBUG] track={track}")
            print(f"[DEBUG] kaisekisya_url={jockey_url}")
            print(f"[DEBUG] jockey_stats_count={len(jockey_stats)}")
            if jockey_stats:
                smp = list(jockey_stats.items())[:5]
                print("[DEBUG] jockey_stats_sample:")
                for nm,(w,q,t) in smp:
                    print(f"  {nm} -> win={w} quin={q} tri={t}")
            print("==================================")

        found = False
        used_series = None
        picked_url = None
        kb_races = None

        for i in SERIES_ORDER:
            url = f"https://keibablood.com/{yyyymmdd}{code}-{i}/"
            html = fetch(url, debug=debug)
            if not html:
                continue

            races = parse_keibablood_tables(html)
            if not races:
                if debug:
                    print(f"[MISS] {track} series=-{i} : tables not found")
                continue

            found = True
            used_series = i
            picked_url = url
            kb_races = races
            break

        if not found or not kb_races:
            print(f"[SKIP] {track}: keibablood 未発見（-2優先で探索済み）")
            continue

        preds = []
        for rno in sorted(kb_races.keys()):
            # 騎手補正を加算して並び替え → TOP5
            horses_scored = []
            for h in kb_races[rno]:
                j = h.get("jockey","")
                rates = match_jockey_by3(norm_jockey3(j), jockey_stats) if (j and jockey_stats) else None
                add = jockey_add_points(*rates) if rates else 0.0
                score = float(h["base_index"]) + float(add)

                horses_scored.append({
                    "umaban": int(h["umaban"]),
                    "name": h["name"],
                    "jockey": j,
                    "base_index": float(h["base_index"]),
                    "jockey_add": float(add),
                    "score": float(score),
                })

            horses_scored.sort(key=lambda x: (-x["score"], -x["base_index"], x["umaban"]))
            top5 = horses_scored[:5]

            if debug and top5:
                # 参考ログ（上位3だけ）
                for k, hh in enumerate(top5[:3], start=1):
                    j3 = norm_jockey3(hh.get("jockey",""))
                    print(f"[DEBUG] top3 r{rno} horse={hh['name']} jockey={hh.get('jockey','')} j3={j3} base={hh['base_index']} add={hh['jockey_add']} score={hh['score']}")

            picks = []
            for j, hh in enumerate(top5):
                picks.append({
                    "mark": MARKS5[j],
                    "umaban": int(hh["umaban"]),
                    "name": hh["name"],
                    "score": float(hh["score"]),
                    # デバッグ用に残す（wp側表示はしない）
                    "base_index": float(hh["base_index"]),
                    "jockey": hh.get("jockey",""),
                    "jockey_add": float(hh["jockey_add"]),
                })

            preds.append({"race_no": int(rno), "picks": picks})

            time.sleep(0.03)

        title = f"{yyyymmdd[0:4]}.{yyyymmdd[4:6]}.{yyyymmdd[6:8]} {track}競馬 予想"
        out = {
            "date": yyyymmdd,
            "place": track,
            "place_code": code,
            "title": title,
            "predictions": preds,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "source": {
                "keibablood_url": picked_url,
                "keibablood_series_used": used_series,
                "kaisekisya_url": jockey_url
            }
        }

        json_path = Path("output") / f"predict_{yyyymmdd}_{code}.json"
        html_path = Path("output") / f"predict_{yyyymmdd}_{code}.html"

        json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path.write_text(render_html(title, preds), encoding="utf-8")

        print(f"[OK] {track} -> {json_path.name} / {html_path.name}  (keibablood=-{used_series})")

if __name__ == "__main__":
    main()