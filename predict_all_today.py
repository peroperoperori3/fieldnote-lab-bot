import os, re, json, time
from datetime import datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent":"Mozilla/5.0", "Accept-Language":"ja,en;q=0.8"}
SIGNS = ["◎","○","▲","△","☆"]

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

MARKS5 = ["◎","〇","▲","△","☆"]

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
        hj = " ".join(headers)
        if not (("指数" in hj) and ("馬名" in hj) and ("番" in hj)):
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

        # 予想の文字は出さない（あなたの指定）
        parts.append(section_title("指数上位5", badge("PRED", "#bfdbfe"), "#eff6ff"))
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
    os.makedirs("output", exist_ok=True)

    active = detect_active_tracks_keibago(yyyymmdd)
    print(f"[INFO] active_tracks = {active}")

    for track in active:
        code = KEIBABLOOD_CODE.get(track)
        if not code:
            print(f"[SKIP] {track}: keibablood code unknown")
            continue

        found = False
        for i in range(1, 13):
            url = f"https://keibablood.com/{yyyymmdd}{code}-{i}/"
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
                for j, h in enumerate(races[rno]):
                    picks.append({
                        "mark": MARKS5[j],
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

            json_path = Path("output") / f"predict_{yyyymmdd}_{code}.json"
            html_path = Path("output") / f"predict_{yyyymmdd}_{code}.html"

            json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            html_path.write_text(render_html(title, preds), encoding="utf-8")

            print(f"[OK] {track} -> {json_path.name} / {html_path.name}")
            break

        if not found:
            print(f"[SKIP] {track}: keibablood 未発見（URL規則違い or 未掲載）")

if __name__ == "__main__":
    main()
