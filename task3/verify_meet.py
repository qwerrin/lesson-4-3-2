"""作ったスペースを Meet API から読み返し、作成時に返った値と突き合わせる。

create_meet のテストは偽の service を使うので、固定できるのは「呼び方」まで。
本当にスペースができたのか、返ってきた参加リンクが会議として成立する形なのかは、
実物を1回読まないと分からない。ここがその1回。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task3\\verify_meet.py spaces/xxxx \\
        --meeting-uri https://meet.google.com/abc-mnop-xyz --meeting-code abc-mnop-xyz

読むだけで、スペースは一切変更しない。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from googleapiclient.errors import HttpError

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common import google_auth  # noqa: E402

import create_meet  # noqa: E402

# 定義に書かれている会議コードの形式は [a-z]+-[a-z]+-[a-z]+。
# 「リンクとして成立しているか」を、値の一致とは別の角度で見るために使う。
MEETING_CODE_PATTERN = re.compile(r"^[a-z]+-[a-z]+-[a-z]+$")


class VerifyError(Exception):
    """利用者にそのまま見せられる失敗。"""


@dataclass(frozen=True)
class Check:
    label: str
    ok: bool
    detail: str = ""


# ---------------------------------------------------------------- 応答を読む


def is_valid_meeting_code(code: str | None) -> bool:
    if not code:
        return False
    return MEETING_CODE_PATTERN.fullmatch(code) is not None


def _access_type_of(space: dict) -> str | None:
    """config.accessType を取り出す。無ければ None（＝照合できなかった）。

    既定値を入れない。入れると、返ってこなかったケースが「一致」に化ける。
    """
    config = space.get("config")
    if not isinstance(config, dict):
        return None
    return config.get("accessType")


# ---------------------------------------------------------------- 照合


def compare_with_expected(
    space: dict,
    *,
    expected_name: str,
    expected_uri: str,
    expected_code: str,
    expected_access_type: str | None = None,
) -> list[Check]:
    """読み返したスペースを、作成時に返った値と突き合わせる。

    項目は README に先に書いた7つ。アクセス種別だけは、指定された場合にのみ見る。
    """
    actual_name = space.get("name")
    actual_uri = space.get("meetingUri")
    actual_code = space.get("meetingCode")

    checks = [
        Check(
            "スペース名が一致",
            actual_name is not None and actual_name == expected_name,
            f"{actual_name if actual_name is not None else '(返らなかった)'} / {expected_name}",
        ),
        Check(
            "参加リンクが一致",
            actual_uri is not None and actual_uri == expected_uri,
            f"{actual_uri if actual_uri is not None else '(返らなかった)'} / {expected_uri}",
        ),
        Check(
            "会議コードが一致",
            actual_code is not None and actual_code == expected_code,
            f"{actual_code if actual_code is not None else '(返らなかった)'} / {expected_code}",
        ),
    ]

    # 応答の中だけで完結する照合。期待値を使わないので、期待値が全部間違っていても
    # ここは正しく判定できる。2番・3番は「同じ値が返った」しか言っていない。
    if actual_uri is None or actual_code is None:
        checks.append(Check("参加リンクと会議コードが整合", False, "(どちらかが返らなかった)"))
    else:
        built = f"{create_meet.MEET_BASE_URL}{actual_code}"
        checks.append(
            Check("参加リンクと会議コードが整合", actual_uri == built, f"{actual_uri} / {built}")
        )

    checks.append(
        Check(
            "会議コードの形が正しい",
            is_valid_meeting_code(actual_code),
            f"{actual_code if actual_code is not None else '(返らなかった)'} / [a-z]+-[a-z]+-[a-z]+",
        )
    )

    # スペースを作ることと、会議が始まることは別。作った直後は会議が無いはず。
    active = space.get("activeConference")
    checks.append(
        Check(
            "会議はまだ始まっていない",
            not active,
            "(activeConference なし)" if not active else f"activeConference あり: {active}",
        )
    )

    if expected_access_type is not None:
        actual_access = _access_type_of(space)
        checks.append(
            Check(
                "アクセス種別が一致",
                actual_access is not None and actual_access == expected_access_type,
                f"{actual_access if actual_access is not None else '(返らなかった)'}"
                f" / {expected_access_type}",
            )
        )

    return checks


def all_ok(checks: Sequence[Check]) -> bool:
    """全部 OK なら True。空なら False。

    照合が0件なのに「全部一致」と言わせないため、空を真にしない。
    """
    if not checks:
        return False
    return all(check.ok for check in checks)


def format_checks(checks: Sequence[Check]) -> str:
    lines = []
    for check in checks:
        mark = "OK" if check.ok else "NG"
        line = f"{mark}  {check.label}"
        if check.detail:
            line += f"  {check.detail}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------- API の呼び方


def _api_message(error: HttpError) -> str:
    try:
        payload = json.loads(error.content.decode("utf-8"))
        return payload.get("error", {}).get("message", "")
    except (ValueError, AttributeError, UnicodeDecodeError):
        return ""


def fetch_space(service, name: str) -> dict:
    """スペースを読む。読むだけで何も変更しない。"""
    try:
        return service.spaces().get(name=name).execute()
    except HttpError as error:
        status = getattr(getattr(error, "resp", None), "status", None)
        detail = _api_message(error) or str(error)
        if status == 404:
            raise VerifyError(
                f"スペースが見つかりません（{status}）: {name}\n"
                "名前の写し間違いか、自分のアプリが作っていないスペースです。\n"
                f"応答: {detail}"
            ) from error
        raise VerifyError(
            f"スペースを読み取れませんでした（{status}）: {name}\n応答: {detail}"
        ) from error


# ---------------------------------------------------------------- 画面まわり


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="作成したスペースを読み返して、作成時に返った値と突き合わせます。"
    )
    parser.add_argument("name", help="スペース名（spaces/xxxx）")
    parser.add_argument("--meeting-uri", required=True, help="作成時に返った参加リンク")
    parser.add_argument("--meeting-code", required=True, help="作成時に返った会議コード")
    parser.add_argument("--access-type", default=None, help="指定して作った場合のアクセス種別")
    parser.add_argument("--credentials", default="credentials.json", help="OAuth クライアントの JSON")
    parser.add_argument("--token", default="token.json", help="トークンの保存先")
    return parser.parse_args(argv)


def _default_service_factory(args: argparse.Namespace):
    credentials = google_auth.load_credentials(args.credentials, args.token, create_meet.SCOPES)
    return create_meet.build_service(credentials)


def main(argv: Sequence[str] | None = None, *, service_factory: Callable | None = None) -> int:
    args = parse_args(argv)
    factory = service_factory or _default_service_factory

    try:
        # 期待値を確定させてから service を作る。落ちると分かっている実行で
        # 認証の同意画面を開かないため。
        expected_access_type = create_meet.resolve_access_type(args.access_type)
    except create_meet.MeetError as error:
        print(error, file=sys.stderr)
        return 1

    try:
        service = factory(args)
        space = fetch_space(service, args.name)
    except (VerifyError, create_meet.MeetError, google_auth.AuthError) as error:
        print(error, file=sys.stderr)
        return 1

    checks = compare_with_expected(
        space,
        expected_name=args.name,
        expected_uri=args.meeting_uri,
        expected_code=args.meeting_code,
        expected_access_type=expected_access_type,
    )
    print(format_checks(checks))
    print(f"参加リンク: {space.get('meetingUri', '(不明)')}")
    return 0 if all_ok(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
