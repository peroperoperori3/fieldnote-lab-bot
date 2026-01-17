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
    # keiba_kesaki_bot の予想HTMLに寄せた（インラインstyle中心）
    # - 見出し h2 / h3
    # - テーブル：太めの見出し下線 + 交互行
    # - 指数セル：80/70/60で色分け（背景ちょい）
    import html as _html

    parts = []
    parts.append(f"<h2>{_html.escape(title)}</h2>")

    for race in preds:
        rno = race["race_no"]
        parts.append(f"<h3>{rno}R</h3>")

        picks = race["picks"]  # 5頭

        parts.append('<table style="width:100%;border-collapse:collapse;">')
        parts.append(
            '<thead><tr>'
            '<th style="border-bottom:2px solid #111827;padding:8px;text-align:center;white-space:nowrap;">印</th>'
            '<th style="border-bottom:2px solid #111827;padding:8px;text-align:center;white-space:nowrap;">馬番</th>'
            '<th style="border-bottom:2px solid #111827;padding:8px;text-align:left;">馬名</th>'
            '<th style="border-bottom:2px solid #111827;padding:8px;text-align:right;white-space:nowrap;">指数</th>'
            '</tr></thead><tbody>'
        )

        for i, p in enumerate(picks):
            mark = str(p.get("mark", ""))
            umaban = str(p.get("umaban", ""))
            name = str(p.get("name", ""))
            score = p.get("score", 0)

            try:
                sc = float(score)
            except:
                sc = 0.0

            rowbg = "#ffffff" if i % 2 == 0 else "#f8fafc"

            # 指数セルだけ色（keiba_kesaki_botと同じ思想）
            if sc >= 80:
                cellbg = "#fee2e2"   # 赤薄
            elif sc >= 70:
                cellbg = "#fff7ed"   # オレンジ薄
            elif sc >= 60:
                cellbg = "#fefce8"   # 黄薄
            else:
                cellbg = rowbg

            parts.append("<tr>")
            parts.append(
                f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;text-align:center;background:{rowbg};font-weight:800;">'
                f'{_html.escape(mark)}</td>'
            )
            parts.append(
                f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;text-align:center;background:{rowbg};font-variant-numeric:tabular-nums;">'
                f'{_html.escape(umaban)}</td>'
            )
            parts.append(
                f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;text-align:left;background:{rowbg};font-weight:700;">'
                f'{_html.escape(name)}</td>'
            )
            parts.append(
                f'<td style="padding:8px;border-bottom:1px solid #e5e7eb;text-align:right;background:{cellbg};font-variant-numeric:tabular-nums;font-weight:800;">'
                f'{sc:.2f}</td>'
            )
            parts.append("</tr>")

        parts.append("</tbody></table>")
        parts.append('<hr style="margin:18px 0;border:none;border-top:1px solid #e5e7eb;">')

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
