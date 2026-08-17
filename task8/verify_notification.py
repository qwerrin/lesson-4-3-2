"""送った通知を読み直して、送った内容と突き合わせる。

**読むだけ。何も投稿しないし、書き換えない。**

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task8\\verify_message.py --results task8/results-bot.json \\
        --guild 1XXXXXXXXXXXXXXXXX --channel 2XXXXXXXXXXXXXXXXX \\
        --expect-text "課題8の動作確認です"

send_notification.py が成功しても、それは「API が 2xx を返した」までしか
意味しない。本当にそのサーバーのそのチャンネルへ、狙った本文が、こちらの
主体の名前で載ったのかは、別のところから読み直さないと閉じない。

**物差しを応答の外から取る。**

===================  ==============================================
何を確かめるか        物差しの出どころ
===================  ==============================================
サーバー・チャンネル  **人間がコマンドラインで渡す**
本文                  **人間がコマンドラインで渡す**
メッセージID          送信時に記録した値
投稿者                ``GET /users/@me``（Bot）／``GET /webhooks/…``（Webhook）
サーバー所属          ``GET /channels/{id}``（メッセージの応答には無い）
===================  ==============================================

この読み返しには**落ちない失敗**が2つある
------------------------------------------------------------------

**1. ``READ_MESSAGE_HISTORY`` が無いと、エラーではなく空。**
公式リファレンス曰く「If the current user is missing the READ_MESSAGE_HISTORY
permission in the channel, then no messages will be returned.」
落ちてくれないぶんこちらのほうが厄介で、**「0 件だったので照合対象なし」が
「全部一致」に化ける**。だから「メッセージの実在」を照合項目に置く。

**2. ``MESSAGE_CONTENT`` 特権インテントが無いと ``content`` が空文字。**
自分が送ったメッセージは例外（「Content in messages that an app sends」）
なので普段は取れる。ただし**「取れるはず」を根拠に空を素通りさせない**。
空だったときは「本文が違う」ではなく「**読めていない**」と報告する。
原因が違えば直しかたも違うので、同じ「不一致」で片付けない。
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
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import send_notification  # noqa: E402
from common import discord_auth  # noqa: E402


_REQUIRED_KEYS = ("via", "guild", "channel", "text", "message_id", "author_id", "link")
_KNOWN_ROUTES = (send_notification.VIA_BOT, send_notification.VIA_WEBHOOK)

_INTENT_HINT = (
    "本文が空で返りました。MESSAGE CONTENT 特権インテントが必要な状態かもしれません"
    "（自分が送ったメッセージは本来この制限の例外です）。"
)


class VerifyError(Exception):
    """利用者にそのまま見せられる失敗。"""


@dataclass
class Check:
    label: str
    ok: bool
    detail: str = ""


# ---------------------------------------------------------------- 結果ファイル


def load_results(path: str | Path) -> dict:
    """send_notification.py が書いた結果ファイルを読む。

    形が違うファイルを黙って受け入れない。中身が欠けたまま照合に進むと
    「確かめた項目ゼロで全部一致」が出る。
    """
    source = Path(path)
    if not source.exists():
        raise VerifyError(
            f"結果ファイルが見つかりません: {source}\n"
            "send_notification.py を --json-out 付きで実行してください。"
        )

    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise VerifyError(f"結果ファイルを JSON として読めません: {source}") from error

    if not isinstance(payload, dict):
        raise VerifyError(f"結果ファイルの形式が不正です（辞書ではありません）: {source}")

    for key in _REQUIRED_KEYS:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            # ID が数値で入っている場合もここで落ちる。snowflake は 64bit なので、
            # 数値になっている時点で末尾が変わっている可能性がある。
            raise VerifyError(
                f"結果ファイルに {key} がありません（空でない文字列である必要があります）"
            )

    return payload


# ---------------------------------------------------------------- 手元でできる照合


def _compare(label: str, expected, actual) -> Check:
    ok = expected == actual
    detail = "" if ok else f"期待 {expected!r} / 実際 {actual!r}"
    return Check(label, ok, detail)


def build_local_checks(
    payload: dict, *, expected_guild: str, expected_channel: str, expected_text: str
) -> list[Check]:
    """API を呼ばずに確かめられることを先に済ませる。

    ここが落ちる実行はネットワークに出す価値がない。
    """
    checks: list[Check] = []

    checks.append(_compare("サーバー", expected_guild, payload.get("guild")))
    checks.append(_compare("チャンネル", expected_channel, payload.get("channel")))
    # 期待値は人間が渡した値。結果ファイルの値で埋めると、ファイルが
    # 間違っていても一致する。
    checks.append(_compare("送った本文", expected_text, payload.get("text")))

    via = payload.get("via")
    checks.append(
        Check(
            "送信経路",
            via in _KNOWN_ROUTES,
            "" if via in _KNOWN_ROUTES else f"知らない経路です: {via!r}",
        )
    )

    # リンクは3つの ID をつないだもの。1つでも欠ければ別の場所を指す。
    link = payload.get("link") or ""
    parts = (payload.get("guild") or "", payload.get("channel") or "", payload.get("message_id") or "")
    missing = [part for part in parts if not part or part not in link]
    checks.append(
        Check(
            "リンクの指す先",
            not missing,
            "" if not missing else f"リンクに含まれていない値があります: {missing}",
        )
    )

    message_id = payload.get("message_id") or ""
    ok = message_id.isdigit()
    checks.append(
        Check("メッセージIDの形式", ok, "" if ok else f"snowflake ではありません: {message_id!r}")
    )

    return checks


# ---------------------------------------------------------------- 読み直す


def _read_message_body(response) -> dict:
    try:
        payload = response.json()
    except Exception as error:  # noqa: BLE001
        raise VerifyError("応答を JSON として読めませんでした") from error

    if not isinstance(payload, dict):
        raise VerifyError("応答の形式が不正です（辞書ではありません）")

    return payload


def fetch_message_via_bot(
    session, *, channel: str, message_id: str, secrets: tuple = ()
) -> dict:
    """``GET /channels/{channel.id}/messages/{message.id}``（Bot Token 経路）。"""
    response = session.get(
        f"{discord_auth.API_BASE}/channels/{channel}/messages/{message_id}"
    )
    discord_auth.raise_for_discord_error(response, *secrets)
    return _read_message_body(response)


def fetch_message_via_webhook(
    session, webhook: discord_auth.Webhook, *, message_id: str, secrets: tuple = ()
) -> dict:
    """``GET /webhooks/{id}/{token}/messages/{message.id}``（Webhook 経路）。

    **認証は要らない**（公式：「the webhook token alone is sufficient」）。
    読めるのは**その webhook が送ったメッセージだけ**で、チャンネルの
    他の投稿は読めない。webhook が「送信専用の口」であることがここに出る。

    send_notification と同じ理由で、**伏せ字は呼び出し側に任せない**。
    """
    response = session.get(f"{webhook.url}/messages/{message_id}")
    discord_auth.raise_for_discord_error(response, webhook.token, *secrets)
    return _read_message_body(response)


def fetch_guild_id(session, *, channel: str, secrets: tuple = ()) -> str:
    """``GET /channels/{id}`` から ``guild_id`` を取る。

    メッセージの応答に ``guild_id`` は入らないので、「**特定のサーバー内の**
    チャンネル」は別のエンドポイントから確かめる。
    """
    channel_object = send_notification.fetch_channel(
        session, channel=channel, secrets=secrets
    )
    return str(channel_object.get("guild_id") or "")


# ---------------------------------------------------------------- 照合


def build_remote_checks(
    payload: dict, message, *, actor_id: str, guild_id: str
) -> list[Check]:
    """読み直した内容と、送った内容を突き合わせる。

    ``message`` が偽値のときは「読み返せなかった」。**0 件を「照合する対象が
    無い＝全部一致」にしない。**
    """
    checks: list[Check] = []

    # 記録した投稿者と、いま動かしている主体が同じか。別のトークンで確認すると、
    # 一致しても他人の投稿を見ているだけになる。
    checks.append(_compare("実行中の主体", payload.get("author_id"), actor_id))

    checks.append(_compare("チャンネルの所属サーバー", payload.get("guild"), guild_id))

    if not message:
        checks.append(
            Check(
                "メッセージの実在",
                False,
                "読み返せませんでした（削除された・チャンネルが違う・"
                "Read Message History が無い、のいずれか）",
            )
        )
        return checks

    checks.append(Check("メッセージの実在", True))

    # 返ってきたのが狙ったメッセージか。**ここを見ないと「別のメッセージが
    # 返ってきた」が「一致した」に化ける。**
    checks.append(_compare("メッセージID", payload.get("message_id"), str(message.get("id") or "")))
    checks.append(_compare("チャンネル", payload.get("channel"), str(message.get("channel_id") or "")))

    author = message.get("author")
    author_id = str(author.get("id") or "") if isinstance(author, dict) else ""
    checks.append(_compare("投稿者", actor_id, author_id))

    # 本文。**Discord は保存時に本文を変換しない**（Slack が & を &amp; に
    # したのとの違い）。ここで「どちらでも通す」判定を入れると照合が
    # 何も確かめなくなるので、そのままの一致を求める。
    expected = payload.get("text") or ""
    actual = str(message.get("content") or "")
    if not actual:
        # 「違う」ではなく「読めていない」。原因が違えば直しかたも違う。
        checks.append(Check("本文", False, _INTENT_HINT))
    else:
        checks.append(_compare("本文", expected, actual))

    return checks


# ---------------------------------------------------------------- 報告


def all_ok(checks: Sequence[Check]) -> bool:
    """全部一致したか。

    空のリストに all() を掛けると True になる。「何も確かめていない」が
    「全部一致」に化けるので、ゼロ件は False にする。
    """
    if not checks:
        return False
    return all(check.ok for check in checks)


def format_checks(checks: Sequence[Check]) -> str:
    lines = []
    for check in checks:
        mark = "OK" if check.ok else "NG"
        line = f"  [{mark}] {check.label}"
        if check.detail:
            line += f"  {check.detail}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------- 入口


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="送った通知を読み直して突き合わせます（読むだけ）。"
    )
    parser.add_argument("--results", required=True, help="send_notification.py が書いたファイル")
    # 期待値は応答の外から取る。必須にして、ファイルの値で埋める逃げ道を作らない。
    parser.add_argument("--guild", required=True, help="送ったサーバーID")
    parser.add_argument("--channel", required=True, help="送ったチャンネルID")
    parser.add_argument("--expect-text", required=True, help="送った本文")
    return parser.parse_args(argv)


def _verify_via_bot(payload, factory):
    session, identity, token = factory()
    secrets = (token,)
    guild_id = fetch_guild_id(session, channel=payload["channel"], secrets=secrets)
    message = fetch_message_via_bot(
        session, channel=payload["channel"], message_id=payload["message_id"], secrets=secrets
    )
    return message, identity.user_id, guild_id, "GET /channels/{id}/messages/{id}"


def _verify_via_webhook(payload, factory):
    session, webhook = factory()
    secrets = (webhook.token,)
    webhook_object = send_notification.fetch_webhook(session, webhook, secrets=secrets)
    guild_id = str(webhook_object.get("guild_id") or "")
    message = fetch_message_via_webhook(
        session, webhook, message_id=payload["message_id"], secrets=secrets
    )
    return message, str(webhook_object.get("id") or ""), guild_id, "GET /webhooks/{id}/{token}/messages/{id}"


def main(
    argv: Sequence[str] | None = None,
    *,
    bot_factory: Callable | None = None,
    webhook_factory: Callable | None = None,
) -> int:
    args = parse_args(argv)

    try:
        payload = load_results(args.results)
    except VerifyError as error:
        print(error, file=sys.stderr)
        return 1

    local = build_local_checks(
        payload,
        expected_guild=args.guild,
        expected_channel=args.channel,
        expected_text=args.expect_text,
    )
    print("結果ファイルの照合（API を呼ばずに確かめられること）:")
    print(format_checks(local))

    if not all_ok(local):
        print("\n食い違いがあります。上の NG を確認してください。", file=sys.stderr)
        return 1

    try:
        if payload["via"] == send_notification.VIA_BOT:
            message, actor_id, guild_id, endpoint = _verify_via_bot(
                payload, bot_factory or send_notification._default_bot_factory
            )
        else:
            message, actor_id, guild_id, endpoint = _verify_via_webhook(
                payload, webhook_factory or send_notification._default_webhook_factory
            )
    except (discord_auth.DiscordError, send_notification.SendError, VerifyError) as error:
        print(error, file=sys.stderr)
        return 1

    remote = build_remote_checks(payload, message, actor_id=actor_id, guild_id=guild_id)
    print(f"\n{endpoint} で読み直した内容との照合（送信とは別のエンドポイント）:")
    print(format_checks(remote))

    if not all_ok(remote):
        print("\n食い違いがあります。上の NG を確認してください。", file=sys.stderr)
        return 1

    print("\nすべて一致しました。")
    # **「すべて一致しました」だけを出す道具は、検査していない場所まで
    # 保証しているように読める**（課題5の教訓）。範囲を明記する。
    print(
        "確かめていないこと: 画面上の見え方 / 通知が誰に飛んだか / 添付とリンクの展開。"
        "この確認が見ているのは、上に並べた項目だけです。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
