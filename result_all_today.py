# result.py  (fieldnote-lab-bot)
# 目的：
# - keibablood（指数）+ kichiuma（SP）+ kaisekisya（騎手）で上位5頭を作る
# - keiba.go.jp から結果(1-3着)＆三連複払戻(100円あたり)を「列ベースで厳密」に取得
# - 注目レース（混戦度>=閾値）のみ三連複BOX(5頭=10点)の収支を計算
# - output/ に結果HTML/JSON と pnl_total.json（トップページ表示用）を出力

import os, re, json, time
from datetime import datetime
from pathlib import Path
from itertools import combinations

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "ja,en;q=0.8"}
MARKS5 = ["◎", "〇", "▲", "△", "☆"]

# ===== スコア重み（予想と同じ）=====
SP_W = float(os.environ.get("SP_W", "1.0"))
KB_W = float(os.environ.get("KB_W", "0.10"))
JOCKEY_W = float(os.environ.get("JOCKEY_W", "0.20"))

# ===== 混戦度（予想と同じ：ギャップ方式）=====
KONSEN_NAME = os.environ.get("KONSEN_NAME", "混戦度")
KONSEN_GAP12_MID = float(os.environ.get("KONSEN_GAP12_MID", "0.8"))
KONSEN_GAP15_MID = float(os.environ.get("KONSEN_GAP15_MID", "3.0"))
KONSEN_FOCUS_TH  = float(os.environ.get("KONSEN_FOCUS_TH", "50"))
KONSEN_DEBUG = os.environ.get("KONSEN_DEBUG", "").strip() == "1"
REFUND_DEBUG = os.environ.get("REFUND_DEBUG", "").strip() == "1"

# ===== 注目レース：三連複BOX（上位5頭）=====
BET_ENABLED = os.environ.get("BET_ENABLED", "1").strip() != "0"
BET_UNIT = int(os.environ.get("BET_UNIT", "100"))  # 1点あたり（円）

# keiba.go.jp babaCode（開催判定用）※帯広除外
BABA_CODE = {
  "門別": 36, "盛岡": 10, "水沢": 11, "浦和": 18, "船橋": 19, "大井": 20, "川崎": 21,
  "金沢": 22, "笠松": 23, "名古屋": 24, "園田": 27, "姫路": 28, "高知": 31, "佐賀": 32,
}

