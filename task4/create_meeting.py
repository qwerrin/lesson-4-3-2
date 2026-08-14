"""Zoom API で会議を作成し、会議 ID・パスワード・参加リンクを出す。

課題4の要件は「Zoom API を利用して、会議を作成し、ID・パスワード・会議リンクを
作成するコード」。この3つが返ってこなかったときに成功として扱わないことが、
このスクリプトの一番の仕事になる。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task4\\create_meeting.py --topic "打ち合わせ"

資格情報は環境変数で渡す（README 参照）::

    ZOOM_ACCOUNT_ID / ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET

作成後の確認は task4/verify_meeting.py が行う。偽物で確かめられるのは
「呼び方」までなので、実物を読み返して閉じるところまでを課題の成果物とする。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Callable

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import zoom_auth  # noqa: E402

# Server-to-Server OAuth はアカウントレベルなので admin 付きのスコープになる。
# 既定値は持たせず、必要なものをここに明示する（common/zoom_auth の約束）。
SCOPES: tuple[str, ...] = ("meeting:write:meeting:admin",)

# 2 = scheduled。1（instant）だと作った瞬間に始まってしまい、あとから
# 読み返して照合する余地が無くなる。
MEETING_TYPE_SCHEDULED = 2

DEFAULT_TOPIC = "AIエンジニア講座 課題4 テスト会議"
DEFAULT_DURATION_MINUTES = 30
TIMEOUT_SECONDS = 30

# Zoom が受け付ける開始時刻の書式。末尾 Z は UTC、無ければ timezone に従う。
# ^ と $ は付けない。fullmatch と二重になり、search に変えても同じ挙動になって
# しまう＝「全体一致で見ている」ことをテストで固定できなくなる。
START_TIME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?")
START_TIME_EXAMPLE = "2026-08-20T10:00:00Z"

# 参加リンクは https://<host>/j/<会議ID> の形。応答の中だけで閉じる照合に使う。
JOIN_PATH = "/j/"

# 1ユーザーあたり1日100回の作成/更新上限が別にある（レート制限の表とは別枠）。
DAILY_CREATE_LIMIT = 100


class MeetingError(Exception):
    """利用者にそのまま見せられる、会議作成まわりの失敗。"""


# ------------------------------------------------------------------ 送る内容


def build_meeting_body(
    topic: str | None,
    *,
    start_time: str | None = None,
    duration: int = DEFAULT_DURATION_MINUTES,
    timezone: str | None = None,
    password: str | None = None,
    agenda: str | None = None,
) -> dict:
    """API へ送る本文を組み立てる。API へ繋ぐ前に、ここで全部弾く。"""
    # 省略（None）は既定値、空文字は打ち間違い。`or` でまとめると
    # --topic "" が黙って既定の議題に化ける。
    if topic is None:
        topic = DEFAULT_TOPIC
    topic = topic.strip()
    if not topic:
        raise MeetingError("議題が空です。--topic に会議の名前を指定してください")

    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        raise MeetingError(f"所要時間は1以上の分数で指定してください（受け取った値: {duration}）")

    body: dict = {
        "topic": topic,
        "type": MEETING_TYPE_SCHEDULED,
        "duration": duration,
    }

    if start_time is not None:
        start_time = start_time.strip()
        if not START_TIME_PATTERN.fullmatch(start_time):
            raise MeetingError(
                f"開始時刻の書式が違います: {start_time}\n"
                f"例: {START_TIME_EXAMPLE}（末尾の Z は UTC。省略した場合は --timezone に従う）"
            )
        body["start_time"] = start_time

    if timezone is not None:
        timezone = timezone.strip()
        if timezone:
            body["timezone"] = timezone

    if password is not None:
        # 指定した以上、空文字は打ち間違い。黙って「未指定」に倒すと、
        # 指定したつもりの値と違うパスワードで会議が立つ。
        password = password.strip()
        if not password:
            raise MeetingError("--password が空です。値を指定するか、指定そのものを外してください")
        body["password"] = password

    if agenda is not None:
        agenda = agenda.strip()
        if agenda:
            body["agenda"] = agenda

    return body


# ------------------------------------------------------------------ 呼び出し


def meetings_url(api_base: str) -> str:
    """会議を作る先。api_base はトークン応答の api_url（地域ごとに変わる）。"""
    return f"{api_base.rstrip('/')}/v2/users/me/meetings"


def _payload_of(response) -> dict | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _detail(response, payload: dict | None) -> str:
    """相手が言っている理由をそのまま返す。こちらで原因候補を並べ直さない。"""
    if payload:
        for key in ("message", "reason", "error"):
            reason = payload.get(key)
            if reason:
                return str(reason)
    text = (getattr(response, "text", "") or "").strip()
    return text[:200] if text else "(応答本文なし)"


def _error_for(response, payload: dict | None) -> MeetingError:
    status = response.status_code
    detail = _detail(response, payload)
    message = f"会議の作成に失敗しました（HTTP {status}）: {detail}"

    if status in (401, 403):
        # スコープが足りているかは common/zoom_auth.require_scopes で先に見ているが、
        # アプリを Activate し直していないとトークンに反映されない。
        message += (
            f"\n必要なスコープ: {' / '.join(SCOPES)}\n"
            "Zoom App Marketplace でスコープを追加したあと、Activate し直したか確認してください。"
        )
    elif status == 429:
        message += (
            f"\n会議の作成は1ユーザーあたり1日 {DAILY_CREATE_LIMIT} 回までの上限が別にあります"
            "（UTC 基準で日が変わるとリセット）。"
        )

    return MeetingError(message)


def create_meeting(
    body: dict,
    *,
    api_base: str,
    access_token: str,
    poster: Callable = requests.post,
    timeout: float = TIMEOUT_SECONDS,
) -> dict:
    """会議を作って、返ってきた会議オブジェクトをそのまま返す。"""
    response = poster(
        meetings_url(api_base),
        json=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )

    payload = _payload_of(response)

    if not response.ok:
        raise _error_for(response, payload)

    if payload is None:
        raise MeetingError(
            f"会議の作成応答が JSON ではありません（HTTP {response.status_code}）: "
            f"{_detail(response, None)}"
        )

    return payload


# ------------------------------------------------------------------ 返ってきた内容の確認

# 課題の要件そのもの。(応答のキー, 画面に出す名前)
REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
    ("id", "会議ID"),
    ("password", "パスワード"),
    ("join_url", "参加リンク"),
)


def require_fields(meeting: dict) -> None:
    """要件の3つが返ってきたことを確認する。既定値で埋めない。"""
    for key, label in REQUIRED_FIELDS:
        value = meeting.get(key)
        # 0 や空文字を「返ってきた」とみなさない。埋めてしまうと、
        # 確かめた気持ちだけが残る。
        if value is None or str(value).strip() == "":
            message = f"応答に{label}（{key}）が入っていません。会議は作成されたが要件を満たしていません。"
            if key == "password":
                message += (
                    "\nパスコードが生成されるかは Zoom のアカウント設定に依存します。"
                    "\n--password で明示的に指定するか、Zoom の設定でミーティングパスコードを"
                    "有効にしてください。"
                )
            raise MeetingError(message)


def check_join_url(meeting: dict) -> None:
    """参加リンクが、この会議の ID を指していることを確認する。

    外部に問い合わせず、応答の中だけで閉じる照合。別の会議のリンクが返っていたり、
    組み立てを間違えていたりを、ここで検出できる。
    """
    join_url = str(meeting.get("join_url", ""))
    meeting_id = str(meeting.get("id", ""))
    if f"{JOIN_PATH}{meeting_id}" not in join_url:
        raise MeetingError(
            f"参加リンクが会議 ID を指していません。\n"
            f"  会議ID: {meeting_id}\n"
            f"  参加リンク: {join_url}\n"
            f"（{JOIN_PATH}{meeting_id} を含む形を期待しています）"
        )


def format_result(meeting: dict) -> str:
    """作成結果を印字する。

    start_url は出さない。ホスト権限のトークン（zak）が入っていて、
    実行画面のスクリーンショットに写ると他人がホストとして入れてしまう。
    """
    lines = [
        "会議を作成しました。",
        f"  議題      : {meeting.get('topic', '')}",
        f"  会議ID    : {meeting.get('id', '')}",
        f"  パスワード: {meeting.get('password', '')}",
        f"  参加リンク: {meeting.get('join_url', '')}",
    ]
    start_time = meeting.get("start_time")
    if start_time:
        lines.append(f"  開始時刻  : {start_time}")
    return "\n".join(lines)


# ------------------------------------------------------------------ 入口


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Zoom API で会議を作成し、会議ID・パスワード・参加リンクを表示する"
    )
    parser.add_argument("--topic", default=None, help=f"会議の議題（既定: {DEFAULT_TOPIC}）")
    parser.add_argument(
        "--start-time", default=None, help=f"開始時刻（例: {START_TIME_EXAMPLE}）"
    )
    parser.add_argument(
        "--duration", type=int, default=DEFAULT_DURATION_MINUTES, help="所要時間（分）"
    )
    parser.add_argument("--timezone", default=None, help="タイムゾーン（例: Asia/Tokyo）")
    parser.add_argument(
        "--password", default=None, help="参加パスワード（未指定なら Zoom 側で生成される）"
    )
    parser.add_argument("--agenda", default=None, help="会議の説明")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    # 送る内容を確定してから API へ繋ぐ。順番が逆だと、落ちると分かっている
    # 実行でトークンを1回取りに行くことになる。
    try:
        body = build_meeting_body(
            args.topic,
            start_time=args.start_time,
            duration=args.duration,
            timezone=args.timezone,
            password=args.password,
            agenda=args.agenda,
        )
    except MeetingError as error:
        print(error, file=sys.stderr)
        return 1

    try:
        credentials = zoom_auth.read_credentials(os.environ)
        token = zoom_auth.fetch_access_token(credentials)
        zoom_auth.require_scopes(token, SCOPES)
        meeting = create_meeting(body, api_base=token.api_url, access_token=token.value)
        require_fields(meeting)
        check_join_url(meeting)
    except (MeetingError, zoom_auth.AuthError) as error:
        print(error, file=sys.stderr)
        return 1

    print(format_result(meeting))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
