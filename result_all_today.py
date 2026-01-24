# result_all_today.py  (fieldnote-lab-bot)  最終版
# 目的：
# - keibablood（指数）+ kichiuma（SP）+ kaisekisya（騎手）で上位5頭を作る
# - keiba.go.jp RaceMarkTable から結果(1-3着)を取得
# - 払戻は RefundMoneyList から「三連複」をレース別に取得（同着で複数行OK）
#   ただし「取れない/変な時」だけ RaceMarkTable から保険抽出（誤爆防止付き）
# - 注目レース（混戦度>=閾値）のみ三連複BOX(上位N頭)の収支を計算
# - output/ に結果HTML/JSON と pnl_total.json（累計/トップページ用）を出力
#
# ★ファイル名は日本語をやめて、keibabloodコード（例：船橋=43）で保存する（文字化け対策）

import os, re, json, time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "ja,en;q=0.8"}
MARKS5 = ["◎", "〇", "▲", "△", "☆"]

# ===== スコア重み（予想と同じ）=====
SP_W = float(os.environ.get("SP_W", "1.0"))
KB_W = float(os.environ.get("KB_W", "0.10"))
JOCKEY_W = float(os.environ.get("JOCKEY_W", "0.20"))

# ===== 混戦度（予想と同じ：ギャップ方式 / 環境変数で調整可）=====
KONSEN_NAME = os.environ.get("KONSEN_NAME", "混戦度")
KONSEN_GAP12_MID = float(os.environ.get("KONSEN_GAP12_MID", "0.8"))
KONSEN_GAP15_MID = float(os.environ.get("KONSEN_GAP15_MID", "3.0"))
KONSEN_FOCUS_TH  = float(os.environ.get("KONSEN_FOCUS_TH", "50"))
KONSEN_DEBUG = os.environ.get("KONSEN_DEBUG", "").strip() == "1"

# ===== 注目レース 三連複BOX収支 =====
BET_ENABLED = os.environ.get("BET_ENABLED", "1").strip() != "0"
BET_UNIT = int(os.environ.get("BET_UNIT", "100"))  # 1点あたり（円）
BET_BOX_N = int(os.environ.get("BET_BOX_N", "5"))  # 上位何頭BOX（デフォルト5→10点）

# ===== デバッグ =====
DEBUG = os.environ.get("DEBUG", "").strip() == "1"
REFUND_DEBUG = os.environ.get("REFUND_DEBUG", "").strip() == "1"

# ===== 累計PnLファイル =====
PNL_FILE = os.environ.get("PNL_FILE", "output/pnl_total.json")

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

# ===== 推定（SP欠損をKBで埋める）=====
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

    info = {
        "pairs_n": len(pairs),
        "has_linear": (a_b is not None),
        "linear": a_b,
        "kb_min": kb_min,
        "kb_max": kb_max,
        "sp_min_obs": sp_min_obs,
        "sp_max_obs": sp_max_obs,
    }
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

# ====== 払戻（RefundMoneyList）から「三連複」の組合せと払戻を拾う（同着で複数行もOK） ======
def refundmoney_url(baba: int, yyyymmdd: str) -> str:
    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"
    return f"https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RefundMoneyList?k_babaCode={baba}&k_raceDate={date_slash}"

def _parse_money_yen(s: str):
    s = str(s)
    m = re.search(r"([\d,]+)\s*円", s)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))

def _norm_combo(s: str):
    s = str(s).strip()
    s = s.replace("－", "-").replace("―", "-").replace("—", "-")
    s = re.sub(r"\s+", "", s)
    return s

