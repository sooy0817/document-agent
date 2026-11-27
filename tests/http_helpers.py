"""tests / http_helpers."""

from starlette.requests import Request


def request(headers):
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/upload",
            "scheme": "http",
            "server": ("127.0.0.1", 8766),
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        }
    )
