"""LINE Messaging API の認証。課題をまたいで使えるように common/ に置く。

common/ の他の5つとは別モジュールにしてある。6つとも「認証」だが手順が違う。

===================== ================================================
何を使うか             どういう相手か
===================== ================================================
google_auth（OAuth）  **本人のデータ**を触る。同意画面 → token.json → リフレッシュ
zoom_auth（S2S）      アカウントの権限で動く。同意画面なし・毎回取り直し
youtube_auth（キー）  **公開データ**を読むだけ。認可する相手がいない
slack_auth（Bot）     **アプリ自身**が動く。インストール時に1回発行、期限なし
discord_auth（Bot）   アプリ自身が動く。**加えて「認証しない経路」が併存する**
line_auth（長期）     アプリ自身が動く。**無期限で、チャネルに1本しか無い**
===================== ================================================

使う側::

    from pathlib import Path
    from common import env_file, line_auth

    ROOT = Path(__file__).resolve().parents[1]
    env = env_file.load(ROOT / env_file.ENV_FILENAME)

    token = line_auth.read_channel_access_token(env)
    session = line_auth.build_session(token)
    info = line_auth.fetch_bot_info(session, secrets=(token,))

LINE に固有の事情が3つある。
------------------------------------------------------------------

**1. 送ったメッセージを読み返す API が無い。**

課題1〜8は全部「送信 → 別経路で読み返して照合」で締めてきたが、LINE には
それに当たるものが無い。``GET /v2/bot/message/{messageId}/content`` は
**ユーザーが送った**画像・動画・音声専用で、bot が送ったテキストは取れない
（2026-08-18 に公式 OpenAPI 定義で確認）。

そこで物差しを ``GET /v2/bot/info`` に置く。**投稿の応答だけを見て「送れた」と
判断すると、同じ応答の中で値を比べるトートロジーになる**（課題4・6・7・8と
同じ形）。別のエンドポイントから取った ``userId`` / ``basicId`` と突き合わせて
初めて閉じる。

なお ``POST /v2/bot/message/broadcast`` の応答は**空オブジェクト**なので、
この経路を選ぶと照合の手段そのものが消える。**push を使う**理由がこれ
（課題8で webhook の 204 を退けたのと同じ判断）。

**2. 紛らわしい識別子が4つある。**

======================== =================================================
名前                      どこにあるか
======================== =================================================
チャネルアクセストークン   「Messaging API設定」タブの**いちばん下**
チャネルシークレット       「チャネル基本設定」タブ（**別物**）
あなたのユーザーID         「チャネル基本設定」タブ（``U`` で始まる）
ボットのベーシックID       「Messaging API設定」タブ（``@`` で始まる）
======================== =================================================

**取り違えても、形の上では「設定されている」ように見える。** 読む側で形を見て、
名指しで違いを言う。課題8の「Bot Token と Webhook URL の取り違え」と同じ対処だが、
LINE のほうが候補が多い。

**3. 応答モードが API から取れる。**

``chatMode`` は ``chat`` か ``bot`` を返す。画面の表示ではなく **API の答え**なので、
「画面ではこうなっているはず」を根拠にしないで済む（課題8で「画面の見え方と、
投稿した主体は別」を踏んだ）。ただし**値を列挙して検査はしない**。LINE 側が
値を増やした日に、動いていたものが止まるため。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

import requests

CHANNEL_ACCESS_TOKEN_ENV = "LINE_CHANNEL_ACCESS_TOKEN"
USER_ID_ENV = "LINE_USER_ID"

# ホストは固定する。組み立てで作ると、いつか別のホストへ資格情報を送る形になる。
API_BASE = "https://api.line.me"

# 何が叩いているかを相手側に残す。既定の requests のままだと分からない。
USER_AGENT = "lesson-4-3-2 (+https://github.com/qwerrin/lesson-4-3-2, 1.0)"

# 伏せたことが分かる印。空文字にすると「元から無かった」と区別がつかない。
REDACTED = "***"

# push の宛先として受け付ける先頭文字。U=ユーザー / C=グループ / R=複数人トーク。
# **ユーザーIDだけに絞らない。** 絞るとグループへ送りたくなったときに形で止まる。
_DESTINATION_PREFIXES = ("U", "C", "R")

_HEX = frozenset("0123456789abcdefABCDEF")

# チャネルシークレットとして疑う形。**長さを仕様として断定はしない**ので、
# 一致したら「では？」と疑うだけにとどめる（弾く理由は形ではなく、
# トークンとして短すぎてどのみち 401 になるから）。
_CHANNEL_SECRET_LENGTH = 32


class LineError(Exception):
    """このモジュールが出す失敗の共通の親。利用者にそのまま見せられる。"""


class AuthError(LineError):
    """資格情報まわりの失敗。"""


class ApiError(LineError):
    """API が 2xx 以外を返した。"""


@dataclass(frozen=True)
class BotInfo:
    """``GET /v2/bot/info`` が答えた「このトークンは誰か」。

    ``user_id`` と ``basic_id`` が照合の物差しになる。前者は bot 自身の ID、
    後者は画面に出ている ``@`` 始まりの ID で、**意図したチャネルを叩いているか**を
    確かめられる。チャネルを2つ作って取り違えたときは、ここでしか気づけない。
    """

    user_id: str
    basic_id: str
    display_name: str
    chat_mode: str
    mark_as_read_mode: str


# ------------------------------------------------------------------ 資格情報を読む


def read_channel_access_token(env: Mapping[str, str]) -> str:
    """チャネルアクセストークン（長期）を読む。

    空文字・空白だけは「未設定」と同じ扱いにする。``LINE_CHANNEL_ACCESS_TOKEN=``
    と書いただけでもキーとしては存在するので、有無だけ見ると素通りして、
    後段の API が 401 を返し、原因がここだと分からなくなる。

    **値そのものは絶対にメッセージへ載せない**（壊れた値であっても）。
    打ち間違いなら本物がそのまま入っている。この文言は公開する
    スクリーンショットに写る。
    """
    value = (env.get(CHANNEL_ACCESS_TOKEN_ENV) or "").strip()

    if not value:
        raise AuthError(
            f"チャネルアクセストークンが設定されていません: {CHANNEL_ACCESS_TOKEN_ENV}\n"
            "LINE Developers Console → 対象のチャネル → 「Messaging API設定」タブの"
            "**いちばん下**にある「チャネルアクセストークン（長期）」の［発行］で"
            "取得し、.env に設定してください。\n"
            "（「チャネル基本設定」タブにあるのはチャネルシークレットで、別物です）"
        )

    if value.lower().startswith("bearer "):
        raise AuthError(
            f"{CHANNEL_ACCESS_TOKEN_ENV} に Authorization ヘッダの形で入っています。\n"
            "先頭の「Bearer 」は送信時にこちらで付けます。トークンだけを設定してください。"
        )

    if len(value) == _CHANNEL_SECRET_LENGTH and all(char in _HEX for char in value):
        raise AuthError(
            f"{CHANNEL_ACCESS_TOKEN_ENV} にチャネルシークレットが入っていませんか。\n"
            "チャネルシークレットは「チャネル基本設定」タブ、"
            "チャネルアクセストークンは「Messaging API設定」タブのいちばん下にあります。\n"
            "API のリクエストに載せるのは後者です。"
        )

    return value


def read_user_id(env: Mapping[str, str]) -> str:
    """push の宛先 ID を読む。

    **この値は秘密ではないので伏せない。** トークンと違い「どこへ送るか」であって
    権限ではない。伏せると、宛先を間違えたときに何を直せばよいか分からなくなる。

    **長さは検査しない。** 実物は ``U`` ＋ 32桁だが、それを仕様として明記した
    ドキュメントを確認していない。確かめていない数字を定数に置くと、
    LINE 側が形を変えた日に**正しい値を拒む**側で壊れる（課題8で
    「``content`` の最大長を自前で持たない」と決めたのと同じ）。
    """
    value = (env.get(USER_ID_ENV) or "").strip()

    if not value:
        raise AuthError(
            f"送信先のユーザーIDが設定されていません: {USER_ID_ENV}\n"
            "LINE Developers Console → 対象のチャネル → 「チャネル基本設定」タブの"
            "「あなたのユーザーID」を .env に設定してください。"
        )

    if value.startswith("@"):
        raise AuthError(
            f"{USER_ID_ENV} にボットのベーシックID（{value}）が入っています。\n"
            "必要なのは「チャネル基本設定」タブの「あなたのユーザーID」（U で始まる値）です。\n"
            "ベーシックIDは友だち追加用の ID で、push の宛先には使えません。"
        )

    if any(char.isspace() for char in value):
        raise AuthError(
            f"{USER_ID_ENV} に空白が含まれています: {value!r}\n"
            "コピーする範囲を確認してください。"
        )

    if not value.startswith(_DESTINATION_PREFIXES):
        raise AuthError(
            f"{USER_ID_ENV} が送信先IDの形ではありません: {value}\n"
            "ユーザーIDは U、グループIDは C、複数人トークIDは R で始まります。"
        )

    return value


# ------------------------------------------------------------------ 伏せ字


def redact(text: str, *secrets: str | None) -> str:
    """文字列から資格情報を伏せる。

    空や None の秘密は素通りさせる。``str.replace("", x)`` は**全部の文字の
    間に x を挿し込む**ので、素通りさせないと文章が壊れる。
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


