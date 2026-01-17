import os
import requests

WP_BASE = os.environ["WP_BASE"]
WP_USER = os.environ["WP_USER"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]

def show(resp, label):
    print(f"[{label}] status={resp.status_code}")
    print(resp.text[:500])

def main():
    # 1) 認証なし REST ルート（ここが 200 になれば nginxブロック解除）
    url1 = f"{WP_BASE.rstrip('/')}/wp-json/"
    r1 = requests.get(url1, timeout=30)
    print("[GET]", url1)
    show(r1, "wp-json")

    # 2) 認証あり（ここが 200 ならアプリパスもOK）
    url2 = f"{WP_BASE.rstrip('/')}/wp-json/wp/v2/users/me"
    r2 = requests.get(url2, auth=(WP_USER, WP_APP_PASSWORD), timeout=30)
    print("[GET]", url2)
    show(r2, "users/me")

    # 403の時点で落とす
    if r1.status_code == 403:
        raise SystemExit("Still blocked by Xserver/WAF (wp-json is 403).")
    r2.raise_for_status()
    print("OK: REST reachable + auth OK")

if __name__ == "__main__":
    main()
