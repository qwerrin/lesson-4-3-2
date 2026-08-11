"""作成したドキュメントを Docs API から読み返し、送ったテキストと突き合わせる。

create_doc のテストは偽の service を使うので、固定できるのは「呼び方」まで。
挿入位置 1 が本当に正しかったか、作成時に本文を送らなくてよかったか、日本語が
そのまま入るかは、実物を1回読まないと分からない。ここがその1回。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task2\\verify_doc.py <ドキュメントID> --text-file task2\\data\\sample.txt

読むだけで、ドキュメントは一切変更しない。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from googleapiclient.errors import HttpError

import create_doc

# 詳細表示で本文を出すときの上限。長文をそのまま出すと画面が流れて読めない。
PREVIEW_LIMIT = 40


class VerifyError(Exception):
    """利用者にそのまま見せられる失敗。"""


@dataclass(frozen=True)
class Check:
    label: str
    ok: bool
    detail: str = ""


# ---------------------------------------------------------------- 応答を読む


def _content(document: dict) -> list:
    """本文の構造要素を取り出す。

    無いときに空リストを返さない。「本文が空のドキュメント」と
    「本文を読めなかった」は別の話で、混ぜると読めなかったほうが一致に化ける。
    """
    body = document.get("body")
    if body is None:
        raise VerifyError("応答に body がありません。ドキュメントを読み取れていません")
    content = body.get("content")
    if content is None:
        raise VerifyError("応答に body.content がありません。ドキュメントを読み取れていません")
    return content


def extract_text(document: dict) -> str:
    """ドキュメント全体の本文を1つの文字列にする。

    段落の textRun だけを拾う。sectionBreak は本文を持たず、画像などの
    inlineObjectElement にも textRun が無い。

    表の中は辿らない。このプログラムは段落しか作らないので、表が入っていたら
    「本文が一致」が NG になる。素通りするより気づけるほうを選ぶ。
    """
    parts: list[str] = []
    for element in _content(document):
        paragraph = element.get("paragraph")
        if paragraph is None:
            continue
        for run in paragraph.get("elements", []):
            text_run = run.get("textRun")
            if text_run is None:
                continue
            # content が無い textRun は中身が空。ここは既定値で正しい。
            parts.append(text_run.get("content", ""))
    return "".join(parts)


def count_paragraphs(document: dict) -> int:
    """段落の数を数える。

    本文を平らな文字列にして比べるのとは別の角度。改行が「文字として入った」だけで
    段落に分かれていない、という壊れ方はこちらでしか見えない。
    """
    return sum(1 for element in _content(document) if "paragraph" in element)


def strip_document_trailing_newline(text: str) -> str | None:
    """ドキュメント末尾の改行を1つだけ外す。

    Docs は本文の最後の改行を消せない。空のドキュメントでも "\\n" が1つ残るので、
    送った文字列と比べるにはこれを外す。

    改行で終わっていなければ None を返す。あり得ない応答なので、
    「たまたま一致」に倒さず NG にする。
    """
    if not text.endswith("\n"):
        return None
    return text[:-1]


# ---------------------------------------------------------------- 照合


def _preview(text: str) -> str:
    shortened = text if len(text) <= PREVIEW_LIMIT else text[:PREVIEW_LIMIT] + "…"
    return shortened.replace("\n", "\\n")


def compare_with_expected(
    document: dict,
    *,
    expected_text: str,
    expected_title: str,
    expected_document_id: str,
) -> list[Check]:
    """読み返したドキュメントと、送ったはずの内容を項目ごとに突き合わせる。

    値が返ってこなかった項目は OK にしない。照合できなかったことと
    一致したことを同じ扱いにすると、確かめた気になるだけになる。
    """
    checks: list[Check] = []

    actual_id = document.get("documentId")
    checks.append(
        Check(
            "ドキュメントIDが一致",
            actual_id == expected_document_id,
            f"{actual_id or '(返らなかった)'} / {expected_document_id}",
        )
    )

    actual_title = document.get("title")
    checks.append(
        Check(
            "タイトルが一致",
            actual_title == expected_title,
            f"{actual_title or '(返らなかった)'} / {expected_title}",
        )
    )

    body_text = strip_document_trailing_newline(extract_text(document))
    checks.append(
        Check(
            "本文が一致",
            body_text is not None and body_text == expected_text,
            f"{_preview(body_text) if body_text is not None else '(末尾の改行が無く読めなかった)'}"
            f" / {_preview(expected_text)}",
        )
    )
    checks.append(
        Check(
            "文字数が一致",
            body_text is not None and len(body_text) == len(expected_text),
            f"{len(body_text) if body_text is not None else '(読めなかった)'} / {len(expected_text)}",
        )
    )

    actual_paragraphs = count_paragraphs(document)
    expected_paragraphs = expected_text.count("\n") + 1
    checks.append(
        Check(
            "段落数が一致",
            actual_paragraphs == expected_paragraphs,
            f"{actual_paragraphs} / {expected_paragraphs}",
        )
    )

    return checks


def all_ok(checks: Sequence[Check]) -> bool:
    # 空を真にしない。何も照合していないのに「全部一致」と言わせないため。
    return bool(checks) and all(check.ok for check in checks)


def format_checks(checks: Sequence[Check]) -> str:
    return "\n".join(
        f"{'OK ' if c.ok else 'NG '} {c.label}{('  ' + c.detail) if c.detail else ''}"
        for c in checks
    )


# ---------------------------------------------------------------- API を呼ぶ


def fetch_document(service, document_id: str) -> dict:
    """ドキュメントを読む。

    fields は指定しない。既定の応答に body も title も含まれる。
    タブ機能を使う場合は includeTabsContent が要るが、既定では先頭タブの内容が
    そのまま body に入るので、このプログラムの用途では触らない。
    """
    try:
        return service.documents().get(documentId=document_id).execute()
    except HttpError as error:
        status = error.resp.status
        raise VerifyError(
            f"[{status}] ドキュメントを読み取れませんでした（ID: {document_id}）"
        ) from error


# ---------------------------------------------------------------- 画面まわり


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="作成済みのドキュメントを読み返し、送ったテキストと突き合わせます。"
    )
    parser.add_argument("document_id", help="確認するドキュメントの ID（作成時に表示される）")
    parser.add_argument("--title", default=None, help="期待するタイトル（既定: create_doc と同じ規則）")
    parser.add_argument("--text", default=None, help="送ったはずの本文を直接指定する")
    parser.add_argument("--text-file", default=None, help="送ったはずの本文が入ったファイル")
    parser.add_argument("--credentials", default="credentials.json")
    parser.add_argument("--token", default="token.json")
    return parser.parse_args(argv)


def _default_service_factory(args: argparse.Namespace):
    credentials = create_doc.load_credentials(args.credentials, args.token)
    return create_doc.build_service(credentials)


def main(argv: Sequence[str] | None = None, *, service_factory: Callable | None = None) -> int:
    args = parse_args(argv)
    factory = service_factory or _default_service_factory

    try:
        # 期待値は create_doc と同じ関数で作る。送るときに LF へ直しているので、
        # 照合側で同じ正規化をかけないと CRLF のファイルが必ず食い違う。
        expected_text = create_doc.resolve_text(args.text, args.text_file)
        expected_title = create_doc.resolve_title(args.title, args.text_file)
        service = factory(args)
        document = fetch_document(service, args.document_id)
        checks = compare_with_expected(
            document,
            expected_text=expected_text,
            expected_title=expected_title,
            expected_document_id=args.document_id,
        )
    except (VerifyError, create_doc.DocError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1

    print(format_checks(checks))
    print(f"リンク: {create_doc.document_url(args.document_id)}")
    return 0 if all_ok(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
