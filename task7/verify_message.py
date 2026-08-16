"""投稿したメッセージを conversations.history で読み直して、送った内容と突き合わせる。

**読むだけ。何も投稿しないし、書き換えない。**

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task7\\verify_message.py --results task7/results.json \\
        --channel C0XXXXXXXXX --expect-text "課題7の動作確認です"

post_message.py が成功しても、それは「API が ok を返した」までしか意味しない。
本当にそのチャンネルへ、狙った本文が、こちらの Bot の名前で載ったのかは、
別のところから読み直さないと閉じない。

**物差しを応答の外から取る。**

1. **チャンネルと本文** — 人間がコマンドラインで渡す
2. **タイムスタンプ** — 投稿時に記録した値と読み返した値を突き合わせる
3. **投稿者** — ``auth.test``（**conversations.history とは別のエンドポイント**）が
   答えた Bot の user_id と突き合わせる

3 が効くのは、投稿の応答と読み返しの応答という同じ系統の中だけで比べていないから。
同じ応答の中で値どうしを比べるのはトートロジーで、何も確かめていない
（課題4で join_url を応答の id と比べかけたのと同じ形）。

この API に固有の罠が2つある。
------------------------------------------------------------------

**1. ``oldest`` は「その時刻より後」で、自分自身を含まない。**
``inclusive=true`` を落とすと 0 件になる。エラーにはならないので、
指定を消したことに気づけない。

**2. 返ってくるのは「その ts のメッセージ」ではなく「その ts 以降の最初の1件」。**
狙ったメッセージが消えていれば**次のメッセージが 1 件返る**。件数だけ見て
「取れた」と判断すると、別のメッセージを相手に照合することになる。
だから**返ってきた ts が渡した ts と同じか**を必ず見る。
課題6の「videos.list は存在しない ID を黙って落として 200 を返す」と同じ形である。

**3. 送った文字列はそのままの形では返らない。**
Slack は ``&`` ``<`` ``>`` を HTML エンティティに変換して保存する。
post_message.escape_for_slack() を通してから比べる。**判定を「どちらでも通す」形に
すると、照合が何も確かめなくなる**ので、変換後の形だけを一致とする。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from slack_sdk.errors import SlackApiError

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import post_message  # noqa: E402
from common import slack_auth  # noqa: E402


# 読み返しに必要なスコープ。投稿の chat:write とは別物で、
# **公開チャンネルでも履歴の読み取りには専用の権限が要る**。
SCOPES = ("channels:history",)

# Slack のタイムスタンプの形。秒とマイクロ秒をドットでつないだ文字列。
_TS_PATTERN = re.compile(r"\d+\.\d+")

_REQUIRED_KEYS = ("channel", "text", "ts", "permalink", "posted_by")


class VerifyError(Exception):
    """利用者にそのまま見せられる失敗。"""


@dataclass
class Check:
    label: str
    ok: bool
    detail: str = ""


# ---------------------------------------------------------------- 結果ファイル


def load_results(path: str | Path) -> dict:
    """post_message.py が書いた結果ファイルを読む。

    形が違うファイルを黙って受け入れない。中身が欠けたまま照合に進むと
    「確かめた項目ゼロで全部一致」が出る。
    """
    source = Path(path)
    if not source.exists():
        raise VerifyError(
            f"結果ファイルが見つかりません: {source}\n"
            "post_message.py を --json-out 付きで実行してください。"
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
            # ts が float で入っている場合もここで落ちる。数値になっている時点で
            # 精度が落ちており、「読めたから大丈夫」にはできない。
            raise VerifyError(
                f"結果ファイルに {key} がありません（空でない文字列である必要があります）"
            )

    return payload


# ---------------------------------------------------------------- 手元でできる照合


def _compare(label: str, expected, actual) -> Check:
    ok = expected == actual
    detail = "" if ok else f"期待 {expected!r} / 実際 {actual!r}"
    return Check(label, ok, detail)


def build_local_checks(payload: dict, *, expected_channel: str, expected_text: str) -> list[Check]:
    """API を呼ばずに確かめられることを先に済ませる。

    ここが落ちる実行はネットワークに出す価値がない。
    """
    checks: list[Check] = []

    # 1. どのチャンネルの話か。人間が指定した値と突き合わせる。
    checks.append(_compare("チャンネル", expected_channel, payload.get("channel")))

    # 2. 何を送ったか。人間が指定した値と突き合わせる。
    #    結果ファイルの値で埋めると、ファイルが間違っていても一致する。
    checks.append(_compare("送った本文", expected_text, payload.get("text")))

    # 3. リンクが同じチャンネルを指しているか。
    permalink = payload.get("permalink") or ""
    channel = payload.get("channel") or ""
    checks.append(
        Check(
            "リンクの指す先",
            bool(channel) and channel in permalink,
            "" if channel and channel in permalink else f"リンクに {channel} が含まれていません",
        )
    )

    # 4. タイムスタンプの形。数値に変換された形跡がないかを見る。
    ts = payload.get("ts") or ""
    ok = bool(_TS_PATTERN.fullmatch(ts))
    checks.append(Check("タイムスタンプの形式", ok, "" if ok else f"想定と違う形です: {ts!r}"))

    return checks


# ---------------------------------------------------------------- 読み直す


def fetch_message(client, *, channel: str, ts: str) -> dict | None:
    """投稿したメッセージを1件だけ読み直す。見つからなければ None。

    ``oldest`` + ``inclusive`` + ``limit=1`` は「その ts 以降の最初の1件」なので、
    **これだけでは狙ったメッセージだと確定しない**。ts の一致は照合の項目として
    別に見る（ここで落とすと、何を確かめて落ちたのかが画面に出ない）。
    """
    response = client.conversations_history(
        channel=channel,
        oldest=ts,
        # 落とすと自分自身が入らない。0 件になるだけでエラーにならない。
        inclusive=True,
        limit=1,
    )

    if not response.get("ok"):
        detail = response.get("error") or "(理由不明)"
        raise VerifyError(f"履歴の取得に失敗しました: {detail}")

    messages = response.get("messages")
    if not isinstance(messages, list):
        raise VerifyError("応答に messages がありません。読み直せませんでした")

    if not messages:
        return None

    first = messages[0]
    if not isinstance(first, dict):
        raise VerifyError("応答の messages の形式が不正です")

    return first


def build_remote_checks(payload: dict, message: dict | None, identity: slack_auth.Identity) -> list[Check]:
    """読み直した内容と、送った内容を突き合わせる。"""
    checks: list[Check] = []

    # 1. 記録した投稿者と、いま動かしている Bot が同じか。
    #    別のアプリのトークンで確認すると、一致しても他人の投稿を見ているだけになる。
    checks.append(_compare("実行中のBot", payload.get("posted_by"), identity.user_id))

    if message is None:
        # 0 件を「照合する対象が無い＝全部一致」にしない。
        checks.append(
            Check(
                "メッセージの実在",
                False,
                "読み返せませんでした（削除された・チャンネルが違う・権限不足のいずれか）",
            )
        )
        return checks

    checks.append(Check("メッセージの実在", True))

    # 2. 返ってきたのが狙ったメッセージか。**ここを見ないと「返ってこなかった」が
    #    「一致した」に化ける。**
    checks.append(_compare("タイムスタンプ", payload.get("ts"), str(message.get("ts") or "")))

    # 3. 誰の投稿か。auth.test（別のエンドポイント）が答えた値と比べる。
    checks.append(_compare("投稿者", identity.user_id, str(message.get("user") or "")))

    # 4. 本文。Slack の変換を掛けてから比べる。掛けないと & を含む本文で必ず外れる。
    expected = post_message.escape_for_slack(payload.get("text") or "")
    actual = str(message.get("text") or "")
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
        description="投稿したメッセージを conversations.history で読み直して突き合わせます（読むだけ）。"
    )
    parser.add_argument("--results", required=True, help="post_message.py が --json-out で書いたファイル")
    # 期待値は応答の外から取る。必須にして、ファイルの値で埋める逃げ道を作らない。
    parser.add_argument("--channel", required=True, help="投稿したチャンネルID")
    parser.add_argument("--expect-text", required=True, help="投稿した本文")
    return parser.parse_args(argv)


def _default_client_factory():
    token = slack_auth.read_bot_token(os.environ)
    client = slack_auth.build_client(token)
    identity = slack_auth.fetch_identity(client)
    return client, identity, token


def main(argv: Sequence[str] | None = None, *, client_factory: Callable | None = None) -> int:
    args = parse_args(argv)
    factory = client_factory or _default_client_factory

    try:
        payload = load_results(args.results)
    except VerifyError as error:
        print(error, file=sys.stderr)
        return 1

    local = build_local_checks(
        payload, expected_channel=args.channel, expected_text=args.expect_text
    )
    print("結果ファイルの照合（API を呼ばずに確かめられること）:")
    print(format_checks(local))

    if not all_ok(local):
        print("\n食い違いがあります。上の NG を確認してください。", file=sys.stderr)
        return 1

    try:
        client, identity, token = factory()
    except slack_auth.AuthError as error:
        print(error, file=sys.stderr)
        return 1

    check = slack_auth.check_scopes(identity, SCOPES)
    print(f"\n{post_message.format_scope_report(check)}")

    if check.known and check.missing:
        print("\n権限が足りないため読み直せません。", file=sys.stderr)
        return 1

    try:
        message = fetch_message(client, channel=payload["channel"], ts=payload["ts"])
    except VerifyError as error:
        print(error, file=sys.stderr)
        return 1
    except SlackApiError as error:
        print(post_message.translate_slack_error(error, token), file=sys.stderr)
        return 1

    remote = build_remote_checks(payload, message, identity)
    print("\nconversations.history で読み直した内容との照合（投稿とは別のエンドポイント）:")
    print(format_checks(remote))

    if not all_ok(remote):
        print("\n食い違いがあります。上の NG を確認してください。", file=sys.stderr)
        return 1

    print("\nすべて一致しました。")
    # **「すべて一致しました」だけを出す道具は、検査していない場所まで
    # 保証しているように読める**（課題5の教訓）。範囲を明記する。
    print(
        "確かめていないこと: 画面上の見え方 / 投稿の並び順 / チャンネルの表示名。"
        "この確認が見ているのは、上に並べた項目だけです。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