def parse_refundmoney_sanrenpuku_by_race(html_text: str):
    """
    return: dict[int, list[{"combo":"7-8-10","payout":1180}]]
      race_no -> 三連複の行を全部（同着などで複数ある場合は複数）
    """
    soup = BeautifulSoup(html_text, "lxml")
    txt = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]

    race_idx = []
    for i, ln in enumerate(lines):
        m = re.fullmatch(r"(\d{1,2})R", ln)
        if m:
            race_idx.append((int(m.group(1)), i))
    if not race_idx:
        for i, ln in enumerate(lines):
            m = re.match(r"^(\d{1,2})R\b", ln)
            if m:
                race_idx.append((int(m.group(1)), i))

    race_idx.sort(key=lambda x: x[1])

    def chunk(start_i, end_i):
        return lines[start_i:end_i]

    out = {}
    for k, (rno, start) in enumerate(race_idx):
        end = race_idx[k+1][1] if k+1 < len(race_idx) else len(lines)
        seg = chunk(start, end)

        hits = []
        for j in range(len(seg)):
            if "三連複" not in seg[j]:
                continue
            ln = seg[j]

            combo_m = re.search(r"三連複\s*([0-9]+(?:[-－―—][0-9]+){2})", ln)
            pay = _parse_money_yen(ln)
            if combo_m and pay is not None:
                hits.append({"combo": _norm_combo(combo_m.group(1)), "payout": int(pay)})
                continue

            look = " ".join(seg[j:j+4])
            combo_m = re.search(r"三連複\s*([0-9]+(?:[-－―—][0-9]+){2})", look)
            pay = _parse_money_yen(look)
            if combo_m and pay is not None:
                hits.append({"combo": _norm_combo(combo_m.group(1)), "payout": int(pay)})

        if hits:
            out[int(rno)] = hits

    return out

# ====== 保険：RaceMarkTableから三連複だけをDOM抽出（誤爆防止） ======
def parse_sanrenpuku_refunds_from_racemark_dom(html_text: str):
    """
    RaceMarkTable の中から「三連複」の行だけをDOMベースで拾う保険。
    - 「三連複」を含む行(tr)を探す
    - その行のテキストから「a-b-c」と「xxxx円」を抽出
    - 異常値(>500000など)は捨てる
    """
    if not html_text:
        return []

    soup = BeautifulSoup(html_text, "lxml")
    out = []
    seen = set()

    for tr in soup.find_all("tr"):
        text = tr.get_text(" ", strip=True)
        if "三連複" not in text:
            continue

        m_combo = re.search(r"(\d{1,2})\s*[-－―—]\s*(\d{1,2})\s*[-－―—]\s*(\d{1,2})", text)
        m_pay = re.search(r"(\d[\d,]{2,})\s*円", text)
        if not (m_combo and m_pay):
            continue

        nums = [int(m_combo.group(1)), int(m_combo.group(2)), int(m_combo.group(3))]
        if any(n < 1 or n > 18 for n in nums):
            continue
        combo = tuple(sorted(nums))
        pay = int(m_pay.group(1).replace(",", ""))

        if pay < 100 or pay > 500000:
            continue
        if combo in seen:
            continue
        seen.add(combo)

        out.append({"combo": "-".join(map(str, combo)), "payout": pay})

    return out

def _looks_bad_sanrenpuku_rows(rows):
    if not rows:
        return True
    for row in rows:
        combo = row.get("combo")
        payout = row.get("payout")
        if not combo:
            return True
        try:
            p = int(payout)
        except Exception:
            return True
        if p < 100 or p > 500000:
            return True
        nums = [x for x in re.split(r"[-]", _norm_combo(combo)) if x.isdigit()]
        if len(nums) != 3:
            return True
    return False

# ===== 注目レースBOX（上位N頭の三連複BOX） =====
def comb3_count(n: int) -> int:
    if n < 3:
        return 0
    return (n * (n - 1) * (n - 2)) // 6

def box_hit_payout(top_umaban_list, sanrenpuku_rows):
    """
    top_umaban_list: [馬番...]
    sanrenpuku_rows: [{"combo":"7-8-10","payout":1180}, ...] ※同着で複数あり得る
    return: (hit:bool, payout_total:int, hit_combos:list[str])
    """
    s = set(int(x) for x in top_umaban_list if str(x).isdigit() or isinstance(x, int))
    payout_total = 0
    hit_combos = []

    for row in (sanrenpuku_rows or []):
        combo = row.get("combo", "")
        payout = int(row.get("payout", 0) or 0)
        nums = [int(x) for x in re.split(r"[-]", _norm_combo(combo)) if x.isdigit()]
        if len(nums) != 3:
            continue
        if set(nums).issubset(s):
            payout_total += payout
            hit_combos.append(_norm_combo(combo))

    return (payout_total > 0), payout_total, hit_combos

