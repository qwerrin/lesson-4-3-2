"""指定のチャンネルに Slack のメッセージを投稿する。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task7\\post_message.py --channel C0XXXXXXXXX \\
        --text "課題7の動作確認です" --json-out task7/results.json

**チャンネルは ID（C で始まる値）で指定する。** chat.postMessage は名前でも
受け付けるが、読み返しに使う conversations.history は ID しか受け付けない。
両方に同じ値を渡せる形にしておくと、「投稿したチャンネル」と「読み返した
チャンネル」がずれない。チャンネル名は変わるが ID は変わらない、という
実務上の理由もある。ID は Slack のチャンネル詳細の一番下に出ている。

この課題で気をつけたこと
------------------------------------------------------------------

**1. 権限（スコープ）と所属（チャンネル参加）は別物。**
chat:write を付けても、Bot をチャンネルに招待していなければ ``not_in_channel``
で落ちる。エラーがこの2つを言い分けられないと、利用者は延々スコープを疑う。
そこでエラーコードごとに直しかたを分けて出す。**相手が名指しで答えている
ときに、こちらで原因候補を並べ直さない。**

**2. タイムスタンプは文字列のまま扱う。**
``1503435956.000247`` を float にすると倍精度で表しきれず、末尾が変わって
別のメッセージを指す。ts はメッセージの識別子であって時刻の計算には使わない。

**3. 応答のチャンネルが、要求したチャンネルと同じかを見る。**
応答の中だけで値を突き合わせると、サーバが何を返しても一致してしまう
（課題4で join_url を応答の id と比べかけたのと同じ形）。物差しは
**こちらが要求した値**から取る。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from slack_sdk.errors import SlackApiError

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common import slack_auth  # noqa: E402


# 投稿に必要なスコープ。slack_auth に既定値を持たせず、使う側に書かせる
# （google_auth・zoom_auth と同じ方針）。
SCOPES = ("chat:write",)


class PostError(Exception):
    """利用者にそのまま見せられる失敗。"""


@dataclass(frozen=True)
class Posted:
    """投稿できたメッセージ。ts は識別子なので文字列のまま持つ。"""

    channel: str
    ts: str
    text: str


def escape_for_slack(text: str) -> str:
    """Slack が本文を保存するときに掛ける変換を、そのまま再現する。

    ``&`` ``<`` ``>`` は Slack の制御文字で、本文として送ると HTML エンティティに
    置き換わって保存される。**送った文字列がそのままの形では返ってこない**ので、
    読み返して比べるときはこの関数を通した値と突き合わせる。
    課題5（Gmail）で件名が RFC 2047 で返ってきたのと同じ形の罠である。

    **``&`` を最初に置換する。** 逆にすると ``<`` → ``&lt;`` で作られた ``&`` を
    次の段でもう一度拾い、``&amp;lt;`` になる。
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# エラーコードごとの直しかた。**相手が名指しで答えているものに候補を並べない。**
# not_in_channel でスコープの話をすると、合っている設定を疑わせて遠回りさせる。
_ERROR_HINTS = {
    "not_in_channel": (
        "Bot がチャンネルに参加していません。"
        "Slack のチャンネルで `/invite @<アプリ名>` を送って招待してください。"
    ),
    "channel_not_found": (
        "チャンネルが見つかりません。チャンネル ID（C で始まる値）を確認してください。"
        "ID はチャンネル詳細の一番下に出ています。"
    ),
    "missing_scope": (
        "アプリのスコープが足りません。OAuth & Permissions の Bot Token Scopes に"
        "追加したうえで、アプリを再インストールしてください。"
    ),
    "invalid_auth": (
        "トークンが無効です。OAuth & Permissions の Bot User OAuth Token を確認してください。"
    ),
    "message_not_found": "メッセージが見つかりません。タイムスタンプを確認してください。",
    "is_archived": "チャンネルがアーカイブされています。",
    "msg_too_long": "メッセージが長すぎます。",
    "ratelimited": (
        "レート制限に達しました。chat.postMessage は1チャンネルあたり毎秒1通です。"
    ),
    "rate_limited": (
        "レート制限に達しました。chat.postMessage は1チャンネルあたり毎秒1通です。"
    ),
}


def _error_code(error: SlackApiError) -> str:
    response = getattr(error, "response", None)
    if response is None:
        return ""
    try:
        return str(response.get("error") or "")
    except AttributeError:
        return ""


def translate_slack_error(error: SlackApiError, token: str | None) -> PostError:
    """SDK の例外を、利用者に見せられる文言に置き換える。

    **エラーコードは必ず出す。** 日本語の説明だけにすると公式ドキュメントを引けない。
    そのうえで、こちらが直しかたを知っているコードにだけ一行足す。

    例外の本文も載せるが、必ず redact() を通す。**確認用に書いた短いコードほど
    素の例外をそのまま画面に出す**ので（課題6で実際に鍵を出した）、
    本番の経路の側で伏せる。
    """
    code = _error_code(error) or "(エラーコードなし)"
    message = f"Slack API がエラーを返しました: {code}"

    hint = _ERROR_HINTS.get(code)
    if hint:
        message += f"\n{hint}"

    message += f"\n詳細: {error}"
    return PostError(slack_auth.redact(message, token))


