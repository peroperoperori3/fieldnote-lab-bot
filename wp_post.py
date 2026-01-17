import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

def pick_latest(prefix: str, suffix: str) -> str:
    if not os.path.isdir("output"):
        raise RuntimeError("output フォルダがありません")
    files = [f for f in os.listdir("output") if f.startswith(prefix) and f.endswith(suffix)]
    files.sort()
    if not files:
        raise RuntimeError(f"output に {prefix}*{suffix} が見つかりません")
    return os.path.join("output", files[-1])

def wp_find_post_id_by_slug(slug: str):
    base = f"{os.environ['WP_BASE'].rstrip('/')}/wp-json/wp/v2/posts"
    auth = (os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"])
    r = requests.get(base, params={"slug": slug, "per_page": 1}, auth=auth, timeout=20)
    r.raise_for_status()
    arr = r.json()
    if isinstance(arr, list) and arr and "id" in arr[0]:
        return int(arr[0]["id"])
    return None

def wp_upsert(title: str, html: str, slug: str, status: str):
    base = f"{os.environ['WP_BASE'].rstrip('/')}/wp-json/wp/v2/posts"
    auth = (os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"])

    payload = {
        "title": title,
        "content": html,
        "status": status,
        "slug": slug,  # ★ここが最重要：固定URLで完全分離
    }

    post_id = wp_find_post_id_by_slug(slug)
    if post_id:
        r = requests.post(f"{base}/{post_id}", auth=auth, json=payload, timeout=30)
        r.raise_for_status()
        return "updated", r.json()
    else:
        r = requests.post(base, auth=auth, json=payload, timeout=30)
        r.raise_for_status()
        return "created", r.json()

def main():
    MODE = os.environ.get("MODE", "predict")
    status = os.environ.get("WP_POST_STATUS", "publish")

    if MODE == "result":
        json_path = pick_latest("result_", ".json")
        html_path = json_path.replace(".json", ".html")
        data = json.loads(open(json_path, encoding="utf-8").read())
        html = open(html_path, encoding="utf-8").read()

        # ★結果は result-YYYYMMDD-PLACE の slug に固定（日本語slug事故を避ける）
        slug = f"result-{data['date'].replace('-','')}-{data['place_code']}"
        title = data["title"]

    else:
        json_path = pick_latest("predict_", ".json")
        html_path = json_path.replace(".json", ".html")
        data = json.loads(open(json_path, encoding="utf-8").read())
        html = open(html_path, encoding="utf-8").read()

        # ★予想は predict-YYYYMMDD-PLACE の slug に固定
        slug = f"predict-{data['date'].replace('-','')}-{data['place_code']}"
        title = data["title"]

    print("[DEBUG] MODE =", MODE)
    print("[DEBUG] json_path =", json_path)
    print("[DEBUG] slug =", slug)

    action, res = wp_upsert(title, html, slug, status)
    print("OK:", action)
    print("Posted:", res.get("link"))

if __name__ == "__main__":
    main()
