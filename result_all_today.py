# result_all_today.py  (fieldnote-lab-bot)  ★PREDICT完全一致版（推奨） + latest_local_result.json 追加
# 目的：
# - 「result の指数（上位5頭）」を predict と 100% 同じにする
# やり方：
# - result 側では指数の再計算をやめて、predict が出力した JSON（predict_YYYYMMDD_XX.json）を読み込んで使う
# - その上で、keiba.go.jp から「結果（1〜3着）」と「払戻（三連複）」を取って、HTML/JSON/PNLを作る
#
# これで「predict と result の上位5頭・指数・混戦度」がズレなくなります。
# ★追加：output/latest_local_result.json を「その日に1つでも結果を書けた時だけ」生成

import os, re, json, time, glob
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0", "Accept-Language": "ja,en;q=0.8"}
MARKS5 = ["◎", "〇", "▲", "△", "☆"]

# ===== 混戦度の名前（表示用：predictからも来るけど保険）=====
KONSEN_NAME = os.environ.get("KONSEN_NAME", "混戦度")

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

            # ★追加：全体予想（上位5で1-3着）累計
            "pred_races": 0,
            "pred_hits": 0,
            "pred_hit_rate": None,
            "pred_by_place": {},  # { "船橋": {"races":..,"hits":..,"hit_rate":..}, ... }
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

        # ★追加キー（既存ファイル互換）
        d.setdefault("pred_races", 0)
        d.setdefault("pred_hits", 0)
        d.setdefault("pred_hit_rate", None)
        d.setdefault("pred_by_place", {})

        if not isinstance(d.get("pred_by_place"), dict):
            d["pred_by_place"] = {}

        return d
    except Exception:
        return {
            "invest": 0, "payout": 0, "profit": 0,
            "races": 0, "hits": 0,
            "last_updated": None,

            "pred_races": 0,
            "pred_hits": 0,
            "pred_hit_rate": None,
            "pred_by_place": {},
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


# =========================
# ★predict JSON を探す（これが“完全一致”のキモ）
# =========================
def _find_predict_json(yyyymmdd: str, code: str):
    # code は「baba（19とか）」または「place_code（43とか）」のどっちでもOK
    code = str(code)
    cand = []
    cand += glob.glob(f"output/predict_{yyyymmdd}_{code}.json")
    cand += glob.glob(f"predict_{yyyymmdd}_{code}.json")
    cand += glob.glob(f"out/predict_{yyyymmdd}_{code}.json")
    cand += glob.glob(f"**/predict_{yyyymmdd}_{code}.json", recursive=True)

    cand = [c for c in cand if Path(c).is_file()]
    return cand[0] if cand else None

def _norm_pred_races(pred_json: dict):
    """predict JSON を result 用に正規化する。
    対応フォーマット：
      - newer: { predictions: [ {race_no, race_name, picks:[...], konsen:{...}} ] }
      - older: { races: [ {race_no, race_name, pred_top5:[...], konsen:{...}} ] }
    """
    # --- 新フォーマット（あなたの実データ）---
    preds = pred_json.get("predictions")
    if isinstance(preds, list):
        out = []
        for r in preds:
            if not isinstance(r, dict):
                continue
            rno = r.get("race_no")
            try:
                rno = int(rno)
            except Exception:
                continue

            race_name = r.get("race_name") or r.get("name") or ""
            konsen = r.get("konsen") if isinstance(r.get("konsen"), dict) else {}

            picks = r.get("picks") or []
            if not isinstance(picks, list):
                picks = []

            pred_top5 = []
            for i, p in enumerate(picks[:5]):
                if not isinstance(p, dict):
                    continue
                umaban = p.get("umaban") or p.get("umaban_no") or p.get("horse_no") or p.get("num")
                try:
                    umaban = int(umaban)
                except Exception:
                    continue

                name = p.get("name") or p.get("horse_name") or ""
                score = p.get("score")
                try:
                    score = float(score)
                except Exception:
                    continue

                mark = p.get("mark") or (MARKS5[i] if i < len(MARKS5) else "—")

                pred_top5.append({
                    "mark": str(mark),
                    "umaban": int(umaban),
                    "name": str(name),
                    "score": float(score),
                    # 互換：predictが持ってるなら残す
                    "sp": p.get("sp"),
                    "base_index": p.get("base_index"),
                    "jockey": p.get("jockey", ""),
                    "jockey_add": p.get("jockey_add", 0.0),
                    "source": p.get("source", {}),
                })

            if pred_top5:
                out.append({
                    "race_no": rno,
                    "race_name": str(race_name or ""),
                    "pred_top5": pred_top5,
                    "konsen": konsen,
                })
        return out

    # --- 旧フォーマット ---
    races = pred_json.get("races") or pred_json.get("RACES") or []
    if not isinstance(races, list):
        return []
    out = []
    for r in races:
        if not isinstance(r, dict):
            continue
        rno = r.get("race_no") or r.get("raceNo") or r.get("no")
        try:
            rno = int(rno)
        except Exception:
            continue

        top5 = r.get("pred_top5") or r.get("top5") or r.get("predict_top5") or []
        if not isinstance(top5, list):
            top5 = []

        pred_top5 = []
        for i, p in enumerate(top5[:5]):
            if not isinstance(p, dict):
                continue
            umaban = p.get("umaban") or p.get("umaban_no") or p.get("horse_no") or p.get("num")
            try:
                umaban = int(umaban)
            except Exception:
                continue
            name = p.get("name") or p.get("horse_name") or ""
            score = p.get("score") if "score" in p else (p.get("sp") or p.get("index"))
            try:
                score = float(score)
            except Exception:
                continue

            mark = p.get("mark") or (MARKS5[i] if i < len(MARKS5) else "—")
            pred_top5.append({
                "mark": mark,
                "umaban": int(umaban),
                "name": str(name),
                "score": float(score),
                "sp": p.get("sp"),
                "base_index": p.get("base_index"),
                "jockey": p.get("jockey", ""),
                "jockey_add": p.get("jockey_add", 0.0),
                "source": p.get("source", {}),
            })

        konsen = r.get("konsen") if isinstance(r.get("konsen"), dict) else {}
        race_name = r.get("race_name") or r.get("name") or ""
        if pred_top5:
            out.append({
                "race_no": rno,
                "race_name": str(race_name or ""),
                "pred_top5": pred_top5,
                "konsen": konsen,
            })
    return out

def load_predict_for_track(yyyymmdd: str, baba: int, place_code: str):
    """predict JSON を読み込んで race_no -> dict を返す。
    探し方：
      - まず baba（例: 19）で探す（あなたの現状のpredict出力はこれ）
      - 見つからなければ place_code（例: 43）でも探す（将来の保険）
    """
    path = _find_predict_json(yyyymmdd, str(baba))
    if not path:
        path = _find_predict_json(yyyymmdd, str(place_code))

    if not path:
        return None, None

    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] predict json read failed: {path} err={e}")
        return None, path

    races = _norm_pred_races(d)
    if not races:
        print(f"[WARN] predict json has no races: {path}")
        return None, path

    race_map = {int(r["race_no"]): r for r in races}
    return race_map, path


