import os
import json
import requests

WP_BASE = os.environ["WP_BASE"]          # https://fieldnote-lab.jp
WP_USER = os.environ["WP_USER"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]

def wp_create_post(title: str, html: str, status: str = "publish"):
    url = f"{WP_BASE.rstrip('/')}/wp-json/wp/v2/posts"
    r = requests.post(
        url,
        auth=(WP_USER, WP_APP_PASSWORD),
        json={"title": title, "content": html, "status": status},
        timeout=30,
    )
    if r.status_code >= 400:
        print("[HTTP ERROR]", r.status_code)
        print("[URL]", url)
        try:
            print("[RESPONSE JSON]", r.json())
        except Exception:
            print("[RESPONSE TEXT]", r.text[:2000])
        r.raise_for_status()
    return r.json()

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

    res = wp_create_post(data["title"], html, status="publish")
    print("Posted:", res.get("link"))

if __name__ == "__main__":
    main()
