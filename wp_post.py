import os
import requests

WP_BASE = os.environ["WP_BASE"]
WP_USER = os.environ["WP_USER"]
WP_APP_PASSWORD = os.environ["WP_APP_PASSWORD"]

def main():
    # 認証なしで叩けるはずのエンドポイント（WPが返すJSONが見たい）
    url1 = f"{WP_BASE.rstrip('/')}/wp-json/"
    r1 = requests.get(url1, timeout=30)
    print("[GET]", url1, "status=", r1.status_code)
    print(r1.text[:500])

    # 認証が通るか（ユーザー情報。ここがJSONで返ればOK）
    url2 = f"{WP_BASE.rstrip('/')}/wp-json/wp/v2/users/me"
    r2 = requests.get(url2, auth=(WP_USER, WP_APP_PASSWORD), timeout=30)
    print("[GET]", url2, "status=", r2.status_code)
    print(r2.text[:500])

    r2.raise_for_status()

if __name__ == "__main__":
    main()
