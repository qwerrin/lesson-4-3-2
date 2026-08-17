"""Discord の認証。課題をまたいで使えるように common/ に置く。

common/ の他の4つとは別モジュールにしてある。5つとも「認証」だが手順が違う。

===================== ================================================
何を使うか             どういう相手か
===================== ================================================
google_auth（OAuth）  **本人のデータ**を触る。同意画面 → token.json → リフレッシュ
zoom_auth（S2S）      アカウントの権限で動く。同意画面なし・毎回取り直し
youtube_auth（キー）  **公開データ**を読むだけ。認可する相手がいない
slack_auth（Bot）     **アプリ自身**が動く。インストール時に1回発行、期限なし
discord_auth（Bot）   アプリ自身が動く。**加えて「認証しない経路」が併存する**
===================== ================================================

使う側::

    from common import discord_auth

    token = discord_auth.read_bot_token(os.environ)
    session = discord_auth.build_session(token)
    identity = discord_auth.fetch_identity(session, secrets=(token,))

Discord に固有の事情が3つある。
------------------------------------------------------------------

**1. 資格情報が2種類あって、片方は URL そのもの。**

Webhook URL は ``Authorization`` ヘッダを付けずに投稿できる（2026-08-17 に
公式リファレンスで確認：「Not required — the webhook token alone is sufficient」）。
つまり **URL を知っている人は誰でもそのチャンネルに投稿できる**。Bot Token と
危険度は同じなのに、「URL」という見た目のせいで軽く扱われやすい。
redact() が可変個の秘密を受けるのはこのため。

**2. User-Agent が必須。**

公式リファレンスは ``DiscordBot ($url, $versionNumber)`` の形を要求し、妥当な
User-Agent が無いリクエストは「may be blocked and return a Cloudflare error」と
書いている。requests の既定 UA では要件を満たさない。**付け忘れても手元の
テストは通る**ので、ヘッダの有無をテストで固定してある。

**3. 付与された権限を問い合わせる安い方法が無い。**

slack_auth は ``x-oauth-scopes`` ヘッダを読んで check_scopes() を出せた。
Discord のチャンネル権限はロールと上書きの計算結果で、1回の API では出ない。
→ **「権限を確認した」という顔をしない。** 代わりにエラーコードを訳し分ける。

この判断には副作用がある。``READ_MESSAGE_HISTORY`` が無いときだけは
**エラーが返らない**（公式：「If the current user is missing the
READ_MESSAGE_HISTORY permission in the channel, then no messages will be
returned.」）。落ちないぶんこちらのほうが厄介で、読み返しが
「0 件だったので照合する対象がありません」に化ける。**そこは呼び出し側の
照合で「0 件は不一致」として捕まえる**（このモジュールの責務外）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import requests

BOT_TOKEN_ENV = "DISCORD_BOT_TOKEN"
WEBHOOK_URL_ENV = "DISCORD_WEBHOOK_URL"

# 公式リファレンスの記載どおり、バージョンを明示して固定する。
# 付けないと既定バージョンに追随してしまい、ある日いきなり形が変わる。
API_BASE = "https://discord.com/api/v10"

# 公式が要求する形 ``DiscordBot ($url, $versionNumber)``。
USER_AGENT = "DiscordBot (https://github.com/qwerrin/lesson-4-3-2, 1.0)"

# 伏せたことが分かる印。空文字にすると「元から無かった」と区別がつかない。
REDACTED = "***"

# Webhook URL として受け付けるホスト。**前方一致や部分一致で見ない。**
# ``discord.com.evil.example`` が通ってしまう。
_WEBHOOK_HOSTS = frozenset(
    {
        "discord.com",
        "www.discord.com",
        "canary.discord.com",
        "ptb.discord.com",
        # 旧ドメイン。既存の webhook URL がこの形で配られていることがある。
        "discordapp.com",
        "www.discordapp.com",
        "canary.discordapp.com",
        "ptb.discordapp.com",
    }
)


class DiscordError(Exception):
    """このモジュールが出す失敗の共通の親。利用者にそのまま見せられる。"""


class AuthError(DiscordError):
    """資格情報まわりの失敗。"""


class ApiError(DiscordError):
    """API が 2xx 以外を返した。"""


@dataclass(frozen=True)
class Identity:
    """``GET /users/@me`` が答えた「あなたは誰か」。

    user_id は **Bot 自身のユーザーID**で、読み返したメッセージの
    ``author.id`` と突き合わせる物差しになる。投稿の応答だけを見て
    「投稿できた」と判断すると、同じ応答の中で値を比べるトートロジーになる
    （課題4・課題6・課題7と同じ形）。別のエンドポイントから取った
    この値と比べて初めて閉じる。
    """

    user_id: str
    username: str


@dataclass(frozen=True)
class Webhook:
    """Webhook URL を分解したもの。

    url は**クエリと断片を落とし、末尾スラッシュも外した形**で持つ。
    読み返しの ``GET {url}/messages/{message.id}`` はこの文字列に継ぎ足して
    組むので、``?thread_id=...`` が残っていると継ぎ足した先が壊れる。
    ホストとパスは受け取った値のまま使う（組み直すとホストや API バージョンを
    取り違える余地ができる）。
    """

    id: str
    token: str
    url: str


# ------------------------------------------------------------------ 資格情報を読む


def read_bot_token(env: Mapping[str, str]) -> str:
    """環境変数から Bot Token を読む。

    空文字・空白だけは「未設定」と同じ扱いにする。``$env:DISCORD_BOT_TOKEN = ""``
    と書いただけでも変数としては存在するので、有無だけ見ると素通りして、
    後段の API が 401 を返し、原因がここだと分からなくなる。

    **値そのものは絶対にメッセージへ載せない**（壊れた値であっても）。
    打ち間違いなら本物がそのまま入っている。この文言は公開する
    スクリーンショットに写る。
    """
    value = (env.get(BOT_TOKEN_ENV) or "").strip()
    if not value:
        raise AuthError(
            f"Discord の Bot Token が設定されていません: {BOT_TOKEN_ENV}\n"
            "https://discord.com/developers/applications でアプリの Bot ページを開き、"
            "「Reset Token」で発行した値を環境変数に設定してください。手順は README を参照。\n"
            f'PowerShell: $env:{BOT_TOKEN_ENV} = "<Bot Token>"'
        )

    if value.lower().startswith("bot "):
        raise AuthError(
            f"{BOT_TOKEN_ENV} に Authorization ヘッダの形で入っています。\n"
            "先頭の「Bot 」は送信時にこちらで付けます。トークンだけを設定してください。"
        )

    if value.lower().startswith(("http://", "https://")):
        raise AuthError(
            f"{BOT_TOKEN_ENV} に URL が入っています。Webhook URL と取り違えていませんか。\n"
            f"Webhook URL は {WEBHOOK_URL_ENV} に設定します。"
            "Bot Token は Bot ページの「Reset Token」で発行する値です。"
        )

    return value


def read_webhook_url(env: Mapping[str, str]) -> str:
    """環境変数から Webhook URL を読み、形まで確かめて返す。

    **この URL は資格情報そのもの。** 形が違うときも値を載せない。
    """
    value = (env.get(WEBHOOK_URL_ENV) or "").strip()
    if not value:
        raise AuthError(
            f"Discord の Webhook URL が設定されていません: {WEBHOOK_URL_ENV}\n"
            "対象チャンネルの設定 → 連携サービス → ウェブフック で作成し、"
            "「ウェブフック URL をコピー」した値を環境変数に設定してください。\n"
            f'PowerShell: $env:{WEBHOOK_URL_ENV} = "https://discord.com/api/webhooks/..."'
        )

    parse_webhook_url(value)  # 形が違えば AuthError（値は載らない）
    return value


def parse_webhook_url(url: str) -> Webhook:
    """Webhook URL から id と token を取り出す。

    受け付ける形は ``https://<discord のホスト>/api[/vN]/webhooks/{id}/{token}``。

    **ホストは完全一致で見る。** 前方一致にすると
    ``https://discord.com.evil.example/api/webhooks/...`` が通り、
    資格情報を他人のサーバーへ送ることになる。

    **例外に URL を載せない。** 壊れているから安全、ではない。打ち間違いなら
    token 部分は本物のままである。
    """
    parts = urlsplit((url or "").strip())

    if parts.scheme != "https":
        raise AuthError(
            "Webhook URL は https である必要があります"
            "（URL 自体が資格情報なので、平文で送らせない）。"
        )

    if parts.hostname not in _WEBHOOK_HOSTS:
        raise AuthError(
            "Webhook URL のホストが Discord のものではありません。"
            "コピー元を確認してください。"
        )

    if parts.username or parts.password:
        # user:pass@host は hostname の検査を素通りする形で紛れ込む。
        # Discord の URL に認証情報部は付かないので、付いていたら偽物を疑う。
        raise AuthError("Webhook URL に認証情報部（user:pass@）が含まれています。")

    if parts.port is not None:
        raise AuthError("Webhook URL にポート指定が含まれています。")

    path_segments = [segment for segment in parts.path.split("/") if segment]

    # /api/webhooks/{id}/{token} と /api/v10/webhooks/{id}/{token} の両方を受ける。
    segments = path_segments
    if len(segments) == 5 and segments[0] == "api" and segments[1].startswith("v"):
        segments = [segments[0], *segments[2:]]

    if len(segments) != 4 or segments[0] != "api" or segments[1] != "webhooks":
        raise AuthError(
            "Webhook URL の形が違います。"
            "https://discord.com/api/webhooks/<id>/<token> の形を設定してください。"
        )

    webhook_id, token = segments[2], segments[3]

    if not webhook_id.isdigit():
        raise AuthError(
            "Webhook URL の ID が数字ではありません（ID は snowflake で数字だけ）。"
        )

    # ここに ``if not token:`` を置いていたが、**到達できない**ので消した。
    # path_segments は空の要素を落としてから数えているので、4要素あるなら
    # segments[3] は必ず空でない。トークンが無い URL は上の「形が違います」で
    # 止まる。**外しても結果が1つも変わらないガードは、仕事をしていない**
    # （課題7の ``if not headers:`` と同じ形。テストを何件足しても kill できない）。

    # **継ぎ足せる形に正規化する。** 読み返しは `{url}/messages/{id}` を組むので、
    # 末尾スラッシュやクエリが残っていると継ぎ足した先が壊れる。
    # ホストは hostname から組み直す（netloc をそのまま使うと認証情報部が残る）。
    normalized = f"{parts.scheme}://{parts.hostname}/" + "/".join(path_segments)

    return Webhook(id=webhook_id, token=token, url=normalized)


# ------------------------------------------------------------------ 伏せ字


def redact(text: str, *secrets: str | None) -> str:
    """文字列から資格情報を伏せる。

    空や None の秘密は素通りさせる。``str.replace("", x)`` は**全部の文字の
    間に x を挿し込む**ので、素通りさせないと文章が壊れる。Webhook を使わない
    実行では webhook の token が None で渡るため、この経路は実在する。
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


