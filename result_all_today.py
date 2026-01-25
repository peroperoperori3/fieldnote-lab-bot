# result_all_today.py  (fieldnote-lab-bot)  NAR(table.php)対応版
# 変更点：
# - 予想の元データを keibablood/kichiuma から NAR(table.php) の「平均指数」に切替
# - keiba.go.jp の結果/払戻/PNL/HTML/JSON構造は極力維持

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


# =========================
# PNL（累計収支）保存用
# =========================
def load_pnl_total(path: str):
    p = Path(path)
    if not p.exists():
        return {
            "invest": 0, "payout": 0, "profit": 0,
            "races": 0, "hits": 0,
            "last_updated": None,
        }
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError("invalid pnl_total.json")

        d.setdefault("invest", 0)
        d.setdefault("payout", 0)
        d.setdefault("profit", 0)
        d.setdefault("races", 0)
        d.setdefault("hits", 0)
        d.setdefault("last_updated", None)
        return d
    except Exception:
        return {
            "invest": 0, "payout": 0, "profit": 0,
            "races": 0, "hits": 0,
            "last_updated": None,
        }

def save_pnl_total(path: str, total: dict):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(total, ensure_ascii=False, indent=2), encoding="utf-8")


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


# keiba.go.jp babaCode（開催判定用）※帯広除外
BABA_CODE = {
  "門別": 36, "盛岡": 10, "水沢": 11, "浦和": 18, "船橋": 19, "大井": 20, "川崎": 21,
  "金沢": 22, "笠松": 23, "名古屋": 24, "園田": 27, "姫路": 28, "高知": 31, "佐賀": 32,
}

# ★出力ファイル名用（従来どおり keibabloodコードを維持）
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


# =========================
# ★NAR table.php（平均指数）取得
# =========================
def _norm_text(s: str) -> str:
    s = str(s).replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def clean_horse_name(name: str) -> str:
    """
    馬名の後ろに付く余計な文字（例: '5ヶ月前' 等）を除去して馬名だけ返す
    """
    s = _norm_text(name)

    # よくある「○ヶ月前」「○日前」などを末尾から除去
    s = re.sub(r"\s*\d+\s*(?:ヶ月前|か月前|日前|時間前)\s*$", "", s)

    # 末尾に余計な注記が入るケースの保険（必要なら追加でパターン増やせる）
    s = re.sub(r"\s*(?:想定|取消|除外)\s*$", "", s)

    return s.strip()


def clean_race_name(race_name: str) -> str:
    s = _norm_text(race_name)
    s = re.sub(r"\s+\d{1,2}R\s*.*$", "", s)
    s = s.split("»")[0].strip()
    s = s.split("«")[0].strip()
    return s.strip()

def nar_tablephp_url(date: str, track: str, number: str, condition: str = "1") -> str:
    # https://nar.k-ba.net/table.php?date=20260125&track=31&number=1&condition=1
    return f"https://nar.k-ba.net/table.php?date={date}&track={track}&number={number}&condition={condition}"

def fetch_nar_tablephp(date: str, track: str, number: str, condition: str = "1", debug=False) -> str:
    url = "https://nar.k-ba.net/table.php"
    params = {"date": date, "track": track, "number": number, "condition": condition}
    try:
        r = requests.get(url, params=params, headers=UA, timeout=25)
    except Exception as e:
        if debug:
            print(f"[GET] {url} params={params} ERROR={e}")
        return ""
    if debug:
        print(f"[GET] {r.url} status={r.status_code} bytes={len(r.content)}")
    if r.status_code != 200:
        return ""
    r.encoding = r.apparent_encoding
    return r.text

