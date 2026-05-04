from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


DEFAULT_WS_URL = "ws://127.0.0.1:8080/onebot/v11/ws"


def normalize_ws_url(ws_url: str) -> str:
    value = (ws_url or "").strip()
    if not value:
        return DEFAULT_WS_URL
    try:
        parsed = urlsplit(value)
    except Exception:
        return value
    if parsed.scheme not in {"ws", "wss"}:
        return value
    path = parsed.path or "/"
    if path in {"", "/"}:
        path = "/onebot/v11/ws"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def build_ws_connect_url(ws_url: str, access_token: str) -> str:
    url = normalize_ws_url(ws_url)
    token = (access_token or "").strip()
    if not token:
        return url
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "access_token" not in query:
        query["access_token"] = token
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def mask_ws_url(ws_url: str) -> str:
    parsed = urlsplit(ws_url)
    if not parsed.query:
        return ws_url
    masked = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in {"access_token", "token"}:
            masked.append((key, "***"))
        else:
            masked.append((key, value))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(masked), parsed.fragment)
    )