# =========================
# 結果ページ用の整形
# =========================
def _norm_text(s: str) -> str:
    s = str(s).replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def clean_horse_name(name: str) -> str:
    s = _norm_text(name)
    s = re.sub(r"\s*\d+\s*(?:ヶ月前|か月前|日前|時間前)\s*$", "", s)
    s = re.sub(r"\s*(?:想定|取消|除外)\s*$", "", s)
    return s.strip()

def clean_race_name(raw: str) -> str:
    s = _norm_text(raw)

    # "8R ..." のようなプレフィックスが入る場合は落とす
    s = re.sub(r"^\s*\d{1,2}R\s*", "", s).strip()

    # "«" 以降（パンくず/ナビ等）を切り捨て
    if "«" in s:
        s = s.split("«")[0].strip()

    # それでも "»" 等が残る場合
    if "»" in s:
        s = s.split("»")[0].strip()

    # 表記ゆれ整形（お好み）
    s = s.replace("－", "-").replace("―", "-").replace("—", "-")
    s = re.sub(r"\s*-\s*", "-", s).strip()

    return s

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


# =========================
# ★追加：全体的中判定（上位5に1-3着が全部入ってたら的中）
# =========================
def is_pred_hit(pred_top5, result_top3) -> bool:
    if not pred_top5 or not result_top3:
        return False
    if len(result_top3) < 3:
        return False
    s5 = set()
    for p in pred_top5[:5]:
        try:
            s5.add(int(p.get("umaban")))
        except Exception:
            pass
    if len(s5) == 0:
        return False
    top3_nums = []
    for x in result_top3[:3]:
        try:
            top3_nums.append(int(x.get("umaban")))
        except Exception:
            return False
    return set(top3_nums).issubset(s5)


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