def load_pnl_total(path: str):
    p = Path(path)
    if not p.exists():
        return {"invest": 0, "payout": 0, "profit": 0, "races": 0, "last_updated": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"invest": 0, "payout": 0, "profit": 0, "races": 0, "last_updated": None}

def save_pnl_total(path: str, total: dict):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(total, ensure_ascii=False, indent=2), encoding="utf-8")

def build_racemark_url(baba: int, yyyymmdd: str, rno: int):
    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"
    return (
        "https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceMarkTable"
        f"?k_babaCode={baba}&k_raceDate={date_slash}&k_raceNo={int(rno)}"
    )

# ====== HTML（予想の相対色分けロジック + 混戦度バッジ + 収支サマリ） ======
def render_result_html(title: str, races_out, pnl_summary: dict) -> str:
    import html as _html

    def esc(s): return _html.escape(str(s))

    # ---- 予想と同じ「相対色分け」ユーティリティ ----
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
        return (f"<span style='display:inline-block;padding:4px 10px;border-radius:999px;"
                f"background:{bg};color:{fg};font-weight:900;font-size:12px;letter-spacing:.02em;line-height:1;white-space:nowrap;'>"
                f"{esc(text)}</span>")

    def section_title(left: str, right_badge: str, bg: str) -> str:
        return (f"<div style='display:flex;align-items:center;justify-content:space-between;"
                f"padding:10px 12px;border-radius:12px;background:{bg};margin:10px 0 8px;'>"
                f"<strong style='font-size:14px;'>{esc(left)}</strong>"
                f"<div style='display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap;'>{right_badge}</div>"
                f"</div>")

    parts = []
    parts.append("<div style='max-width:980px;margin:0 auto;line-height:1.7;'>")
    parts.append(f"<div style='font-size:20px;font-weight:900;margin:10px 0 6px;color:#111827;'>{esc(title)}</div>")

    # ===== 収支サマリ =====
    if pnl_summary:
        invest = int(pnl_summary.get("invest", 0) or 0)
        payout = int(pnl_summary.get("payout", 0) or 0)
        profit = int(pnl_summary.get("profit", 0) or 0)
        races = int(pnl_summary.get("focus_races", 0) or 0)
        unit = int(pnl_summary.get("bet_unit", BET_UNIT) or BET_UNIT)
        nbox = int(pnl_summary.get("box_n", BET_BOX_N) or BET_BOX_N)
        pts = int(pnl_summary.get("bet_points_per_race", comb3_count(nbox)) or comb3_count(nbox))
        roi = 0.0
        if invest > 0:
            roi = round((payout / invest) * 100.0, 1)

        profit_badge = badge(f"収支 {profit:+,}円", "#f59e0b" if profit >= 0 else "#ef4444", "#ffffff")
        roi_badge = badge(f"回収率 {roi:.1f}%", "#6b7280", "#ffffff")

        parts.append(
            "<div style='margin:14px 0 18px;padding:12px 12px;"
            "border:1px solid #e5e7eb;border-radius:14px;background:#ffffff;'>"
            "<div style='display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;'>"
            f"<div style='font-size:16px;font-weight:900;color:#111827;'>注目レース（三連複BOX） 収支サマリ</div>"
            f"<div style='display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap;'>{profit_badge}{roi_badge}</div>"
            "</div>"
            "<div style='margin-top:10px;display:flex;gap:10px;flex-wrap:wrap;'>"
            f"{badge(f'注目レース {races}本', '#bfdbfe')}"
            f"{badge(f'買い目 {nbox}頭BOX（{pts}点）', '#bfdbfe')}"
            f"{badge(f'1点 {unit}円', '#bfdbfe')}"
            f"{badge(f'投資 {invest:,}円', '#6b7280', '#ffffff')}"
            f"{badge(f'払戻 {payout:,}円', '#6b7280', '#ffffff')}"
            "</div>"
            "<div style='margin-top:8px;color:#6b7280;font-size:12px;'>※配当は当日払戻金（keiba.go.jp）から取得。注目レースのみ集計。</div>"
            "</div>"
        )

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

        parts.append(
            "<div style='margin:16px 0 18px;padding:12px 12px;"
            "border:1px solid #e5e7eb;border-radius:14px;background:#ffffff;'>"
        )

        head = f"{rno}R" + (f" {race_name}" if race_name else "")
        parts.append(
            "<div style='display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;'>"
            f"<div style='font-size:18px;font-weight:900;color:#111827;'>{esc(head)}</div>"
            f"<div style='display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap;'>{konsen_badge}</div>"
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

        bet = r.get("bet_box") or {}
        if bet.get("is_focus"):
            hit = bool(bet.get("hit"))
            payout = int(bet.get("payout", 0) or 0)
            invest = int(bet.get("invest", 0) or 0)
            profit = int(bet.get("profit", 0) or 0)

            b1 = badge("注目BOX", "#f59e0b", "#ffffff")
            b2 = badge(("的中" if hit else "不的中"), "#f59e0b" if hit else "#6b7280", "#ffffff")
            b3 = badge(f"払戻 {payout:,}円", "#6b7280", "#ffffff")
            b4 = badge(f"投資 {invest:,}円", "#6b7280", "#ffffff")
            b5 = badge(f"収支 {profit:+,}円", "#f59e0b" if profit >= 0 else "#ef4444", "#ffffff")

            parts.append("<div style='margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;'>"
                         f"{b1}{b2}{b3}{b4}{b5}</div>")

        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)

