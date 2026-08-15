"""YouTube Data API の API キー認証。課題をまたいで使う。

common/google_auth.py（Google の OAuth）とも common/zoom_auth.py（Zoom の
Server-to-Server OAuth）とも別物にしてある。3つとも「認証」だが手順が違う。

===================== ============================================
何を使うか             どういう相手か
===================== ============================================
google_auth（OAuth）  **本人のデータ**を触る。同意画面 → token.json → リフレッシュ
zoom_auth（S2S）      アカウントの権限で動く。同意画面なし・毎回取り直し
youtube_auth（キー）  **公開データ**を読むだけ。認可する相手がいない
===================== ============================================

YouTube の検索は誰のデータでもない公開情報なので、そもそも「誰かが許可する」
という段がない。公式が案内するのも API キーで、OAuth は「ユーザーの認可が必要な
メソッド」のためのものである。同意画面もリフレッシュトークンも出てこない。

使う側::

    from common import youtube_auth

    api_key = youtube_auth.read_api_key(os.environ)
    service = youtube_auth.build_service(api_key)

**API キーは URL のクエリに載る。** ここが Zoom の Client Secret とも
Google のトークンとも違う、この課題に固有の危険である。google-api-python-client は
失敗したリクエストの URI を HttpError に持たせるので、例外をそのまま印字すると
``...&key=<APIキー>`` が画面に出る。実行画面は public リポジトリに置く
スクリーンショットになるため、**印字した時点で公開事故**になる。

そこで redact() を用意した。表に出す文字列は必ずここを通す。
"""

from __future__ import annotations

from typing import Callable, Mapping

from googleapiclient.discovery import build

API_SERVICE_NAME = "youtube"
API_VERSION = "v3"

API_KEY_ENV = "YOUTUBE_API_KEY"

# 伏せたことが分かる印。空文字にすると「元から何も無かった」と区別がつかない。
REDACTED = "***"


class AuthError(Exception):
    """利用者にそのまま見せられる認証まわりの失敗。"""


def read_api_key(env: Mapping[str, str]) -> str:
    """環境変数から API キーを読む。

    空文字・空白だけは「未設定」と同じ扱いにする。``$env:YOUTUBE_API_KEY = ""``
    と書いただけでも変数としては存在するので、有無だけ見ると素通りして、
    後段の API が 400 を返し、原因がここだと分からなくなる。
    """
    value = (env.get(API_KEY_ENV) or "").strip()
    if value:
        return value

    # 鍵の値そのものは絶対に載せない（壊れた値であっても）。
    # このメッセージは公開されるスクリーンショットに写る。
    raise AuthError(
        f"API キーが設定されていません: {API_KEY_ENV}\n"
        "Google Cloud コンソールの「APIとサービス」→「認証情報」で API キーを作成し、"
        "環境変数に設定してください。手順は README を参照。\n"
        'PowerShell: $env:' + API_KEY_ENV + ' = "<APIキー>"'
    )


def redact(text: str, api_key: str | None) -> str:
    """文字列から API キーを伏せる。

    鍵が空や None のときは何もしない。``str.replace("", x)`` は
    **全部の文字の間に x を挿し込む**ので、素通りさせると文章が壊れる。
    """
    if not api_key:
        return text
    return text.replace(api_key, REDACTED)


def build_service(api_key: str, *, builder: Callable = build):
    """YouTube Data API のクライアントを組む。

    cache_discovery を切っているのは、環境によってはファイルキャッシュの警告が
    出るため。無関係な警告が実行画面のスクリーンショットに写るのを避ける。
    """
    key = (api_key or "").strip()
    if not key:
        # 空のまま組むと、実行時に 400 が返って原因がここだと分からなくなる。
        raise AuthError(f"API キーが空です。{API_KEY_ENV} を設定してください")

    return builder(
        API_SERVICE_NAME,
        API_VERSION,
        developerKey=key,
        cache_discovery=False,
    )
