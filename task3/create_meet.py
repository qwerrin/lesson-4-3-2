"""Google Meet のスペースを作り、参加リンクを表示する。

課題3: Meet の API を使い、オンラインミーティングの作成または参加リンクの生成を行う。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task3\\create_meet.py
    .venv\\Scripts\\python.exe task3\\create_meet.py --access-type OPEN

Meet では「スペース」が会議の入れ物で、「会議」はその中で始まるもの。
このスクリプトが作るのはスペースまで。作った時点では会議はまだ始まっていない。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Sequence

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common import google_auth  # noqa: E402


# 作成に必要な最小のスコープ。「自分のアプリが作ったスペースだけ」に届く。
# readonly では作成できないので使わない。settings は今回要らない。
SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/meetings.space.created",)

# config を設定するには別のスコープが要る（公式ガイド: "Setting or accessing
# meetings settings requires ... meetings.space.settings"）。作成するだけなら要らない。
SETTINGS_SCOPE = "https://www.googleapis.com/auth/meetings.space.settings"

# meetingUri は「この URL に meetingCode を続けたもの」と定義されている。
# 照合でこの規則そのものを使うので、定数にして両方から参照する。
MEET_BASE_URL = "https://meet.google.com/"

# config.accessType が取りうる値。ACCESS_TYPE_UNSPECIFIED は「指定しない」を
# 意味するので、利用者が明示的に選ぶ値としては受け付けない。
ACCESS_TYPES: tuple[str, ...] = ("OPEN", "TRUSTED", "RESTRICTED")


class MeetError(Exception):
    """利用者にそのまま見せられる失敗。"""


# ---------------------------------------------------------------- 送る前


def resolve_access_type(value: str | None) -> str | None:
    """--access-type の値を確定する。未指定なら None（＝送らない）。"""
    if value is None:
        return None
    normalized = value.strip().upper()
    if normalized not in ACCESS_TYPES:
        raise MeetError(
            f"アクセス種別が不正です: {value!r}\n"
            f"使えるのは {' / '.join(ACCESS_TYPES)} です。"
        )
    return normalized


def scopes_for(access_type: str | None) -> tuple[str, ...]:
    """その実行に必要なスコープを決める。

    アクセス種別を指定するときだけ設定スコープを足す。指定しない実行で
    設定権限まで取ると、要らない権限を持ったトークンが残る。
    """
    if access_type is None:
        return SCOPES
    return SCOPES + (SETTINGS_SCOPE,)


def build_space_body(access_type: str | None) -> dict:
    """spaces.create に送る body を組む。

    未指定のときは config ごと省く。空の config を送ることは「指定しない」とは
    別の意味になりうる（課題1で parents=[] が「親を消す」と取られたのと同じ形）。
    """
    if access_type is None:
        return {}
    return {"config": {"accessType": access_type}}


def meeting_uri_for(meeting_code: str) -> str:
    """会議コードから参加リンクを組み立てる。照合でも同じ関数を使う。"""
    if not meeting_code:
        raise MeetError("会議コードが空です。参加リンクを組み立てられません")
    return f"{MEET_BASE_URL}{meeting_code}"


# ---------------------------------------------------------------- エラーの翻訳


def _api_message(error: HttpError) -> str:
    try:
        payload = json.loads(error.content.decode("utf-8"))
        return payload.get("error", {}).get("message", "")
    except (ValueError, AttributeError, UnicodeDecodeError):
        return ""


def _looks_like_api_disabled(detail: str) -> bool:
    lowered = detail.lower()
    return "has not been used" in lowered or "is disabled" in lowered


def _looks_like_scope_problem(detail: str) -> bool:
    lowered = detail.lower()
    return "scope" in lowered or "insufficient" in lowered


def _looks_like_field_unavailable(detail: str) -> bool:
    """「その項目はこのユーザーには使えない」形の 403 か。

    実機で出た（2026-08-14）。個人アカウントでは spaces.create 自体は通るのに、
    config.accessType を指定したときだけ 403 になる。
    応答は項目名まで教えてくれるので、原因を並べただけの案内に混ぜない。
    """
    return "is not available to the user" in detail


# 応答に出てくる操作名と、利用者に見せる言葉・対処の対応。
_UNAVAILABLE_FIELDS: dict[str, tuple[str, str]] = {
    "updateAccessType": (
        "アクセス種別（config.accessType）",
        f"設定には {SETTINGS_SCOPE} が要るため、--access-type を付けたときだけ要求しています。"
        "ただし個人アカウントでは、そのスコープを取得しても同じ 403 になりました（2026-08-14 実測）。"
        "スコープではなくアカウントの制限です。--access-type を外せば既定のまま作成できます。",
    ),
}


def _status_of(error: HttpError) -> int | None:
    response = getattr(error, "resp", None)
    return getattr(response, "status", None)


def _translate_http_error(error: HttpError) -> MeetError:
    """Google が返す英語を、原因と対処に置き換える。

    403 には原因が3種類ある。混ぜると、どれを直せばいいのか読み取れなくなる。
    課題1で権限エラーが 404 で返り、ID の打ち間違いにしか見えなかったのと同じ形を避ける。
    """
    status = _status_of(error)
    detail = _api_message(error) or str(error)

    if status == 403 and _looks_like_field_unavailable(detail):
        label = "指定した項目"
        advice = "その項目を指定せずに実行してください。"
        for key, (name, how) in _UNAVAILABLE_FIELDS.items():
            if key in detail:
                label, advice = name, how
                break
        return MeetError(
            f"{label}の設定が許可されませんでした（{status}）。\n"
            f"{advice}\n"
            f"応答: {detail}"
        )

    if status == 403 and _looks_like_api_disabled(detail):
        return MeetError(
            f"Google Meet API が有効になっていません（{status}）。\n"
            "Google Cloud コンソールの「APIとサービス」→「ライブラリ」で "
            "Google Meet API を検索して有効にしてください。\n"
            "課題1で有効にしたのは Drive API、課題2は Docs API で、どれも別の API です。\n"
            "有効化の反映に数分かかることがあります。\n"
            f"応答: {detail}"
        )

    if status == 403 and _looks_like_scope_problem(detail):
        return MeetError(
            f"権限（スコープ）が足りません（{status}）。\n"
            f"このスクリプトが要求するのは {SCOPES[0]} です。\n"
            "token.json を消してから実行し直すと、同意画面から取り直せます。\n"
            f"応答: {detail}"
        )

    if status == 403:
        return MeetError(
            f"操作が許可されませんでした（{status}）。\n"
            "原因の候補が3つあります。\n"
            "  1. Google Meet API が有効になっていない\n"
            "  2. token.json の権限が足りない\n"
            "  3. 使っている Google アカウントの種類が Meet API に対応していない\n"
            "3 は、コードでも設定でも直せません。別のアカウントで試す必要があります。\n"
            f"応答: {detail}"
        )

    if status == 404:
        return MeetError(
            f"対象が見つかりませんでした（{status}）。\n"
            "スペース名の写し間違いか、自分のアプリが作っていないスペースです。\n"
            f"要求しているスコープ（{SCOPES[0]}）は、自分のアプリが作ったスペースにしか届きません。\n"
            f"応答: {detail}"
        )

    return MeetError(f"Meet API の呼び出しに失敗しました（{status}）。\n応答: {detail}")


# ---------------------------------------------------------------- API の呼び方


def _require(space: dict, key: str, label: str) -> str:
    value = space.get(key)
    if not value:
        raise MeetError(
            f"応答に{label}がありません。スペースを作れたか確認できません。\n"
            f"応答: {space}"
        )
    return value


def create_space(service, body: dict) -> dict:
    """スペースを1つ作る。応答をそのまま返す。"""
    try:
        space = service.spaces().create(body=body).execute()
    except HttpError as error:
        raise _translate_http_error(error) from error

    _require(space, "name", "スペース名")
    _require(space, "meetingUri", "参加リンク")
    _require(space, "meetingCode", "会議コード")
    return space


def build_service(credentials):
    return build("meet", "v2", credentials=credentials)


# ---------------------------------------------------------------- 画面まわり


def format_result(space: dict) -> str:
    access_type = (space.get("config") or {}).get("accessType", "(未指定)")
    return "\n".join(
        [
            "ミーティングのスペースを作成しました",
            f"  スペース名    : {space.get('name', '(不明)')}",
            f"  会議コード    : {space.get('meetingCode', '(不明)')}",
            f"  アクセス種別  : {access_type}",
            f"  参加リンク    : {space.get('meetingUri', '(不明)')}",
        ]
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Google Meet のスペースを作り、参加リンクを表示します。"
    )
    parser.add_argument(
        "--access-type",
        default=None,
        help=f"アクセス種別（{' / '.join(ACCESS_TYPES)}）。未指定ならアカウントの既定に従う",
    )
    # 既定は相対パス。公開する実行画面に自宅のパスを写さないため。
    parser.add_argument("--credentials", default="credentials.json", help="OAuth クライアントの JSON")
    parser.add_argument("--token", default="token.json", help="トークンの保存先")
    return parser.parse_args(argv)


def _default_service_factory(args: argparse.Namespace):
    # 送る内容から必要な権限を決める。resolve_access_type はここでも呼ぶ。
    # 不正な値なら、認証（＝本人のブラウザが開く）より前に落ちる。
    scopes = scopes_for(resolve_access_type(args.access_type))
    credentials = google_auth.load_credentials(args.credentials, args.token, scopes)
    return build_service(credentials)


def main(argv: Sequence[str] | None = None, *, service_factory: Callable | None = None) -> int:
    args = parse_args(argv)
    factory = service_factory or _default_service_factory

    try:
        # 送る内容を確定させてから service を作る。service を作る＝認証で
        # 本人のブラウザが開くので、落ちると分かっている実行で同意画面を出さない。
        body = build_space_body(resolve_access_type(args.access_type))
    except MeetError as error:
        print(error, file=sys.stderr)
        return 1

    try:
        service = factory(args)
        space = create_space(service, body)
    except (MeetError, google_auth.AuthError) as error:
        print(error, file=sys.stderr)
        return 1

    print(format_result(space))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