# ------------------------------------------------------------------ セッション


def build_session(token: str, *, factory: Callable = requests.Session):
    """チャネルアクセストークンを載せた HTTP セッションを組む。

    トークンは ``Authorization`` ヘッダで送る。**URL には載らない**ので、
    課題6（YouTube の API キーが URI に載って例外で漏れる）と同じ漏れ方は
    しない。ただし *その漏れ方が* 起きないだけなので、表に出す文字列は
    redact() を通す方針を変えない。
    """
    value = (token or "").strip()
    if not value:
        # 空のまま組むと、実行時に 401 が返って原因がここだと分からなくなる。
        raise AuthError(
            f"チャネルアクセストークンが空です。{CHANNEL_ACCESS_TOKEN_ENV} を設定してください"
        )

    session = factory()
    session.headers.update(
        {"Authorization": f"Bearer {value}", "User-Agent": USER_AGENT}
    )
    return session


# ------------------------------------------------------------------ エラーの訳し分け


def _payload_of(response) -> Any:
    try:
        return response.json()
    except Exception:  # noqa: BLE001 - 本文が JSON でないことは実際に起きる
        return None


def _request_id_of(response) -> str:
    """``x-line-request-id`` を取り出す。

    **HTTP ヘッダ名は大文字小文字を区別しない。** requests の Response.headers は
    区別しない辞書だが、テストの偽物や別のクライアントは素の dict のことがある。
    ここで畳んでおかないと「本物では取れるがテストでは取れない」が起きる。
    """
    headers = getattr(response, "headers", None) or {}
    for name, value in headers.items():
        if str(name).lower() == "x-line-request-id":
            return str(value)
    return ""


