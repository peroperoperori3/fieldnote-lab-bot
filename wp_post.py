import os, json, glob
import requests

WP_BASE = os.environ["WP_BASE"].rstrip("/")
WP_USER = os.environ["WP_USER"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]
WP_POST_STATUS = os.environ.get("WP_POST_STATUS", "publish")

MODE = os.environ.get("MODE", "predict").strip().lower()
print(f"[DEBUG] MODE = {MODE}")

def wp_request(method, path, **kwargs):
    url = f"{WP_BASE}{path}"
    auth = (WP_USER, WP_APP_PASSWORD)
    return requests.request(method, url, auth=auth, timeout=30, **kwargs)

def upsert_post(slug: str, title: str, html: str, status: str):
    # 既存検索（slug一致）
    r = wp_request("GET", "/wp-json/wp/v2/posts", params={"slug": slug, "per_page": 1})
    r.raise_for_status()
    items = r.json()

    payload = {"title": title, "content": html, "status": status, "slug": slug}

    if items:
        post_id = items[0]["id"]
        u = wp_request("POST", f"/wp-json/wp/v2/posts/{post_id}", json=payload)
        u.raise_for_status()
        return "updated", u.json().get("link")
    else:
        c = wp_request("POST", "/wp-json/wp/v2/posts", json=payload)
        c.raise_for_status()
        return "created", c.json().get("link")

def main():
    # MODEで対象ファイルを決める
    if MODE == "predict":
        prefix = "predict_"
        slug_prefix = "predict"
    else:
        prefix = "result_"
        slug_prefix = "result"

    files = sorted(glob.glob(f"output/{prefix}*.json"))
    if not files:
        raise RuntimeError(f"output に {prefix}*.json が見つかりません")

    print(f"[DEBUG] files = {files}")

    # 全部投稿（開催場ぶん）
    for json_path in files:
        data = json.load(open(json_path, encoding="utf-8"))

        date = data.get("date")  # yyyymmdd
        place = data.get("place") or data.get("track")  # 念のため両対応
        place_code = data.get("place_code") or ""

        # htmlは同名ファイルを読む
        html_path = json_path.replace(".json", ".html")
        html = open(html_path, encoding="utf-8").read()

        # slugを日付+開催場で固定（=1日1場1記事）
        slug = f"{slug_prefix}-{date}-{place_code}"
        title = data.get("title") or f"{date} {place} {MODE}"

        print(f"[DEBUG] json_path = {json_path}")
        print(f"[DEBUG] slug = {slug}")

        action, link = upsert_post(slug, title, html, WP_POST_STATUS)
        print(f"OK: {action}")
        print(f"Posted: {link}")

if __name__ == "__main__":
    main()
