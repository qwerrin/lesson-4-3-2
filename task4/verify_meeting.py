"""作成した Zoom 会議を読み返して、要件の3つが本当にあるかを照合する。

create_meeting.py は「作った」ところまでしか言えない。偽物で確かめられるのは
呼び方までなので、実物を1回読み返して閉じる。

このスクリプトは読むだけで、会議を変更しない。確認のつもりの実行が状態を変えると、
やり直しがきかなくなる。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task4\\verify_meeting.py 81234567890 ^
        --expect-topic "打ち合わせ" --expect-password "aB3xY9"

食い違いが1つでもあれば終了コード 1 を返す。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import create_meeting  # noqa: E402

from common import zoom_auth  # noqa: E402

# 読むだけなので read スコープ。write は要求しない。
SCOPES: tuple[str, ...] = ("meeting:read:meeting:admin",)

TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Check:
    label: str
    ok: bool
    detail: str = field(default="")


# ------------------------------------------------------------------ 読み取り


def meeting_url(api_base: str, meeting_id) -> str:
    return f"{api_base.rstrip('/')}/v2/meetings/{meeting_id}"


def fetch_meeting(
    meeting_id,
    *,
    api_base: str,
    access_token: str,
    getter: Callable = requests.get,
    timeout: float = TIMEOUT_SECONDS,
) -> dict:
    """会議を読み返す。json も data も渡さない（書き換えない）。"""
    response = getter(
        meeting_url(api_base, meeting_id),
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=timeout,
    )

    payload = create_meeting._payload_of(response)

    if not response.ok:
        detail = create_meeting._detail(response, payload)
        raise create_meeting.MeetingError(
            f"会議を読み取れませんでした（HTTP {response.status_code}）: 会議ID {meeting_id}\n"
            f"応答: {detail}"
        )

    if payload is None:
        raise create_meeting.MeetingError(
            f"会議の応答が JSON ではありません（HTTP {response.status_code}）: "
            f"{create_meeting._detail(response, None)}"
        )

    return payload


# ------------------------------------------------------------------ 照合


def _present(value) -> bool:
    """返ってきたと言えるか。None も空文字も「返ってきていない」。"""
    return value is not None and str(value).strip() != ""


def build_checks(
    meeting: dict,
    *,
    meeting_id,
    expected_topic: str | None = None,
    expected_password: str | None = None,
) -> list[Check]:
    """照合の一覧を作る。

    「返ってこなかった」を「一致した」にしないこと。`actual is not None and ...`
    の形を崩して `or` にすると、欠けている項目が全部 OK になる。
    """
    checks: list[Check] = []

    actual_id = meeting.get("id")
    checks.append(
        Check(
            "会議IDが一致する",
            _present(actual_id) and str(actual_id) == str(meeting_id),
            f"要求 {meeting_id} / 応答 {actual_id}",
        )
    )

    actual_password = meeting.get("password")
    checks.append(
        Check(
            "パスワードが入っている",
            _present(actual_password),
            "" if _present(actual_password) else "空、または応答に含まれていない",
        )
    )
    if expected_password is not None:
        checks.append(
            Check(
                "パスワードが一致する",
                _present(actual_password) and str(actual_password) == expected_password,
                f"期待 {expected_password} / 応答 {actual_password}",
            )
        )

    actual_join_url = meeting.get("join_url")
    checks.append(
        Check(
            "参加リンクが入っている",
            _present(actual_join_url),
            "" if _present(actual_join_url) else "空、または応答に含まれていない",
        )
    )
    # 物差しは「要求した会議ID」。応答の id と比べると、両方そろって
    # 間違っている場合にトートロジーになって通ってしまう。
    expected_fragment = f"{create_meeting.JOIN_PATH}{meeting_id}"
    checks.append(
        Check(
            "参加リンクが同じ会議を指している",
            _present(actual_join_url) and expected_fragment in str(actual_join_url),
            f"{expected_fragment} を含むか / 応答 {actual_join_url}",
        )
    )

    if expected_topic is not None:
        actual_topic = meeting.get("topic")
        checks.append(
            Check(
                "議題が一致する",
                _present(actual_topic) and str(actual_topic) == expected_topic,
                f"期待 {expected_topic} / 応答 {actual_topic}",
            )
        )

    actual_type = meeting.get("type")
    checks.append(
        Check(
            "予定された会議になっている",
            actual_type is not None and actual_type == create_meeting.MEETING_TYPE_SCHEDULED,
            f"期待 {create_meeting.MEETING_TYPE_SCHEDULED} / 応答 {actual_type}",
        )
    )

    actual_status = meeting.get("status")
    checks.append(
        Check(
            "会議はまだ始まっていない",
            _present(actual_status) and str(actual_status) == "waiting",
            f"応答 {actual_status}",
        )
    )

    return checks


def all_ok(checks: list[Check]) -> bool:
    # 空のリストを all() に渡すと True になる。「何も確かめていない」が
    # 「全部一致」として出るのを防ぐ。
    if not checks:
        return False
    return all(check.ok for check in checks)


def format_checks(checks: list[Check]) -> str:
    lines = []
    for check in checks:
        mark = "OK" if check.ok else "NG"
        line = f"[{mark}] {check.label}"
        if check.detail:
            line += f"  {check.detail}"
        lines.append(line)
    return "\n".join(lines)


# ------------------------------------------------------------------ 入口


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="作成した Zoom 会議を読み返して、ID・パスワード・参加リンクを照合する"
    )
    parser.add_argument("meeting_id", help="確認する会議の ID")
    parser.add_argument("--expect-topic", default=None, help="期待する議題")
    parser.add_argument("--expect-password", default=None, help="期待するパスワード")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        credentials = zoom_auth.read_credentials(os.environ)
        token = zoom_auth.fetch_access_token(credentials)
        zoom_auth.require_scopes(token, SCOPES)
        meeting = fetch_meeting(
            args.meeting_id, api_base=token.api_url, access_token=token.value
        )
    except (create_meeting.MeetingError, zoom_auth.AuthError) as error:
        print(error, file=sys.stderr)
        return 1

    checks = build_checks(
        meeting,
        meeting_id=args.meeting_id,
        expected_topic=args.expect_topic,
        expected_password=args.expect_password,
    )
    print(format_checks(checks))

    if not all_ok(checks):
        print("\n食い違いがあります。", file=sys.stderr)
        return 1

    print("\nすべて一致しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
