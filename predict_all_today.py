# ===== PART 1 / 4 =====
# predict_all_today.py（改造反映版）
# 範囲：先頭〜開催判定(detect_active_tracks)まで
# ※このPARTに「追加①：低シグナルスキップ用の環境変数」も反映済み
# 次は PART 2 / 4 を貼ってください（解析系：kaisekisya/NAR/吉馬/混戦度/スキップ関数追加）

import os, re, json, time, math
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "ja,en;q=0.8"}
MARKS5 = ["◎", "〇", "▲", "△", "☆"]

# ===== スコア重み（環境変数で調整可）=====
# 今回は “同じ土俵で足す” 前提なので、KBもデフォで 1.0 に寄せる
SP_W = float(os.environ.get("SP_W", "1.0"))
KB_W = float(os.environ.get("KB_W", "1.0"))
JOCKEY_W = float(os.environ.get("JOCKEY_W", "0.4"))

# ===== 正規化設定 =====
# NORM_METHOD: "z"（平均/標準偏差） or "robust"（中央値/MAD）
NORM_METHOD = os.environ.get("NORM_METHOD", "z").strip().lower()
# 最終指数の見た目（スケール）
# score = SCORE_BASE + SCORE_SCALE * combined_z
SCORE_BASE = float(os.environ.get("SCORE_BASE", "50.0"))
SCORE_SCALE = float(os.environ.get("SCORE_SCALE", "10.0"))

# ===== 混戦度（残す：表示用。いらなければ KONSEN_ENABLE=0）=====
KONSEN_ENABLE = os.environ.get("KONSEN_ENABLE", "1").strip() != "0"
KONSEN_NAME = os.environ.get("KONSEN_NAME", "混戦度")
KONSEN_GAP12_MID = float(os.environ.get("KONSEN_GAP12_MID", "0.8"))
KONSEN_GAP15_MID = float(os.environ.get("KONSEN_GAP15_MID", "3.0"))
KONSEN_FOCUS_TH = float(os.environ.get("KONSEN_FOCUS_TH", "30"))
KONSEN_DEBUG = os.environ.get("KONSEN_DEBUG", "").strip() == "1"

# ===== 新馬戦っぽいレースをスキップ（指数が横並び等） =====
SKIP_FLAT_INDEX = os.environ.get("SKIP_FLAT_INDEX", "1").strip() != "0"
# 「ほぼ全員同じ」の判定幅（指数の最大-最小がこれ以下ならスキップ）
FLAT_INDEX_RANGE_MAX = float(os.environ.get("FLAT_INDEX_RANGE_MAX", "0.8"))
# 指数が何頭以上そろってたら判定するか（少なすぎると誤判定するので）
FLAT_INDEX_MIN_COUNT = int(os.environ.get("FLAT_INDEX_MIN_COUNT", "6"))

# ===== 低シグナル（新馬/指数欠損だらけ）スキップ =====
SKIP_LOW_SIGNAL = os.environ.get("SKIP_LOW_SIGNAL", "1").strip() != "0"
# 平均指数が揃ってる頭数がこれ未満なら「指数不足」
MIN_KB_COUNT = int(os.environ.get("MIN_KB_COUNT", "6"))
# SPが揃ってる頭数がこれ未満なら「SP不足」
MIN_SP_COUNT = int(os.environ.get("MIN_SP_COUNT", "3"))
# 平均指数の有効比率がこれ未満なら「欠損過多」
MIN_VALID_RATIO = float(os.environ.get("MIN_VALID_RATIO", "0.6"))
# “全部同点っぽい” の判定（スコア最大-最小がこれ以下ならスキップ）
FLAT_SCORE_RANGE_MAX = float(os.environ.get("FLAT_SCORE_RANGE_MAX", "0.2"))

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

# ===== kaisekisya（開催場別）騎手成績 =====
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

# =========================
# 余計な文字を消す（馬名/レース名）
# =========================
def _norm_text(s: str) -> str:
    s = str(s).replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def clean_horse_name(raw: str) -> str:
    s = _norm_text(raw)
    s = re.sub(r"\s+\d+\s*(?:日|週|ヶ月|か月|月|年)\s*前.*$", "", s).strip()
    s = re.sub(r"[（(].*?[）)]", "", s).strip()
    s = s.split(" ")[0].strip()
    return s