# ------------------------------------------------------------------ セッション


def _new_session(factory: Callable):
    session = factory()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def build_session(token: str, *, factory: Callable = requests.Session):
    """Bot Token を載せた HTTP セッションを組む。

    トークンは ``Authorization`` ヘッダで送る。**URL には載らない**ので、
    課題6（YouTube の API キーが URI に載って例外で漏れる）と同じ漏れ方は
    しない。ただし *その漏れ方が* 起きないだけなので、表に出す文字列は
    redact() を通す方針を変えない。
    """
    value = (token or "").strip()
    if not value:
        # 空のまま組むと、実行時に 401 が返って原因がここだと分からなくなる。
        raise AuthError(f"Bot Token が空です。{BOT_TOKEN_ENV} を設定してください")

    session = _new_session(factory)
    session.headers.update({"Authorization": f"Bot {value}"})
    return session


def build_anonymous_session(*, factory: Callable = requests.Session):
    """Webhook 用の、``Authorization`` を付けないセッション。

    付けても動くが、**付ける必要が無いことがこの経路の性質**である。
    ここを曖昧にすると「webhook も認証している」という誤った理解のまま
    記事を書くことになる。分けてあるのは、その違いを実行で見せるため。
    """
    return _new_session(factory)


# ------------------------------------------------------------------ エラーの訳し分け


