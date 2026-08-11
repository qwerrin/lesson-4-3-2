"""ローカルのファイルを Google ドライブにアップロードする。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task1\\drive_upload.py task1\\data\\sample.txt

初回は既定のブラウザが開いて Google の同意画面が出る。許可すると token.json が
できて、次からはブラウザが開かない。

スコープは既定で drive.file（このプログラムが作ったファイルだけ触れる）を使う。
最小権限で足りるのは「新しく上げる」だけだからで、既にあるフォルダに入れたい
場合は --full-drive-scope が要る。理由は build_metadata と _translate_http_error
のコメントに書いた。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from pathlib import Path
from typing import Callable, Iterable, Sequence

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# このプログラムが作った・開いたファイルだけを対象にする最小権限。
DEFAULT_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/drive.file",)

# 既存フォルダを親に指定する場合だけ必要になる広い権限。
FULL_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/drive",)

# API から取り戻す項目。webViewLink が無いと「上がったこと」を人が確かめにいけない。
RESPONSE_FIELDS = "id, name, mimeType, size, webViewLink"

DEFAULT_MIME_TYPE = "application/octet-stream"


class UploadError(Exception):
    """利用者にそのまま見せられる失敗。想定外の例外とは区別する。"""


# ---------------------------------------------------------------- 送る前に決めること


def validate_local_file(path: str | Path) -> Path:
    local_path = Path(path)
    if not local_path.exists():
        raise UploadError(f"ファイルが見つかりません: {local_path}")
    if not local_path.is_file():
        raise UploadError(f"ファイルではありません（フォルダは送れません）: {local_path}")
    return local_path


def resolve_upload_name(local_path: Path, name: str | None = None) -> str:
    if name is None:
        return local_path.name
    trimmed = name.strip()
    if not trimmed:
        raise UploadError("--name に空の名前は指定できません")
    return trimmed


def guess_mime_type(local_path: Path) -> str:
    guessed, _ = mimetypes.guess_type(local_path.name)
    return guessed or DEFAULT_MIME_TYPE


def build_metadata(name: str, folder_id: str | None = None) -> dict:
    """Drive に渡すファイル情報を組み立てる。

    parents はキーごと省く。空リストを送るとルート直下の扱いにならず、
    「親を消す」意味に取られるため。
    """
    metadata: dict = {"name": name}
    if folder_id and folder_id.strip():
        metadata["parents"] = [folder_id.strip()]
    return metadata


# ---------------------------------------------------------------- アップロード


def _api_message(error: HttpError) -> str:
    """HttpError の本文から Google が返した説明文だけを取り出す。"""
    try:
        payload = json.loads(error.content.decode("utf-8"))
        return str(payload["error"]["message"])
    except (ValueError, KeyError, AttributeError, UnicodeDecodeError):
        return str(error)


def _translate_http_error(error: HttpError, folder_id: str | None) -> UploadError:
    status = error.resp.status
    detail = _api_message(error)

    if status == 404:
        # drive.file スコープでは、このプログラムが作っていないフォルダは
        # 「存在しない」として返ってくる。権限エラーではなく 404 になるのが罠。
        hint = (
            f"フォルダが見つかりません（ID: {folder_id}）。"
            "ID の写し間違いか、既定の drive.file スコープでは触れないフォルダです。"
            "既にあるフォルダに入れるなら --full-drive-scope を付けて権限を取り直してください。"
            if folder_id
            else "対象が見つかりません。"
        )
        return UploadError(f"[{status}] {hint} / API の応答: {detail}")

    if status == 403:
        return UploadError(
            f"[{status}] 権限が足りません。"
            "Google Cloud で Drive API が有効か、同意した権限の範囲を確認してください。"
            f" / API の応答: {detail}"
        )

    return UploadError(f"[{status}] アップロードに失敗しました / API の応答: {detail}")


def upload_file(
    service,
    local_path: str | Path,
    *,
    name: str | None = None,
    folder_id: str | None = None,
) -> dict:
    """1ファイルを Drive に上げて、API が返したファイル情報を返す。"""
    validated = validate_local_file(local_path)
    metadata = build_metadata(resolve_upload_name(validated, name), folder_id)

    # resumable=True にすると回線が切れても途中から送り直せる。
    media = MediaFileUpload(
        str(validated), mimetype=guess_mime_type(validated), resumable=True
    )

    try:
        return (
            service.files()
            .create(body=metadata, media_body=media, fields=RESPONSE_FIELDS)
            .execute()
        )
    except HttpError as error:
        raise _translate_http_error(error, folder_id) from error


# ---------------------------------------------------------------- 認証


def _default_refresher(credentials: Credentials) -> None:
    credentials.refresh(Request())


def _save_token(token_path: Path, credentials: Credentials) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")


def _read_token(token_path: Path) -> Credentials | None:
    """保存済みトークンを読む。読めなければ捨てる（取り直せばいいので落とさない）。"""
    if not token_path.exists():
        return None
    try:
        # scopes を渡さないこと。渡すとファイルに書かれた実際の権限が
        # 引数で上書きされ、権限不足を検出できなくなる。
        return Credentials.from_authorized_user_file(str(token_path))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def load_credentials(
    credentials_path: str | Path,
    token_path: str | Path,
    scopes: Iterable[str] = DEFAULT_SCOPES,
    *,
    flow_factory: Callable = InstalledAppFlow.from_client_secrets_file,
    refresher: Callable[[Credentials], None] = _default_refresher,
) -> Credentials:
    credentials_path = Path(credentials_path)
    token_path = Path(token_path)
    wanted = list(scopes)

    credentials = _read_token(token_path)
    if credentials is not None and not credentials.has_scopes(wanted):
        credentials = None

    if credentials is not None:
        if credentials.valid:
            return credentials
        if credentials.expired and credentials.refresh_token:
            refresher(credentials)
            _save_token(token_path, credentials)
            return credentials

    if not credentials_path.exists():
        raise UploadError(
            f"credentials.json が見つかりません: {credentials_path}\n"
            "Google Cloud コンソールで OAuth 2.0 クライアント ID（デスクトップアプリ）を作り、"
            "ダウンロードした JSON をこのパスに置いてください。手順は README を参照。"
        )

    flow = flow_factory(str(credentials_path), wanted)
    credentials = flow.run_local_server(port=0)
    _save_token(token_path, credentials)
    return credentials


def build_service(credentials: Credentials):
    return build("drive", "v3", credentials=credentials)


# ---------------------------------------------------------------- 画面まわり


def format_result(uploaded: dict) -> str:
    lines = [
        "アップロードしました",
        f"  ファイル名: {uploaded.get('name', '(不明)')}",
        f"  ファイルID: {uploaded.get('id', '(不明)')}",
    ]
    link = uploaded.get("webViewLink")
    if link:
        lines.append(f"  リンク    : {link}")
    return "\n".join(lines)


def scopes_for(args: argparse.Namespace) -> tuple[str, ...]:
    return FULL_SCOPES if args.full_drive_scope else DEFAULT_SCOPES


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ローカルのファイルを Google ドライブにアップロードします。"
    )
    parser.add_argument("path", help="アップロードするローカルファイルのパス")
    parser.add_argument("--name", default=None, help="ドライブ上でのファイル名（既定: 元のファイル名）")
    parser.add_argument("--folder-id", default=None, help="保存先フォルダの ID（既定: マイドライブ直下）")
    # 既定は相対パス。公開するスクリーンショットに C:\Users\... を写さないため。
    parser.add_argument(
        "--credentials", default="credentials.json", help="OAuth クライアントの JSON（既定: credentials.json）"
    )
    parser.add_argument(
        "--token", default="token.json", help="アクセストークンの保存先（既定: token.json）"
    )
    parser.add_argument(
        "--full-drive-scope",
        action="store_true",
        help="既にあるフォルダに入れる場合に指定する（drive スコープを要求し、同意を取り直す）",
    )
    return parser.parse_args(argv)


def _default_service_factory(args: argparse.Namespace):
    credentials = load_credentials(args.credentials, args.token, scopes_for(args))
    return build_service(credentials)


def main(argv: Sequence[str] | None = None, *, service_factory: Callable | None = None) -> int:
    args = parse_args(argv)
    factory = service_factory or _default_service_factory

    try:
        service = factory(args)
        uploaded = upload_file(
            service, args.path, name=args.name, folder_id=args.folder_id
        )
    except UploadError as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1

    print(format_result(uploaded))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
