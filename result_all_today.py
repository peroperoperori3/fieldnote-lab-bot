import os, re, json, time
from pathlib import Path
from datetime import datetime
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent":"Mozilla/5.0", "Accept-Language":"ja,en;q=0.8"}
MARKS = ["◎","〇","▲","△","☆"]

# keiba.go.jp babaCode
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

def fetch(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=25)
    if r.status_code != 200:
        return ""
    r.encoding = r.apparent_encoding
    return r.text

def parse_top3_from_racemark(html_text: str):
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
        top.append({"pos": int(pos), "umaban": umaban, "name": name})
        if len(top) >= 3:
            break
    return top

def parse_race_names_from_racelist(html_text: str):
    soup = BeautifulSoup(html_text, "lxml")
    m = {}
    for tr in soup.find_all("tr"):
        row_txt = tr.get_text(" ", strip=True)
        mo = re.search(r"(\d{1,2})R", row_txt)
        if not mo:
            continue
        rn = int(mo.group(1))
        a = tr.find("a")
        if not a:
            continue
        name = a.get_text(strip=True)
        if not name:
            continue
        if any(x in name for x in ["オッズ","映像","成績","払戻","当日メニュー"]):
            continue
        if rn not in m:
            m[rn] = name
    return m

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

def build_result_html(track: str, yyyymmdd: str, races: list, race_names: dict, babaCode: int):
    # タイトル（あなた指定フォーマット）
    title = f"{yyyymmdd[0:4]}.{yyyymmdd[4:6]}.{yyyymmdd[6:8]} {track}競馬 結果"

    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"

    parts = []
    parts.append("<div style='max-width:980px;margin:0 auto;line-height:1.7;'>")
    parts.append(f"<h2 style='margin:10px 0 10px;'>{esc(title)}</h2>")

    for r in races:
        rno = int(r.get("race_no"))
        pred = r.get("pred_top5", [])[:5]
        pred_by_umaban = {x["umaban"]: x for x in pred}

        # 結果上位3
        url = f"https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceMarkTable?k_babaCode={babaCode}&k_raceDate={date_slash}&k_raceNo={rno}"
        html_rm = fetch(url)
        top3 = parse_top3_from_racemark(html_rm) if html_rm else []
        pos_by_umaban = {x["umaban"]: x["pos"] for x in top3}

        # レース名は“あってもいい”けど、無くてもOK（今回は出してOKにしてる）
        rname = race_names.get(rno, "")
        head = f"{rno}R" + (f" {rname}" if rname else "")

        parts.append(
            "<div style='margin:16px 0 18px;padding:12px 12px;"
            "border:1px solid #e5e7eb;border-radius:14px;background:#ffffff;'>"
        )

        # 見出し（RESULT/PREDは残す：結果ページの見やすさのため）
        parts.append(
            "<div style='display:flex;align-items:baseline;gap:10px;'>"
            f"<div style='font-size:18px;font-weight:900;color:#111827;'>{esc(head)}</div>"
            f"{badge('結果', '#fee2e2')}"
            f"{badge('予想', '#dbeafe')}"
            "</div>"
        )

        # --- 結果（赤） ---
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
        if top3:
            for x in top3:
                u = x["umaban"]
                nm = x["name"]
                p = pred_by_umaban.get(u)
                mark = p["mark"] if p else "—"
                idx = p["idx"] if p else None
                idx_txt = f"{idx:.2f}" if isinstance(idx,(int,float)) else "—"
                col = idx_color(idx) if isinstance(idx,(int,float)) else "#374151"
                parts.append(
                    "<tr>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:center;font-weight:900;'>{x['pos']}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:center;'>{u}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:left;font-weight:750;'>{esc(nm)}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:center;font-weight:900;'>{esc(mark)}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:right;font-weight:900;color:{col};'>{esc(idx_txt)}</td>"
                    "</tr>"
                )
        else:
            parts.append("<tr><td colspan='5' style='padding:10px;color:#6b7280;'>結果取得できませんでした</td></tr>")
        parts.append("</tbody></table></div>")

        # --- 予想（青） ---
        parts.append(section_title("予想（指数上位5）", badge("PRED", "#bfdbfe"), "#eff6ff"))
        parts.append("<div style='overflow-x:auto;'>")
        parts.append("<table style='width:100%;border-collapse:collapse;'>")
        parts.append(
            "<thead><tr>"
            "<th style='border-bottom:2px solid #1d4ed8;padding:8px;text-align:center;white-space:nowrap;'>印</th>"
            "<th style='border-bottom:2px solid #1d4ed8;padding:8px;text-align:center;white-space:nowrap;'>馬番</th>"
            "<th style='border-bottom:2px solid #1d4ed8;padding:8px;text-align:left;'>馬名</th>"
            "<th style='border-bottom:2px solid #1d4ed8;padding:8px;text-align:right;white-space:nowrap;'>指数</th>"
            "<th style='border-bottom:2px solid #1d4ed8;padding:8px;text-align:center;white-space:nowrap;'>結果</th>"
            "</tr></thead><tbody>"
        )
        for i, p in enumerate(pred):
            bg = "#ffffff" if i % 2 == 0 else "#f8fafc"
            pos = pos_by_umaban.get(p["umaban"])
            pos_txt = f"{pos}着" if pos else "—"
            parts.append(
                f"<tr style='background:{bg};'>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;font-weight:900;'>{esc(p['mark'])}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;'>{p['umaban']}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:left;font-weight:750;'>{esc(p['name'])}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:right;font-weight:900;color:{idx_color(p['idx'])};'>{p['idx']:.2f}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;font-weight:900;'>{esc(pos_txt)}</td>"
                f"</tr>"
            )
        parts.append("</tbody></table></div>")

        parts.append("</div>")  # card end
        time.sleep(0.08)

    parts.append("</div>")
    return title, "\n".join(parts)

