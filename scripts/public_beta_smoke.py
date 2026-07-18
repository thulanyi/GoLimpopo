import argparse
import json
import sys
import urllib.error
import urllib.request


def fetch_json(url, timeout=10):
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body)


def fetch_status(url, timeout=10):
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def main():
    parser = argparse.ArgumentParser(description="Public beta smoke checks for Magayisa")
    parser.add_argument("--base-url", required=True, help="Base URL, e.g. https://your-beta.example.com")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    checks = [
        ("healthz", f"{base_url}/healthz"),
        ("readyz", f"{base_url}/readyz"),
        ("home", f"{base_url}/"),
        ("login", f"{base_url}/login"),
        ("register", f"{base_url}/register"),
    ]

    failed = []

    print("Running public beta smoke checks...")

    for name, url in checks:
        try:
            if name in {"healthz", "readyz"}:
                status, payload = fetch_json(url)
                ok = status == 200
                if name == "readyz":
                    ok = ok and payload.get("status") == "ready"
                print(f"[{name}] status={status} payload={payload}")
                if not ok:
                    failed.append((name, status, payload))
            else:
                status = fetch_status(url)
                print(f"[{name}] status={status}")
                if status != 200:
                    failed.append((name, status, {}))
        except urllib.error.HTTPError as exc:
            print(f"[{name}] HTTP error: {exc.code}")
            failed.append((name, exc.code, {}))
        except Exception as exc:
            print(f"[{name}] error: {exc}")
            failed.append((name, 0, {"error": str(exc)}))

    if failed:
        print("\nSmoke checks failed:")
        for item in failed:
            print(item)
        return 1

    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