# =========================
# ★追加（最小変更）：表示用の三連複を1つ選ぶ
# - 結果(1-3着)が取れていれば、その組合せと一致する三連複を優先
# - 同着などで複数あっても、まずは先頭1つだけ表示用に返す（JS崩さない）
# =========================
def pick_sanrenpuku_for_display(result_top3, san_rows):
    # 結果があるなら、その三連複（1-2-3着）と一致するものを探す
    if result_top3 and len(result_top3) >= 3:
        try:
            nums = sorted([
                int(result_top3[0]["umaban"]),
                int(result_top3[1]["umaban"]),
                int(result_top3[2]["umaban"]),
            ])
            key = "-".join(map(str, nums))
        except Exception:
            key = ""

        if key:
            matched = [r for r in (san_rows or []) if _norm_combo(r.get("combo", "")) == key]
            if matched:
                return matched[:1]

    # 結果がない/一致なし → 取れた先頭を表示
    return (san_rows or [])[:1]


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


# ====== HTML（あなたの現行デザイン維持） ======
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
    parts.append("<div class='fn-post' style='max-width:980px;margin:0 auto;line-height:1.7;color:#111827;'>")

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
        race_name = clean_race_name((r.get("race_name") or "").strip())

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

        # ★追加：全体的中バッジ（上位5で1-3着）
        pred_hit = bool(r.get("pred_hit", False))
        pred_hit_badge = badge(("的中" if pred_hit else "不的中"), "#10b981" if pred_hit else "#6b7280", "#ffffff")

        parts.append(
            "<div style='margin:16px 0 18px;padding:12px 12px;"
            "border:1px solid #e5e7eb;border-radius:14px;background:#ffffff;'>"
        )

        head = f"{rno}R" + (f" {race_name}" if race_name else "")
        parts.append(
            "<div style='display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;'>"
            f"<div style='font-size:18px;font-weight:900;color:#111827;'>{esc(head)}</div>"
            f"<div style='display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap;'>{konsen_badge}{pred_hit_badge}</div>"
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
    print(f"[INFO] BET enabled={BET_ENABLED} bet_unit={BET_UNIT} box_n={BET_BOX_N}")
    print(f"[INFO] SOURCE = predict JSON (指数/混戦度は完全一致) + keiba.go.jp(result/refund)")

    active = detect_active_tracks_keibago(yyyymmdd, debug=DEBUG)
    print(f"[INFO] active_tracks = {active}")

    # ===== 累計PnL（1回だけ読み込む）=====
    pnl_total = load_pnl_total(PNL_FILE)

    # =========================
    # ★LATEST 用（追加）
    # =========================
    wrote_any = False
    wrote_places = []
    wrote_files = []

    for track in active:
        baba = BABA_CODE.get(track)
        place_code = KEIBABLOOD_CODE.get(track)  # ★ファイル名（従来どおり）
        if not baba or not place_code:
            print(f"[SKIP] {track}: code missing")
            continue

        # ★predict読み込み（ここが最重要）
        pred_map, pred_path = load_predict_for_track(yyyymmdd, baba, place_code)
        if not pred_map:
            print(f"[SKIP] {track}: predict json not found. (need predict run first) baba={baba} place_code={place_code}")
            continue

        if DEBUG:
            print(f"[DEBUG] {track}: using predict={pred_path}")

        # ---- 払戻（当日払戻金）を先にまとめて取得（同着対応） ----
        ref_url = refundmoney_url(baba, yyyymmdd)
        ref_html = fetch(ref_url, debug=False)
        sanrenpuku_map = parse_refundmoney_sanrenpuku_by_race(ref_html) if ref_html else {}
        if REFUND_DEBUG:
            print(f"[REFUND_DEBUG] {track} refundmoney_url={ref_url} races_with_sanrenpuku={len(sanrenpuku_map)}")

        races_out = []

        # track内の注目BOX収支（記事サマリ用）
        focus_races = 0
        invest_sum = 0
        payout_sum = 0
        hits_sum = 0

        # ★追加：開催場ごとの全体的中（当日分の集計）
        track_pred_races = 0
        track_pred_hits = 0

        # ★地方は最大12R想定（predictにあるレースだけ処理）
        for rno in range(1, 13):
            pr = pred_map.get(int(rno))
            if not pr:
                continue

            pred_top5 = pr.get("pred_top5", [])
            if not pred_top5 or len(pred_top5) < 5:
                continue

            konsen = pr.get("konsen") or {}
            race_name = clean_race_name(pr.get("race_name") or "")

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

            # ★追加：全体的中判定（結果が取れてる時だけ累計）
            pred_hit = is_pred_hit(pred_top5, result_top3)
            if result_top3 and len(result_top3) >= 3:
                track_pred_races += 1
                track_pred_hits += (1 if pred_hit else 0)

            # ★追加（最小変更）：表示用 sanrenpuku を 1つ作る（JS用）
            san_disp_rows = pick_sanrenpuku_for_display(result_top3, san)
            sanrenpuku_obj = None
            if san_disp_rows:
                sanrenpuku_obj = {
                    "combo": _norm_combo(san_disp_rows[0].get("combo", "")),
                    "payout": int(san_disp_rows[0].get("payout", 0) or 0),
                }

            # ---- 注目レースの三連複BOX収支（同着で複数三連複があれば全部加算）----
            bet_box = {"is_focus": False}
            is_focus = bool((konsen or {}).get("is_focus", False))
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

                # ★追加：全体的中バッジ用フラグ
                "pred_hit": bool(pred_hit),

                # ★追加（最小変更）：三連複バッジ用（地方RESULTのJS表示で使う）
                "sanrenpuku": sanrenpuku_obj,

                "bet_box": bet_box,
                "source": {
                    "predict_json": pred_path,
                    "racemark_url": rm_url,
                    "refundmoney_url": ref_url,
                }
            })

            time.sleep(0.08)

        if not races_out:
            print(f"[SKIP] {track}: no races built (maybe predict json empty)")
            continue

        # ★追加：開催場ごとの全体的中を累計に反映（結果が取れてるレースのみ）
        if track_pred_races > 0:
            pnl_total["pred_races"] = int(pnl_total.get("pred_races", 0) or 0) + int(track_pred_races)
            pnl_total["pred_hits"]  = int(pnl_total.get("pred_hits", 0) or 0) + int(track_pred_hits)

            byp = pnl_total.get("pred_by_place", {})
            if not isinstance(byp, dict):
                byp = {}
            rec = byp.get(track, {})
            if not isinstance(rec, dict):
                rec = {}
            rec["races"] = int(rec.get("races", 0) or 0) + int(track_pred_races)
            rec["hits"]  = int(rec.get("hits", 0) or 0) + int(track_pred_hits)
            rrr = int(rec.get("races", 0) or 0)
            hhh = int(rec.get("hits", 0) or 0)
            rec["hit_rate"] = round((hhh / rrr) * 100.0, 1) if rrr > 0 else None
            byp[track] = rec
            pnl_total["pred_by_place"] = byp

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
            "source": {
                "predict_json": pred_path,
                "refundmoney_url": ref_url,
            }
        }

        json_path = Path("output") / f"result_{yyyymmdd}_{place_code}.json"
        html_path = Path("output") / f"result_{yyyymmdd}_{place_code}.html"

        json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path.write_text(render_result_html(title, races_out, pnl_summary), encoding="utf-8")

        print(f"[OK] {track} -> {json_path.name} / {html_path.name}  focus={focus_races} hits={hits_sum} profit={profit_sum:+,}円")

        # =========================
        # ★LATEST 用（追加）：この開催場で出力できた印
        # =========================
        wrote_any = True
        wrote_places.append(track)
        wrote_files.append({
            "place": track,
            "place_code": place_code,
            "json": str(json_path).replace("\\", "/"),
            "html": str(html_path).replace("\\", "/"),
        })

    # ===== 最後に累計を整形して保存 =====
    invest = int(pnl_total.get("invest", 0) or 0)
    payout = int(pnl_total.get("payout", 0) or 0)
    races  = int(pnl_total.get("races", 0) or 0)
    hits   = int(pnl_total.get("hits", 0) or 0)

    pnl_total["profit"] = int(payout - invest)
    pnl_total["roi"] = round((payout / invest) * 100.0, 1) if invest > 0 else None
    pnl_total["hit_rate"] = round((hits / races) * 100.0, 1) if races > 0 else None

    # ★追加：全体予想（上位5で1-3着）累計の率
    pr = int(pnl_total.get("pred_races", 0) or 0)
    ph = int(pnl_total.get("pred_hits", 0) or 0)
    pnl_total["pred_hit_rate"] = round((ph / pr) * 100.0, 1) if pr > 0 else None

    pnl_total["last_updated"] = datetime.now().isoformat(timespec="seconds")

    save_pnl_total(PNL_FILE, pnl_total)
    print(f"[OK] wrote {PNL_FILE}")

    # =========================
    # ★LATEST 地方結果（追加）
    # - その日に1つでも result を書けた時だけ更新
    # =========================
        if wrote_any:
            latest_path = Path("output/latest_local_result.json")
            latest_path.write_text(
                json.dumps({"date": yyyymmdd}, ensure_ascii=False),
                encoding="utf-8"
            )
            print(f"[OK] wrote {latest_path.as_posix()} ({yyyymmdd})")

if __name__ == "__main__":
    main()