def post_message(client, *, channel: str, text: str) -> Posted:
    """chat.postMessage で投稿する。"""
    channel = (channel or "").strip()
    if not channel:
        raise PostError("チャンネルが指定されていません（--channel）")

    if not (text or "").strip():
        # 空のまま送ると Slack 側でも弾かれるが、手前で止めたほうが原因が近い。
        raise PostError("メッセージが空です。--text に本文を渡してください")

    response = client.chat_postMessage(channel=channel, text=text)

    if not response.get("ok"):
        detail = response.get("error") or "(理由不明)"
        raise PostError(f"投稿に失敗しました: {detail}")

    # 「返ってこなかった」を「投稿できた」にしない。ts が無いと読み返せない。
    ts = str(response.get("ts") or "").strip()
    if not ts:
        raise PostError(
            "応答に ts がありません。投稿できたか確認できないため失敗として扱います"
        )

    returned = str(response.get("channel") or "").strip()
    if returned != channel:
        raise PostError(
            "要求したチャンネルと違うチャンネルに投稿されました: "
            f"要求 {channel} / 応答 {returned}"
        )

    return Posted(channel=channel, ts=ts, text=text)


def fetch_permalink(client, *, channel: str, ts: str) -> str:
    """メッセージへのリンクを取る。

    chat.getPermalink は**スコープを要求しない**（2026-08-16 に公式リファレンスで確認）。
    実行画面にリンクを出しておくと、そのリンクを開いた画面と突き合わせられる。
    """
    response = client.chat_getPermalink(channel=channel, message_ts=ts)

    if not response.get("ok"):
        detail = response.get("error") or "(理由不明)"
        raise PostError(f"リンクの取得に失敗しました: {detail}")

    link = str(response.get("permalink") or "").strip()
    if not link:
        raise PostError("応答に permalink がありません")

    return link


def build_record(
    *, identity: slack_auth.Identity, channel: str, text: str, ts: str, permalink: str
) -> dict:
    """読み返しの照合に使う記録を組む。

    posted_by は auth.test が答えた Bot 自身の user_id。読み返したメッセージの
    ``user`` と突き合わせる**別エンドポイント由来の物差し**になる。
    """
    return {
        "team": identity.team,
        "channel": channel,
        "text": text,
        "ts": ts,
        "permalink": permalink,
        "posted_by": identity.user_id,
    }


def write_record(path: str | Path, record: dict) -> None:
    destination = Path(path)
    if destination.parent != Path(""):
        destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def format_scope_report(check: slack_auth.ScopeCheck) -> str:
    """スコープの状態を1行で報告する。

    **「確認できなかった」を「足りている」と書かない。** 検査していない場所まで
    保証しているように読める出力は、実際に足りていないときに利用者から
    原因を探す手がかりを奪う（課題5で「すべて一致しました」だけを出して、
    画像は枚数しか見ていなかったのと同じ形）。
    """
    if not check.known:
        return (
            f"スコープ: 確認できません（応答に {slack_auth.SCOPE_HEADER} ヘッダがありませんでした）。"
            "付与済みの権限を検査せずに続行します"
        )

    if check.missing:
        return (
            "スコープが足りません: " + " / ".join(check.missing) + "\n"
            "OAuth & Permissions の Bot Token Scopes に追加し、再インストールしてください。"
        )

    return "スコープ: " + " / ".join(check.granted or ())


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="指定のチャンネルに Slack のメッセージを投稿します。"
    )
    parser.add_argument(
        "--channel", required=True, help="投稿先のチャンネルID（C で始まる値）"
    )
    parser.add_argument("--text", required=True, help="投稿する本文")
    parser.add_argument(
        "--json-out",
        help="結果の記録先。指定したときだけ書く（既定のパスへ勝手に上書きしない）",
    )
    # --token は用意しない。コマンドライン引数は履歴と ps に残るため。
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
        client, identity, token = factory()
    except slack_auth.AuthError as error:
        print(error, file=sys.stderr)
        return 1

    print(f"ワークスペース: {identity.team}")
    print(f"投稿するBot: {identity.user_id}")

    check = slack_auth.check_scopes(identity, SCOPES)
    print(format_scope_report(check))

    # 読めたうえで足りないときだけ止める。読めなかっただけで止めると、
    # ヘッダが返らない環境で課題そのものが実行できなくなる。
    if check.known and check.missing:
        print("\n権限が足りないため投稿しません。", file=sys.stderr)
        return 1

    try:
        posted = post_message(client, channel=args.channel, text=args.text)
        permalink = fetch_permalink(client, channel=posted.channel, ts=posted.ts)
    except PostError as error:
        print(error, file=sys.stderr)
        return 1
    except SlackApiError as error:
        print(translate_slack_error(error, token), file=sys.stderr)
        return 1

    print("\n投稿しました。")
    print(f"  チャンネル: {posted.channel}")
    print(f"  タイムスタンプ: {posted.ts}")
    print(f"  本文: {posted.text}")
    print(f"  リンク: {permalink}")

    if args.json_out:
        record = build_record(
            identity=identity,
            channel=posted.channel,
            text=posted.text,
            ts=posted.ts,
            permalink=permalink,
        )
        write_record(args.json_out, record)
        print(f"\n結果を書き出しました: {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
