"""送ったメールを Gmail から読み返して、指定した内容と突き合わせる。

**読むだけ。何も送らないし、書き換えない。**

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task5\\verify_mail.py --message-id <ID> --to you@example.com

send_mail.py が成功しても、それは「API が受け付けた」までしか意味しない。
偽物で確かめられるのは呼び方までで、実際に相手に届く形でサーバに載ったかは、
実物を1回読み返して初めて分かる。

**照合の物差しは応答の外から取る。** 応答の値どうしを比べると、サーバが
おかしな値を返したときトートロジーで通る（課題4の教訓）。期待値は
コマンドラインから渡させる。

この課題に固有の罠が2つある。どちらも「送った文字列」と「返ってきた文字列」が
そのままでは一致しない形で出る。

1. **日本語の件名は RFC 2047 で符号化されて返る**（``=?utf-8?b?...?=``）
2. **本文の行末は CRLF になって返る**（RFC 2822 の要求。LF で送っても変換される）
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from email.header import decode_header
from pathlib import Path
from typing import Callable, Sequence

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import send_mail  # noqa: E402
from common import google_auth  # noqa: E402


# 読み取り専用。gmail.send では messages.get が通らない（公式のスコープ一覧で確認済み）。
# 逆にここで送信権限を持たない。確認するだけのスクリプトが送れてはいけない。
SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/gmail.readonly",)

# 送信側とはスコープが違うので、トークンのファイルも分ける。
# 共有すると load_credentials が権限の足りないトークンを捨てて取り直すため、
# 送る→読む→送る のたびに同意画面が出る。
DEFAULT_TOKEN = "task5/token-verify.json"

# metadata では本文が返らない（ヘッダとラベルのみ）。本文まで照合するので full。
MESSAGE_FORMAT = "full"

# 送信済みを表すラベル。下書きのままなら DRAFT が付く。
SENT_LABEL = "SENT"

# 本文として読む MIME 型。マルチパートのときは HTML ではなくこちらを選ぶ。
TEXT_MIME_TYPE = "text/plain"


class VerifyError(Exception):
    """利用者にそのまま見せられる失敗。"""


@dataclass
class Check:
    label: str
    ok: bool
    detail: str = ""


# ---------------------------------------------------------------- 応答の読み方


def header_value(payload: dict, name: str) -> str | None:
    """ヘッダを1つ取り出す。

    ヘッダ名の大文字小文字は保証されない。決め打ちで比べると取りこぼす。
    """
    wanted = name.lower()
    for header in (payload or {}).get("headers", []) or []:
        if str(header.get("name", "")).lower() == wanted:
            return header.get("value")
    return None


def decode_subject(value: str | None) -> str:
    """RFC 2047 の符号化を解く。

    日本語の件名は ``Subject: Gmail API =?utf-8?b?...?=`` の形で返る。
    解かずに比べると永久に一致しない。

    符号化語の直前の空白は ASCII 側のチャンクに残るので、単純に連結してよい
    （2026-08-15 に decode_header / make_header / ポリシー付きパーサの3通りで
    同じ結果になることを実測した）。
    """
    if value is None:
        return ""
    parts: list[str] = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "ascii", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


def _decode_part_data(body: dict | None) -> str | None:
    body = body or {}
    # attachmentId があるとき data は空で、中身は別リクエストの向こうにある。
    # 「本文が空」と区別できないと、届いていないのに一致扱いになる。
    if body.get("attachmentId"):
        return None
    data = body.get("data")
    if not data:
        return None
    return send_mail.decode_raw(data).decode("utf-8", errors="replace")


def extract_body(payload: dict) -> str | None:
    """本文（text/plain）を取り出す。

    非コンテナ型（text/plain など）は payload.body.data に入り、
    コンテナ型（multipart/*）は parts[] の中にある。
    """
    payload = payload or {}
    parts = payload.get("parts")
    if not parts:
        return _decode_part_data(payload.get("body"))

    for part in parts:
        if part.get("mimeType") == TEXT_MIME_TYPE:
            text = _decode_part_data(part.get("body"))
            if text is not None:
                return text

    # 入れ子のマルチパート（multipart/mixed の中に multipart/alternative など）。
    for part in parts:
        text = extract_body(part)
        if text is not None:
            return text
    return None


def normalize_newlines(text: str) -> str:
    """行末を LF に揃える。

    送るときは LF、サーバに載るときは CRLF（RFC 2822）。
    正規化しないと、複数行の本文は内容が合っていても必ず NG になる。
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _present(value) -> bool:
    """「返ってきた」と言えるか。空文字を返ってきた扱いにしない。"""
    return value is not None and str(value).strip() != ""


# ---------------------------------------------------------------- 照合


def _compare(label: str, expected: str, actual: str | None) -> Check:
    if not _present(actual):
        return Check(label, False, f"期待 {expected!r} / 実際 返ってきませんでした")
    ok = str(actual) == expected
    detail = "" if ok else f"期待 {expected!r} / 実際 {str(actual)!r}"
    return Check(label, ok, detail)


def build_checks(
    message: dict,
    *,
    message_id: str,
    expected_to: str,
    expected_subject: str,
    expected_body: str,
) -> list[Check]:
    """読み返した内容を、こちらが指定した値と突き合わせる。

    message_id / expected_* はすべて応答の外から来る。応答の中の値どうしを
    比べる項目を作らない（トートロジーになる）。
    """
    payload = message.get("payload") or {}
    checks: list[Check] = []

    # 1. こちらが要求した ID のメッセージが返ってきたか。
    checks.append(_compare("メッセージID", message_id, message.get("id")))

    # 2. 送信済みか。下書きのまま残っていれば DRAFT になる。
    labels = message.get("labelIds")
    if labels is None:
        checks.append(Check("送信済みラベル", False, "labelIds が返ってきませんでした"))
    else:
        checks.append(
            Check(
                "送信済みラベル",
                SENT_LABEL in labels,
                "" if SENT_LABEL in labels else f"期待 {SENT_LABEL} / 実際 {labels}",
            )
        )

    # 3. 宛先。
    checks.append(_compare("宛先", expected_to, header_value(payload, "To")))

    # 4. 件名。符号化を解いてから比べる。
    #    欠落の判定は _compare に任せる（decode_subject(None) は空文字を返し、
    #    _compare が空文字を「返ってこなかった」として扱う）。ここで先回りして
    #    同じ判定を書くと、消しても誰も落ちない冗長な分岐になる。
    checks.append(_compare("件名", expected_subject, decode_subject(header_value(payload, "Subject"))))

    # 5. 本文。行末を揃えてから比べる。
    #    末尾の改行はワイヤに載る時点で必ず1つ足されるので、両側から落とす。
    #    ここだけは完全一致にできない（末尾の空行の有無は確認できない）。
    actual_body = extract_body(payload)
    if actual_body is None:
        checks.append(Check("本文", False, "本文が返ってきませんでした"))
    else:
        want = normalize_newlines(expected_body).rstrip("\n")
        got = normalize_newlines(actual_body).rstrip("\n")
        checks.append(Check("本文", want == got, "" if want == got else f"期待 {want!r} / 実際 {got!r}"))

    # 6. 送信元が入っているか。指定していないので値は照合できないが、
    #    Gmail が埋めるはずの欄が空なら何かがおかしい。
    sender = header_value(payload, "From")
    checks.append(
        Check("送信元", _present(sender), "" if _present(sender) else "From が返ってきませんでした")
    )

    # 7. スレッドID。
    thread_id = message.get("threadId")
    checks.append(
        Check(
            "スレッドID",
            _present(thread_id),
            "" if _present(thread_id) else "threadId が返ってきませんでした",
        )
    )

    return checks


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


# ---------------------------------------------------------------- 読み取り


def _api_message(error: HttpError) -> str:
    try:
        payload = json.loads(error.content.decode("utf-8"))
        return payload.get("error", {}).get("message", "")
    except (ValueError, AttributeError, UnicodeDecodeError):
        return ""


def fetch_message(service, message_id: str) -> dict:
    """メッセージを1件読む。読むだけで、何も書き換えない。"""
    try:
        return (
            service.users()
            .messages()
            .get(userId=send_mail.GMAIL_USER_ID, id=message_id, format=MESSAGE_FORMAT)
            .execute()
        )
    except HttpError as error:
        status = getattr(getattr(error, "resp", None), "status", None)
        detail = _api_message(error)
        # どのメッセージを読もうとしたのかを必ず載せる。無いと直しようがない。
        raise VerifyError(
            f"メールを読み取れませんでした（HTTP {status}）: メッセージID {message_id}\n"
            f"応答: {detail or error}"
        ) from error


def build_service(credentials):
    return build("gmail", "v1", credentials=credentials)


# ---------------------------------------------------------------- 入口


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="送ったメールを Gmail から読み返して、指定した内容と突き合わせます（読むだけ）。"
    )
    parser.add_argument("--message-id", required=True, help="send_mail.py が表示したメッセージID")
    # 期待値は応答の外から取る。必須にして、応答の値で埋める逃げ道を作らない。
    parser.add_argument("--to", required=True, help="送ったときの宛先")
    parser.add_argument("--subject", default=send_mail.DEFAULT_SUBJECT, help="送ったときの件名")
    parser.add_argument("--body", default=None, help="送ったときの本文（--body-file と排他）")
    parser.add_argument("--body-file", default=None, help="送ったときの本文を読むファイル（UTF-8）")
    parser.add_argument("--credentials", default="credentials.json", help="OAuth クライアントの JSON")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="トークンの保存先")
    return parser.parse_args(argv)


def _default_service_factory(args: argparse.Namespace):
    credentials = google_auth.load_credentials(args.credentials, args.token, SCOPES)
    return build_service(credentials)


def main(argv: Sequence[str] | None = None, *, service_factory: Callable | None = None) -> int:
    args = parse_args(argv)
    factory = service_factory or _default_service_factory

    try:
        # 期待値を先に確定させる。ここで落ちる実行は API に届かないので、
        # 同意画面（＝本人のブラウザ）を開かせない。
        expected_to = send_mail.normalize_address(args.to)
        expected_subject = send_mail.normalize_subject(args.subject)
        expected_body = send_mail.resolve_body(args.body, args.body_file)
    except send_mail.MailError as error:
        print(error, file=sys.stderr)
        return 1

    try:
        service = factory(args)
        message = fetch_message(service, args.message_id)
    except (VerifyError, send_mail.MailError, google_auth.AuthError) as error:
        print(error, file=sys.stderr)
        return 1

    checks = build_checks(
        message,
        message_id=args.message_id,
        expected_to=expected_to,
        expected_subject=expected_subject,
        expected_body=expected_body,
    )
    print("読み返した内容と、指定した内容の照合:")
    print(format_checks(checks))

    if not all_ok(checks):
        print("\n食い違いがあります。上の NG を確認してください。", file=sys.stderr)
        return 1

    print("\nすべて一致しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
