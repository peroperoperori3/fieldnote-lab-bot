import os, re, json, time
from datetime import datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent":"Mozilla/5.0", "Accept-Language":"ja,en;q=0.8"}
MARKS = ["◎","〇","▲","△","☆"]

# keiba.go.jp babaCode（帯広は対象外）
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

def parse_keibablood_tables_top5(html: str):
    """
    keibablood の指数表からレースごとの上位5頭（馬番/馬名/指数）を作る
    """
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
                "idx": float(midx.group()),
            })
        if rows:
            rows.sort(key=lambda x:(-x["idx"], x["umaban"]))
            races[rno] = rows[:5]
    return races

def parse_top3_from_racemark(html_text: str):
    """
    keiba.go.jp RaceMarkTable から上位3頭（着順/馬番/馬名）を拾う
    """
    soup = BeautifulSoup(html_text, "lxml")
    top = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all(["th","td"])]
        if len(tds) < 4:
            continue
        pos = tds[0]
        if not re.fullmatch(r"\d+", pos):
            continue

        umaban = None
        name = None
        if len(tds) >= 4 and re.fullmatch(r"\d+", tds[2]):
            umaban = int(tds[2]); name = tds[3]
        elif len(tds) >= 3 and re.fullmatch(r"\d+", tds[1]):
            umaban = int(tds[1]); name = tds[2]
        else:
            continue

        if not name:
            continue
        top.append({"rank": int(pos), "umaban": umaban, "name": name})
        if len(top) >= 3:
            break
    return top

def esc(s):
    import html
    return html.escape(str(s))

def idx_color(v: float) -> str:
    if v >= 75: return "#b91c1c"
    if v >= 68: return "#c2410c"
    if v >= 60: return "#1d4ed8"
    if v >= 55: return "#0f766e"
    return "#374151"

def badge(text: str, bg: str, fg: str="#111827") -> str:
    return (f"<span style='display:inline-block;padding:4px 10px;border-radius:999px;"
            f"background:{bg};color:{fg};font-weight:900;font-size:12px;letter-spacing:.02em;'>"
            f"{esc(text)}</span>")

def section_title(left: str, right_badge: str, bg: str) -> str:
    return (f"<div style='display:flex;align-items:center;justify-content:space-between;"
            f"padding:10px 12px;border-radius:12px;background:{bg};margin:10px 0 8px;'>"
            f"<strong style='font-size:14px;'>{esc(left)}</strong>"
            f"{right_badge}"
            f"</div>")

