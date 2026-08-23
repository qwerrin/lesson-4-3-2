#!/usr/bin/env python3
"""流したものが、狙いどおりに届いたかを読み返して確かめる。**読むだけ。**

課題10（連携した API に機能を追加）の Discord 側の照合。

物差しを、照合対象の中から取らない
------------------------------------------------------------------

投稿した本文は ``playlistItems.list`` の応答から組み立てた。同じ応答を
もう一度読んで比べても、**何も確かめたことにならない**——課題6で
「トートロジー」として踏んだ形である。

そこでこの照合は、3つの**別々の源**から値を持ってきて突き合わせる。

============================== ================================================
源                              そこからしか分からないこと
============================== ================================================
Discord ``messages/{id}``       **実際に載ったもの**。送信の応答ではない
YouTube ``videos.list``         本文を作った ``playlistItems.list`` と別の口
``state.json``                  次回に再送しない状態になっているか
============================== ================================================

``videos.list`` を使うのは、``part=snippet`` なら **1 unit** で済むうえ、
``playlistItems`` とは別のエンドポイントだからである（``search.list`` を
使うと 100 units かかるうえ、別枠の1日100回を照合で食いつぶす）。

**「確かめられなかった」を「合格」にしない。** 相手が空を返したり、
違う動画を返したりしたら、そこで止める。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task10\\discord\\verify_relay.py \\
        --results task10/discord/results.json \\
        --channel <チャンネルID>
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from googleapiclient.errors import HttpError

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASK8 = _REPO_ROOT / "task8"
for _extra_path in (_REPO_ROOT, _TASK8, str(Path(__file__).resolve().parent)):
    if str(_extra_path) not in sys.path:
        sys.path.insert(0, str(_extra_path))

from common import discord_auth, env_file, youtube_auth  # noqa: E402

import relay_uploads  # noqa: E402


class VerifyError(Exception):
    """利用者にそのまま見せられる失敗。"""


# 1本あたりの照合項目数。**名乗った数と実際の数がずれていないか**を
# テストで固定してある（課題9から持ち越した宿題と同じ形）。
CHECKS_PER_VIDEO = 8


@dataclass(frozen=True)
class Check:
    """照合1項目。**合格したものも残す。**

    落ちたものだけ集めると、「何項目を見たか」が分からない。検査を1つ
    落としても、出力は静かに短くなるだけで、合格数は増えたように見える。
    """

    label: str
    expected: str
    actual: str
    ok: bool


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _contains(haystack: str, needle: str, *, label: str) -> Check:
    return Check(
        label=label,
        expected=needle,
        actual="（本文に含まれる）" if needle in haystack else "（本文に無い）",
        ok=bool(needle) and needle in haystack,
    )


def _equals(actual: str, expected: str, *, label: str) -> Check:
    return Check(label=label, expected=expected, actual=actual, ok=actual == expected)


# ------------------------------------------------------------------ 読み取り


def read_video(payload, *, video_id: str) -> dict:
    """``videos.list`` の応答から1件を取り出す。

    **空も、別の動画も、失敗として扱う。** 「確かめられなかった」を
    「合格」にすると、照合が通ったという事実そのものが嘘になる。
    """
    if not isinstance(payload, dict):
        raise VerifyError("videos.list の応答が辞書ではありません")

    items = payload.get("items") or []
    if not items:
        raise VerifyError(
            f"videos.list が動画を返しませんでした: {video_id}\n"
            "削除・非公開になった可能性があります。照合できないので失敗として扱います"
        )

    got = items[0]
    returned = _text(got.get("id")).strip()
    if returned != video_id:
        raise VerifyError(
            f"要求した動画と違うものが返りました: 要求 {video_id} / 応答 {returned}"
        )

    return got


def fetch_video(service, *, video_id: str, api_key: str | None = None) -> dict:
    """``videos.list``（``part=snippet`` は 1 unit）。**読むだけ。**"""
    try:
        response = service.videos().list(part="snippet", id=video_id).execute()
    except HttpError as error:
        # API キーは URL のクエリに載る。例外をそのまま出すと画面に写る。
        raise VerifyError(youtube_auth.redact(str(error), api_key)) from error

    return read_video(response, video_id=video_id)


def fetch_message(session, *, channel: str, message_id: str, secrets: tuple = ()) -> dict:
    """``GET /channels/{channel}/messages/{message}``。**読むだけ。**"""
    response = session.get(
        f"{discord_auth.API_BASE}/channels/{channel}/messages/{message_id}"
    )
    discord_auth.raise_for_discord_error(response, *secrets)

    try:
        payload = response.json()
    except Exception as error:  # noqa: BLE001
        raise VerifyError("メッセージを JSON として読めませんでした") from error

    if not isinstance(payload, dict):
        raise VerifyError("メッセージの形式が不正です")

    return payload


# ------------------------------------------------------------------ 照合


def compare(
    *,
    record: dict,
    message: dict,
    video: dict,
    channel: str,
    author_id: str,
    state: relay_uploads.State,
) -> list[Check]:
    """1本ぶんを突き合わせる。**8項目とも返す（落ちたものだけにしない）。**"""
    content = _text(message.get("content"))
    snippet = video.get("snippet") or {}
    video_id = _text(record.get("video_id"))

    # **照合側でも unescape する。** videos.list も "&" を "&amp;" にして返す。
    # 戻し忘れると、正しく動いているのに毎回 NG が出る——そして
    # 「照合が厳しすぎる」と判断して検査を緩める方向に倒れやすい。
    title = html.unescape(_text(snippet.get("title")))
    channel_title = html.unescape(_text(snippet.get("channelTitle")))

    published = relay_uploads.parse_time(
        snippet.get("publishedAt"), label="videos.list の publishedAt"
    )

    author = (message.get("author") or {}).get("id")

    return [
        _equals(_text(message.get("channel_id")), channel, label="チャンネル"),
        _equals(_text(author), author_id, label="投稿者"),
        _equals(_text(message.get("id")), _text(record.get("message_id")), label="メッセージID"),
        _contains(content, relay_uploads.video_url(video_id), label="動画URL"),
        _contains(content, title, label="タイトル"),
        _contains(content, channel_title, label="チャンネル名"),
        _contains(content, f"{published:%Y-%m-%d %H:%M UTC}", label="公開時刻"),
        Check(
            label="状態に記録",
            expected=video_id,
            actual="（記録あり）" if video_id in state.sent_ids else "（記録なし）",
            ok=video_id in state.sent_ids,
        ),
    ]


# ------------------------------------------------------------------ CLI


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discord へ流した新着通知を読み返して照合する（読むだけ）"
    )
    parser.add_argument(
        "--results",
        default=str(Path(__file__).resolve().parent / "results.json"),
        help="relay_uploads.py が --json-out で書いた記録",
    )
    parser.add_argument(
        "--state",
        default=str(Path(__file__).resolve().parent / "state.json"),
        help="どこまで送ったかの状態ファイル",
    )
    parser.add_argument("--channel", required=True, help="Discord のチャンネルID")
    parser.add_argument(
        "--env",
        default=str(_REPO_ROOT / env_file.ENV_FILENAME),
        help=f"資格情報が入った {env_file.ENV_FILENAME}",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    env=None,
    service_factory: Callable | None = None,
    session_factory: Callable | None = None,
    out: Callable[[str], None] | None = None,
) -> int:
    args = parse_args(argv)
    say = out if out is not None else (lambda text: print(text))

    try:
        resolved_env = env if env is not None else env_file.load(args.env)
        api_key = youtube_auth.read_api_key(resolved_env)
        token = discord_auth.read_bot_token(resolved_env)

        results = json.loads(Path(args.results).read_text(encoding="utf-8"))
        sent = results.get("sent") or []
        if not sent:
            raise VerifyError(
                f"記録に送信ぶんがありません: {relay_uploads.shown_path(args.results)}\n"
                "先に relay_uploads.py を --json-out 付きで実行してください"
            )

        state = relay_uploads.load_state(args.state)

        build_service = (
            service_factory
            if service_factory is not None
            else (lambda key: youtube_auth.build_service(key))
        )
        service = build_service(api_key)

        session = (
            discord_auth.build_session(token)
            if session_factory is None
            else discord_auth.build_session(token, factory=session_factory)
        )
        identity = discord_auth.fetch_identity(session, secrets=(token,))

        total = 0
        failed = 0
        for entry in sent:
            video = fetch_video(
                service, video_id=entry["video_id"], api_key=api_key
            )
            message = fetch_message(
                session,
                channel=args.channel,
                message_id=entry["message_id"],
                secrets=(token,),
            )
            checks = compare(
                record=entry,
                message=message,
                video=video,
                channel=args.channel,
                # **``id`` ではなく ``user_id``。** 実機で初めて落ちた
                # （`AttributeError: 'Identity' object has no attribute 'id'`）。
                # 属性名の取り違えは、偽物を作る側が実装に合わせてしまうので
                # 単体テストでは出ない。本物の Identity を通す結合テストで塞いだ。
                author_id=identity.user_id,
                state=state,
            )

            say(f"\n{entry['video_id']}  {entry.get('title', '')}")
            for check in checks:
                total += 1
                mark = "OK " if check.ok else "NG "
                if not check.ok:
                    failed += 1
                    say(f"  {mark} {check.label}: 期待 {check.expected} / 実際 {check.actual}")
                else:
                    say(f"  {mark} {check.label}")

        say(f"\n照合 {total} 項目 / NG {failed} 件")
        return 0 if failed == 0 else 1

    except (
        VerifyError,
        relay_uploads.RelayError,
        youtube_auth.AuthError,
        discord_auth.DiscordError,
        env_file.EnvFileError,
        OSError,
        ValueError,
        KeyError,
    ) as error:
        say(f"エラー: {youtube_auth.redact(str(error), locals().get('api_key'))}")
        return 1


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

    raise SystemExit(main())