# エラーコードごとの直しかた。**相手が名指しで答えているものに候補を並べない**
# （課題4・Meet の教訓）。ここに無いコードは、コードと本文をそのまま出す。
_ERROR_HINTS = {
    50001: (
        "Bot がそのチャンネルを見られません。"
        "アプリの権限に View Channels があるか、チャンネル個別の権限で"
        "上書きされていないかを確認してください。"
    ),
    50013: (
        "Bot に操作の権限がありません。"
        "アプリの権限に Send Messages（投稿）と Read Message History（読み返し）が"
        "あるかを確認してください。"
    ),
    10003: (
        "チャンネルが見つかりません。チャンネル ID を確認してください"
        "（開発者モードを ON にして、チャンネルを右クリック →「チャンネル ID をコピー」）。"
    ),
    10008: "メッセージが見つかりません。メッセージ ID を確認してください。",
    10015: "Webhook が見つかりません。Webhook URL を確認してください。",
    50006: "空のメッセージは送れません。本文を指定してください。",
    50035: "リクエストの形が不正です。本文の長さや項目名を確認してください。",
}


def _payload_of(response) -> Any:
    try:
        return response.json()
    except Exception:  # noqa: BLE001 - 本文が JSON でないことは実際に起きる
        return None


def raise_for_discord_error(response, *secrets: str | None) -> None:
    """2xx でなければ、利用者に見せられる ApiError にして投げる。

    **エラーコードは必ず出す。** 日本語の説明だけにすると公式ドキュメントを
    引けない。そのうえで、こちらが直しかたを知っているコードにだけ一行足す。

    本文が JSON でないことは実際に起きる（Cloudflare が挟まると HTML）。
    素の例外を出すと、利用者に読めないものが表示される。
    """
    status = response.status_code
    if 200 <= status < 300:
        return

    payload = _payload_of(response)
    body = payload if isinstance(payload, dict) else {}

    message = f"Discord API がエラーを返しました: HTTP {status}"

    code = body.get("code")
    if code is not None:
        message += f" / code {code}"

    detail = body.get("message")
    if detail:
        message += f"\n{detail}"
    elif not body:
        # JSON ではなかった。中身は載せない（HTML がそのまま流れると読めない）。
        message += "\n応答が JSON ではありませんでした。"

    hint = _ERROR_HINTS.get(code)
    if hint:
        message += f"\n{hint}"

    if status == 429:
        # **retry_after は秒**（小数あり）。ミリ秒と取り違えると待ち時間が1000倍ずれる。
        retry_after = body.get("retry_after")
        if retry_after is not None:
            message += f"\nレート制限です。{retry_after} 秒待ってから再実行してください。"
        if body.get("global"):
            message += "\n（グローバルの制限です。全 Bot 共通で毎秒 50 リクエストまで）"

    raise ApiError(redact(message, *secrets))


