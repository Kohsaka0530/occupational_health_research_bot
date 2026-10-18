from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request


class FetchError(RuntimeError):
    pass


def request(url: str, *, data: dict | None = None, headers: dict | None = None,
            attempts: int = 3, timeout: int = 60, method: str | None = None) -> bytes:
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    h = {"User-Agent": "OccupationalHealthResearchBot/0.1", "Accept": "application/json, application/xml, text/xml"}
    if body is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    for n in range(attempts):
        try:
            req = urllib.request.Request(url, data=body, headers=h, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = response.read(12_000_001)
                if len(payload) > 12_000_000:
                    raise FetchError("応答サイズが上限を超えました")
                return payload
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or n == attempts - 1:
                # Do not include response bodies, headers, or credentials in errors.
                detail = ""
                try:
                    payload = json.loads(exc.read(16000))
                    error = payload.get("error", {})
                    if isinstance(error, dict):
                        labels = {
                            "invalid_api_key": "APIキーの認証失敗",
                            "credit_balance_exhausted": "OpenAI APIのクレジット残高不足",
                            "insufficient_quota": "APIの利用枠・残高不足",
                            "rate_limit_exceeded": "一時的なレート制限",
                            "model_not_found": "指定モデルを利用できません",
                        }
                        code = error.get("code") or error.get("type")
                        if isinstance(code,str) and code in labels:
                            detail = " / " + labels[code] + " (" + code + ")"
                except (ValueError, AttributeError, TypeError):
                    pass
                raise FetchError(f"HTTP {exc.code}: {urllib.parse.urlsplit(url).hostname}" + detail) from None
            retry = exc.headers.get("Retry-After", "")
            time.sleep(min(float(retry), 15) if retry.isdigit() else 2 ** n)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if n == attempts - 1:
                raise FetchError(f"接続失敗: {urllib.parse.urlsplit(url).hostname} ({type(exc).__name__})") from None
            time.sleep(2 ** n)
    raise FetchError("通信に失敗しました")


def get_json(url: str) -> dict:
    try:
        return json.loads(request(url))
    except (ValueError, UnicodeDecodeError):
        raise FetchError("情報源から有効なJSONが返りませんでした") from None