def clean_race_name(raw: str) -> str:
    s = _norm_text(raw)

    # 先頭の「8R」「８Ｒ」などを落とす（rno表示は別で付くので）
    s = re.sub(r"^\s*[0-9０-９]{1,2}\s*[RＲ]\s*", "", s).strip()

    # パンくず（« »）以降をカット
    if "«" in s:
        s = s.split("«")[0].strip()
    if "»" in s:
        s = s.split("»")[0].strip()

    s = s.replace("－", "-").replace("―", "-").replace("—", "-")
    s = re.sub(r"\s*-\s*", "-", s).strip()
    return s

# ===== HTTP =====
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

# =========================================================
# 開催判定：keiba.go.jp を基本、NAR(table.html)で補完
# =========================================================
def keibago_racelist_has_race(html: str) -> bool:
    if not html:
        return False
    return ("1R" in html) or ("２Ｒ" in html) or ("出馬表" in html)

def nar_tablehtml_url(date: str, track: str, number: str) -> str:
    return f"https://nar.k-ba.net/{date}/{int(track)}/{int(number)}/table.html"

def nar_tablehtml_seems_valid(html: str) -> bool:
    if not html:
        return False
    txt = html
    return ("平均指数" in txt) and (
        ("馬番" in txt) or re.search(r"\b1\s+\S+", BeautifulSoup(html, "lxml").get_text("\n", strip=True) or "")
    )

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

    missing = [t for t in BABA_CODE.keys() if t not in active and BABA_CODE[t] not in EXCLUDE_BABA]
    if missing:
        if debug:
            print(f"[INFO] fallback check by NAR table.html for: {missing}")
        for track in missing:
            track_id = BABA_CODE[track]
            url = nar_tablehtml_url(yyyymmdd, str(track_id), "1")
            html = fetch(url, debug=debug)
            if nar_tablehtml_seems_valid(html):
                active.append(track)
            time.sleep(0.06)

    force = os.environ.get("TRACKS_FORCE", "").strip()
    if force:
        add = [x.strip() for x in force.split(",") if x.strip()]
        for t in add:
            if t in BABA_CODE and (t not in active) and (BABA_CODE[t] not in EXCLUDE_BABA):
                active.append(t)

    return active
# ===== PART 2 / 4 =====
# predict_all_today.py（改造反映版）
# 範囲：kaisekisya解析〜NAR解析〜吉馬SP解析〜混戦度〜スキップ判定関数まで
# ※このPARTに「追加②：is_maiden_like / should_skip_low_signal」も反映済み
# 次は PART 3 / 4（HTML描画＋スコア計算compute_scores_newまで）を貼ってください

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
    # 生値（0〜25くらい）想定：勝率/連対/三連対を合成
    raw = win * 0.45 + quin * 0.35 + tri * 0.20
    return raw / 4.0

