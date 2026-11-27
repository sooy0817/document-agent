"""Credential-free connectivity check before reserving paid model work."""

import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HOST = "api.openai.com"
URL = "https://api.openai.com/v1/responses"


def failure(exc):
    reason = exc.reason if isinstance(exc, URLError) else exc
    number = getattr(reason, "winerror", None) or getattr(reason, "errno", None)
    if number in (13, 10013):
        code = "NETWORK_ACCESS_DENIED"
    elif isinstance(reason, socket.gaierror):
        code = "DNS_RESOLUTION_FAILED"
    elif isinstance(reason, TimeoutError):
        code = "NETWORK_TIMEOUT"
    else:
        code = "NETWORK_CONNECTION_FAILED"
    return {"error_code": code, "exception_type": type(reason).__name__, "errno": number}


def inspect(*, resolver=socket.getaddrinfo, opener=urlopen):
    result = {
        "host": HOST,
        "ready": False,
        "dns": "not_checked",
        "https": "not_checked",
        "model_calls": 0,
        "credentials_checked": False,
    }
    try:
        resolver(HOST, 443, type=socket.SOCK_STREAM)
        result["dns"] = "ok"
    except OSError as exc:
        return {**result, "dns": "failed", **failure(exc)}
    try:
        with opener(Request(URL, method="HEAD"), timeout=8) as response:
            status = response.status
    except HTTPError as exc:
        status = exc.code
    except (OSError, URLError) as exc:
        return {**result, "https": "failed", **failure(exc)}
    # Without credentials, 401 or method-not-allowed proves reachability only.
    if 200 <= status < 300 or status in (401, 405):
        return {**result, "https": "reachable", "http_status": status, "ready": True}
    return {**result, "https": "rejected", "http_status": status,
            "error_code": "NETWORK_HTTP_REJECTED"}


def main():
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = inspect()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
