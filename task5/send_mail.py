"""Gmail API で、指定した宛先にメールを送る。

課題5: Gmail API を使用して、特定の宛先にメールを送信するプログラムを作成する。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task5\\send_mail.py --to you@example.com --dry-run
    .venv\\Scripts\\python.exe task5\\send_mail.py --to you@example.com

**この課題だけ、前の4課題と前提が違う。送信は取り消せない。**
Drive のファイルも Docs も Zoom の会議も、間違えたら消してやり直せた。
メールは相手の受信箱に残る。API に「送信を取り消す」操作は無い。

そこで確認を2つに分けている。

- **送る前に確かめられること** — 宛先・件名・本文・MIME の組み立て・base64url 化。
  ネットワークに出ないので何度でもやり直せる。`--dry-run` はここまでを実行して止まる
- **送った後にしか確かめられないこと** — サーバに実際に載った値。`verify_mail.py` の仕事

`--dry-run` は **service を作らない**。service を作る＝同意画面が開きうるので、
送らないと分かっている実行で本人のブラウザを触らない。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Callable, Sequence

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common import google_auth  # noqa: E402


# 送信専用のスコープ。これだけでは messages.get が通らない（＝受信箱は読めない）。
# 読み返して照合するのは verify_mail.py の仕事で、あちらが readonly を要求する。
# 送るだけのスクリプトに読み取り権限を持たせない。同意画面に出る権限は公開される。
SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/gmail.send",)

# token は課題ごとに分ける。同意フローは token.json を丸ごと置き換えるので、
# 1つのファイルを使い回すと課題を行き来するたびに同意画面が出る（課題3で実測）。
#
# さらに課題5では、**同じ課題の中でも送信用と読み取り用を分ける**。
# send_mail は gmail.send、verify_mail は gmail.readonly を要求する。
# load_credentials は権限が足りないトークンを捨てて取り直すので、1つの
# ファイルを共有すると、送る→読む→送る のたびに相手の権限が消え、
# 毎回同意画面が出る。分ければ最初の1回ずつで済む。
DEFAULT_TOKEN = "task5/token-send.json"

# 認証済みのユーザー自身。アドレスを書かずに済むので、実行画面に写らない。
GMAIL_USER_ID = "me"

DEFAULT_SUBJECT = "Gmail API からの送信テスト"
DEFAULT_BODY = (
    "こんにちは。\n"
    "これは Gmail API から送信したメールです。\n"
    "\n"
    "AIエンジニア講座 Section 4-3 課題5"
)

# ヘッダに入ってはいけない文字。改行から先が別のヘッダとして解釈されると、
# 見えない宛先（Bcc）を足される。**送信は取り消せない**ので、送る前に落とす。
FORBIDDEN_IN_HEADER = ("\r", "\n")

# 応答に無ければ「送れた」と言えない項目。
REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
    ("id", "メッセージID"),
    ("threadId", "スレッドID"),
)


class MailError(Exception):
    """利用者にそのまま見せられる失敗。"""


# ---------------------------------------------------------------- 送る前


def _reject_line_breaks(value: str, label: str) -> None:
    for char in FORBIDDEN_IN_HEADER:
        if char in value:
            raise MailError(
                f"{label}に改行が含まれています。\n"
                "改行から先は別のヘッダとして解釈されるため、意図しない宛先が"
                "足される恐れがあります（ヘッダインジェクション）。"
            )


def normalize_address(value: str) -> str:
    """宛先を確定する。形がおかしければ送る前に落とす。"""
    address = (value or "").strip()
    if not address:
        raise MailError("宛先が空です。--to でメールアドレスを指定してください")

    _reject_line_breaks(address, "宛先")

    if any(char.isspace() for char in address):
        raise MailError(f"宛先に空白が含まれています: {address!r}")

    if address.count("@") != 1:
        raise MailError(
            f"宛先の形式が不正です: {address!r}\n"
            "メールアドレスは @ をちょうど1つ含む必要があります。"
        )

    local, domain = address.split("@")
    if not local or not domain:
        raise MailError(f"宛先の形式が不正です: {address!r}\n@ の前後がどちらも必要です。")

    return address


def normalize_subject(value: str) -> str:
    """件名を確定する。

    空を既定値で埋めない。埋めると「指定し忘れ」が成功として通ってしまう。
    既定値を使うかどうかは引数の既定値（DEFAULT_SUBJECT）で決める話で、
    明示的に空を渡されたときは失敗が正しい。
    """
    subject = (value or "").strip()
    if not subject:
        raise MailError("件名が空です。--subject で件名を指定してください")
    _reject_line_breaks(subject, "件名")
    return subject


def resolve_body(text: str | None, path: str | Path | None) -> str:
    """本文を確定する。--body と --body-file のどちらか一方。

    件名と違って本文の前後の改行は意味を持つので strip した値は返さない
    （空かどうかの判定にだけ strip を使う）。
    """
    if text is not None and path is not None:
        raise MailError("--body と --body-file は同時に指定できません。どちらか一方にしてください")

    if path is not None:
        source = Path(path)
        if not source.exists():
            raise MailError(f"本文のファイルが見つかりません: {source}")
        try:
            body = source.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise MailError(f"本文のファイルを UTF-8 として読めません: {source}") from error
    elif text is not None:
        body = text
    else:
        return DEFAULT_BODY

    if not body.strip():
        raise MailError("本文が空です。空のメールは送りません")
    return body


def build_message(to: str, subject: str, body: str, sender: str | None = None) -> EmailMessage:
    """送る MIME メッセージを組む。

    policy.SMTP と cte="base64" の両方が要る（2026-08-15 実測）。

    | 組み合わせ                   | CRLF | 7bit |
    |------------------------------|------|------|
    | 既定 policy・既定 cte        | ✗    | ✗    |
    | 既定 policy・cte=base64      | ✗    | ✓    |
    | policy.SMTP・既定 cte        | ✓    | ✗    |
    | policy.SMTP・cte=base64      | ✓    | ✓    |

    RFC 2822 は行末に CRLF を、本文に 7bit を要求する。既定のままだと
    行末が LF・本文が生の UTF-8（8bit）になり、どちらも満たさない。

    From は指定されたときだけ付ける。付けなければ Gmail が認証済みの
    アカウントで埋めるので、偽の送信元を書かずに済む。
    """
    to = normalize_address(to)
    subject = normalize_subject(subject)

    message = EmailMessage(policy=policy.SMTP)
    message["To"] = to
    message["Subject"] = subject
    if sender is not None:
        message["From"] = normalize_address(sender)
    message.set_content(body, cte="base64")
    return message


def encode_raw(message: EmailMessage) -> str:
    """MIME を base64url にする。

    Gmail の raw は base64url と定められている。標準 base64 の + と / は
    URL やクエリで別の意味を持つため、- と _ に置き換わった別の表である。
    """
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def decode_raw(raw: str) -> bytes:
    """base64url を MIME のバイト列に戻す。

    Gmail はパディング（=）を落とした形で返すことがあるので、足してから復号する。
    """
    padded = raw + "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(padded)


def build_send_body(to: str, subject: str, body: str, sender: str | None = None) -> dict:
    """messages.send に渡す body を組む。"""
    return {"raw": encode_raw(build_message(to, subject, body, sender))}


def _parse(raw: bytes) -> EmailMessage:
    return BytesParser(policy=policy.SMTP).parsebytes(raw)


def subject_of(raw: bytes) -> str:
    """MIME から件名を読む。RFC 2047 の符号化は解いて返す。

    日本語の件名は生のヘッダ行では =?utf-8?b?...?= の形になっている。
    解かずに比べると永久に一致しない。
    """
    return str(_parse(raw)["Subject"])


def body_text_of(raw: bytes) -> str:
    """MIME から本文を読む。"""
    return _parse(raw).get_content()


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


def _status_of(error: HttpError) -> int | None:
    response = getattr(error, "resp", None)
    return getattr(response, "status", None)


def _translate_http_error(error: HttpError) -> MailError:
    """Google が返す英語を、原因と対処に置き換える。

    403 には原因が2種類ある（未有効化・権限不足）。混ぜると、どちらを直せば
    いいのか読み取れなくなる。相手が理由を具体的に言っているときは、
    こちらで候補を並べ直さない。
    """
    status = _status_of(error)
    detail = _api_message(error) or str(error)

    if _looks_like_api_disabled(detail):
        return MailError(
            f"Gmail API が有効になっていません（{status}）。\n"
            "Google Cloud コンソールの「APIとサービス」→「ライブラリ」で "
            "Gmail API を検索して有効にしてください。\n"
            "課題1で有効にしたのは Drive API、課題2は Docs API、課題3は Meet API で、"
            "どれも別の API です。\n"
            "反映に数分かかることがあります。\n"
            f"応答: {detail}"
        )

    if _looks_like_scope_problem(detail):
        return MailError(
            f"権限（スコープ）が足りません（{status}）。\n"
            f"このスクリプトが要求するのは {SCOPES[0]} です。\n"
            f"{DEFAULT_TOKEN} を消してから実行し直すと、同意画面から取り直せます。\n"
            f"応答: {detail}"
        )

    if status == 429:
        return MailError(
            f"送信の回数制限に掛かりました（{status}）。\n"
            "しばらく待ってから実行し直してください。\n"
            f"応答: {detail}"
        )

    return MailError(f"メールの送信に失敗しました（HTTP {status}）。\n応答: {detail}")


# ---------------------------------------------------------------- 送る


def _require(sent: dict, key: str, label: str) -> None:
    value = sent.get(key)
    # 空文字を「返ってきた」とみなさない。既定値で埋めると全部 OK になり、
    # 確かめた気持ちだけが残る。
    if value is None or str(value).strip() == "":
        raise MailError(
            f"応答に{label}がありません。送信できたか確認できません。\n"
            f"応答: {sent}"
        )


def send_message(service, body: dict) -> dict:
    """メールを1通送る。応答をそのまま返す。"""
    try:
        sent = service.users().messages().send(userId=GMAIL_USER_ID, body=body).execute()
    except HttpError as error:
        raise _translate_http_error(error) from error

    for key, label in REQUIRED_FIELDS:
        _require(sent, key, label)
    return sent


def build_service(credentials):
    return build("gmail", "v1", credentials=credentials)


# ---------------------------------------------------------------- 画面まわり


def format_preview(to: str, subject: str, body: str) -> str:
    """--dry-run の出力。

    送信成功の画面と見分けられる文言にする。見分けられないと
    「送ったつもりで送っていない」「送っていないつもりで送った」が起きる。
    """
    return "\n".join(
        [
            "--- 組み立てた内容（まだ送信していません）---",
            f"  宛先: {to}",
            f"  件名: {subject}",
            "  本文:",
            *[f"    {line}" for line in body.splitlines()],
            "",
            "送信するには --dry-run を外して実行してください。",
        ]
    )


def format_result(sent: dict, to: str, subject: str) -> str:
    message_id = sent.get("id", "")
    return "\n".join(
        [
            "メールを送信しました",
            f"  宛先          : {to}",
            f"  件名          : {subject}",
            f"  メッセージID  : {message_id}",
            f"  スレッドID    : {sent.get('threadId', '')}",
            "",
            "送信できたことと、正しい形でサーバに載ったことは別です。読み返して照合するには:",
            f"  .venv\\Scripts\\python.exe task5\\verify_mail.py --message-id {message_id}",
        ]
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gmail API で、指定した宛先にメールを送信します。"
    )
    parser.add_argument("--to", required=True, help="宛先のメールアドレス")
    parser.add_argument("--subject", default=DEFAULT_SUBJECT, help="件名")
    parser.add_argument("--body", default=None, help="本文（--body-file と排他）")
    parser.add_argument("--body-file", default=None, help="本文を読むファイル（UTF-8）")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="組み立てた内容を表示するだけで送信しない。認証も行わない",
    )
    # 既定は相対パス。公開する実行画面に自宅の絶対パスを写さないため。
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
        # 送る内容を先に確定させる。ここで落ちる実行は API に届かないので、
        # 同意画面（＝本人のブラウザ）を開かせない。
        to = normalize_address(args.to)
        subject = normalize_subject(args.subject)
        body = resolve_body(args.body, args.body_file)
        send_body = build_send_body(to, subject, body)
    except MailError as error:
        print(error, file=sys.stderr)
        return 1

    if args.dry_run:
        # service を作らずに戻る。送らないと分かっている実行で認証しない。
        print(format_preview(to, subject, body))
        return 0

    try:
        service = factory(args)
        sent = send_message(service, send_body)
    except (MailError, google_auth.AuthError) as error:
        print(error, file=sys.stderr)
        return 1

    print(format_result(sent, to, subject))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