def parse_nar_tablephp(html: str):
    """
    return: (rows, race_name)
      rows: [{"umaban":1,"name":"...","jockey":"...","avg_index":56.0}, ...]
      race_name: h3見出し等（取れなければ空）
    """
    if not html:
        return [], ""

    soup = BeautifulSoup(html, "lxml")

    # レース名/条件クラス：ページ内の <h3> がそれっぽい（例: ３歳－５）
    race_name = ""
    h3 = soup.find("h3")
    if h3:
        race_name = clean_race_name(h3.get_text(" ", strip=True))

    t = soup.find("table", id="table")
    if not t:
        return [], race_name

    trs = t.find_all("tr")
    if len(trs) < 2:
        return [], race_name

    head = [_norm_text(c.get_text(" ", strip=True)) for c in trs[0].find_all(["th","td"])]

    def find_col(keys):
        for i, h in enumerate(head):
            for k in keys:
                if k in h:
                    return i
        return None

    c_umaban = find_col(["馬番", "馬", "番"])
    c_name   = find_col(["馬名"])
    c_jockey = find_col(["騎手"])

    c_avg    = find_col(["平均指数"])
    if c_avg is None:
        idx_cols = [i for i, h in enumerate(head) if "指数" in h]
        c_avg = max(idx_cols) if idx_cols else None

    if None in (c_umaban, c_name, c_jockey, c_avg):
        return [], race_name

    rows = []
    for tr in trs[1:]:
        tds = tr.find_all(["td","th"])
        if not tds:
            continue
        vals = [_norm_text(td.get_text(" ", strip=True)) for td in tds]
        mx = max(c_umaban, c_name, c_jockey, c_avg)
        if len(vals) <= mx:
            continue

        mban = re.search(r"\d{1,2}", vals[c_umaban])
        if not mban:
            continue

        name = clean_horse_name(vals[c_name])
        jockey = re.sub(r"[◀◁▶▷\s]+", "", vals[c_jockey])

        mavg = re.search(r"(\d+(?:\.\d+)?)", vals[c_avg])
        if not mavg:
            continue

        rows.append({
            "umaban": int(mban.group()),
            "name": name,
            "jockey": jockey,
            "avg_index": float(mavg.group(1)),
        })

    return rows, race_name


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
            umaban = int(tds[2]); name = clean_horse_name(tds[3])
        elif len(tds) >= 3 and re.fullmatch(r"\d+", tds[1]):
            umaban = int(tds[1]); name = clean_horse_name(tds[2])
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


def build_racemark_url(baba: int, yyyymmdd: str, rno: int):
    date_slash = f"{yyyymmdd[0:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:8]}"
    return (
        "https://www.keiba.go.jp/KeibaWeb/TodayRaceInfo/RaceMarkTable"
        f"?k_babaCode={baba}&k_raceDate={date_slash}&k_raceNo={int(rno)}"
    )


