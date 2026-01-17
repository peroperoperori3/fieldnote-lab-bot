import os
import json
import requests

WP_BASE = os.environ["WP_BASE"]          # 例: https://fieldnote-lab.jp
WP_USER = os.environ["WP_USER"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]
WP_POST_STATUS = os.environ.get("WP_POST_STATUS", "publish")  # publish / draft

def wp_find_post_id_by_dedup(dedup_key: str) -> int | None:
    """
    meta(fieldnote_key) == dedup_key の投稿を検索して、あれば post_id を返す
    """
    url = f"{WP_BASE.rstrip('/')}/wp-json/wp/v2/posts"
    r = requests.get(
        url,
        params={"meta_key": "fieldnote_key", "meta_value": dedup_key, "per_page": 1},
        auth=(WP_USER, WP_APP_PASSWORD),
        timeout=20,
    )
    r.raise_for_status()
    arr = r.json()
    if isinstance(arr, list) and len(arr) >= 1 and "id" in arr[0]:
        return int(arr[0]["id"])
    return None

def wp_upsert_post(title: str, html: str, status: str, dedup_key: str):
    """
    同じ dedup_key があれば更新、なければ新規投稿
    """
    base_url = f"{WP_BASE.rstrip('/')}/wp-json/wp/v2/posts"

    payload = {
        "title": title,
        "content": html,
        "status": status,
        "meta": {
            "fieldnote_key": dedup_key
        }
    }

    post_id = wp_find_post_id_by_dedup(dedup_key)

    if post_id:
        url = f"{base_url}/{post_id}"
        r = requests.post(url, auth=(WP_USER, WP_APP_PASSWORD), json=payload, timeout=30)
    else:
        url = base_url
        r = requests.post(url, auth=(WP_USER, WP_APP_PASSWORD), json=payload, timeout=30)

    if r.status_code >= 400:
        print("[HTTP ERROR]", r.status_code)
        print("[URL]", url)
        try:
            print("[RESPONSE JSON]", r.json())
        except Exception:
            print("[RESPONSE TEXT]", r.text[:2000])
        r.raise_for_status()

    res = r.json()
    return res

def main():
    # output の中で最新の predict_*.json を探す
    if not os.path.isdir("output"):
        raise SystemExit("outputフォルダがありません。先に predict_saga_today.py を実行して output を作ってください。")

    files = [f for f in os.listdir("output") if f.startswith("predict_") and f.endswith(".json")]
    files.sort()
    if not files:
        raise SystemExit("outputにpredict_*.jsonがありません。先に predict_saga_today.py を実行してください。")

    json_path = os.path.join("output", files[-1])
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    html_path = json_path.replace(".json", ".html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    title = data["title"]

    # ★二重投稿防止キー（予想）
    # 例: predict_2026-01-17_55 みたいになる
    dedup_key = f"predict_{data['date']}_{data['place_code']}"

    res = wp_upsert_post(title, html, status=WP_POST_STATUS, dedup_key=dedup_key)

    print("OK:", "updated" if "id" in res else "done")
    print("Posted:", res.get("link"))

if __name__ == "__main__":
    main()
