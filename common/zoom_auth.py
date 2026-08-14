"""Zoom の Server-to-Server OAuth。アクセストークンを取る。課題をまたいで使う。

common/google_auth.py とは別物にしてある。Google は「ブラウザで同意 → token.json に
保存 → 期限が切れたらリフレッシュ」だが、Zoom の Server-to-Server OAuth は同意画面も
リフレッシュトークンも無く、client_id / client_secret から毎回取り直す（有効期限1時間）。
共通の抽象を無理に被せると、どちらの説明も嘘になるので分けた。

使う側は必要なスコープを渡す::

    from common import zoom_auth

    SCOPES = ("meeting:write:meeting:admin",)
    credentials = zoom_auth.read_credentials(os.environ)
    token = zoom_auth.fetch_access_token(credentials)
    zoom_auth.require_scopes(token, SCOPES)

スコープに既定値は持たせない。課題ごとに違う値で、間違えると「権限が足りないまま
動いているように見える」状態になるため、呼ぶ側に必ず書かせる（google_auth と同じ）。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

import requests

TOKEN_URL = "https://zoom.us/oauth/token"
TIMEOUT_SECONDS = 30

ACCOUNT_ID_ENV = "ZOOM_ACCOUNT_ID"
CLIENT_ID_ENV = "ZOOM_CLIENT_ID"
CLIENT_SECRET_ENV = "ZOOM_CLIENT_SECRET"
_REQUIRED_ENV = (ACCOUNT_ID_ENV, CLIENT_ID_ENV, CLIENT_SECRET_ENV)

# 応答に api_url が無かったときの行き先。公式ドキュメントがグローバルの
# ベース URL として定めている値で、こちらが決めた既定値ではない。
DEFAULT_API_URL = "https://api.zoom.us"


class AuthError(Exception):
    """利用者にそのまま見せられる認証まわりの失敗。"""


@dataclass(frozen=True)
class ZoomCredentials:
    """Marketplace の App Credentials に出ている3つの値。"""

    account_id: str
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class AccessToken:
    value: str
    expires_in: int
    scopes: tuple[str, ...]
    api_url: str


def read_credentials(env: Mapping[str, str]) -> ZoomCredentials:
    """環境変数から資格情報を読む。足りなければ変数名を名指しして落とす。"""
    values: dict[str, str] = {}
    missing: list[str] = []
    for name in _REQUIRED_ENV:
        # 空文字・空白だけは「未設定」と同じ扱いにする。`ZOOM_CLIENT_SECRET=` と
        # 書いただけの .env は変数としては存在するので、有無だけ見ると素通りする。
        value = (env.get(name) or "").strip()
        if value:
            values[name] = value
        else:
            missing.append(name)

    if missing:
        # シークレットの値そのものは絶対に載せない。実行画面のスクリーンショットを
        # public リポジトリに置くため。
        raise AuthError(
            "Zoom の資格情報が設定されていません: " + " / ".join(missing) + "\n"
            "Zoom App Marketplace の Server-to-Server OAuth アプリを開き、"
            "App Credentials の Account ID / Client ID / Client Secret を"
            "環境変数に設定してください。手順は README を参照。"
        )

    return ZoomCredentials(
        account_id=values[ACCOUNT_ID_ENV],
        client_id=values[CLIENT_ID_ENV],
        client_secret=values[CLIENT_SECRET_ENV],
    )


def basic_auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    # b64encode は折り返さない。encodebytes だと76文字ごとに改行が入り、
    # ヘッダとして壊れる。
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _payload_of(response) -> dict | None:
    """JSON として読めれば dict を返す。読めなければ None（例外は外に出さない）。"""
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _detail(response, payload: dict | None) -> str:
    """相手が言っている理由をそのまま返す。こちらで原因候補を並べ直さない。"""
    if payload:
        for key in ("reason", "message", "error_description", "error"):
            reason = payload.get(key)
            if reason:
                return str(reason)
    text = (getattr(response, "text", "") or "").strip()
    return text[:200] if text else "(応答本文なし)"


def _activation_hint(detail: str) -> str:
    """アプリ未有効化のときだけ、直しかたを足す。

    Zoom は "The app has been disabled by the developer" とだけ返す（2026-08-14 実測）。
    英文からは「どこで何を押すか」が分からないので、そこを補う。
    原因が違うときは足さない。合っている設定を疑わせて遠回りさせるため。
    """
    if "disabled" not in detail.lower():
        return ""
    return (
        "\nZoom App Marketplace のアプリが有効化されていません。"
        "アプリの Activation で Activate してください。"
        "\nスコープを追加したあとは有効化が外れるので、追加のたびに Activate し直す必要があります。"
    )


def fetch_access_token(
    credentials: ZoomCredentials,
    *,
    poster: Callable = requests.post,
    timeout: float = TIMEOUT_SECONDS,
) -> AccessToken:
    """アクセストークンを取る。リフレッシュトークンは無いので毎回ここを通る。"""
    response = poster(
        TOKEN_URL,
        params={
            "grant_type": "account_credentials",
            "account_id": credentials.account_id,
        },
        headers={
            "Authorization": basic_auth_header(
                credentials.client_id, credentials.client_secret
            )
        },
        timeout=timeout,
    )

    payload = _payload_of(response)

    if not response.ok:
        detail = _detail(response, payload)
        raise AuthError(
            f"Zoom のトークン取得に失敗しました（HTTP {response.status_code}）: "
            f"{detail}{_activation_hint(detail)}"
        )

    if payload is None:
        # 200 なのに JSON でない。プロキシや障害ページを掴んでいる。
        raise AuthError(
            f"Zoom のトークン応答が JSON ではありません（HTTP {response.status_code}）: "
            f"{_detail(response, None)}"
        )

    value = str(payload.get("access_token") or "").strip()
    if not value:
        # 「返ってこなかった」を「成功した」にしない。空のまま進むと、
        # 後段の API が 401 を返して、原因がここだと分からなくなる。
        raise AuthError(
            "Zoom の応答に access_token がありません。"
            f"受け取ったキー: {', '.join(sorted(payload)) or '(なし)'}"
        )

    # Zoom は scope を空白区切りの1文字列で返す（OAuth 2.0 の書式）。
    # 割らないと、後段の権限チェックが「1個の長いスコープ」を相手にして必ず外れる。
    scopes = tuple(str(payload.get("scope") or "").split())

    return AccessToken(
        value=value,
        expires_in=int(payload.get("expires_in") or 0),
        scopes=scopes,
        api_url=str(payload.get("api_url") or DEFAULT_API_URL),
    )


def require_scopes(token: AccessToken, wanted: Iterable[str]) -> None:
    """権限が足りているか確認する。足りない分だけを名指しして落とす。"""
    wanted = list(wanted)
    if not wanted:
        raise AuthError("確認するスコープが空です。呼び出し側で指定してください")

    granted = set(token.scopes)
    # 完全一致で見る。meeting:read:meeting は meeting:read:meeting:admin の
    # 前方一致になっているが、別のスコープなので通さない。前方一致で通すと
    # 「足りている」と報告したうえで実際の呼び出しが 401 になり、原因が遠くなる。
    missing = [scope for scope in wanted if scope not in granted]
    if missing:
        raise AuthError(
            "Zoom アプリに権限が足りません: " + " / ".join(missing) + "\n"
            "Zoom App Marketplace でアプリの Scopes に追加し、Activate し直してください。"
        )