# =========================================================
# NAR(table.html) 解析（table id="table" が無いケース対策）
# =========================================================
def parse_nar_race_name(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    h3s = soup.find_all("h3")
    if not h3s:
        return ""
    best = ""
    for h in h3s:
        t = _norm_text(h.get_text(" ", strip=True))
        if re.search(r"\b\d{1,2}R\b", t):
            best = t
            break
    if not best:
        best = _norm_text(h3s[0].get_text(" ", strip=True))
    return clean_race_name(best)

def parse_nar_rows_text_fallback(html: str):
    """
    返り値: [{umaban,name,jockey,avg_index(Noneありうる)}, ...]
    avg_index が * の馬も “行は返す” → 後段で中央値補完する
    """
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    rows = []
    cur = None

    re_jockey = re.compile(r"\b\d{1,2}\.\d\b\s+(\S+)\s+\d{3}\b")
    # "(5)" みたいなカッコの後ろの数値を拾う（最後が平均指数であることが多い）
    re_idx = re.compile(r"\)\s*(\d+(?:\.\d+)?)")

    for ln in lines:
        m = re.match(r"^(\d{1,2})\s+(.+)$", ln)
        if m:
            if cur:
                rows.append(cur)
            umaban = int(m.group(1))
            name = clean_horse_name(m.group(2))
            cur = {"umaban": umaban, "name": name, "jockey": "", "avg_index": None}
            continue

        if not cur:
            continue

        if not cur["jockey"]:
            mj = re_jockey.search(ln)
            if mj:
                cur["jockey"] = re.sub(r"[◀◁▶▷\s]+", "", mj.group(1))

        # 平均指数らしき数値が拾えたら入れる
        if cur["avg_index"] is None:
            nums = re_idx.findall(ln)
            if nums:
                try:
                    cur["avg_index"] = float(nums[-1])
                except Exception:
                    pass

    if cur:
        rows.append(cur)

    # 最低限：馬名と馬番がある行だけ
    rows = [r for r in rows if r.get("name") and isinstance(r.get("umaban"), int)]
    return rows

def norm(s: str) -> str:
    s = str(s).replace("\u3000"," ")
    s = re.sub(r"\s+"," ",s).strip()
    return s

def nar_tablephp_html(date: str, track: str, number: str, condition: str, debug=False) -> str:
    url = "https://nar.k-ba.net/table.php"
    params = {"date": date, "track": track, "number": number, "condition": condition}
    return fetch(url, debug=debug, params=params)

def parse_nar_tablephp_rows(html: str):
    soup = BeautifulSoup(html, "lxml")
    t = soup.find("table", id="table")
    if not t:
        return []

    trs = t.find_all("tr")
    if len(trs) < 2:
        return []

    head = [norm(c.get_text(" ", strip=True)) for c in trs[0].find_all(["th","td"])]

    def find_col(keys):
        for i,h in enumerate(head):
            for k in keys:
                if k in h:
                    return i
        return None

    c_umaban = find_col(["馬番","馬","番"])
    c_name   = find_col(["馬名"])
    c_jockey = find_col(["騎手"])
    c_avg    = find_col(["平均指数"])
    if c_avg is None:
        idx_cols = [i for i,h in enumerate(head) if "指数" in h]
        c_avg = max(idx_cols) if idx_cols else None

    if None in (c_umaban, c_name, c_jockey):
        return []

    rows = []
    for tr in trs[1:]:
        tds = tr.find_all(["td","th"])
        if not tds:
            continue
        vals = [norm(td.get_text(" ", strip=True)) for td in tds]
        mx = max(c_umaban, c_name, c_jockey, c_avg or 0)
        if len(vals) <= mx:
            continue

        mban = re.search(r"\d{1,2}", vals[c_umaban])
        if not mban:
            continue

        name = clean_horse_name(vals[c_name])
        jockey = re.sub(r"[◀◁▶▷\s]+", "", vals[c_jockey])

        avg = None
        if c_avg is not None and c_avg < len(vals):
            mavg = re.search(r"(\d+(?:\.\d+)?)", vals[c_avg])
            if mavg:
                try:
                    avg = float(mavg.group(1))
                except Exception:
                    avg = None

        rows.append({
            "umaban": int(mban.group()),
            "name": name,
            "jockey": jockey,
            "avg_index": avg,
        })
    return rows

def fetch_nar_rows_best(date: str, track_id: int, rno: int, debug=False):
    """
    return: (rows, used_condition, source_url, race_name_from_nar)
    """
    track = str(track_id)
    number = str(int(rno))

    url = nar_tablehtml_url(date, track, number)
    html = fetch(url, debug=debug)
    rows = parse_nar_rows_text_fallback(html)
    if rows:
        race_name = parse_nar_race_name(html)
        return rows, None, url, race_name

    for cond in ["1","2","3","4"]:
        html2 = nar_tablephp_html(date, track, number, cond, debug=debug)
        if not html2:
            continue
        rows2 = parse_nar_tablephp_rows(html2) or parse_nar_rows_text_fallback(html2)
        if rows2:
            src = f"https://nar.k-ba.net/table.php?date={date}&track={track}&number={number}&condition={cond}"
            race_name = parse_nar_race_name(html2)
            return rows2, cond, src, race_name
        time.sleep(0.03)

    return [], None, None, ""

# ====== 吉馬（SP能力値） ======
def build_kichiuma_fp_url(yyyymmdd: str, track_id: int, race_no: int) -> str:
    date_slash = f"{yyyymmdd[:4]}/{int(yyyymmdd[4:6])}/{int(yyyymmdd[6:8])}"
    date_enc = date_slash.replace("/", "%2F")
    race_id = f"{yyyymmdd}{race_no:02d}{track_id:02d}"
    return (
        "https://www.kichiuma-chiho.net/php/search.php"
        f"?race_id={race_id}&date={date_enc}&no={race_no}&id={track_id}&p=fp"
    )

def _norm2(s: str) -> str:
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

        headers = [_norm2(c.get_text(" ", strip=True)) for c in head_cells]
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
            first = _norm2(row2[0].get_text(" ", strip=True))
            if re.fullmatch(r"\d{1,2}", first):
                score += 3

        if score > best_score:
            best_score = score
            best = t

    return best if (best and best_score >= 8) else None

def parse_kichiuma_sp(html: str):
    """
    return: (sp_by_umaban: dict[int,float], race_name: str)
    """
    soup = BeautifulSoup(html, "lxml")
    meta = parse_kichiuma_race_meta(html)

    t = find_sp_table(soup)
    if not t:
        return {}, clean_race_name(meta.get("race_name", "") or "")

    trs = t.find_all("tr")
    headers_raw = [c.get_text(" ", strip=True) for c in trs[0].find_all(["th","td"])]
    headers = [_norm2(h) for h in headers_raw]

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
        return {}, clean_race_name(meta.get("race_name", "") or "")

    sp_by = {}
    for tr in trs[1:]:
        cells = tr.find_all(["td","th"])
        if not cells:
            continue
        vals = [c.get_text(" ", strip=True) for c in cells]
        if len(vals) <= max(c_umaban, c_sp):
            continue

        b = _norm2(vals[c_umaban])
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

    return sp_by, clean_race_name(meta.get("race_name", "") or "")

# ===== 混戦度（表示用：任意）=====
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

def should_skip_flat_index(rows) -> bool:
    """
    rows: [{base_index: float or None, ...}, ...]
    指数（base_index）がほぼ横並び（最大-最小が小さい）なら True
    """
    if not SKIP_FLAT_INDEX:
        return False

    vals = []
    for r in rows:
        v = r.get("base_index")
        if isinstance(v, (int, float)):
            vals.append(float(v))

    if len(vals) < FLAT_INDEX_MIN_COUNT:
        return False

    rng = max(vals) - min(vals)
    return rng <= float(FLAT_INDEX_RANGE_MAX)

# ===== 追加②：低シグナル判定（新馬/欠損過多） =====
def is_maiden_like(race_name: str) -> bool:
    s = _norm_text(race_name)
    return ("新馬" in s)

def should_skip_low_signal(rows, race_name: str, sp_by_umaban: dict) -> (bool, str):
    """
    低シグナル（指数欠損だらけ/新馬で材料不足/全部同点）ならスキップ
    戻り値: (skip?, reason)
    """
    if not SKIP_LOW_SIGNAL:
        return False, ""

    n = len(rows)
    if n <= 0:
        return True, "no runners"

    kb_valid = sum(1 for r in rows if isinstance(r.get("base_index"), (int, float)))
    sp_valid = len(sp_by_umaban or {})
    kb_ratio = kb_valid / n

    maiden = is_maiden_like(race_name)

    # 新馬 ＋ 平均指数が少なすぎる
    if maiden and kb_valid < MIN_KB_COUNT:
        return True, f"maiden kb too few (kb_valid={kb_valid}/{n})"

    # 新馬 ＋ SPも少なすぎる
    if maiden and sp_valid < MIN_SP_COUNT:
        return True, f"maiden sp too few (sp_valid={sp_valid})"

    # 非新馬でも欠損が多すぎるなら落とす（保険）
    if kb_ratio < MIN_VALID_RATIO and kb_valid < MIN_KB_COUNT:
        return True, f"kb missing too much (kb_valid={kb_valid}/{n}, ratio={kb_ratio:.2f})"

    return False, ""
# ===== PART 3 / 4 =====
# predict_all_today.py（改造反映版）
# 範囲：HTML描画(render_html)〜スコア計算一式（compute_scores_new）まで
# 次は PART 4 / 4（main本体：変更③＋追加④反映）を貼ってください

# ====== HTML（表示） ======
def render_html(title: str, preds) -> str:
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

    LO = (239, 246, 255)
    HI = (29, 78, 216)

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

    def badge(text: str, bg: str, fg: str = "#111827") -> str:
        return (
            "<span style='display:inline-block;padding:4px 10px;border-radius:999px;"
            f"background:{bg};color:{fg};font-weight:900;font-size:12px;letter-spacing:.02em;"
            "line-height:1;white-space:nowrap;'>"
            f"{esc(text)}</span>"
        )

    def section_title(left: str, right_badge: str, bg: str) -> str:
        return (
            "<div style='display:flex;align-items:center;justify-content:space-between;"
            f"padding:10px 12px;border-radius:12px;background:{bg};margin:10px 0 8px;'>"
            f"<strong style='font-size:14px;'>{esc(left)}</strong>"
            f"<div style='display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap;'>{right_badge}</div>"
            "</div>"
        )

    parts = []
    parts.append("<div style='max-width:980px;margin:0 auto;line-height:1.7;'>")
    parts.append(f"<h2 style='margin:12px 0 8px;font-size:20px;font-weight:900;'>{esc(title)}</h2>")

    for race in preds:
        rno = int(race["race_no"])
        race_name = (race.get("race_name") or "").strip()
        picks = race["picks"]

        scores_in_race = [float(p.get("score", 0.0)) for p in picks if isinstance(p.get("score", None), (int, float))]

        konsen_badge = ""
        if KONSEN_ENABLE:
            k = race.get("konsen") or {}
            kval = k.get("value", None)
            is_focus = bool(k.get("is_focus", False))
            kname = k.get("name", KONSEN_NAME)
            if isinstance(kval, (int, float)):
                if is_focus:
                    konsen_badge = badge(f"注目 {kname}{float(kval):.1f}", "#f59e0b", "#ffffff")
                else:
                    konsen_badge = badge(f"{kname}{float(kval):.1f}", "#6b7280", "#ffffff")

        head = f"{rno}R" + (f" {race_name}" if race_name else "")

        parts.append(
            "<div style='margin:16px 0 18px;padding:12px 12px;"
            "border:1px solid #e5e7eb;border-radius:14px;background:#ffffff;'>"
        )

        parts.append(
            "<div style='display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;'>"
            f"<div style='font-size:18px;font-weight:900;color:#111827;'>{esc(head)}</div>"
            f"<div style='display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap;'>{konsen_badge}</div>"
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
            bgrow = "#ffffff" if i % 2 == 0 else "#f8fafc"
            sc = float(p.get("score", 0.0))
            sc_style = score_style(sc, scores_in_race)

            parts.append(
                f"<tr style='background:{bgrow};'>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;font-weight:900;'>{esc(p.get('mark',''))}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:center;font-variant-numeric:tabular-nums;'>{int(p.get('umaban',0))}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:left;font-weight:750;'>{esc(p.get('name',''))}</td>"
                f"<td style='padding:8px;border-bottom:1px solid #dbeafe;text-align:right;'>"
                f"<span style=\"{sc_style}\">{sc:.2f}</span>"
                f"</td>"
                f"</tr>"
            )

        parts.append("</tbody></table></div>")
        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)

# =========================
# ここから：指数算出（新方式）
# =========================
def _median(vals):
    vals = [v for v in vals if isinstance(v, (int, float)) and not math.isnan(v)]
    if not vals:
        return None
    vals = sorted(vals)
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 == 1 else (vals[mid-1] + vals[mid]) / 2.0

def _mean_std(vals):
    vals = [v for v in vals if isinstance(v, (int, float)) and not math.isnan(v)]
    if not vals:
        return (0.0, 1.0)
    m = sum(vals) / len(vals)
    v = sum((x - m) ** 2 for x in vals) / max(1, (len(vals) - 1))
    sd = math.sqrt(v) if v > 0 else 1.0
    return (m, sd)

def _mad(vals, med):
    vals = [v for v in vals if isinstance(v, (int, float)) and not math.isnan(v)]
    if not vals or med is None:
        return 1.0
    dev = [abs(x - med) for x in vals]
    mad = _median(dev)
    return mad if (mad and mad > 1e-9) else 1.0

def zscore_values(values, method="z"):
    """
    values: list[float or None]
    return: list[z] (Noneは返さない＝全部 float に揃えて返す)
    """
    xs = [v for v in values if isinstance(v, (int, float)) and not math.isnan(v)]
    if not xs:
        # 全部欠損なら全部0扱い
        return [0.0 for _ in values], {"center": 0.0, "scale": 1.0, "method": method}

    if method == "robust":
        med = _median(xs)
        mad = _mad(xs, med)
        # 1.4826 * MAD を “標準偏差相当” に
        scale = 1.4826 * mad
        if scale <= 1e-9:
            scale = 1.0
        z = []
        for v in values:
            if not isinstance(v, (int, float)) or math.isnan(v):
                z.append(0.0)
            else:
                z.append((v - med) / scale)
        return z, {"center": med, "scale": scale, "method": method}

    # method == "z"
    mu, sd = _mean_std(xs)
    if sd <= 1e-9:
        sd = 1.0
    z = []
    for v in values:
        if not isinstance(v, (int, float)) or math.isnan(v):
            z.append(0.0)
        else:
            z.append((v - mu) / sd)
    return z, {"center": mu, "scale": sd, "method": method}

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
    """
    rows: [{base_index(float or None), sp_raw(None可)}...]
    """
    pairs = [(r["base_index"], r["sp_raw"]) for r in rows if (r.get("sp_raw") is not None and isinstance(r.get("base_index"), (int, float)))]
    kb_vals = [r["base_index"] for r in rows if isinstance(r.get("base_index"), (int,float))]

    kb_min = min(kb_vals) if kb_vals else 0.0
    kb_max = max(kb_vals) if kb_vals else 1.0

    sp_obs = [y for _, y in pairs]
    sp_min_obs = min(sp_obs) if sp_obs else 50.0
    sp_max_obs = max(sp_obs) if sp_obs else 75.0

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
        "sp_min_obs": sp_min_obs,
        "sp_max_obs": sp_max_obs,
        "has_linear": (a_b is not None),
        "linear": a_b,
        "kb_min": kb_min,
        "kb_max": kb_max,
    }
    if debug:
        print(f"[DEBUG] SP-estimator info: {info}")
    return est, info