def raise_for_line_error(response, *secrets: str | None) -> None:
    """2xx でなければ、利用者に見せられる ApiError にして投げる。

    LINE のエラー本文は ``{"message": ..., "details": [{"property", "message"}]}``。
    **``details`` があるときは候補を並べない。** 相手が壊れている場所を
    ``/messages/0/text`` の形で名指ししているので、こちらの推測を足すと
    その情報が埋もれる（課題4・Meet の教訓）。

    本文が JSON でないことは実際に起きる。**中身をそのまま流さない**——
    HTML が画面いっぱいに出ても利用者には読めない。
    """
    status = response.status_code
    if 200 <= status < 300:
        return

    payload = _payload_of(response)
    body = payload if isinstance(payload, dict) else {}

    message = f"LINE API がエラーを返しました: HTTP {status}"

    detail = body.get("message")
    if detail:
        message += f"\n{detail}"
    elif not body:
        # JSON ではなかった。中身は載せない。
        message += "\n応答が JSON ではありませんでした。"

    details = body.get("details")
    named = isinstance(details, list) and bool(details)
    if named:
        for item in details:
            if not isinstance(item, dict):
                continue
            where = item.get("property") or "(場所の指定なし)"
            what = item.get("message") or ""
            message += f"\n  {where}: {what}".rstrip()

    if status == 400 and not named:
        # 名指しが無い 400 だけ候補を出す。push が友だちでない相手に失敗する経路は
        # details を伴わないことがあり、ここで何も言わないと英語の本文だけが残る。
        message += (
            "\n次のどれかの可能性があります:\n"
            f"  - 宛先の ID が違う（{USER_ID_ENV} を確認）\n"
            "  - 宛先のユーザーが LINE公式アカウントを友だち追加していない\n"
            "  - 宛先のユーザーが LINE公式アカウントをブロックしている"
        )

    if status == 401:
        message += (
            f"\nチャネルアクセストークンを確認してください（{CHANNEL_ACCESS_TOKEN_ENV}）。"
        )

    if status == 429:
        message += (
            "\nレート制限です。しばらく待ってから再実行してください。\n"
            "（無料のコミュニケーションプランは月200通。通数は"
            "「送信対象になった人数」でカウントされます）"
        )

    request_id = _request_id_of(response)
    if request_id:
        # 問い合わせるときに必要な唯一の手掛かり。
        message += f"\nx-line-request-id: {request_id}"

    raise ApiError(redact(message, *secrets))


