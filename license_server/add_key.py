"""
Add a license key to your server (run this when you make a sale).
Usage:
  python add_key.py
  python add_key.py KAPTAAN-ABCD-1234
  python add_key.py --url https://your-app.railway.app --secret YOUR_ADMIN_SECRET --key KAPTAAN-XXXX
"""
import os
import sys
import urllib.request
import urllib.error
import json

# Set these or pass via env / args
SERVER_URL = os.environ.get("LICENSE_SERVER_URL", "http://localhost:5000")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "change-me-in-production")


def add_key(key, url=SERVER_URL, secret=ADMIN_SECRET):
    data = json.dumps({"secret": secret, "key": key}).encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/add_key",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"OK: Key '{key}' added.")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"Error {e.code}: {body}")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


if __name__ == "__main__":
    key = None
    url = SERVER_URL
    secret = ADMIN_SECRET
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] in ("--url", "-u") and i + 1 < len(sys.argv):
            url = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] in ("--secret", "-s") and i + 1 < len(sys.argv):
            secret = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] in ("--key", "-k") and i + 1 < len(sys.argv):
            key = sys.argv[i + 1]
            i += 2
        elif not sys.argv[i].startswith("-"):
            key = sys.argv[i]
            i += 1
        else:
            i += 1
    if not key:
        key = input("Enter new license key (e.g. KAPTAAN-XXXX-XXXX): ").strip()
    if not key:
        print("No key given.")
        sys.exit(1)
    add_key(key, url=url, secret=secret)