# ------------------------------------------------------------------ 自分が誰か


def fetch_identity(session, *, base: str = API_BASE, secrets: tuple = ()) -> Identity:
    """``GET /users/@me`` で「このトークンが誰なのか」を確かめる。

    最初の1回をここに置くと、「トークンが無効」と「権限が足りない」を
    分けて報告できる。投稿してから初めて気づく、という順番にしない。
    """
    response = session.get(f"{base}/users/@me")

    try:
        raise_for_discord_error(response, *secrets)
    except ApiError as error:
        raise AuthError(
            f"{error}\n"
            f"{BOT_TOKEN_ENV} の値が正しいか、アプリがサーバーに追加されているかを"
            "確認してください。"
        ) from error

    payload = _payload_of(response)
    if not isinstance(payload, dict):
        raise AuthError("応答を JSON として読めませんでした（/users/@me）。")

    # **プレフィックス検査は形式しか見ていない。** ここが性質の検査になる。
    # ユーザーのトークンでも /users/@me は成功するが、bot は真にならない。
    # 真でないものを通すと「アプリが投稿した」つもりで本人名義の投稿ができる。
    if payload.get("bot") is not True:
        raise AuthError(
            "このトークンは Bot Token ではありません（/users/@me の bot が真ではありません）。\n"
            "Discord Developer Portal の Bot ページで発行したトークンを使ってください。"
        )

    # 投稿者の照合に使う物差し。空のまま進むと照合が素通りする。
    user_id = str(payload.get("id") or "").strip()
    if not user_id:
        raise AuthError(
            "/users/@me が id を返しませんでした。投稿者の照合ができないため中断します。"
        )

    return Identity(user_id=user_id, username=str(payload.get("username") or ""))