def compute_scores_new(rows, debug=False):
    """
    rows: [{umaban,name,jockey, base_index(None可), jockey_add, sp_raw(None可)}...]

    return: horses_scored list (scoreはfloat 小数2桁)
    """
    # 1) base_index の中央値（KB欠損補完）
    kb_list = [r.get("base_index") for r in rows if isinstance(r.get("base_index"), (int, float))]
    kb_med = _median(kb_list)
    if kb_med is None:
        kb_med = 0.0

    # 2) SP推定器（KBがある馬に対して）
    est_sp, est_info = estimate_sp_factory(rows, debug=debug)

    # 3) SP（欠損は推定、推定不可は中央値）
    sp_raw_list = [r.get("sp_raw") for r in rows if isinstance(r.get("sp_raw"), (int, float))]
    sp_med = _median(sp_raw_list)
    if sp_med is None:
        sp_med = 60.0

    for r in rows:
        if not isinstance(r.get("base_index"), (int, float)):
            r["base_index_filled"] = float(kb_med)
        else:
            r["base_index_filled"] = float(r["base_index"])

        if isinstance(r.get("sp_raw"), (int, float)):
            r["sp_filled"] = float(r["sp_raw"])
        else:
            # 推定：KBがある（filledにしてあるので常に推定可能）
            try:
                r["sp_filled"] = float(est_sp(r["base_index_filled"]))
            except Exception:
                r["sp_filled"] = float(sp_med)

    # 4) Z化（SP / KB / 騎手）
    sp_vals = [r["sp_filled"] for r in rows]
    kb_vals = [r["base_index_filled"] for r in rows]
    j_vals = [float(r.get("jockey_add", 0.0)) for r in rows]

    z_sp, sp_norm = zscore_values(sp_vals, method=("robust" if NORM_METHOD == "robust" else "z"))
    z_kb, kb_norm = zscore_values(kb_vals, method=("robust" if NORM_METHOD == "robust" else "z"))
    z_j,  j_norm  = zscore_values(j_vals,  method=("robust" if NORM_METHOD == "robust" else "z"))

    # 5) 合成 → 表示スケールへ
    wsum = abs(SP_W) + abs(KB_W) + abs(JOCKEY_W)
    if wsum <= 1e-9:
        wsum = 1.0

    horses = []
    for i, r in enumerate(rows):
        comb_z = (SP_W * z_sp[i] + KB_W * z_kb[i] + JOCKEY_W * z_j[i]) / wsum
        score = SCORE_BASE + SCORE_SCALE * comb_z

        horses.append({
            "umaban": int(r["umaban"]),
            "name": clean_horse_name(r["name"]),
            "jockey": r.get("jockey", ""),
            "sp": float(r["sp_filled"]),
            "base_index": float(r["base_index_filled"]),
            "jockey_add": float(r.get("jockey_add", 0.0)),
            "z": {
                "sp": float(z_sp[i]),
                "kb": float(z_kb[i]),
                "jockey": float(z_j[i]),
                "combined": float(comb_z),
                "norm_method": NORM_METHOD,
            },
            "score": float(round(score, 2)),
        })

    if debug:
        print(f"[DEBUG] norm info: sp={sp_norm} kb={kb_norm} j={j_norm}")
        print(f"[DEBUG] sp_est_info: {est_info}")

    return horses