def build_result_html(track: str, yyyymmdd: str, races_block: list, baba: int):
    """
    races_block:
      [
        { race_no: int, top5:[...], top3:[...] or None }
      ]
    """
    title = f"{yyyymmdd[0:4]}.{yyyymmdd[4:6]}.{yyyymmdd[6:8]} {track}競馬 結果"

    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"

    parts = []
    parts.append("<div style='max-width:980px;margin:0 auto;line-height:1.7;'>")
    parts.append(f"<h2 style='margin:10px 0 10px;'>{esc(title)}</h2>")

    for r in races_block:
        rno = int(r["race_no"])
        top5 = r.get("top5", [])[:5]
        top3 = r.get("top3", None)  # None or list

        pred = []
        for i, h in enumerate(top5):
            pred.append({
                "mark": MARKS[i],
                "umaban": int(h["umaban"]),
                "name": str(h["name"]),
                "idx": float(h["idx"]),
            })
        pred_by_umaban = {x["umaban"]: x for x in pred}

        # カード枠
        parts.append(
            "<div style='margin:16px 0 18px;padding:12px 12px;"
            "border:1px solid #e5e7eb;border-radius:14px;background:#ffffff;'>"
        )

        # 見出し（結果だけ）
        head = f"{rno}R"
        parts.append(
            "<div style='display:flex;align-items:baseline;gap:10px;'>"
            f"<div style='font-size:18px;font-weight:900;color:#111827;'>{esc(head)}</div>"
            "</div>"
        )

        # --- 結果セクション（赤系） ---
        parts.append(section_title("結果（1〜3着）", badge("RESULT", "#fecaca"), "#fff1f2"))
        parts.append("<div style='overflow-x:auto;'>")
        parts.append("<table style='width:100%;border-collapse:collapse;margin-bottom:10px;'>")
        parts.append(
            "<thead><tr>"
            "<th style='border-bottom:2px solid #991b1b;padding:8px;text-align:center;white-space:nowrap;'>着</th>"
            "<th style='border-bottom:2px solid #991b1b;padding:8px;text-align:center;white-space:nowrap;'>馬番</th>"
            "<th style='border-bottom:2px solid #991b1b;padding:8px;text-align:left;'>馬名</th>"
            "<th style='border-bottom:2px solid #991b1b;padding:8px;text-align:center;white-space:nowrap;'>予想印</th>"
            "<th style='border-bottom:2px solid #991b1b;padding:8px;text-align:right;white-space:nowrap;'>予想指数</th>"
            "</tr></thead><tbody>"
        )

        if isinstance(top3, list) and top3:
            for x in top3:
                u = int(x["umaban"])
                nm = x["name"]
                p = pred_by_umaban.get(u)
                mark = p["mark"] if p else "—"
                idxv = p["idx"] if p else None
                idx_txt = f"{idxv:.2f}" if isinstance(idxv,(int,float)) else "—"
                col = idx_color(idxv) if isinstance(idxv,(int,float)) else "#374151"
                parts.append(
                    "<tr>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:center;font-weight:900;'>{x['rank']}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:center;'>{u}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:left;font-weight:750;'>{esc(nm)}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:center;font-weight:900;'>{esc(mark)}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:right;font-weight:900;color:{col};'>{esc(idx_txt)}</td>"
                    "</tr>"
                )
        else:
            parts.append("<tr><td colspan='5' style='padding:10px;color:#6b7280;'>結果取得できませんでした</td></tr>")

        parts.append("</tbody></table></div>")

        # --- 予想セクション（青系） ---
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
        for i, p in enumerate(pred):
            bg = "#ffffff" if i % 2 == 0 else "#f8fafc"
            parts.append(
                f"<tr style='background:{bg};'>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;font-weight:900;'>{esc(p['mark'])}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;font-variant-numeric:tabular-nums;'>{p['umaban']}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:left;font-weight:750;'>{esc(p['name'])}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:right;font-weight:900;color:{idx_color(p['idx'])};font-variant-numeric:tabular-nums;'>{p['idx']:.2f}</td>"
                f"</tr>"
            )
        parts.append("</tbody></table></div>")

        parts.append("</div>")  # card end
        time.sleep(0.05)

    parts.append("</div>")
    return title, "\n".join(parts)

def main():
    yyyymmdd = os.environ.get("DATE") or datetime.now().strftime("%Y%m%d")
    os.makedirs("output", exist_ok=True)

    active = detect_active_tracks_keibago(yyyymmdd)
    print(f"[INFO] active_tracks = {active}")

    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"

    for track in active:
        baba = BABA_CODE.get(track)
        code = KEIBABLOOD_CODE.get(track)
        if not baba or not code:
            print(f"[SKIP] {track}: code missing")
            continue

        # keibablood の「その日その場」のページを探す（-1..-12）
        kb_races = None
        kb_url_used = None
        for i in range(1, 13):
            kb_url = f"https://keibablood.com/{yyyymmdd}{code}-{i}/"
            kb_html = fetch(kb_url)
            if not kb_html:
                continue
            races = parse_keibablood_tables_top5(kb_html)
            if races:
                kb_races = races
                kb_url_used = kb_url
                break

        if not kb_races:
            print(f"[SKIP] {track}: keibablood 未発見")
            continue

        races_block = []
        for rno in sorted(kb_races.keys()):
            top5 = kb_races[rno]

            # keiba.go.jp 結果（上位3）
            rm_url = f"https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceMarkTable?k_babaCode={baba}&k_raceDate={date_slash}&k_raceNo={rno}"
            rm_html = fetch(rm_url)
            top3 = parse_top3_from_racemark(rm_html) if rm_html else None

            races_block.append({
                "race_no": rno,
                "top5": top5,
                "top3": top3,
            })

            time.sleep(0.08)

        title, html = build_result_html(track, yyyymmdd, races_block, baba)

        out = {
            "date": yyyymmdd,
            "place": track,
            "place_code": code,
            "title": title,
            "races": races_block,
            "source": {
                "keibablood_url": kb_url_used,
                "keiba_gojp": "RaceMarkTable"
            },
            "generated_at": datetime.now().isoformat(timespec="seconds")
        }

        json_path = Path("output") / f"result_{yyyymmdd}_{code}.json"
        html_path = Path("output") / f"result_{yyyymmdd}_{code}.html"
        json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path.write_text(html, encoding="utf-8")
        print(f"[OK] {track} -> {json_path.name} / {html_path.name}")

if __name__ == "__main__":
    main()
