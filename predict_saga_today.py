import re
import json
import requests
from datetime import datetime
from bs4 import BeautifulSoup

DATE = "2026-01-17"
PLACE_NAME = "佐賀"
PLACE_CODE = "55"

KEIBABLOOD_URL = "https://keibablood.com/2026011755-2/"
KAISEKISYA_SAGA_JOCKEY_URL = "https://www.kaisekisya.net/local/jockey/saga.html"

SIGNS = ["◎", "○", "▲", "△", "☆"]

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

def fetch(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=30)
    print(f"[GET] {url}  status={r.status_code}  bytes={len(r.content)}")
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return r.text

# ---------- 解析者（佐賀：騎手成績一覧） ----------
def parse_kaisekisya_jockey_saga(html: str):
    soup = BeautifulSoup(html, "lxml")

    target_table = None
    for t in soup.find_all("table"):
        head_txt = t.get_text(" ", strip=True)
        if ("勝率" in head_txt) and ("連対率" in head_txt) and ("三連対率" in head_txt):
            target_table = t
            break

    if not target_table:
        return {}

    rows = target_table.find_all("tr")
    if len(rows) < 2:
        return {}

    header_cells = rows[0].find_all(["th", "td"])
    headers = [c.get_text(" ", strip=True) for c in header_cells]

    def find_col(keys):
        for i, h in enumerate(headers):
            for k in keys:
                if k in h:
                    return i
        return None

    c_name = 0
    c_win = find_col(["勝率"])
    c_quin = find_col(["連対率"])
    c_tri = find_col(["三連対率"])

    if None in (c_win, c_quin, c_tri):
        return {}

    table = {}
    for tr in rows[1:]:
        tds = tr.find_all(["td", "th"])
        if not tds:
            continue

        vals = [td.get_text(" ", strip=True) for td in tds]
        mx = max(c_name, c_win, c_quin, c_tri)
        if len(vals) <= mx:
            continue

        name = re.sub(r"\s+", "", vals[c_name])

        def pct(x):
            m = re.search(r"([\d.]+)\s*%", x)
            return float(m.group(1)) if m else None

        win = pct(vals[c_win])
        quin = pct(vals[c_quin])
        tri = pct(vals[c_tri])

        if name and (win is not None) and (quin is not None) and (tri is not None):
            table[name] = (win, quin, tri)

    return table

def jockey_points(win: float, quin: float, tri: float) -> int:
    raw = win * 0.45 + quin * 0.35 + tri * 0.20
    return int(round(raw / 4.0))

def norm_jockey3(s: str) -> str:
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[◀◁▶▷]+", "", s)
    s = re.sub(r"[()（）]", "", s)
    return s[:3]

def match_jockey_by3(j3: str, stats: dict):
    for full, rates in stats.items():
        if full.startswith(j3):
            return rates
    return None

# ---------- keibablood（指数表） ----------
def parse_keibablood_tables(html: str):
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    picked = []

    for t in tables:
        first = t.find("tr")
        if not first:
            continue
        headers = [c.get_text(" ", strip=True) for c in first.find_all(["th", "td"])]
        hj = " ".join(headers)
        if ("指数" in hj) and ("馬名" in hj) and ("騎手" in hj) and ("番" in hj):
            picked.append((t, headers))

    def idx_of(headers, key):
        for i, h in enumerate(headers):
            if key in h:
                return i
        return None

    races = {}
    for race_no, (t, headers) in enumerate(picked, start=1):
        i_ban = idx_of(headers, "番")
        i_idx = idx_of(headers, "指数")
        i_name = idx_of(headers, "馬名")
        i_jok = idx_of(headers, "騎手")
        if None in (i_ban, i_idx, i_name, i_jok):
            continue

        rows = []
        for tr in t.find_all("tr")[1:]:
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue

            vals = [c.get_text(" ", strip=True) for c in cells]
            if len(vals) <= max(i_ban, i_idx, i_name, i_jok):
                continue

            mban = re.search(r"\d+", vals[i_ban])
            midx = re.search(r"\d+", vals[i_idx])
            if not (mban and midx):
                continue

            jockey = re.sub(r"[◀◁▶▷\s]+", "", vals[i_jok])

            rows.append({
                "umaban": int(mban.group(0)),
                "name": vals[i_name].strip(),
                "base_index": int(midx.group(0)),
                "jockey": jockey,
            })

        if rows:
            races[race_no] = rows

    return races

def build_predictions():
    kb_races = parse_keibablood_tables(fetch(KEIBABLOOD_URL))
    jockey_stats = parse_kaisekisya_jockey_saga(fetch(KAISEKISYA_SAGA_JOCKEY_URL))

    predictions = []
    for rno in sorted(kb_races.keys()):
        horses = []
        for h in kb_races[rno]:
            rates = match_jockey_by3(norm_jockey3(h["jockey"]), jockey_stats)
            add = jockey_points(*rates) if rates else 0
            score = h["base_index"] + add
            horses.append({**h, "jockey_add": add, "score": score})

        horses.sort(key=lambda x: (-x["score"], -x["base_index"], x["umaban"]))
        top5 = horses[:5]

        picks = []
        for i, h in enumerate(top5):
            picks.append({
                "mark": SIGNS[i],
                "umaban": h["umaban"],
                "name": h["name"],
                "score": h["score"],
                "base_index": h["base_index"],
                "jockey": h["jockey"],
                "jockey_add": h["jockey_add"],
            })

        predictions.append({
            "race_no": rno,
            "picks": picks
        })

    return predictions