# ===== PART 4 / 4 =====
# predict_all_today.py（改造反映版）
# 範囲：main() 全部
# ※このPARTに「変更③：rows作成後の低シグナルSKIP」＋「追加④：スコア横並びSKIP」反映済み

# =========================================================
# main
# =========================================================
def main():
    yyyymmdd = os.environ.get("DATE") or datetime.now().strftime("%Y%m%d")
    debug = os.environ.get("DEBUG", "").strip() == "1"
    os.makedirs("output", exist_ok=True)

    print(f"[INFO] DATE={yyyymmdd}")
    print(f"[INFO] WEIGHTS SP_W={SP_W} KB_W={KB_W} JOCKEY_W={JOCKEY_W}")
    print(f"[INFO] NORM_METHOD={NORM_METHOD} SCORE_BASE={SCORE_BASE} SCORE_SCALE={SCORE_SCALE}")
    if KONSEN_ENABLE:
        print(f"[INFO] KONSEN name={KONSEN_NAME} gap12_mid={KONSEN_GAP12_MID} gap15_mid={KONSEN_GAP15_MID} focus_th={KONSEN_FOCUS_TH}")
    else:
        print("[INFO] KONSEN disabled")

    active = detect_active_tracks(yyyymmdd, debug=debug)
    print(f"[INFO] active_tracks = {active}")

    for track in active:
        track_id = BABA_CODE.get(track)
        if not track_id:
            print(f"[SKIP] {track}: track_id missing")
            continue
        if track_id in EXCLUDE_BABA:
            print(f"[SKIP] {track}: excluded")
            continue

        jockey_url = KAISEKISYA_JOCKEY_URL.get(track, "")
        jockey_stats = parse_kaisekisya_jockey_table(fetch(jockey_url, debug=False)) if jockey_url else {}

        preds = []
        track_incomplete = False
        nar_missing_streak = 0

        for rno in range(1, 13):
            nar_rows, used_cond, nar_src, race_name_from_nar = fetch_nar_rows_best(yyyymmdd, track_id, rno, debug=False)

            if not nar_rows:
                if rno == 1:
                    print(f"[SKIP] {track}: NAR rows not found (rno=1) -> NO OUTPUT")
                    track_incomplete = True
                    break
                nar_missing_streak += 1
                print(f"[SKIP] {track} {rno}R: データ不足（NAR指数なし） -> skip race")
                if nar_missing_streak >= 2:
                    if debug:
                        print(f"[INFO] {track}: stop at {rno}R (NAR missing streak={nar_missing_streak})")
                    break
                continue
            else:
                nar_missing_streak = 0

            fp_url = build_kichiuma_fp_url(yyyymmdd, track_id, int(rno))
            fp_html = fetch(fp_url, debug=False)
            if not fp_html:
                print(f"[SKIP] {track} {rno}R: データ不足（吉馬SP取得失敗） -> skip race")
                continue

            sp_by_umaban, race_name_kichiuma = parse_kichiuma_sp(fp_html)

            race_name = clean_race_name(race_name_kichiuma) if race_name_kichiuma else ""
            if not race_name:
                race_name = clean_race_name(race_name_from_nar) if race_name_from_nar else ""

            # rows（欠損avg_indexも保持して後段で中央値補完）
            rows = []
            for h in nar_rows:
                try:
                    u = int(h.get("umaban"))
                except Exception:
                    continue

                base = h.get("avg_index", None)
                if isinstance(base, (int, float)):
                    base_val = float(base)
                else:
                    base_val = None  # 欠損

                j = h.get("jockey", "") or ""
                rates = match_jockey_by3(norm_jockey3(j), jockey_stats) if (j and jockey_stats) else None
                add = jockey_add_points(*rates) if rates else 0.0

                sp = sp_by_umaban.get(u)  # Noneあり
                rows.append({
                    "umaban": u,
                    "name": clean_horse_name(h.get("name", "")),
                    "jockey": j,
                    "base_index": base_val,          # Noneあり
                    "jockey_add": float(add),
                    "sp_raw": (float(sp) if sp is not None else None),
                })

            # --- 低シグナル（新馬/欠損過多）をスキップ ---
            race_name_for_judge = race_name or race_name_from_nar or race_name_kichiuma or ""
            skip_low, reason_low = should_skip_low_signal(rows, race_name_for_judge, sp_by_umaban)
            if skip_low:
                print(f"[SKIP] {track} {rno}R: 低シグナル（{reason_low} / race='{race_name_for_judge}'） -> skip race")
                continue

            # 最低限：馬が少なすぎるレースはやめる
            if len(rows) < 5:
                print(f"[SKIP] {track} {rno}R: データ不足（出走馬<5） -> skip race")
                continue

            # 新馬戦っぽい（指数が横並び）レースはスキップ
            if should_skip_flat_index(rows):
                vals = [float(r["base_index"]) for r in rows if isinstance(r.get("base_index"), (int, float))]
                rng = (max(vals) - min(vals)) if vals else 0.0
                print(f"[SKIP] {track} {rno}R: 指数が横並びっぽい（range={rng:.2f} <= {FLAT_INDEX_RANGE_MAX}） -> skip race")
                continue

            horses_scored = compute_scores_new(rows, debug=debug)

            # --- スコアが横並び（ほぼ同点）ならスキップ ---
            scs = [h["score"] for h in horses_scored if isinstance(h.get("score"), (int, float))]
            if scs and (max(scs) - min(scs) <= FLAT_SCORE_RANGE_MAX):
                print(f"[SKIP] {track} {rno}R: スコア横並び（range={max(scs)-min(scs):.2f} <= {FLAT_SCORE_RANGE_MAX}） -> skip race")
                continue

            # スコアでソート（同点はSP→KB→馬番）
            horses_scored.sort(key=lambda x: (-x["score"], -x["sp"], -x["base_index"], x["umaban"]))
            top5 = horses_scored[:5]

            konsen = None
            if KONSEN_ENABLE and len(top5) >= 5:
                top5_scores = [float(h["score"]) for h in top5]
                konsen = calc_konsen_gap(top5_scores)
                if KONSEN_DEBUG:
                    print(f"[KONSEN] {track} {rno}R top5_scores={top5_scores} konsen={konsen}")

            picks = []
            for j, hh in enumerate(top5):
                picks.append({
                    "mark": MARKS5[j],
                    "umaban": int(hh["umaban"]),
                    "name": clean_horse_name(hh["name"]),
                    "score": float(hh["score"]),               # ★小数2桁
                    "sp": float(hh["sp"]),
                    "base_index": float(hh["base_index"]),
                    "jockey": hh.get("jockey", ""),
                    "jockey_add": float(hh["jockey_add"]),
                    "z": hh.get("z", {}),
                    "source": {
                        "kichiuma_fp_url": fp_url,
                        "nar_table_url": nar_src,
                        "nar_condition": used_cond,
                    },
                })

            payload = {
                "race_no": int(rno),
                "race_name": race_name,
                "picks": picks,
            }
            if KONSEN_ENABLE:
                payload["konsen"] = (konsen or {
                    "name": KONSEN_NAME, "value": 0.0, "is_focus": False,
                    "gap12": None, "gap15": None, "gap12_mid": KONSEN_GAP12_MID, "gap15_mid": KONSEN_GAP15_MID, "focus_th": KONSEN_FOCUS_TH
                })

            preds.append(payload)
            time.sleep(0.05)

        if track_incomplete:
            continue
        if not preds:
            print(f"[SKIP] {track}: preds empty -> NO OUTPUT")
            continue

        title = f"{yyyymmdd[0:4]}.{yyyymmdd[4:6]}.{yyyymmdd[6:8]} {track}競馬 予想"

        out = {
            "date": yyyymmdd,
            "place": track,
            "place_code": str(track_id),
            "title": title,
            "predictions": preds,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "weights": {"SP_W": SP_W, "KB_W": KB_W, "JOCKEY_W": JOCKEY_W},
            "scoring": {
                "method": "zscore_composite",
                "norm_method": NORM_METHOD,
                "score_base": SCORE_BASE,
                "score_scale": SCORE_SCALE,
                "notes": "SP/KB/騎手をレース内で標準化（Z化）し、欠損は推定 or レース中央値で補完して合成",
            },
            "konsen_config": {
                "enabled": KONSEN_ENABLE,
                "name": KONSEN_NAME,
                "gap12_mid": KONSEN_GAP12_MID,
                "gap15_mid": KONSEN_GAP15_MID,
                "focus_th": KONSEN_FOCUS_TH,
            },
            "source": {
                "kaisekisya_url": jockey_url,
                "nar_base": "https://nar.k-ba.net/",
            }
        }

        code = str(track_id)
        json_path = Path("output") / f"predict_{yyyymmdd}_{code}.json"
        html_path = Path("output") / f"predict_{yyyymmdd}_{code}.html"

        json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path.write_text(render_html(title, preds), encoding="utf-8")

        print(f"[OK] {track} -> {json_path.name} / {html_path.name}  (track={track_id})")

if __name__ == "__main__":
    main()
