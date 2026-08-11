"""Google ドキュメントを新規作成し、指定したテキストを挿入する。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task2\\create_doc.py --text "こんにちは" --title あいさつ
    .venv\\Scripts\\python.exe task2\\create_doc.py --text-file task2\\data\\sample.txt

初回は既定のブラウザが開いて Google の同意画面が出る。許可すると token.json が
できて、次からはブラウザが開かない。

課題1で作った token.json は drive.file しか持っていないので、この課題を最初に
動かすときは必ず同意を取り直すことになる。load_credentials がそれを検出する。

認証まわりは task1/drive_upload.py とほぼ同じ形だが、意図的に写している。
提出物は課題ごとに単独で読めるほうがよく、task1 は提出済みで触らないため。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Iterable, Sequence

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ドキュメントの作成と編集に必要な権限。読み取り専用（documents.readonly）では
# documents.create も batchUpdate もできない。
DEFAULT_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/documents",)

# 本文の先頭。インデックス 0 は本文の外（sectionBreak の位置）で、
# そこへ挿入しようとすると「段落の中ではない」として 400 で弾かれる。
BODY_START_INDEX = 1

DEFAULT_TITLE = "無題のドキュメント"

DOCUMENT_URL_TEMPLATE = "https://docs.google.com/document/d/{document_id}/edit"


class DocError(Exception):
    """利用者にそのまま見せられる失敗。想定外の例外とは区別する。"""


# ---------------------------------------------------------------- 送る前に決めること


def resolve_title(title: str | None, text_file: str | Path | None = None) -> str:
    """ドキュメントのタイトルを決める。

    指定があればそれ、無ければテキストファイル名、それも無ければ既定値。
    """
    if title is not None:
        trimmed = title.strip()
        if not trimmed:
            raise DocError("--title に空のタイトルは指定できません")
        return trimmed
    if text_file is not None:
        return Path(text_file).stem
    return DEFAULT_TITLE


def normalize_newlines(text: str) -> str:
    """改行を LF に揃える。

    Docs の改行は LF。CRLF のまま送ると本文に CR が余分な文字として残り、
    読み返したときに「送った文字列と違う」ことになる。
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text_file(path: str | Path) -> str:
    """テキストファイルを読む。

    utf-8-sig で読むのは、Windows のメモ帳が付ける BOM を落とすため。
    残すとドキュメントの1文字目が見えないゴミになる。
    """
    file_path = Path(path)
    if not file_path.exists():
        raise DocError(f"テキストファイルが見つかりません: {file_path}")
    if not file_path.is_file():
        raise DocError(f"ファイルではありません（フォルダは読めません）: {file_path}")
    try:
        return file_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise DocError(f"UTF-8 として読めませんでした: {file_path}") from error


def resolve_text(text: str | None = None, text_file: str | Path | None = None) -> str:
    """挿入する本文を1つに決める。

    --text と --text-file はどちらか一方。両方受け取ると、どちらが使われたかが
    実行画面から読み取れなくなる。
    """
    if text is not None and text_file is not None:
        raise DocError("--text と --text-file は同時に指定できません。どちらか一方にしてください")
    if text is None and text_file is None:
        raise DocError("挿入するテキストがありません。--text か --text-file を指定してください")

    raw = text if text is not None else read_text_file(text_file)  # type: ignore[arg-type]
    normalized = normalize_newlines(raw)

    # 空文字の insertText は Docs API が 400 で弾く。手前で止めて理由を出す。
    # 空白だけは通す（意味のある空白かもしれない）。
    if not normalized:
        source = "--text" if text is not None else f"ファイル（{text_file}）"
        raise DocError(f"挿入するテキストが空です: {source}")
    return normalized


def build_insert_requests(text: str) -> list[dict]:
    """batchUpdate に渡すリクエストを組み立てる。

    本文の先頭に1回だけ挿入する。複数回に分けるとインデックスが挿入のたびに
    ずれて、書いた順と並ぶ順が食い違う。
    """
    return [{"insertText": {"location": {"index": BODY_START_INDEX}, "text": text}}]


def document_url(document_id: str) -> str:
    return DOCUMENT_URL_TEMPLATE.format(document_id=document_id)


# ---------------------------------------------------------------- API を呼ぶ


def _api_message(error: HttpError) -> str:
    """HttpError の本文から Google が返した説明文だけを取り出す。"""
    try:
        payload = json.loads(error.content.decode("utf-8"))
        return str(payload["error"]["message"])
    except (ValueError, KeyError, AttributeError, UnicodeDecodeError):
        return str(error)


def _looks_like_api_disabled(detail: str) -> bool:
    lowered = detail.lower()
    return "has not been used in project" in lowered or "it is disabled" in lowered


def _translate_http_error(error: HttpError, document_id: str | None = None) -> DocError:
    status = error.resp.status
    detail = _api_message(error)

    if status == 403 and _looks_like_api_disabled(detail):
        return DocError(
            f"[{status}] Google Docs API がこのプロジェクトで有効になっていません。"
            "Google Cloud コンソールの「API とサービス」→「ライブラリ」で "
            "Google Docs API を有効にし、数分おいてから実行し直してください。"
            f" / API の応答: {detail}"
        )

    if status == 403:
        return DocError(
            f"[{status}] 権限が足りません。"
            "同意した権限に Docs API の documents スコープが含まれているか確認してください。"
            "token.json を消して同意を取り直すと直ることがあります。"
            f" / API の応答: {detail}"
        )

    if status == 404:
        target = f"（ID: {document_id}）" if document_id else ""
        return DocError(f"[{status}] ドキュメントが見つかりません{target} / API の応答: {detail}")

    if status == 400:
        return DocError(
            f"[{status}] リクエストの組み立てが正しくありません。"
            "挿入位置（インデックス）や本文の中身を確認してください。"
            f" / API の応答: {detail}"
        )

    return DocError(f"[{status}] Docs API の呼び出しに失敗しました / API の応答: {detail}")