# keibablood 開催場コード（実績ベース）
KEIBABLOOD_CODE = {
  "門別": "30","盛岡": "35","水沢": "36","浦和": "42","船橋": "43","大井": "44","川崎": "45",
  "金沢": "46","笠松": "47","名古屋": "48","園田": "50","姫路": "51","高知": "54","佐賀": "55",
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
    if debug:
        ct = r.headers.get("Content-Type", "")
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
            if debug: print(f"[ACTIVE] {track} (babaCode={baba})")
        else:
            if debug: print(f"[NO] {track} (babaCode={baba})")
        time.sleep(0.08)
    return active

# ===== 混戦度（ギャップ方式）=====
def _clamp(x, lo, hi):
    return max(lo, min(hi, x))

def _sig_score_from_gap(gap: float, mid: float) -> float:
    gap = max(0.0, float(gap))
    mid = max(1e-9, float(mid))
    return 1.0 / (1.0 + (gap / mid))

def calc_konsen_gap(top5_scores_desc):
    if not top5_scores_desc or len(top5_scores_desc) < 5:
        return {
            "name": KONSEN_NAME,
            "value": 0.0,
            "is_focus": False,
            "gap12": None,
            "gap15": None,
            "gap12_mid": KONSEN_GAP12_MID,
            "gap15_mid": KONSEN_GAP15_MID,
            "focus_th": KONSEN_FOCUS_TH,
        }

    s1, s2, s3, s4, s5 = [float(x) for x in top5_scores_desc[:5]]
    gap12 = max(0.0, s1 - s2)
    gap15 = max(0.0, s1 - s5)

    sc12 = _sig_score_from_gap(gap12, KONSEN_GAP12_MID)
    sc15 = _sig_score_from_gap(gap15, KONSEN_GAP15_MID)

    konsen01 = 0.65 * sc12 + 0.35 * sc15
    konsen = round(100.0 * _clamp(konsen01, 0.0, 1.0), 1)
    is_focus = bool(konsen >= float(KONSEN_FOCUS_TH))

    return {
        "name": KONSEN_NAME,
        "value": konsen,
        "is_focus": is_focus,
        "gap12": round(gap12, 3),
        "gap15": round(gap15, 3),
        "gap12_mid": KONSEN_GAP12_MID,
        "gap15_mid": KONSEN_GAP15_MID,
        "focus_th": KONSEN_FOCUS_TH,
        "sc12": round(sc12, 4),
        "sc15": round(sc15, 4),
    }

# ====== kaisekisya 解析（騎手補正） ======
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
    raw = win * 0.45 + quin * 0.35 + tri * 0.20
    return raw / 4.0

# ====== keibablood（指数表 + 騎手） ======
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

            max_need = max(i_ban, i_name, i_idx, (i_jok or 0))
            if len(vals) <= max_need:
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

# ====== 吉馬（SP能力値） ======
def build_kichiuma_fp_url(yyyymmdd: str, track_id: int, race_no: int) -> str:
    date_slash = f"{yyyymmdd[:4]}/{int(yyyymmdd[4:6])}/{int(yyyymmdd[6:8])}"
    date_enc = date_slash.replace("/", "%2F")
    race_id = f"{yyyymmdd}{race_no:02d}{track_id:02d}"
    return (
        "https://www.kichiuma-chiho.net/php/search.php"
        f"?race_id={race_id}&date={date_enc}&no={race_no}&id={track_id}&p=fp"
    )

def _norm(s: str) -> str:
    s = str(s).replace("\u3000", " ")
    s = re.sub(r"\s+", "", s)
    return s

def parse_kichiuma_race_meta(html: str):
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    title_line = ""
    race_name = ""
    race_info = ""

    for ln in lines:
        if re.search(r"\d{4}年\d{2}月\d{2}日.*?競馬.*?第\d+競走", ln):
            title_line = ln
            break

    idx5 = None
    for i, ln in enumerate(lines):
        if "(5着)" in ln:
            idx5 = i
            break
    if idx5 is not None and idx5 + 1 < len(lines):
        race_name = lines[idx5 + 1]
    if idx5 is not None and idx5 + 2 < len(lines):
        race_info = lines[idx5 + 2]

    return {"title_line": title_line, "race_name": race_name, "race_info": race_info}

def find_sp_table(soup: BeautifulSoup):
    best = None
    best_score = -1
    for t in soup.find_all("table"):
        trs = t.find_all("tr")
        if len(trs) < 2:
            continue

        head_cells = trs[0].find_all(["th","td"])
        if not head_cells:
            continue

        headers = [_norm(c.get_text(" ", strip=True)) for c in head_cells]
        hdr_join = " ".join(headers)

        score = 0
        if "SP能力値" in hdr_join: score += 5
        if "競走馬名" in hdr_join: score += 3
        if "先行力" in hdr_join: score += 2
        if "末脚力" in hdr_join: score += 2
        if "SP信頼" in hdr_join: score += 1
        if "SP調整" in hdr_join: score += 1
        if "SP最大" in hdr_join: score += 1
        if "評価" in hdr_join: score += 1
        if "馬" in hdr_join: score += 1

        row2 = trs[1].find_all(["td","th"])
        if row2:
            first = _norm(row2[0].get_text(" ", strip=True))
            if re.fullmatch(r"\d{1,2}", first):
                score += 3

        if score > best_score:
            best_score = score
            best = t

    return best if (best and best_score >= 8) else None

def parse_kichiuma_sp(html: str):
    soup = BeautifulSoup(html, "lxml")
    meta = parse_kichiuma_race_meta(html)

    t = find_sp_table(soup)
    if not t:
        return {}, meta.get("race_name", "")

    trs = t.find_all("tr")
    headers_raw = [c.get_text(" ", strip=True) for c in trs[0].find_all(["th","td"])]
    headers = [_norm(h) for h in headers_raw]

    def find_col_exact(key_norm):
        for i, h in enumerate(headers):
            if h == key_norm:
                return i
        return None

    c_umaban = find_col_exact("馬") or 0
    c_sp = find_col_exact("SP能力値")
    if c_sp is None:
        for i in range(len(headers) - 1):
            if headers[i] == "SP" and headers[i+1] == "能力値":
                c_sp = i
                break
    if c_sp is None:
        return {}, meta.get("race_name", "")

    sp_by = {}
    for tr in trs[1:]:
        cells = tr.find_all(["td","th"])
        if not cells:
            continue
        vals = [c.get_text(" ", strip=True) for c in cells]
        if len(vals) <= max(c_umaban, c_sp):
            continue

        b = _norm(vals[c_umaban])
        if not re.fullmatch(r"\d{1,2}", b):
            continue
        umaban = int(b)
        if not (1 <= umaban <= 18):
            continue

        cell = str(vals[c_sp]).strip()
        if cell == "" or cell in {"-", "—", "―"}:
            continue
        msp = re.search(r"(\d+(?:\.\d+)?)", cell)
        if not msp:
            continue
        sp_by[umaban] = float(msp.group(1))

    return sp_by, meta.get("race_name", "")

# ===== 推定（SP欠損をKBで「周りに合わせて」埋める）=====
def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def fit_linear(xs, ys):
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return None
    a = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
    b = my - a * mx
    return a, b

def estimate_sp_factory(rows, debug=False):
    pairs = [(r["base_index"], r["sp_raw"]) for r in rows if r["sp_raw"] is not None]
    kb_vals = [r["base_index"] for r in rows if isinstance(r.get("base_index"), (int,float))]

    kb_min = min(kb_vals) if kb_vals else 0.0
    kb_max = max(kb_vals) if kb_vals else 1.0

    sp_min_obs = min([y for _, y in pairs], default=50.0)
    sp_max_obs = max([y for _, y in pairs], default=75.0)

    a_b = None
    if len(pairs) >= 3:
        xs = [x for x, _ in pairs]
        ys = [y for _, y in pairs]
        a_b = fit_linear(xs, ys)

    def est(base_index: float) -> float:
        if a_b is not None:
            a, b = a_b
            v = a * base_index + b
            return clamp(v, sp_min_obs - 2.0, sp_max_obs + 2.0)

        if len(pairs) >= 1:
            sp_med = sorted([y for _, y in pairs])[len(pairs) // 2]
            kb_med = sorted([x for x, _ in pairs])[len(pairs) // 2]
            v = base_index + (sp_med - kb_med)
            return clamp(v, 45.0, 78.0)

        if kb_max == kb_min:
            return 62.0
        t = (base_index - kb_min) / (kb_max - kb_min)
        v = 55.0 + t * 15.0
        return clamp(v, 45.0, 78.0)

    info = {"pairs_n": len(pairs), "has_linear": (a_b is not None), "linear": a_b}
    if debug:
        print(f"[DEBUG] SP-estimator info: {info}")
    return est, info

# ====== keiba.go.jp 結果（RaceMarkTable）上位3 ======
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
        top.append({"rank": int(pos), "umaban": umaban, "name": name})
        if len(top) >= 3:
            break
    return top

# ====== 払戻：三連複（RefundMoneyList）列ベースで厳密 ======
# ====== 払戻：三連複（RefundMoneyList）列ベースで厳密 ======
def parse_sanrenpuku_refunds(html_text: str, rno: int):
    """
    RefundMoneyList は「当日全レース」が1ページに入ってることがある。
    なので "rnoR" の近辺だけを抜き出して、そこから三連複を拾う。
    return: list[ {combo:(a,b,c), payout:int} ]  (payout=100円あたり)
    """
    if not html_text:
        return []

    # まずはテキストで「何R」周辺に切り出す（DOM構造が変わっても耐える）
    # 例: "4R" の見出し付近を中心に前後を抜く
    target = f"{int(rno)}R"
    pos = html_text.find(target)
    if pos < 0:
        # "第4競走" 表記の可能性も見る
        target2 = f"第{int(rno)}競走"
        pos = html_text.find(target2)
        if pos < 0:
            return []
        target = target2

    # 前後を適当に切り出し（長すぎると遅いので）
    start = max(0, pos - 20000)
    end   = min(len(html_text), pos + 40000)
    chunk = html_text[start:end]

    soup = BeautifulSoup(chunk, "lxml")

    # chunk内の table を走査して「式別/組番/払戻」を含む行を探す
    out = []
    for t in soup.find_all("table"):
        trs = t.find_all("tr")
        if len(trs) < 2:
            continue

        # ヘッダっぽい行を探す（複数あることがある）
        header_idx = None
        headers = None
        for i, tr in enumerate(trs[:3]):  # 先頭3行ぐらい見れば十分
            hs = [c.get_text(" ", strip=True) for c in tr.find_all(["th","td"])]
            hjoin = " ".join(hs)
            if ("式別" in hjoin) and (("払戻" in hjoin) or ("払戻金" in hjoin)):
                header_idx = i
                headers = hs
                break
        if header_idx is None or not headers:
            continue

        def find_col(keys, hdrs):
            for j, h in enumerate(hdrs):
                for k in keys:
                    if k in h:
                        return j
            return None

        c_type = find_col(["式別", "式"], headers)
        c_kumi = find_col(["組番", "組", "馬番"], headers)
        c_pay  = find_col(["払戻", "払戻金", "払戻額", "金額"], headers)
        if c_type is None or c_kumi is None or c_pay is None:
            continue

        for tr in trs[header_idx+1:]:
            cells = tr.find_all(["th","td"])
            if not cells:
                continue
            vals = [c.get_text(" ", strip=True) for c in cells]
            if len(vals) <= max(c_type, c_kumi, c_pay):
                continue

            bet_type = re.sub(r"\s+", "", vals[c_type])
            if "三連複" not in bet_type:
                continue

            kumi_raw = vals[c_kumi]
            pay_raw  = vals[c_pay]

            nums = re.findall(r"\d+", kumi_raw)
            if len(nums) < 3:
                continue
            a, b, c = sorted([int(nums[0]), int(nums[1]), int(nums[2])])

            m = re.search(r"([\d,]+)", pay_raw)
            if not m:
                continue
            payout = int(m.group(1).replace(",", ""))

            out.append({"combo": (a, b, c), "payout": payout})

        if out:
            # このtableで取れたらもう十分（同じchunk内に複数ある場合もあるが、基本OK）
            break

    return out
  
def build_refund_url_fallback(baba: int, yyyymmdd: str, rno: int):
    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"
    return (
        "https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RefundMoneyList"
        f"?k_babaCode={baba}&k_raceDate={date_slash}&k_raceNo={int(rno)}"
    )

def build_racemark_url(baba: int, yyyymmdd: str, rno: int):
    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"
    return (
        "https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceMarkTable"
        f"?k_babaCode={baba}&k_raceDate={date_slash}&k_raceNo={int(rno)}"
    )

# ===== HTML（相対色分け＋混戦度バッジ）=====
def render_result_html(title: str, races_out) -> str:
    import html as _html

    def esc(s): return _html.escape(str(s))

    def _clamp01(x: float) -> float:
        return max(0.0, min(1.0, x))

    def _mix(c1, c2, t: float):
        t = _clamp01(t)
        return (
            int(round(c1[0] + (c2[0] - c1[0]) * t)),
            int(round(c1[1] + (c2[1] - c1[1]) * t)),
            int(round(c1[2] + (c2[2] - c1[2]) * t)),
        )

    def _rgb(rgb):
        return f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"

    def _luma(rgb):
        r, g, b = rgb
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    LO = (239, 246, 255)  # #eff6ff
    HI = (29, 78, 216)    # #1d4ed8

    def score_style(sc: float, scores_in_race):
        if not scores_in_race:
            return "color:#111827;"
        mn = min(scores_in_race)
        mx = max(scores_in_race)
        if mx == mn:
            t = 0.55
        else:
            t = (float(sc) - mn) / (mx - mn)
        t2 = _clamp01(t ** 0.75)

        bg = _mix(LO, HI, t2)
        fg = (255, 255, 255) if _luma(bg) < 140 else (17, 24, 39)

        return (
            f"background:{_rgb(bg)};"
            f"color:{_rgb(fg)};"
            "padding:2px 8px;border-radius:10px;display:inline-block;"
            "min-width:72px;text-align:right;font-variant-numeric:tabular-nums;"
            "font-weight:900;"
        )

    def badge(text: str, bg: str, fg: str="#111827") -> str:
        return (
            "<span style='display:inline-block;padding:4px 10px;border-radius:999px;"
            f"background:{bg};color:{fg};font-weight:900;font-size:12px;letter-spacing:.02em;line-height:1;white-space:nowrap;'>"
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
    parts.append(f"<div style='font-size:18px;font-weight:900;margin:8px 0 14px;color:#111827;'>{esc(title)}</div>")

    for r in races_out:
        rno = int(r["race_no"])
        race_name = (r.get("race_name") or "").strip()
        pred = r.get("pred_top5", [])
        top3 = r.get("result_top3", [])
        pred_by_umaban = {int(x["umaban"]): x for x in pred}
        scores_in_race = [float(p.get("score", 0.0)) for p in pred if isinstance(p.get("score", None), (int, float))]

        k = r.get("konsen") or {}
        kval = k.get("value", None)
        is_focus = bool(k.get("is_focus", False))
        kname = k.get("name", KONSEN_NAME)

        konsen_badge = ""
        if isinstance(kval, (int, float)):
            if is_focus:
                konsen_badge = badge(f"注目 {kname}{float(kval):.1f}", "#f59e0b", "#ffffff")
            else:
                konsen_badge = badge(f"{kname}{float(kval):.1f}", "#6b7280", "#ffffff")

        bet = r.get("bet") or {}
        bet_badge = ""
        if bet.get("enabled") and bet.get("is_focus"):
            inv = int(bet.get("invest", 0))
            pay = int(bet.get("payout", 0))
            prof = int(bet.get("profit", pay - inv))
            if prof >= 0:
                bet_badge = badge(f"BOX +{prof}円", "#f59e0b", "#ffffff")
            else:
                bet_badge = badge(f"BOX {prof}円", "#ef4444", "#ffffff")

        parts.append(
            "<div style='margin:16px 0 18px;padding:12px 12px;"
            "border:1px solid #e5e7eb;border-radius:14px;background:#ffffff;'>"
        )

        head = f"{rno}R" + (f" {race_name}" if race_name else "")
        parts.append(
            "<div style='display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;'>"
            f"<div style='font-size:18px;font-weight:900;color:#111827;'>{esc(head)}</div>"
            f"<div style='display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap;'>{konsen_badge}{bet_badge}</div>"
            "</div>"
        )

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
                idx = p.get("score") if p else None

                if isinstance(idx, (int, float)):
                    sc = float(idx)
                    sc_style = score_style(sc, scores_in_race)
                    idx_html = f"<span style=\"{sc_style}\">{sc:.2f}</span>"
                else:
                    idx_html = "<span style='color:#6b7280;'>—</span>"

                parts.append(
                    "<tr>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:center;font-weight:900;'>{x['rank']}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:center;'>{u}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:left;font-weight:750;'>{esc(nm)}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:center;font-weight:900;'>{esc(mark)}</td>"
                    f"<td style='padding:8px;border-bottom:1px solid #fee2e2;text-align:right;'>{idx_html}</td>"
                    "</tr>"
                )
        else:
            parts.append("<tr><td colspan='5' style='padding:10px;color:#6b7280;'>結果取得できませんでした</td></tr>")

        parts.append("</tbody></table></div>")

        parts.append(section_title("指数上位5頭", badge("PRED", "#bfdbfe"), "#eff6ff"))
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

        pos_by_umaban = {}
        for x in top3:
            pos_by_umaban[int(x["umaban"])] = int(x["rank"])

        for i, p in enumerate(pred):
            bgrow = "#ffffff" if i % 2 == 0 else "#f8fafc"
            pos = pos_by_umaban.get(int(p["umaban"]))
            pos_txt = f"{pos}着" if pos else "—"
            sc = float(p["score"])
            sc_style = score_style(sc, scores_in_race)

            parts.append(
                f"<tr style='background:{bgrow};'>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;font-weight:900;'>{esc(p['mark'])}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;font-variant-numeric:tabular-nums;'>{int(p['umaban'])}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:left;font-weight:750;'>{esc(p['name'])}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:right;'>"
                f"<span style=\"{sc_style}\">{sc:.2f}</span>"
                f"</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;font-weight:900;'>{esc(pos_txt)}</td>"
                f"</tr>"
            )

        parts.append("</tbody></table></div>")
        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)

def main():
    yyyymmdd = os.environ.get("DATE") or datetime.now().strftime("%Y%m%d")
    debug = os.environ.get("DEBUG", "").strip() == "1"

    outdir = Path("output")
    outdir.mkdir(parents=True, exist_ok=True)

    now_iso = datetime.now().isoformat(timespec="seconds")

    print(f"[INFO] DATE={yyyymmdd}")
    print(f"[INFO] WEIGHTS SP_W={SP_W} KB_W={KB_W} JOCKEY_W={JOCKEY_W}")
    print(f"[INFO] KONSEN name={KONSEN_NAME} gap12_mid={KONSEN_GAP12_MID} gap15_mid={KONSEN_GAP15_MID} focus_th={KONSEN_FOCUS_TH}")
    print(f"[INFO] BET enabled={BET_ENABLED} unit={BET_UNIT}")

    active = detect_active_tracks_keibago(yyyymmdd, debug=debug)
    print(f"[INFO] active_tracks = {active}")

    # keibablood は -2 が基本（無いときだけ -1, -3... を試す）
    SERIES_ORDER = [2, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]

    # 合算（注目レースBOXのみ）
    pnl_total = {
        "title": "現在の収支（注目レース：三連複BOX）",
        "date_from": yyyymmdd,
        "date_to": yyyymmdd,
        "races": 0,      # 注目レース数
        "hits": 0,       # 注目レース的中数（BOXが当たった=1）
        "invest": 0,     # 購入金額合計
        "payout": 0,     # 払戻合計
        "profit": 0,     # 収支
        "roi": None,     # 回収率（%）
        "hit_rate": None,# 的中率（%）
        "last_updated": now_iso,
    }

    for track in active:
        baba = BABA_CODE.get(track)
        track_id = BABA_CODE.get(track)
        code = KEIBABLOOD_CODE.get(track)
        if not baba or not track_id or not code:
            print(f"[SKIP] {track}: code missing")
            continue

        # 騎手表
        jockey_url = KAISEKISYA_JOCKEY_URL.get(track, "")
        jockey_stats = parse_kaisekisya_jockey_table(fetch(jockey_url, debug=False)) if jockey_url else {}

        # keibablood 探索
        picked_url = None
        used_series = None
        kb_races = None
        for i in SERIES_ORDER:
            kb_url = f"https://keibablood.com/{yyyymmdd}{code}-{i}/"
            kb_html = fetch(kb_url, debug=debug)
            if not kb_html:
                continue
            races = parse_keibablood_tables(kb_html)
            if not races:
                if debug:
                    print(f"[MISS] {track} series=-{i} : tables not found")
                continue
            picked_url = kb_url
            used_series = i
            kb_races = races
            break

        if not kb_races:
            print(f"[SKIP] {track}: keibablood 未発見（-2優先で探索済み）")
            continue

        races_out = []
        track_incomplete = False

        # レースごと
        for rno in sorted(kb_races.keys()):
            rno = int(rno)

            # kichiuma（SP）
            fp_url = build_kichiuma_fp_url(yyyymmdd, track_id, rno)
            fp_html = fetch(fp_url, debug=False)
            if not fp_html:
                print(f"[SKIP] {track} {rno}R: kichiuma fetch failed -> skip track")
                track_incomplete = True
                break

            sp_by_umaban, race_name = parse_kichiuma_sp(fp_html)

            # 指数表（KB）に SP / 騎手補正を合流
            rows = []
            for h in kb_races[rno]:
                u = int(h["umaban"])
                base = float(h["base_index"])
                j = h.get("jockey", "")

                rates = match_jockey_by3(norm_jockey3(j), jockey_stats) if (j and jockey_stats) else None
                add = jockey_add_points(*rates) if rates else 0.0
                sp = sp_by_umaban.get(u)

                rows.append({
                    "umaban": u,
                    "name": h["name"],
                    "jockey": j,
                    "base_index": base,
                    "jockey_add": float(add),
                    "sp_raw": (float(sp) if sp is not None else None),
                })

            # SP欠損推定
            est_sp, _ = estimate_sp_factory(rows, debug=False)
            for r in rows:
                r["sp_est"] = float(est_sp(r["base_index"])) if r["sp_raw"] is None else float(r["sp_raw"])

            # 総合スコア
            horses_scored = []
            for r in rows:
                sp = float(r["sp_est"])
                base = float(r["base_index"])
                add = float(r["jockey_add"])
                score = (SP_W * sp) + (KB_W * base) + (JOCKEY_W * add)

                horses_scored.append({
                    "umaban": int(r["umaban"]),
                    "name": r["name"],
                    "jockey": r.get("jockey", ""),
                    "sp": sp,
                    "base_index": base,
                    "jockey_add": add,
                    "score": float(score),
                    "source": {"kichiuma_fp_url": fp_url},
                })

            if len(horses_scored) < 5:
                print(f"[SKIP] {track} {rno}R: horses < 5 -> skip track")
                track_incomplete = True
                break

            horses_scored.sort(key=lambda x: (-x["score"], -x["sp"], -x["base_index"], x["umaban"]))
            top5 = horses_scored[:5]
            top5_scores = [float(h["score"]) for h in top5]
            konsen = calc_konsen_gap(top5_scores)

            if KONSEN_DEBUG:
                print(f"[KONSEN] {track} {rno}R top5_scores={list(map(lambda v: round(v,2), top5_scores))} konsen={konsen}")

            # 予想上位5
            pred_top5 = []
            top5_umaban = []
            for j, hh in enumerate(top5):
                pred_top5.append({
                    "mark": MARKS5[j],
                    "umaban": int(hh["umaban"]),
                    "name": hh["name"],
                    "score": float(hh["score"]),
                    "sp": float(hh["sp"]),
                    "base_index": float(hh["base_index"]),
                    "jockey": hh.get("jockey",""),
                    "jockey_add": float(hh.get("jockey_add", 0.0)),
                })
                top5_umaban.append(int(hh["umaban"]))

            # keiba.go.jp 結果（1-3）
            rm_url = build_racemark_url(baba, yyyymmdd, rno)
            rm_html = fetch(rm_url, debug=debug)
            result_top3 = parse_top3_from_racemark(rm_html) if rm_html else []

            # keiba.go.jp 払戻（三連複）
            refund_url = build_refund_url_fallback(baba, yyyymmdd, rno)
            refund_html = fetch(refund_url, debug=debug) if refund_url else ""
            refunds = parse_sanrenpuku_refunds(refund_html, rno) if refund_html else []
            
            if REFUND_DEBUG:
                print(f"[REFUND_DEBUG] {track} {rno}R refund_html_has_sanrenpuku={'三連複' in (refund_html or '')}")
                print(f"[REFUND_DEBUG] {track} {rno}R refunds_found={len(refunds)} refunds={refunds[:5]}")
            
            if REFUND_DEBUG:
                print(f"[REFUND_DEBUG] {track} {rno}R refund_url={refund_url}")
                print(f"[REFUND_DEBUG] {track} {rno}R refunds_found={len(refunds)} refunds={refunds[:5]}")


            # 注目レースBOX収支（混戦度>=閾値のみ）
            bet = {
                "enabled": bool(BET_ENABLED),
                "unit": int(BET_UNIT),
                "is_focus": bool(konsen.get("is_focus", False)),
                "box_umaban": sorted(top5_umaban),
                "invest": 0,
                "payout": 0,
                "profit": 0,
                "hits": 0,     # 1 or 0
                "refunds_found": int(len(refunds)),
                "refund_url": refund_url,
            }

            if BET_ENABLED and bet["is_focus"]:
                inv = calc_trifecta_box_invest(BET_UNIT)
                bet["invest"] = inv

                hit_combo_list = []
                if len(result_top3) >= 3:
                    a, b, c = sorted([
                        int(result_top3[0]["umaban"]),
                        int(result_top3[1]["umaban"]),
                        int(result_top3[2]["umaban"]),
                    ])
                    combo = (a, b, c)
                    box_set = set(top5_umaban)
                    if (a in box_set) and (b in box_set) and (c in box_set):
                        hit_combo_list = [combo]

                if REFUND_DEBUG:
                    print(f"[REFUND_DEBUG] {track} {rno}R top3={result_top3}")
                    print(f"[REFUND_DEBUG] {track} {rno}R box_umaban={sorted(top5_umaban)}")
                    print(f"[REFUND_DEBUG] {track} {rno}R hit_combo_list={hit_combo_list}")

                pay = calc_payout_for_box(hit_combo_list, refunds, BET_UNIT)

                if REFUND_DEBUG:
                    print(f"[REFUND_DEBUG] {track} {rno}R pay={pay} (unit={BET_UNIT})")

                bet["payout"] = int(pay)
                bet["profit"] = int(pay - inv)
                bet["hits"] = 1 if pay > 0 else 0

                # ★ 合算は必ずこのifの中 ★
                pnl_total["races"] += 1
                pnl_total["hits"] += bet["hits"]
                pnl_total["invest"] += inv
                pnl_total["payout"] += int(pay)


            races_out.append({
                "track": track,
                "race_no": rno,
                "race_name": race_name,
                "konsen": konsen,
                "pred_top5": pred_top5,
                "result_top3": result_top3,
                "sources": {
                    "keibablood_url": picked_url,
                    "keibablood_series": used_series,
                    "keiba_go_racemark_url": rm_url,
                    "keiba_go_refund_url": refund_url,
                    "kichiuma_fp_url": fp_url,
                },
                "bet": bet,
            })

            time.sleep(0.06)

        if track_incomplete:
            print(f"[WARN] {track}: track incomplete -> no outputs")
            continue

        # 1開催場ぶん出力
        title = f"{yyyymmdd} {track} 結果（fieldnote）"
        html = render_result_html(title, races_out)

        out_json = outdir / f"result_{yyyymmdd}_{track}.json"
        out_html = outdir / f"result_{yyyymmdd}_{track}.html"

        payload = {
            "type": "fieldnote_result",
            "date": yyyymmdd,
            "track": track,
            "title": title,
            "generated_at": now_iso,
            "races": races_out,
        }

        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        out_html.write_text(html, encoding="utf-8")
        print(f"[OK] wrote {out_json} / {out_html}")

    # pnl_total まとめ
    invest = int(pnl_total["invest"])
    payout = int(pnl_total["payout"])
    profit = int(payout - invest)
    pnl_total["profit"] = profit
    pnl_total["roi"] = round((payout / invest) * 100.0, 1) if invest > 0 else None
    pnl_total["hit_rate"] = round((pnl_total["hits"] / pnl_total["races"]) * 100.0, 1) if pnl_total["races"] > 0 else None

    (outdir / "pnl_total.json").write_text(json.dumps(pnl_total, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] wrote {outdir / 'pnl_total.json'}")

if __name__ == "__main__":
    main()