def render_text(title: str, preds) -> str:
    lines = [title]
    for race in preds:
        lines.append("")
        lines.append(f"{race['race_no']}R")
        for p in race["picks"]:
            lines.append(f"{p['mark']} {p['umaban']} {p['name']} {p['score']}")
    lines.append("")
    return "\n".join(lines)

def render_html(title: str, preds) -> str:
    """
    - レースごとにテーブル化
    - 指数(score)に応じてセル背景をグラデ風に色付け
    - スマホでも見やすい（横スクロールなし / 余白調整）
    """

    def esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def clamp(x, lo, hi):
        return max(lo, min(hi, x))

    # score を 0..1 に正規化して色（淡→濃）を決める
    # ここでは「そのレース内のmin..max」を使う（相対評価で見やすい）
    def score_to_style(score: int, smin: int, smax: int) -> str:
        if smax <= smin:
            t = 0.5
        else:
            t = (score - smin) / (smax - smin)
        t = clamp(t, 0.0, 1.0)

        # 背景色：低い=薄いグレー、高い=薄い黄色→オレンジ寄り
        # tに応じて少し濃くする（読みやすさ優先で“薄め”）
        # 例: t=0 => #f5f5f5, t=1 => #ffe3a3
        r = int(245 + (255 - 245) * t)
        g = int(245 + (227 - 245) * t)  # 少し下げる
        b = int(245 + (163 - 245) * t)  # さらに下げる
        r, g, b = clamp(r, 0, 255), clamp(g, 0, 255), clamp(b, 0, 255)

        # 文字は基本黒でOK（淡色なので）
        return f"background: rgb({r},{g},{b}); font-weight: 700;" if t > 0.85 else f"background: rgb({r},{g},{b});"

    css = """
<style>
.fn-predict-wrap{max-width:780px;margin:0 auto;}
.fn-title{font-size:22px;line-height:1.3;margin:8px 0 18px;font-weight:800;}
.fn-race{margin:18px 0 22px;padding:14px;border:1px solid #eee;border-radius:14px;background:#fff;box-shadow:0 2px 10px rgba(0,0,0,.04);}
.fn-race h3{margin:0 0 10px;font-size:18px;font-weight:800;}
.fn-table{width:100%;border-collapse:separate;border-spacing:0;overflow:hidden;border-radius:12px;border:1px solid #eee;}
.fn-table th,.fn-table td{padding:10px 10px;border-bottom:1px solid #eee;font-size:15px;vertical-align:middle;}
.fn-table th{background:#fafafa;font-weight:800;text-align:left;}
.fn-table tr:last-child td{border-bottom:none;}
.fn-mark{width:42px;text-align:center;font-weight:900;}
.fn-umaban{width:54px;text-align:center;font-variant-numeric:tabular-nums;}
.fn-score{width:84px;text-align:center;font-variant-numeric:tabular-nums;}
.fn-name{font-weight:700;}
.fn-note{color:#666;font-size:12px;margin-top:10px;}
/* モバイル調整 */
@media (max-width: 520px){
  .fn-title{font-size:20px;}
  .fn-table th,.fn-table td{padding:9px 8px;font-size:14px;}
  .fn-race{padding:12px;border-radius:12px;}
}
</style>
"""

    parts = [css, '<div class="fn-predict-wrap">', f'<div class="fn-title">{esc(title)}</div>']

    for race in preds:
        rno = race["race_no"]
        picks = race["picks"]

        scores = [p["score"] for p in picks] if picks else [0]
        smin, smax = min(scores), max(scores)

        parts.append('<div class="fn-race">')
        parts.append(f"<h3>{rno}R</h3>")
        parts.append('<table class="fn-table">')
        parts.append("<thead><tr>"
                     "<th class='fn-mark'>印</th>"
                     "<th class='fn-umaban'>馬番</th>"
                     "<th>馬名</th>"
                     "<th class='fn-score'>指数</th>"
                     "</tr></thead>")
        parts.append("<tbody>")

        for p in picks:
            style = score_to_style(p["score"], smin, smax)
            parts.append("<tr>")
            parts.append(f"<td class='fn-mark'>{esc(p['mark'])}</td>")
            parts.append(f"<td class='fn-umaban'>{p['umaban']}</td>")
            parts.append(f"<td class='fn-name'>{esc(p['name'])}</td>")
            parts.append(f"<td class='fn-score' style='{style}'>{p['score']}</td>")
            parts.append("</tr>")

        parts.append("</tbody></table>")
        parts.append("<div class='fn-note'>※ 指数セルは「そのレース内で高いほど濃く」表示（相対）</div>")
        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)

def main():
    title = f"{DATE.replace('-', '.')} {PLACE_NAME}競馬 予想"
    preds = build_predictions()

    # 出力フォルダ
    import os
    os.makedirs("output", exist_ok=True)

    ymd = DATE.replace("-", "")
    txt_path = f"output/predict_{ymd}_{PLACE_CODE}.txt"
    json_path = f"output/predict_{ymd}_{PLACE_CODE}.json"
    html_path = f"output/predict_{ymd}_{PLACE_CODE}.html"

    txt = render_text(title, preds)
    html = render_html(title, preds)

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "date": DATE,
            "place": PLACE_NAME,
            "place_code": PLACE_CODE,
            "title": title,
            "predictions": preds,
            "source": {
                "keibablood_url": KEIBABLOOD_URL,
                "kaisekisya_url": KAISEKISYA_SAGA_JOCKEY_URL
            },
            "generated_at": datetime.now().isoformat(timespec="seconds")
        }, f, ensure_ascii=False, indent=2)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print("\n" + txt)
    print(f"Saved: {txt_path}")
    print(f"Saved: {json_path}")
    print(f"Saved: {html_path}")

if __name__ == "__main__":
    main()