def main():
    # DATE は env があればそれ、なければ今日
    today = datetime.now().strftime("%Y%m%d")
    yyyymmdd = os.environ.get("DATE", today)

    os.makedirs("output", exist_ok=True)

    # 今日の予想json（全場）を読む
    pred_jsons = sorted(Path("output").glob(f"predict_{yyyymmdd}_*.json"))
    if not pred_jsons:
        raise RuntimeError(f"output に predict_{yyyymmdd}_*.json がありません（先に予想生成してください）")

    made = 0
    for p in pred_jsons:
        d = json.loads(p.read_text(encoding="utf-8"))
        track = d.get("place") or ""
        place_code = str(d.get("place_code") or "")

        babaCode = BABA_CODE.get(track)
        if not babaCode:
            print(f"[SKIP] {track}: keiba.go.jp babaCode 不明")
            continue

        date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"

        # レース名（まとめて）
        racelist_url = f"https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceList?k_babaCode={babaCode}&k_raceDate={date_slash}"
        rl_html = fetch(racelist_url)
        race_names = parse_race_names_from_racelist(rl_html) if rl_html else {}

        # 予想 top5 を作る（result用に整形）
        races = []
        for race in d.get("predictions", []):
            rno = int(race["race_no"])
            picks = race["picks"][:5]
            pred_top5 = []
            for x in picks:
                pred_top5.append({
                    "mark": x.get("mark",""),
                    "umaban": int(x.get("umaban", 0)),
                    "name": str(x.get("name","")),
                    "idx": float(x.get("score", 0.0)),
                })
            races.append({"race_no": rno, "pred_top5": pred_top5})

        title, html = build_result_html(track, yyyymmdd, races, race_names, babaCode)

        out_json = Path("output") / f"result_{yyyymmdd}_{place_code}.json"
        out_html = Path("output") / f"result_{yyyymmdd}_{place_code}.html"

        out_json.write_text(json.dumps({
            "date": yyyymmdd,
            "place": track,
            "place_code": place_code,
            "title": title,
            "races": races,
            "generated_at": datetime.now().isoformat(timespec="seconds")
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        out_html.write_text(html, encoding="utf-8")

        print(f"[OK] {track} saved -> {out_html.name}")
        made += 1

    print(f"=== DONE result: tracks={made} ===")

if __name__ == "__main__":
    main()