def main():
    yyyymmdd = os.environ.get("DATE") or datetime.now().strftime("%Y%m%d")
    os.makedirs("output", exist_ok=True)

    print(f"[INFO] DATE={yyyymmdd}")
    print(f"[INFO] WEIGHTS SP_W={SP_W} KB_W={KB_W} JOCKEY_W={JOCKEY_W}")
    print(f"[INFO] KONSEN name={KONSEN_NAME} gap12_mid={KONSEN_GAP12_MID} gap15_mid={KONSEN_GAP15_MID} focus_th={KONSEN_FOCUS_TH}")
    print(f"[INFO] BET enabled={BET_ENABLED} bet_unit={BET_UNIT} box_n={BET_BOX_N}")

    active = detect_active_tracks_keibago(yyyymmdd, debug=DEBUG)
    print(f"[INFO] active_tracks = {active}")

    SERIES_ORDER = [2, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]

    # ===== 累計P/L（トップページ用の素材）=====
    pnl_total = load_pnl_total(PNL_FILE)

    for track in active:
        baba = BABA_CODE.get(track)
        track_id = BABA_CODE.get(track)
        place_code = KEIBABLOOD_CODE.get(track)  # ★ファイル名にも使う（日本語回避）
        if not baba or not track_id or not place_code:
            print(f"[SKIP] {track}: code missing")
            continue

        # ---- 騎手成績 ----
        jockey_url = KAISEKISYA_JOCKEY_URL.get(track, "")
        jockey_stats = parse_kaisekisya_jockey_table(fetch(jockey_url, debug=False)) if jockey_url else {}

        # ---- keibablood 取得 ----
        picked_url = None
        used_series = None
        kb_races = None

        for i in SERIES_ORDER:
            kb_url = f"https://keibablood.com/{yyyymmdd}{place_code}-{i}/"
            kb_html = fetch(kb_url, debug=DEBUG)
            if not kb_html:
                continue
            races = parse_keibablood_tables(kb_html)
            if not races:
                if DEBUG:
                    print(f"[MISS] {track} series=-{i} : tables not found")
                continue
            picked_url = kb_url
            used_series = i
            kb_races = races
            break

        if not kb_races:
            print(f"[SKIP] {track}: keibablood 未発見（-2優先で探索済み）")
            continue

        # ---- 払戻（当日払戻金）を先にまとめて取得（同着対応） ----
        ref_url = refundmoney_url(baba, yyyymmdd)
        ref_html = fetch(ref_url, debug=False)
        sanrenpuku_map = parse_refundmoney_sanrenpuku_by_race(ref_html) if ref_html else {}
        if REFUND_DEBUG:
            print(f"[REFUND_DEBUG] {track} refundmoney_url={ref_url} races_with_sanrenpuku={len(sanrenpuku_map)}")

        races_out = []
        track_incomplete = False

        # track内の注目BOX収支
        focus_races = 0
        invest_sum = 0
        payout_sum = 0

        for rno in sorted(kb_races.keys()):
            rno = int(rno)

            # ---- 吉馬取得（レース名用 + SP推定用）----
            fp_url = build_kichiuma_fp_url(yyyymmdd, track_id, rno)
            fp_html = fetch(fp_url, debug=False)
            if not fp_html:
                print(f"[SKIP] {track} {rno}R: kichiuma fetch failed -> skip track")
                track_incomplete = True
                break

            sp_by_umaban, race_name = parse_kichiuma_sp(fp_html)

            # ---- KB側の馬リスト作成（SP欠損は None）----
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

            # ---- SP推定器を作成して欠損を埋める ----
            est_sp, _ = estimate_sp_factory(rows, debug=False)
            for r in rows:
                r["sp_est"] = float(est_sp(r["base_index"])) if r["sp_raw"] is None else float(r["sp_raw"])

            # ---- スコア計算 ----
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

          # ===== デバッグ：混戦度の中身を見る =====
            if KONSEN_DEBUG:
                 print(
                   f"[KONSEN] {track} {rno}R "
                   f"value={konsen.get('value')} "
                   f"focus={konsen.get('is_focus')} "
                   f"gap12={konsen.get('gap12')} "
                   f"gap15={konsen.get('gap15')} "
                   f"top5_scores={[round(x,2) for x in top5_scores]}"
                  )
          
            if KONSEN_DEBUG:
                print(f"[KONSEN] {track} {rno}R top5_scores={list(map(lambda v: round(v,2), top5_scores))} konsen={konsen}")

            pred_top5 = []
            for j, hh in enumerate(top5):
                pred_top5.append({
                    "mark": MARKS5[j],
                    "umaban": int(hh["umaban"]),
                    "name": hh["name"],
                    "score": float(hh["score"]),
                    "sp": float(hh["sp"]),
                    "base_index": float(hh["base_index"]),
                    "jockey": hh.get("jockey",""),
                    "jockey_add": float(hh["jockey_add"]),
                    "source": hh.get("source", {}),
                })

            # ---- 結果（上位3）----
            rm_url = build_racemark_url(baba, yyyymmdd, rno)
            rm_html = fetch(rm_url, debug=False)
            result_top3 = parse_top3_from_racemark(rm_html) if rm_html else []

            # ---- 払戻（三連複）RefundMoneyList優先 ----
            san = sanrenpuku_map.get(int(rno), [])

            # 取れない/変な時だけ保険（RaceMarkTable DOM抽出）
            if rm_html and _looks_bad_sanrenpuku_rows(san):
                san2 = parse_sanrenpuku_refunds_from_racemark_dom(rm_html)
                if san2:
                    san = san2

            if REFUND_DEBUG:
                print(f"[REFUND_DEBUG] {track} {rno}R racemark_url={rm_url}")
                print(f"[REFUND_DEBUG] {track} {rno}R refunds_found={len(san)} refunds={san[:5]}")

            # ---- 注目レースの三連複BOX収支（同着で複数三連複があれば全部加算）----
            bet_box = {"is_focus": False}
            is_focus = bool(konsen.get("is_focus", False))
            if BET_ENABLED and is_focus:
                focus_races += 1
                nbox = min(BET_BOX_N, len(pred_top5))
                pts = comb3_count(nbox)
                invest = pts * BET_UNIT

                top_umaban = [p["umaban"] for p in pred_top5[:nbox]]
                hit, payout, combos = box_hit_payout(top_umaban, san)
                profit = int(payout) - int(invest)

                invest_sum += invest
                payout_sum += payout

                bet_box = {
                    "is_focus": True,
                    "threshold": float(KONSEN_FOCUS_TH),
                    "box_n": int(nbox),
                    "bet_unit": int(BET_UNIT),
                    "bet_points": int(pts),
                    "invest": int(invest),
                    "hit": bool(hit),
                    "payout": int(payout),
                    "profit": int(profit),
                    "hit_combos": combos,
                    "sanrenpuku_rows": san,
                }

            races_out.append({
                "race_no": int(rno),
                "race_name": race_name,
                "konsen": konsen,
                "pred_top5": pred_top5,
                "result_top3": result_top3,
                "bet_box": bet_box,
                "source": {
                    "racemark_url": rm_url,
                    "kichiuma_fp_url": fp_url,
                    "refundmoney_url": ref_url,
                }
            })

            time.sleep(0.08)

        if track_incomplete:
            print(f"[SKIP] {track}: indices not usable -> NO OUTPUT")
            continue

        title = f"{yyyymmdd[0:4]}.{yyyymmdd[4:6]}.{yyyymmdd[6:8]} {track}競馬 結果"

        profit_sum = int(payout_sum) - int(invest_sum)
        pnl_summary = {
            "focus_races": int(focus_races),
            "box_n": int(BET_BOX_N),
            "bet_unit": int(BET_UNIT),
            "bet_points_per_race": int(comb3_count(BET_BOX_N)),
            "invest": int(invest_sum),
            "payout": int(payout_sum),
            "profit": int(profit_sum),
        }

        # 累計も更新（トップページ用に使える）
        pnl_total["invest"] = int(pnl_total.get("invest", 0) or 0) + int(invest_sum)
        pnl_total["payout"] = int(pnl_total.get("payout", 0) or 0) + int(payout_sum)
        pnl_total["profit"] = int(pnl_total["payout"]) - int(pnl_total["invest"])
        pnl_total["races"] = int(pnl_total.get("races", 0) or 0) + int(focus_races)
        pnl_total["last_updated"] = datetime.now().isoformat(timespec="seconds")
        save_pnl_total(PNL_FILE, pnl_total)

        out = {
            "type": "fieldnote_result",
            "date": yyyymmdd,
            "place": track,
            "place_code": place_code,
            "baba_code": baba,
            "title": title,
            "races": races_out,
            "pnl_summary": pnl_summary,
            "pnl_total": pnl_total,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "weights": {"SP_W": SP_W, "KB_W": KB_W, "JOCKEY_W": JOCKEY_W},
            "konsen_config": {
                "name": KONSEN_NAME,
                "gap12_mid": KONSEN_GAP12_MID,
                "gap15_mid": KONSEN_GAP15_MID,
                "focus_th": KONSEN_FOCUS_TH,
            },
            "source": {
                "keibablood_url": picked_url,
                "keibablood_series_used": used_series,
                "kaisekisya_url": jockey_url,
                "refundmoney_url": ref_url,
            }
        }

        # ★日本語ファイル名をやめる（place_codeで保存）
        json_path = Path("output") / f"result_{yyyymmdd}_{place_code}.json"
        html_path = Path("output") / f"result_{yyyymmdd}_{place_code}.html"

        json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path.write_text(render_result_html(title, races_out, pnl_summary), encoding="utf-8")

        print(f"[OK] {track} -> {json_path.name} / {html_path.name}  (keibablood=-{used_series})  focus={focus_races} profit={profit_sum:+,}円")

    # pnl_total は随時保存済みだけど、最後にもう一回整形して書き直し（保険）
    save_pnl_total(PNL_FILE, pnl_total)
    print(f"[OK] wrote {PNL_FILE}")

if __name__ == "__main__":
    main()
