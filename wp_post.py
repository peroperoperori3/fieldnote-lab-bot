import os, json, glob, re
import requests

WP_BASE = os.environ["WP_BASE"].rstrip("/")
WP_USER = os.environ["WP_USER"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]

# ★ここだけ修正：空文字でも publish に落とす
WP_POST_STATUS = (os.environ.get("WP_POST_STATUS") or "publish").strip()
print(f"[DEBUG] WP_POST_STATUS = {WP_POST_STATUS!r}")

MODE = os.environ.get("MODE", "predict").strip().lower()
print(f"[DEBUG] MODE = {MODE}")

def wp_request(method, path, **kwargs):
    url = f"{WP_BASE}{path}"
    auth = (WP_USER, WP_APP_PASSWORD)
    return requests.request(method, url, auth=auth, timeout=30, **kwargs)

# ==========================
# カテゴリIDを取得（slug優先・安全版）
# ==========================
def get_category_id_by_slug(slug: str):
    r = wp_request(
        "GET",
        "/wp-json/wp/v2/categories",
        params={"slug": slug, "per_page": 100},
    )
    if r.status_code != 200:
        print(f"[WARN] categories slug search failed: {r.status_code} body={r.text[:200]}")
        return None
    items = r.json()
    if items:
        return items[0].get("id")
    return None

def get_category_id_by_name(name: str):
    r = wp_request(
        "GET",
        "/wp-json/wp/v2/categories",
        params={"search": name, "per_page": 100},
    )
    if r.status_code != 200:
        print(f"[WARN] categories name search failed: {r.status_code} body={r.text[:200]}")
        return None
    for it in r.json():
        if it.get("name") == name:
            return it.get("id")
    return None

def get_category_id(slug: str, name: str):
    # まずslugで確実に取る（推奨）
    cid = get_category_id_by_slug(slug)
    if cid:
        return cid
    # ダメなら名前でフォールバック
    cid = get_category_id_by_name(name)
    return cid

# ==========================
# 投稿（既存があればスキップ）
# ==========================
def create_post_if_not_exists(slug: str, title: str, html: str, status: str, category_id=None):
    # slug一致で既存確認
    r = wp_request("GET", "/wp-json/wp/v2/posts", params={"slug": slug, "per_page": 1})
    r.raise_for_status()
    items = r.json()

    if items:
        post_id = items[0].get("id")
        link = items[0].get("link")
        print(f"[SKIP] already exists: id={post_id} slug={slug}")
        return "skipped", link

    payload = {
        "title": title,
        "content": html,
        "status": status,
        "slug": slug,
    }

    if category_id is not None:
        payload["categories"] = [int(category_id)]

    c = wp_request("POST", "/wp-json/wp/v2/posts", json=payload)
    if c.status_code not in (200, 201):
        print(f"[ERROR] post failed: {c.status_code} body={c.text[:1000]}")
    c.raise_for_status()
    return "created", c.json().get("link")

# ==========================
# 追加：slug/title 安定化（最低限）
# ==========================
TRACK_SLUG = {
    "門別": "monbetsu", "盛岡": "morioka", "水沢": "mizusawa", "浦和": "urawa",
    "船橋": "funabashi", "大井": "ooi", "川崎": "kawasaki", "金沢": "kanazawa",
    "笠松": "kasamatsu", "名古屋": "nagoya", "園田": "sonoda", "姫路": "himeji",
    "高知": "kochi", "佐賀": "saga",
}

def ymd_dot(yyyymmdd: str) -> str:
    s = re.sub(r"\D", "", str(yyyymmdd or ""))
    if len(s) == 8:
        return f"{s[0:4]}.{s[4:6]}.{s[6:8]}"
    return str(yyyymmdd)

def safe_track_slug(place: str, place_code: str = "") -> str:
    p = str(place or "").strip()
    if p in TRACK_SLUG:
        return TRACK_SLUG[p]

    pc = str(place_code or "").strip()
    if pc:
        s = re.sub(r"[^a-zA-Z0-9]+", "-", pc).strip("-").lower()
        if s:
            return s

    s = re.sub(r"[^a-zA-Z0-9]+", "-", p).strip("-").lower()
    return s if s else "track"

def main():
    # MODEで対象ファイルを決める
    if MODE == "predict":
        prefix = "predict_"
        slug_prefix = "predict"
        category_slug = "keiba-predict"
        category_name = "地方競馬予想"
        mode_label = "予想"
    else:
        prefix = "result_"
        slug_prefix = "result"
        category_slug = "keiba-result"
        category_name = "地方競馬結果"
        mode_label = "結果"

    # ★DATEが指定されている場合は、その日付のファイルだけ投稿する
    DATE = re.sub(r"\D", "", os.environ.get("DATE", "").strip())
    pattern = f"output/{prefix}{DATE}_*.json" if DATE else f"output/{prefix}*.json"
    files = sorted(glob.glob(pattern))
    print(f"[DEBUG] DATE={DATE!r} pattern={pattern}")

    if not files:
        print(f"[SKIP] output に {pattern} が見つかりません（まだデータが出てないので終了）")
        return

    print(f"[DEBUG] files = {files}")

    category_id = get_category_id(category_slug, category_name)
    print(f"[DEBUG] category_slug={category_slug} category_name={category_name} category_id={category_id}")

    # ★カテゴリが取れないなら「未分類で投稿」しない（事故防止）
    if category_id is None:
        raise SystemExit(f"[FATAL] category not found: slug={category_slug} name={category_name}")

    # 全部投稿（開催場ぶん）
    for json_path in files:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        date = data.get("date")              # yyyymmdd
        place = data.get("place") or data.get("track") or data.get("venue")
        place_code = data.get("place_code") or data.get("track_code") or ""

        html_path = json_path.replace(".json", ".html")
        try:
            with open(html_path, encoding="utf-8") as f:
                html = f.read()
        except FileNotFoundError:
            print(f"[SKIP] html not found: {html_path} (jsonだけあるので投稿せずスキップ)")
            continue

        track_slug = safe_track_slug(place, place_code)
        date_num = re.sub(r"\D", "", str(date or ""))
        slug = f"{slug_prefix}-{date_num}-{track_slug}"

        title = f"{ymd_dot(date)} {place}競馬 {mode_label}"

        print(f"[DEBUG] json_path = {json_path}")
        print(f"[DEBUG] slug = {slug}")
        print(f"[DEBUG] title = {title}")

        action, link = create_post_if_not_exists(
            slug=slug,
            title=title,
            html=html,
            status=WP_POST_STATUS,
            category_id=category_id,
        )
        print(f"OK: {action}")
        if link:
            print(f"Link: {link}")

if __name__ == "__main__":
    main()