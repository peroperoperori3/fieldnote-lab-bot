import os, re, json
from datetime import datetime
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent":"Mozilla/5.0", "Accept-Language":"ja,en;q=0.8"}
MARKS = ["◎","〇","▲","△","☆"]

DATE = "2026-01-17"
PLACE_NAME = "佐賀"
PLACE_CODE = "55"      # keibablood側（あなたの運用コード）
KEIBAGO_BABACODE = 32  # keiba.go.jp babaCode（佐賀=32）

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

def ymd_dash_to_slash(dash: str) -> str:
    y,m,d = dash.split("-")
    return f"{y}/{m}/{d}"

def load_predict_for_date_place():
    if not os.path.isdir("output"):
        raise SystemExit("outputフォルダがありません")
    ymd = DATE.replace("-", "")
    path = os.path.join("output", f"predict_{ymd}_{PLACE_CODE}.json")
    if not os.path.exists(path):
        # なければ最新のpredictを拾う（保険）
        files = [f for f in os.listdir("output") if f.startswith("predict_") and f.endswith(f"_{PLACE_CODE}.json")]
        files.sort()
        if not files:
            raise SystemExit("predict_*.jsonがありません")
        path = os.path.join("output", files[-1])
    data = json.loads(open(path, "r", encoding="utf-8").read())
    return path, data

def build_result_html(title: str, pred_data: dict, top3_by_race: dict):
    parts = []
    parts.append("<div style='max-width:980px;margin:0 auto;line-height:1.7;'>")
    parts.append(f"<h2 style='margin:10px 0 10px;'>{esc(title)}</h2>")

    for race in pred_data["predictions"]:
        rno = int(race["race_no"])
        picks = race["picks"]  # 予想上位5
        pred_by_umaban = {int(p["umaban"]): p for p in picks}

        top3 = top3_by_race.get(rno, [])
        pos_by_umaban = {int(x["umaban"]): int(x["pos"]) for x in top3}

        # カード枠
        parts.append(
            "<div style='margin:16px 0 18px;padding:12px 12px;"
            "border:1px solid #e5e7eb;border-radius:14px;background:#ffffff;'>"
        )

        # 見出し（1Rだけ）
        head = f"{rno}R"
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
                u = int(x["umaban"])
                nm = x["name"]
                p = pred_by_umaban.get(u)
                mark = p["mark"] if p else "—"
                idx = float(p["score"]) if p else None
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
        for i, p in enumerate(picks):
            bg = "#ffffff" if i % 2 == 0 else "#f8fafc"
            u = int(p["umaban"])
            sc = float(p["score"])
            pos = pos_by_umaban.get(u)
            pos_txt = f"{pos}着" if pos else "—"
            parts.append(
                f"<tr style='background:{bg};'>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;font-weight:900;'>{esc(p['mark'])}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;'>{u}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:left;font-weight:750;'>{esc(p['name'])}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:right;font-weight:900;color:{idx_color(sc)};'>{sc:.2f}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;font-weight:900;'>{esc(pos_txt)}</td>"
                f"</tr>"
            )
        parts.append("</tbody></table></div>")

        parts.append("</div>")  # card end

    parts.append("</div>")
    return "\n".join(parts)

def main():
    pred_path, pred_data = load_predict_for_date_place()

    # 結果記事タイトル（別記事）
    title = f"{DATE.replace('-','.') } {PLACE_NAME}競馬 結果"

    date_slash = ymd_dash_to_slash(DATE)

    top3_by_race = {}
    for race in pred_data["predictions"]:
        rno = int(race["race_no"])
        url = f"https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceMarkTable?k_babaCode={KEIBAGO_BABACODE}&k_raceDate={date_slash}&k_raceNo={rno}"
        r = requests.get(url, headers=UA, timeout=25)
        if r.status_code == 200:
            top3_by_race[rno] = parse_top3_from_racemark(r.text)
        else:
            top3_by_race[rno] = []

    html = build_result_html(title, pred_data, top3_by_race)

    os.makedirs("output", exist_ok=True)
    ymd = DATE.replace("-", "")

    json_path = f"output/result_{ymd}_{PLACE_CODE}.json"
    html_path = f"output/result_{ymd}_{PLACE_CODE}.html"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "date": pred_data.get("date", DATE),
            "place": pred_data.get("place", PLACE_NAME),
            "place_code": pred_data.get("place_code", PLACE_CODE),
            "title": title,
            "generated_at": datetime.now().isoformat(timespec="seconds")
        }, f, ensure_ascii=False, indent=2)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print("Saved:", json_path)
    print("Saved:", html_path)

if __name__ == "__main__":
    main()
