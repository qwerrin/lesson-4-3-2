"""特定のサーバーのチャンネルへ、自動通知を1件送る。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task8\\send_notification.py --via bot \\
        --guild 1XXXXXXXXXXXXXXXXX --channel 2XXXXXXXXXXXXXXXXX \\
        --text "課題8の動作確認です" --json-out task8/results-bot.json

要件は「**ボットを作成し**」、ヒントは「**webhook を使うと簡単**」で、この2つは
同じものではない。そこで ``--via`` で経路を選べるようにして、両方を実測する。

===============  ==========================  ==============================
                 ``--via bot``               ``--via webhook``
===============  ==========================  ==============================
認証             Authorization: Bot <token>  **無し**（URL 自体が資格情報）
送信先           トークンが見えるチャンネル  その webhook のチャンネルだけ
既定の応答       作成したメッセージ          **204 No Content**（空）
メンションの既定 全種類を解釈                **ユーザーのみ**を解釈
===============  ==========================  ==============================

この課題で気をつけたこと
------------------------------------------------------------------

**1. 既定が経路によって違うので、既定に頼らない。**
公式リファレンスは、``allowed_mentions`` を省略した場合の既定を
通常のメッセージでは ``{"parse": ["users","roles","everyone"]}`` 相当、
「In interactions and webhooks, only user mentions are parsed」としている。
自動通知でいちばん怖いのは本文に紛れた ``@everyone`` が全員に飛ぶことなので、
**既定では全部のメンションを抑止する**（``{"parse": []}``）。
許すときだけ ``--allow-mentions`` を明示させる。

**2. ID は snowflake（数字）。名前を貼った取り違えを手前で止める。**
チャンネル「名」を渡しても Discord は 404 を返すだけで、原因が遠い。

**3. 「特定のサーバー内の」を確かめる物差しは、別のエンドポイントから取る。**
メッセージ作成の応答に ``guild_id`` は入らない。``GET /channels/{id}`` を
先に叩いて、人間が ``--guild`` で渡した値と突き合わせてから送る。
**送ってから確かめるのではなく、送る前に確かめられることは送る前に確かめる**
（課題5の「やり直せる操作とやり直せない操作で確認の設計が変わる」の延長。
 投稿は消せるが、通知が飛ぶのは取り消せない）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common import discord_auth  # noqa: E402


# 全部のメンションを抑止する指定。空配列が「1つも解釈しない」の意味で、
# 省略とは別物（省略は「全部解釈する」）。
MENTIONS_SUPPRESSED = {"parse": []}

VIA_BOT = "bot"
VIA_WEBHOOK = "webhook"


class SendError(Exception):
    """利用者にそのまま見せられる失敗。"""


@dataclass(frozen=True)
class Sent:
    """送れた通知。ID は snowflake なので**文字列のまま**持つ。

    64bit なので、JSON を経由する先で数値として扱われると末尾が変わる。
    課題7の「``ts`` を float にすると別のメッセージを指す」と同じ形。
    """

    via: str
    guild: str
    channel: str
    message_id: str
    author_id: str
    text: str


# ------------------------------------------------------------------ 入力の検査


def build_payload(text: str, *, allow_mentions: bool = False) -> dict:
    """送る本文を組む。

    **text を整形しない。** 前後の空白も落とさずそのまま送る。ここで黙って
    整えると、記録した値と実際に送った値が食い違い、読み返しの照合が
    「なぜか一致しない」になる。課題7で末尾の改行1バイトの差に気づけたのは、
    途中で整形していなかったから。
    """
    if not (text or "").strip():
        # 空のまま送ると API も弾く（code 50006）が、手前で止めたほうが原因が近い。
        raise SendError("メッセージが空です。--text に本文を渡してください")

    payload: dict = {"content": text}

    if not allow_mentions:
        payload["allowed_mentions"] = MENTIONS_SUPPRESSED

    return payload


def require_snowflake(value: str, label: str) -> str:
    """ID が snowflake（数字だけ）であることを確かめる。

    チャンネル「名」を貼る取り違えが実際に起きる。API は 404 を返すだけなので、
    手前で名指しして止める。
    """
    text = (value or "").strip()
    if not text:
        raise SendError(f"{label} が指定されていません")

    if not text.isdigit():
        raise SendError(
            f"{label} が数字ではありません。"
            "ID は開発者モードを ON にして右クリック →「ID をコピー」で取れます"
            "（名前ではありません）。"
        )

    return text


# ------------------------------------------------------------------ 応答の読み取り


def read_message(response, *, expected_channel: str | None) -> dict:
    """作成の応答からメッセージを取り出し、狙った先に載ったかを確かめる。

    **物差しは、こちらが要求した値から取る。** 応答の中だけで値どうしを
    比べるのはトートロジーで、何も確かめていない。
    """
    if response.status_code == 204:
        # webhook で ``wait`` を落とすとここに来る。エラーではないので、
        # 何が起きたかを言わないと利用者は原因にたどり着けない。
        raise SendError(
            "応答が 204 No Content でした（本文が返っていません）。"
            "webhook はクエリに wait=true を付けないと、作成したメッセージを返しません。"
            "返らないと message id が無く、読み返しの照合ができません。"
        )

    try:
        payload = response.json()
    except Exception as error:  # noqa: BLE001 - 本文が JSON でないことは実際に起きる
        raise SendError(
            "応答を JSON として読めませんでした。送れたかどうか確認できないため失敗として扱います。"
        ) from error

    if not isinstance(payload, dict):
        raise SendError("応答の形式が不正です（辞書ではありません）")

    message_id = str(payload.get("id") or "").strip()
    if not message_id:
        # 「返ってこなかった」を「送れた」にしない。id が無いと読み返せない。
        raise SendError(
            "応答に id がありません。送れたか確認できないため失敗として扱います"
        )

    if expected_channel is not None:
        returned = str(payload.get("channel_id") or "").strip()
        if returned != expected_channel:
            raise SendError(
                "要求したチャンネルと違うチャンネルに載りました: "
                f"要求 {expected_channel} / 応答 {returned}"
            )

    return payload


def read_author_id(message: dict) -> str:
    """メッセージの投稿者 ID を取り出す。

    webhook で送った場合もここに **webhook の id** が入る。
    """
    author = message.get("author")
    if not isinstance(author, dict):
        raise SendError("応答に author がありません。投稿者を確かめられません")

    author_id = str(author.get("id") or "").strip()
    if not author_id:
        raise SendError("応答の author に id がありません")

    return author_id


def require_author(message: dict, *, expected: str) -> str:
    """投稿者が、こちらが動かしている主体と同じかを確かめる。

    別のエンドポイント（``/users/@me`` または ``GET {webhook}``）が答えた
    値を物差しにする。同じ応答の中で比べない。
    """
    author_id = read_author_id(message)
    if author_id != expected:
        raise SendError(
            f"投稿者が想定と違います: 期待 {expected} / 応答 {author_id}"
        )
    return author_id


def require_guild(guild_id, *, expected: str) -> str:
    """チャンネルが、狙ったサーバーのものかを確かめる。

    ``guild_id`` が無いチャンネルは DM。要件は「サーバー内のチャンネル」なので、
    **無いことを「たぶん合っている」に倒さない**。
    """
    value = str(guild_id or "").strip()
    if not value:
        raise SendError(
            "このチャンネルはサーバーに属していません（DM の可能性があります）。"
            "要件はサーバー内のチャンネルです。"
        )

    if value != expected:
        raise SendError(
            f"チャンネルが別のサーバーのものです: 期待 {expected} / 実際 {value}"
        )

    return value


# ------------------------------------------------------------------ API 呼び出し


def fetch_channel(session, *, channel: str, secrets: tuple = ()) -> dict:
    """``GET /channels/{channel.id}``。``guild_id`` を取るために叩く。

    メッセージ作成の応答に ``guild_id`` は入らないので、
    「**特定のサーバー内の**チャンネル」は別のエンドポイントから確かめる。
    """
    channel = require_snowflake(channel, "チャンネルID")
    response = session.get(f"{discord_auth.API_BASE}/channels/{channel}")
    discord_auth.raise_for_discord_error(response, *secrets)

    try:
        payload = response.json()
    except Exception as error:  # noqa: BLE001
        raise SendError("チャンネル情報を JSON として読めませんでした") from error

    if not isinstance(payload, dict):
        raise SendError("チャンネル情報の形式が不正です")

    return payload


def post_via_bot(session, *, channel: str, payload: dict, secrets: tuple = ()) -> dict:
    """``POST /channels/{channel.id}/messages``（Bot Token 経路）。"""
    channel = require_snowflake(channel, "チャンネルID")
    response = session.post(
        f"{discord_auth.API_BASE}/channels/{channel}/messages", json=payload
    )
    discord_auth.raise_for_discord_error(response, *secrets)
    return read_message(response, expected_channel=channel)


def fetch_webhook(session, webhook: discord_auth.Webhook, *, secrets: tuple = ()) -> dict:
    """``GET /webhooks/{id}/{token}``（Get Webhook with Token）。

    **認証は要らない。** トークンが URL に入っているため。送る前に
    「この URL はどのチャンネルを向いているのか」を確かめられる。

    **伏せ字は呼び出し側に任せない。** webhook の token は「いま叩いている
    URL の一部」なので、例外の文面にも URL にも紛れ込みやすい。それなのに
    ``secrets`` を渡すかどうかを呼ぶ側の記憶に任せると、渡し忘れた経路だけ
    素で漏れる。課題6で「本番に入れた安全策が、その場で書いた確認用スクリプトには
    効かなかった」のと同じ形なので、**token を持っている関数の側で必ず伏せる**。
    """
    response = session.get(webhook.url)
    discord_auth.raise_for_discord_error(response, webhook.token, *secrets)

    try:
        payload = response.json()
    except Exception as error:  # noqa: BLE001
        raise SendError("Webhook の情報を JSON として読めませんでした") from error

    if not isinstance(payload, dict):
        raise SendError("Webhook の情報の形式が不正です")

    return payload


def post_via_webhook(
    session,
    webhook: discord_auth.Webhook,
    *,
    payload: dict,
    expected_channel: str,
    secrets: tuple = (),
) -> dict:
    """``POST /webhooks/{id}/{token}?wait=true``（Webhook 経路）。

    **``wait`` はクエリ文字列を自分で組まずに ``params`` で渡す。**
    URL にすでに ``?`` が付いていても壊れないうえ、付け忘れが
    「204 で静かに何も返らない」に化けるのを read_message() が捕まえる。

    fetch_webhook() と同じ理由で、**伏せ字は呼び出し側に任せない**。
    """
    response = session.post(webhook.url, params={"wait": "true"}, json=payload)
    discord_auth.raise_for_discord_error(response, webhook.token, *secrets)
    return read_message(response, expected_channel=expected_channel)


# ------------------------------------------------------------------ 記録


def message_link(*, guild: str, channel: str, message_id: str) -> str:
    """Discord クライアントで開けるメッセージのリンク。

    この形は API リファレンスではなく**クライアントの URL 規則**なので、
    実機で開いて確かめたうえで記事に載せる。
    """
    return f"https://discord.com/channels/{guild}/{channel}/{message_id}"


def build_record(
    *, via: str, guild: str, channel: str, text: str, message_id: str, author_id: str
) -> dict:
    """読み返しの照合に使う記録を組む。

    **資格情報を書かない。** このファイルは git に入る。webhook 経路でも
    入るのは webhook の **id**（URL の前半）だけで、資格情報である
    token（URL の後半）は書かない。
    """
    return {
        "via": via,
        "guild": guild,
        "channel": channel,
        "text": text,
        "message_id": message_id,
        "author_id": author_id,
        "link": message_link(guild=guild, channel=channel, message_id=message_id),
    }


def write_record(path: str | Path, record: dict) -> None:
    destination = Path(path)
    if destination.parent != Path(""):
        destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ------------------------------------------------------------------ 入口


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="特定のサーバーのチャンネルへ自動通知を1件送ります。"
    )
    parser.add_argument(
        "--via",
        choices=(VIA_BOT, VIA_WEBHOOK),
        default=VIA_BOT,
        help="送信経路。bot は Bot Token、webhook は Webhook URL を使う（既定: bot）",
    )
    parser.add_argument("--guild", required=True, help="サーバーID（数字）")
    parser.add_argument("--channel", required=True, help="チャンネルID（数字）")
    parser.add_argument("--text", required=True, help="送る本文")
    parser.add_argument(
        "--json-out", help="結果の記録先。指定したときだけ書く（既定のパスへ勝手に上書きしない）"
    )
    parser.add_argument(
        "--allow-mentions",
        action="store_true",
        help="本文中のメンションを有効にする（既定は全部抑止。@everyone の事故を防ぐため）",
    )
    # --token / --webhook-url は用意しない。コマンドライン引数は履歴と ps に残る。
    return parser.parse_args(argv)


def _default_bot_factory():
    token = discord_auth.read_bot_token(os.environ)
    session = discord_auth.build_session(token)
    identity = discord_auth.fetch_identity(session, secrets=(token,))
    return session, identity, token


def _default_webhook_factory():
    url = discord_auth.read_webhook_url(os.environ)
    webhook = discord_auth.parse_webhook_url(url)
    session = discord_auth.build_anonymous_session()
    return session, webhook


def _run_bot(args, factory) -> tuple[Sent, tuple]:
    session, identity, token = factory()
    secrets = (token,)

    print("経路: Bot Token（Authorization ヘッダで送る）")
    print(f"Bot: {identity.username} ({identity.user_id})")

    channel = require_snowflake(args.channel, "チャンネルID")
    guild = require_snowflake(args.guild, "サーバーID")

    # 送る前に、狙ったサーバーのチャンネルかを別のエンドポイントで確かめる。
    channel_object = fetch_channel(session, channel=channel, secrets=secrets)
    require_guild(channel_object.get("guild_id"), expected=guild)
    print(f"サーバー: {guild} / チャンネル: {channel}（GET /channels で確認済み）")

    payload = build_payload(args.text, allow_mentions=args.allow_mentions)
    message = post_via_bot(session, channel=channel, payload=payload, secrets=secrets)
    author_id = require_author(message, expected=identity.user_id)

    return (
        Sent(
            via=VIA_BOT,
            guild=guild,
            channel=channel,
            message_id=str(message["id"]),
            author_id=author_id,
            text=args.text,
        ),
        secrets,
    )


def _run_webhook(args, factory) -> tuple[Sent, tuple]:
    session, webhook = factory()
    secrets = (webhook.token,)

    print("経路: Webhook（Authorization ヘッダを付けない。URL 自体が資格情報）")

    channel = require_snowflake(args.channel, "チャンネルID")
    guild = require_snowflake(args.guild, "サーバーID")

    # 送る前に、この URL がどこを向いているかを確かめる。
    webhook_object = fetch_webhook(session, webhook, secrets=secrets)

    target = str(webhook_object.get("channel_id") or "").strip()
    if target != channel:
        raise SendError(
            "Webhook が別のチャンネルを向いています: "
            f"指定 {channel} / Webhook の宛先 {target}\n"
            "Webhook はチャンネルに固定されています。宛先は URL 側で決まります。"
        )

    if webhook_object.get("guild_id") is not None:
        require_guild(webhook_object.get("guild_id"), expected=guild)
        print(f"サーバー: {guild} / チャンネル: {channel}（GET /webhooks で確認済み）")
    else:
        # **確認できなかったことを「一致した」に倒さない**（課題7の教訓）。
        print(
            f"チャンネル: {channel}（GET /webhooks で確認済み）。"
            "サーバーは応答に guild_id が無いため確認できません"
        )

    payload = build_payload(args.text, allow_mentions=args.allow_mentions)
    message = post_via_webhook(
        session, webhook, payload=payload, expected_channel=channel, secrets=secrets
    )

    # webhook で送ったメッセージの author.id は webhook の id になる。
    author_id = require_author(message, expected=webhook.id)

    marker = str(message.get("webhook_id") or "").strip()
    if marker != webhook.id:
        raise SendError(
            "このメッセージは webhook 経由に見えません: "
            f"期待 {webhook.id} / 応答の webhook_id {marker or '(無し)'}"
        )

    return (
        Sent(
            via=VIA_WEBHOOK,
            guild=guild,
            channel=channel,
            message_id=str(message["id"]),
            author_id=author_id,
            text=args.text,
        ),
        secrets,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    bot_factory: Callable | None = None,
    webhook_factory: Callable | None = None,
) -> int:
    args = parse_args(argv)

    try:
        if args.via == VIA_BOT:
            sent, _secrets = _run_bot(args, bot_factory or _default_bot_factory)
        else:
            sent, _secrets = _run_webhook(args, webhook_factory or _default_webhook_factory)
    except (discord_auth.DiscordError, SendError) as error:
        print(error, file=sys.stderr)
        return 1

    record = build_record(
        via=sent.via,
        guild=sent.guild,
        channel=sent.channel,
        text=sent.text,
        message_id=sent.message_id,
        author_id=sent.author_id,
    )

    print("\n送信しました。")
    print(f"  メッセージID: {sent.message_id}")
    print(f"  投稿者ID: {sent.author_id}")
    print(f"  本文: {sent.text}")
    print(f"  リンク: {record['link']}")

    if args.json_out:
        write_record(args.json_out, record)
        print(f"\n結果を書き出しました: {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