# ------------------------------------------------------------------ 自分が誰か


def fetch_bot_info(session, *, base: str = API_BASE, secrets: tuple = ()) -> BotInfo:
    """``GET /v2/bot/info`` で「このトークンが誰なのか」を確かめる。

    最初の1回をここに置くと、「トークンが無効」と「宛先が違う」を分けて
    報告できる。**送信してから初めて気づく、という順番にしない。**
    """
    response = session.get(f"{base}/v2/bot/info")

    try:
        raise_for_line_error(response, *secrets)
    except ApiError as error:
        raise AuthError(
            f"{error}\n"
            f"{CHANNEL_ACCESS_TOKEN_ENV} の値が正しいか、"
            "対象のチャネルで Messaging API が有効かを確認してください。"
        ) from error

    payload = _payload_of(response)
    if not isinstance(payload, dict):
        raise AuthError("応答を JSON として読めませんでした（/v2/bot/info）。")

    # 照合の物差し。空のまま進むと照合が素通りする（課題8「0 件は不一致」と同じ形）。
    user_id = str(payload.get("userId") or "").strip()
    if not user_id:
        raise AuthError(
            "/v2/bot/info が userId を返しませんでした。"
            "送信者の照合ができないため中断します。"
        )

    basic_id = str(payload.get("basicId") or "").strip()
    if not basic_id:
        raise AuthError(
            "/v2/bot/info が basicId を返しませんでした。"
            "意図したチャネルかどうかを確かめられないため中断します。"
        )

    return BotInfo(
        user_id=user_id,
        basic_id=basic_id,
        display_name=str(payload.get("displayName") or ""),
        # **値は列挙して検査しない。** LINE 側が増やした日に動いていたものが止まる。
        chat_mode=str(payload.get("chatMode") or ""),
        mark_as_read_mode=str(payload.get("markAsReadMode") or ""),
    )