# ====== HTML（あなたの現行をそのまま） ======
def render_result_html(title: str, races_out, pnl_summary: dict) -> str:
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

    if pnl_summary:
        invest = int(pnl_summary.get("invest", 0) or 0)
        payout = int(pnl_summary.get("payout", 0) or 0)
        profit = int(pnl_summary.get("profit", 0) or 0)
        races = int(pnl_summary.get("focus_races", 0) or 0)
        hits = int(pnl_summary.get("hits", 0) or 0)
        unit = int(pnl_summary.get("bet_unit", BET_UNIT) or BET_UNIT)
        nbox = int(pnl_summary.get("box_n", BET_BOX_N) or BET_BOX_N)
        pts = int(pnl_summary.get("bet_points_per_race", comb3_count(nbox)) or comb3_count(nbox))

        roi = round((payout / invest) * 100.0, 1) if invest > 0 else 0.0
        hit_rate = round((hits / races) * 100.0, 1) if races > 0 else 0.0

        profit_badge = badge(f"収支 {profit:+,}円", "#f59e0b" if profit >= 0 else "#ef4444", "#ffffff")
        roi_badge = badge(f"回収率 {roi:.1f}%", "#6b7280", "#ffffff")
        hit_badge = badge(f"的中率 {hit_rate:.1f}%（{hits}/{races}）", "#6b7280", "#ffffff")

        parts.append(
            "<div style='margin:14px 0 18px;padding:12px 12px;"
            "border:1px solid #e5e7eb;border-radius:14px;background:#ffffff;'>"
            "<div style='display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;'>"
            f"<div style='font-size:16px;font-weight:900;color:#111827;'>注目レース（三連複BOX） 収支サマリ</div>"
            f"<div style='display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap;'>{profit_badge}{roi_badge}{hit_badge}</div>"
            "</div>"
            "<div style='margin-top:10px;display:flex;gap:10px;flex-wrap:wrap;'>"
            f"{badge(f'注目レース {races}本', '#bfdbfe')}"
            f"{badge(f'買い目 {nbox}頭BOX（{pts}点）', '#bfdbfe')}"
            f"{badge(f'1点 {unit}円', '#bfdbfe')}"
            f"{badge(f'投資 {invest:,}円', '#6b7280', '#ffffff')}"
            f"{badge(f'払戻 {payout:,}円', '#6b7280', '#ffffff')}"
            "</div>"
            "<div style='margin-top:8px;color:#6b7280;font-size:12px;'>※注目レースのみ指数上位5頭三連複BOX集計</div>"
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
    print(f"[INFO] SOURCE = NAR table.php (avg_index) + kaisekisya(jockey) + keiba.go.jp(result/refund)")

    active = detect_active_tracks_keibago(yyyymmdd, debug=DEBUG)
    print(f"[INFO] active_tracks = {active}")

    # ===== 累計PnL（1回だけ読み込む）=====
    pnl_total = load_pnl_total(PNL_FILE)

    for track in active:
        baba = BABA_CODE.get(track)
        track_id = BABA_CODE.get(track)  # ★NARのtrackもこれを使う（あなたの検証通り）
        place_code = KEIBABLOOD_CODE.get(track)  # ★ファイル名（従来どおり）
        if not baba or not track_id or not place_code:
            print(f"[SKIP] {track}: code missing")
            continue

        # ---- 騎手成績 ----
        jockey_url = KAISEKISYA_JOCKEY_URL.get(track, "")
        jockey_stats = parse_kaisekisya_jockey_table(fetch(jockey_url, debug=False)) if jockey_url else {}

        # ---- 払戻（当日払戻金）を先にまとめて取得（同着対応） ----
        ref_url = refundmoney_url(baba, yyyymmdd)
        ref_html = fetch(ref_url, debug=False)
        sanrenpuku_map = parse_refundmoney_sanrenpuku_by_race(ref_html) if ref_html else {}
        if REFUND_DEBUG:
            print(f"[REFUND_DEBUG] {track} refundmoney_url={ref_url} races_with_sanrenpuku={len(sanrenpuku_map)}")

        races_out = []
        track_incomplete = False

        # track内の注目BOX収支（記事サマリ用）
        focus_races = 0
        invest_sum = 0
        payout_sum = 0
        hits_sum = 0

        # ★地方は最大12R想定（足りなければ途中でbreak）
        miss_streak = 0
        for rno in range(1, 13):
            # ---- NAR 平均指数（table.php）取得 ----
            nar_html = fetch_nar_tablephp(
                date=yyyymmdd,
                track=str(track_id),
                number=str(rno),
                condition="1",  # 良（まずは固定：必要なら後で条件自動推定も可能）
                debug=False
            )
            nar_rows, race_name = parse_nar_tablephp(nar_html)

            if not nar_rows or len(nar_rows) < 5:
                miss_streak += 1
                if DEBUG:
                    print(f"[MISS] {track} {rno}R nar_rows={len(nar_rows)} -> skip")
                # 連続で取れないなら終了（例：その日9Rまで）
                if miss_streak >= 2 and rno >= 8:
                    break
                continue
            miss_streak = 0

            # ---- スコア計算（平均指数 + 騎手補正）----
            horses_scored = []
            for h in nar_rows:
                u = int(h["umaban"])
                base = float(h["avg_index"])  # 平均指数
                j = h.get("jockey", "")

                rates = match_jockey_by3(norm_jockey3(j), jockey_stats) if (j and jockey_stats) else None
                add = jockey_add_points(*rates) if rates else 0.0

                # ★「SP」として平均指数を使う（最小変更）
                sp = base
                score = (SP_W * sp) + (KB_W * base) + (JOCKEY_W * add)

                horses_scored.append({
                    "umaban": u,
                    "name": h.get("name", ""),
                    "jockey": j,
                    "sp": float(sp),
                    "base_index": float(base),
                    "jockey_add": float(add),
                    "score": float(score),
                    "source": {
                        "nar_tablephp_url": nar_tablephp_url(yyyymmdd, str(track_id), str(rno), "1")
                    },
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
                print(
                    f"[KONSEN] {track} {rno}R "
                    f"value={konsen.get('value')} "
                    f"focus={konsen.get('is_focus')} "
                    f"gap12={konsen.get('gap12')} "
                    f"gap15={konsen.get('gap15')} "
                    f"top5_scores={[round(x,2) for x in top5_scores]}"
                )

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
                hit, payout100, combos = box_hit_payout(top_umaban, san)
                payout = int(payout100 * (BET_UNIT / 100.0))
                profit = int(payout) - int(invest)

                invest_sum += int(invest)
                payout_sum += int(payout)
                hits_sum += (1 if hit else 0)

                pnl_total["races"] = int(pnl_total.get("races", 0) or 0) + 1
                pnl_total["hits"]  = int(pnl_total.get("hits", 0) or 0) + (1 if hit else 0)
                pnl_total["invest"] = int(pnl_total.get("invest", 0) or 0) + int(invest)
                pnl_total["payout"] = int(pnl_total.get("payout", 0) or 0) + int(payout)

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
                    "refundmoney_url": ref_url,
                    "nar_tablephp_url": nar_tablephp_url(yyyymmdd, str(track_id), str(rno), "1"),
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
            "hits": int(hits_sum),
            "box_n": int(BET_BOX_N),
            "bet_unit": int(BET_UNIT),
            "bet_points_per_race": int(comb3_count(BET_BOX_N)),
            "invest": int(invest_sum),
            "payout": int(payout_sum),
            "profit": int(profit_sum),
        }

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
                "kaisekisya_url": jockey_url,
                "refundmoney_url": ref_url,
                "nar_track_code": track_id,
            }
        }

        json_path = Path("output") / f"result_{yyyymmdd}_{place_code}.json"
        html_path = Path("output") / f"result_{yyyymmdd}_{place_code}.html"

        json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path.write_text(render_result_html(title, races_out, pnl_summary), encoding="utf-8")

        print(f"[OK] {track} -> {json_path.name} / {html_path.name}  focus={focus_races} hits={hits_sum} profit={profit_sum:+,}円")

    # ===== 最後に累計を整形して保存 =====
    invest = int(pnl_total.get("invest", 0) or 0)
    payout = int(pnl_total.get("payout", 0) or 0)
    races  = int(pnl_total.get("races", 0) or 0)
    hits   = int(pnl_total.get("hits", 0) or 0)

    pnl_total["profit"] = int(payout - invest)
    pnl_total["roi"] = round((payout / invest) * 100.0, 1) if invest > 0 else None
    pnl_total["hit_rate"] = round((hits / races) * 100.0, 1) if races > 0 else None
    pnl_total["last_updated"] = datetime.now().isoformat(timespec="seconds")

    save_pnl_total(PNL_FILE, pnl_total)
    print(f"[OK] wrote {PNL_FILE}")


if __name__ == "__main__":
    main()
