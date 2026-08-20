#!/usr/bin/env python3
"""``MESSAGE_CONTENT`` 特権インテントが、``GET /channels/{id}/messages`` の
``content`` に実際どう効くかを実測する。

課題8の ``verify_notification.py`` は

    「``MESSAGE_CONTENT`` 特権インテントが無いと ``content`` が空文字。
      自分が送ったメッセージは例外なので普段は取れる」

と書いた。だが課題8が読んでいたのは**自分が送ったメッセージだけ**で、
**この分岐が発火する状況を一度も作っていない**。ここで作る。

===================  ==================================================
確かめること          インテントの ON/OFF で ``content`` がどう変わるか
物差しをどこから取るか  **投稿した本文を人間がコマンドラインで渡す**（応答の外から取る）
====================  ==================================================

**「空」には2つの原因がある。**

1. インテントが無くて**読めていない**
2. そのメッセージが**本当に本文を持たない**（system message、画像だけ、など）

応答1回だけでは1と2は区別できない。区別する手は2つ用意した。

* ``--expect-text`` … 投稿したはずの文字列を外から渡す。それが空で返れば1
* ``--compare`` … **同じメッセージ ID** について前回の結果と突き合わせる。
  インテントを切り替えただけで値が変われば1、変わらなければ2

``type``（メッセージ種別）も必ず出す。system message は本文を持たないので、
**種別を見ずに「空だった」と言うと2を1と取り違える**。

トークンは環境変数からのみ読む（コマンドライン引数は履歴と ``ps`` に残る）。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common import discord_auth  # noqa: E402

# 公式が「インテントが無いと空になる」と名指ししているフィールド。
# poll は「空」ではなく**省略**されるので、扱いを分ける。
GATED_FIELDS = ("content", "embeds", "attachments", "components")

# メッセージ種別。**0 以外は本文を持たないことがある**ので、
# 「空だった」を語る前にここを見る。全部は載せず、出たら番号で残す。
MESSAGE_TYPES = {
    0: "DEFAULT",
    1: "RECIPIENT_ADD",
    2: "RECIPIENT_REMOVE",
    3: "CALL",
    4: "CHANNEL_NAME_CHANGE",
    5: "CHANNEL_ICON_CHANGE",
    6: "CHANNEL_PINNED_MESSAGE",
    7: "USER_JOIN",
    12: "CHANNEL_FOLLOW_ADD",
    18: "THREAD_CREATED",
    19: "REPLY",
    20: "CHAT_INPUT_COMMAND",
    21: "THREAD_STARTER_MESSAGE",
    22: "GUILD_INVITE_REMINDER",
    23: "CONTEXT_MENU_COMMAND",
    24: "AUTO_MODERATION_ACTION",
}

MESSAGE_FLAGS = {
    1 << 0: "CROSSPOSTED",
    1 << 1: "IS_CROSSPOST",
    1 << 2: "SUPPRESS_EMBEDS",
    1 << 3: "SOURCE_MESSAGE_DELETED",
    1 << 4: "URGENT",
    1 << 5: "HAS_THREAD",
    1 << 6: "EPHEMERAL",
    1 << 7: "LOADING",
    1 << 12: "SUPPRESS_NOTIFICATIONS",
    1 << 13: "IS_VOICE_MESSAGE",
}

DISCORD_EPOCH_MS = 1420070400000


def snowflake_time(raw_id: str) -> str:
    """ID から送信時刻を割り出す（応答の ``timestamp`` とは別経路の物差し）。"""
    try:
        value = int(raw_id)
    except (TypeError, ValueError):
        return ""
    ms = (value >> 22) + DISCORD_EPOCH_MS
    jst = datetime.timezone(datetime.timedelta(hours=9))
    return datetime.datetime.fromtimestamp(ms / 1000, jst).strftime("%Y-%m-%d %H:%M:%S")


def decode_flags(value: int | None) -> list[str]:
    if not value:
        return []
    return [name for bit, name in MESSAGE_FLAGS.items() if value & bit]


def fetch_messages(session, *, channel: str, limit: int, secrets: tuple = ()) -> list[dict]:
    """``GET /channels/{channel.id}/messages``。

    課題8が使ったのは ``/messages/{message.id}``（単体）で、一覧は初めて叩く。
    """
    url = f"{discord_auth.API_BASE}/channels/{channel}/messages"
    response = session.get(url, params={"limit": limit})
    discord_auth.raise_for_discord_error(response, *secrets)
    payload = response.json()
    if not isinstance(payload, list):
        raise discord_auth.ApiError(
            f"メッセージ一覧が配列で返りませんでした: {type(payload).__name__}"
        )
    return payload


def describe(message: dict, *, me: str) -> dict:
    """1件を「誰が・いつ・どの種別で送ったか」と「ゲート対象の状態」に還元する。"""
    author = message.get("author") or {}
    author_id = str(author.get("id") or "")
    content = message.get("content")
    raw_type = message.get("type")
    message_id = str(message.get("id") or "")
    return {
        "id": message_id,
        "sent_at_from_id": snowflake_time(message_id),
        "timestamp": message.get("timestamp"),
        "edited_timestamp": message.get("edited_timestamp"),
        "type": raw_type,
        "type_name": MESSAGE_TYPES.get(raw_type, f"UNKNOWN({raw_type})"),
        "flags": message.get("flags"),
        "flag_names": decode_flags(message.get("flags")),
        "author_id": author_id,
        "author_name": str(author.get("username") or ""),
        "author_is_bot": bool(author.get("bot")),
        "is_self": author_id == me,
        "webhook_id": message.get("webhook_id"),
        "has_interaction": "interaction" in message or "interaction_metadata" in message,
        "has_referenced_message": bool(message.get("referenced_message")),
        # content は「空文字」と「キーが無い」を混ぜない。
        "content_present": "content" in message,
        "content_len": len(content) if isinstance(content, str) else None,
        "content": content if isinstance(content, str) else None,
        "embeds": len(message.get("embeds") or []),
        "attachments": len(message.get("attachments") or []),
        "components": len(message.get("components") or []),
        "poll_present": "poll" in message,
    }


def format_rows(rows: Sequence[dict]) -> str:
    lines = []
    for i, row in enumerate(rows, 1):
        who = "自分(bot)" if row["is_self"] else ("bot" if row["author_is_bot"] else "人間")
        length = row["content_len"]
        if not row["content_present"]:
            state = "キー自体が無い"
        elif length == 0:
            state = "空"
        else:
            state = f"{length}文字"
        flags = ",".join(row["flag_names"]) or "-"
        lines.append(
            f"  #{i:<2} {row['sent_at_from_id']}  {row['author_name']:<14}[{who:<8}]"
            f" type={row['type_name']:<22} content={state:<14}"
            f" emb={row['embeds']} att={row['attachments']} cmp={row['components']} flags={flags}"
        )
    return "\n".join(lines)


def judge(rows: Sequence[dict], expect_text: str | None) -> tuple[bool | None, list[str]]:
    """判定を返す。物差し（expect_text）が無いときは None（＝判定しない）。"""
    notes: list[str] = []

    # system message（type != 0）を集計から分ける。
    # USER_JOIN は**サーバーが生成する**通知だが ``author`` は bot 自身になるため、
    # author だけで分類すると「自分が送ったのに空」という誤った観測になる
    # （2026-08-20 に実際に踏んだ。bot をサーバーに招待したときの1件が混ざっていた）。
    system = [r for r in rows if r["type"] != 0]
    normal = [r for r in rows if r["type"] == 0]

    others = [r for r in normal if not r["is_self"]]
    mine = [r for r in normal if r["is_self"]]

    notes.append(f"通常メッセージ(DEFAULT) : {len(normal)} 件")
    notes.append(
        f"  自分(bot)が送ったもの : {len(mine)} 件 / うち content が空: "
        f"{sum(1 for r in mine if r['content_len'] == 0)} 件"
    )
    notes.append(
        f"  他人が送ったもの       : {len(others)} 件 / うち content が空: "
        f"{sum(1 for r in others if r['content_len'] == 0)} 件"
    )

    if system:
        notes.append("")
        notes.append(
            f"system message : {len(system)} 件（**集計から除外**。本文を持たない側）"
        )
        for row in system:
            owner = "author は自分(bot)" if row["is_self"] else "author は他人"
            notes.append(
                f"  - {row['id']} type={row['type_name']} / {owner}"
                f" / content={'空' if row['content_len'] == 0 else str(row['content_len']) + '文字'}"
            )

    if expect_text is None:
        notes.append("")
        notes.append("--expect-text が渡されていないので **判定は出さない**。")
        notes.append("応答だけでは「読めていない」と「本当に本文が無い」を区別できないため。")
        return None, notes

    if not others:
        notes.append("")
        notes.append("他人が送ったメッセージが 0 件。**この実行は何も確かめていない**。")
        notes.append("bot 以外のアカウントでチャンネルに投稿してから再実行すること。")
        return None, notes

    hit = [r for r in others if r["content"] == expect_text]
    empty = [r for r in others if r["content_len"] == 0]

    notes.append("")
    if hit:
        notes.append(f"渡された本文と一致するメッセージが {len(hit)} 件あった。")
        notes.append("=> content は **読めている**。")
        return True, notes
    if empty:
        notes.append(f"渡された本文は 1 件も一致せず、他人のメッセージ {len(empty)} 件が空だった。")
        notes.append("=> **読めていない**。エラーは出ていないが content がゲートされている。")
        return False, notes

    notes.append("一致もせず、空でもなかった。**投稿した本文と渡した本文がずれている**可能性がある。")
    notes.append("（この場合インテントの話ではないので、まず本文を突き合わせること）")
    return None, notes


def compare(rows: Sequence[dict], previous_path: str) -> list[str]:
    """**同じ ID** について前回と突き合わせる。

    インテントを切り替えただけで値が変わったなら、その差はインテントに由来する。
    変わらなかったなら、そのメッセージは**元から本文を持っていない**。
    """
    lines: list[str] = []
    try:
        old = json.loads(Path(previous_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"前回の結果を読めませんでした（{previous_path}）: {error}"]

    old_by_id = {m["id"]: m for m in old.get("messages", [])}
    lines.append(f"前回との比較（{Path(previous_path).name} / {old.get('count')} 件）")

    changed = unchanged = missing = 0
    for row in rows:
        before = old_by_id.get(row["id"])
        if before is None:
            missing += 1
            lines.append(f"  {row['id']}  前回に無い（今回追加された）")
            continue
        b_len, a_len = before.get("content_len"), row["content_len"]
        if b_len == a_len:
            unchanged += 1
            mark = "変化なし"
        else:
            changed += 1
            mark = "**変化した**"
        lines.append(
            f"  {row['id']}  content {b_len} -> {a_len} 文字  {mark}"
            f"  [{row['type_name']}/{'自分' if row['is_self'] else '他人'}]"
        )

    lines.append("")
    lines.append(f"変化した: {changed} 件 / 変化なし: {unchanged} 件 / 前回に無い: {missing} 件")
    lines.append("")
    lines.append("**変化した = インテントに由来する**（切り替え以外は何も変えていないため）。")
    lines.append("**変化なし = そのメッセージは元から本文を持たない**（種別を併せて見ること）。")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MESSAGE_CONTENT インテントの実効を、チャンネルのメッセージ一覧で実測する"
    )
    parser.add_argument("--channel", required=True, help="チャンネル ID")
    parser.add_argument("--limit", type=int, default=20, help="取得件数（既定 20）")
    parser.add_argument(
        "--expect-text",
        default=None,
        help="人間が投稿した本文。**応答の外から渡す物差し**。省略すると判定を出さない",
    )
    parser.add_argument(
        "--compare", default=None, help="前回の結果 JSON。同じ ID どうしを突き合わせる"
    )
    parser.add_argument("--label", default="", help="この実行の目印（intent=OFF など）")
    parser.add_argument("--json-out", default=None, help="結果の保存先")
    args = parser.parse_args(argv)

    try:
        token = discord_auth.read_bot_token(os.environ)
    except discord_auth.AuthError as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 2

    secrets = (token,)
    session = discord_auth.build_session(token)

    try:
        identity = discord_auth.fetch_identity(session, secrets=secrets)
        messages = fetch_messages(
            session, channel=args.channel, limit=args.limit, secrets=secrets
        )
    except discord_auth.DiscordError as error:
        print(f"エラー: {discord_auth.redact(str(error), *secrets)}", file=sys.stderr)
        return 1

    rows = [describe(m, me=identity.user_id) for m in messages]

    print(f"ラベル     : {args.label or '(なし)'}")
    print(f"チャンネル : {args.channel}")
    print(f"自分(bot)  : {identity.user_id}")
    print(f"取得       : {len(rows)} 件")
    print()
    print(format_rows(rows))
    print()

    verdict, notes = judge(rows, args.expect_text)
    for note in notes:
        print(note)

    if args.compare:
        print()
        for line in compare(rows, args.compare):
            print(line)

    if args.json_out:
        payload: dict[str, Any] = {
            "label": args.label,
            "channel": args.channel,
            "me": identity.user_id,
            "count": len(rows),
            "expect_text": args.expect_text,
            "verdict_content_readable": verdict,
            "messages": rows,
        }
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print()
        print(f"保存しました: {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