def create_document(service, title: str) -> dict:
    """空のドキュメントを作り、API が返したドキュメント情報を返す。

    body には title しか入れない。documents.create は title 以外を無視する仕様で、
    本文を一緒に送っても反映されないまま成功が返る。挿入は batchUpdate で行う。
    """
    try:
        created = service.documents().create(body={"title": title}).execute()
    except HttpError as error:
        raise _translate_http_error(error) from error

    if not created.get("documentId"):
        raise DocError(
            "ドキュメントは作られたようですが、応答に documentId がありません。"
            f"応答: {created}"
        )
    return created


def insert_text(service, document_id: str, text: str) -> dict:
    """作成済みのドキュメントの先頭に本文を挿入する。"""
    body = {"requests": build_insert_requests(text)}
    try:
        return service.documents().batchUpdate(documentId=document_id, body=body).execute()
    except HttpError as error:
        raise _translate_http_error(error, document_id) from error


def create_document_with_text(service, title: str, text: str) -> dict:
    """作成 → 挿入をまとめて行い、画面に出す材料を返す。"""
    created = create_document(service, title)
    document_id = created["documentId"]

    try:
        insert_text(service, document_id, text)
    except DocError as error:
        # 作成だけ通って挿入で落ちると、空のドキュメントがドライブに残る。
        # ID を出さないと、どれを消せばいいか分からない。
        raise DocError(
            f"{error}\n"
            f"※ 空のドキュメントが作られたまま残っています（ID: {document_id}）。"
            f"不要なら削除してください: {document_url(document_id)}"
        ) from error

    return {
        "documentId": document_id,
        "title": created.get("title") or title,
        "url": document_url(document_id),
        # Python の文字数。Docs API のインデックスは UTF-16 単位なので、
        # 絵文字などサロゲートペアを含む場合はこの数と一致しない。表示用。
        "insertedLength": len(text),
    }


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
    except (ValueError, UnicodeDecodeError):
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
        # 課題1の token.json は drive.file しか持っていない。捨てて取り直す。
        credentials = None

    if credentials is not None:
        if credentials.valid:
            return credentials
        if credentials.expired and credentials.refresh_token:
            refresher(credentials)
            _save_token(token_path, credentials)
            return credentials

    if not credentials_path.exists():
        raise DocError(
            f"credentials.json が見つかりません: {credentials_path}\n"
            "Google Cloud コンソールで OAuth 2.0 クライアント ID（デスクトップアプリ）を作り、"
            "ダウンロードした JSON をこのパスに置いてください。手順は README を参照。"
        )

    flow = flow_factory(str(credentials_path), wanted)
    credentials = flow.run_local_server(port=0)
    _save_token(token_path, credentials)
    return credentials


def build_service(credentials: Credentials):
    return build("docs", "v1", credentials=credentials)


# ---------------------------------------------------------------- 画面まわり


def format_result(created: dict) -> str:
    lines = [
        "ドキュメントを作成しました",
        f"  タイトル      : {created.get('title', '(不明)')}",
        f"  ドキュメントID: {created.get('documentId', '(不明)')}",
    ]
    length = created.get("insertedLength")
    if length is not None:
        lines.append(f"  挿入した文字数: {length}")
    url = created.get("url")
    if url:
        lines.append(f"  リンク        : {url}")
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Google ドキュメントを新規作成し、指定したテキストを挿入します。"
    )
    parser.add_argument("--title", default=None, help="ドキュメントのタイトル（既定: ファイル名、または無題のドキュメント）")
    parser.add_argument("--text", default=None, help="挿入する本文を直接指定する")
    parser.add_argument("--text-file", default=None, help="挿入する本文をファイルから読む（UTF-8）")
    # 既定は相対パス。公開するスクリーンショットに C:\Users\... を写さないため。
    parser.add_argument(
        "--credentials", default="credentials.json", help="OAuth クライアントの JSON（既定: credentials.json）"
    )
    parser.add_argument(
        "--token", default="token.json", help="アクセストークンの保存先（既定: token.json）"
    )
    return parser.parse_args(argv)


def _default_service_factory(args: argparse.Namespace):
    credentials = load_credentials(args.credentials, args.token)
    return build_service(credentials)


def main(argv: Sequence[str] | None = None, *, service_factory: Callable | None = None) -> int:
    args = parse_args(argv)
    factory = service_factory or _default_service_factory

    try:
        # テキストを先に確定させる。API を呼んでから空だと分かると、
        # 中身の無いドキュメントだけがドライブに残る。
        text = resolve_text(args.text, args.text_file)
        title = resolve_title(args.title, args.text_file)
        service = factory(args)
        created = create_document_with_text(service, title, text)
    except DocError as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1

    print(format_result(created))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
