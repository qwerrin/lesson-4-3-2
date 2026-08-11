"""task2 のコードを1か所ずつ壊して、テストが落ちることを確認する。

通っているテストの数は、守られている範囲を意味しない。
落ちなかった行は「テストが見ていない場所」なので、そこだけ手当てする。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task2\\tools\\mutate.py

**このスクリプトはソースファイルを一時的に書き換える。**
1件ごとに元へ戻し、途中で中断された場合も atexit で戻す。それでも強制終了
（タスクマネージャなど）で止めると壊れたまま残るので、実行前に作業ツリーを
きれいにしておくこと。異常終了したら次で戻せる::

    git checkout -- task2/create_doc.py task2/verify_doc.py
"""

from __future__ import annotations

import atexit
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
TESTS = ROOT / "task2" / "tests"

CREATE = ROOT / "task2" / "create_doc.py"
VERIFY = ROOT / "task2" / "verify_doc.py"

# 挿入の失敗を包み直しているところ。2通りの壊しかたで使うのでまとめておく。
INSERT_GUARD = """    try:
        insert_text(service, document_id, text)
    except DocError as error:
        # 作成だけ通って挿入で落ちると、空のドキュメントがドライブに残る。
        # ID を出さないと、どれを消せばいいか分からない。
        raise DocError(
            f"{error}\\n"
            f"※ 空のドキュメントが作られたまま残っています（ID: {document_id}）。"
            f"不要なら削除してください: {document_url(document_id)}"
        ) from error
"""

# (対象ファイル, 壊した内容, 置換前, 置換後)
MUTATIONS: list[tuple[Path, str, str, str]] = [
    # ------------------------------------------------------------ create_doc.py
    (CREATE, "挿入位置を 0 にする", "BODY_START_INDEX = 1", "BODY_START_INDEX = 0"),
    (
        CREATE,
        "作成時に本文も一緒に送る",
        'service.documents().create(body={"title": title}).execute()',
        'service.documents().create(body={"title": title, "body": {}}).execute()',
    ),
    (
        CREATE,
        "documentId が返らなくても成功にする",
        'if not created.get("documentId"):',
        "if False:",
    ),
    (
        CREATE,
        "改行を LF に直さない",
        'return text.replace("\\r\\n", "\\n").replace("\\r", "\\n")',
        "return text",
    ),
    (CREATE, "BOM を落とさない", 'encoding="utf-8-sig"', 'encoding="utf-8"'),
    (CREATE, "空のテキストを通す", "if not normalized:", "if False:"),
    (
        CREATE,
        "--text と --text-file の同時指定を通す",
        "if text is not None and text_file is not None:",
        "if False:",
    ),
    (CREATE, "タイトルの前後の空白を落とさない", "trimmed = title.strip()", "trimmed = title"),
    (
        CREATE,
        "タイトル未指定でファイル名を使わない",
        "return Path(text_file).stem",
        "return DEFAULT_TITLE",
    ),
    (
        CREATE,
        "保存済みトークンの権限を確認しない",
        "if credentials is not None and not credentials.has_scopes(wanted):",
        "if credentials is not None and False:",
    ),
    (
        CREATE,
        "リフレッシュしたトークンを保存し直さない",
        "            refresher(credentials)\n            _save_token(token_path, credentials)",
        "            refresher(credentials)",
    ),
    (CREATE, "main が結果を印字しない", "    print(format_result(created))", "    pass"),
    (
        CREATE,
        "失敗しても 0 を返す",
        '        print(f"エラー: {error}", file=sys.stderr)\n        return 1',
        '        print(f"エラー: {error}", file=sys.stderr)\n        return 0',
    ),
    (
        CREATE,
        "既定の資格情報パスを絶対パスにする",
        '"--credentials", default="credentials.json"',
        '"--credentials", default=r"C:\\Users\\example\\credentials.json"',
    ),
    (
        CREATE,
        "テキストを確定する前に API へ繋ぐ",
        "        text = resolve_text(args.text, args.text_file)\n"
        "        title = resolve_title(args.title, args.text_file)\n"
        "        service = factory(args)",
        "        service = factory(args)\n"
        "        text = resolve_text(args.text, args.text_file)\n"
        "        title = resolve_title(args.title, args.text_file)",
    ),
    (
        CREATE,
        "API 未有効化の案内を権限不足と同じ扱いにする",
        "if status == 403 and _looks_like_api_disabled(detail):",
        "if False:",
    ),
    (
        CREATE,
        "エラーからステータスコードを落とす",
        'return DocError(f"[{status}] Docs API の呼び出しに失敗しました / API の応答: {detail}")',
        'return DocError(f"Docs API の呼び出しに失敗しました / API の応答: {detail}")',
    ),
    (
        CREATE,
        "batchUpdate に別のドキュメント ID を渡す",
        "service.documents().batchUpdate(documentId=document_id, body=body).execute()",
        'service.documents().batchUpdate(documentId="FIXED", body=body).execute()',
    ),
    (CREATE, "作成だけして挿入しない", INSERT_GUARD, ""),
    (
        CREATE,
        "挿入に失敗しても残ったドキュメントの ID を伝えない",
        INSERT_GUARD,
        "    insert_text(service, document_id, text)\n",
    ),
    (
        CREATE,
        "挿入するテキストを空にする",
        '{"insertText": {"location": {"index": BODY_START_INDEX}, "text": text}}',
        '{"insertText": {"location": {"index": BODY_START_INDEX}, "text": ""}}',
    ),
    (
        CREATE,
        "末尾追記（endOfSegmentLocation）に変える",
        '{"insertText": {"location": {"index": BODY_START_INDEX}, "text": text}}',
        '{"insertText": {"endOfSegmentLocation": {}, "text": text}}',
    ),
    (
        CREATE,
        "リンクの組み立てを変える",
        'DOCUMENT_URL_TEMPLATE = "https://docs.google.com/document/d/{document_id}/edit"',
        'DOCUMENT_URL_TEMPLATE = "https://docs.google.com/{document_id}"',
    ),
    (CREATE, "テキストファイルの実在を確認しない", "    if not file_path.exists():", "    if False:"),
    (CREATE, "フォルダを渡されても読もうとする", "    if not file_path.is_file():", "    if False:"),
    (
        CREATE,
        "読み取り専用スコープを要求する",
        'DEFAULT_SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/documents",)',
        'DEFAULT_SCOPES: tuple[str, ...] = '
        '("https://www.googleapis.com/auth/documents.readonly",)',
    ),
    (
        CREATE,
        "flow に要求スコープを渡さない",
        "flow = flow_factory(str(credentials_path), wanted)",
        "flow = flow_factory(str(credentials_path), [])",
    ),
    (
        CREATE,
        "credentials.json が無くても進む",
        "    if not credentials_path.exists():",
        "    if False:",
    ),
    # ------------------------------------------------------------ verify_doc.py
    (
        VERIFY,
        "末尾に改行が無くても一致扱いにする",
        '    if not text.endswith("\\n"):\n        return None\n    return text[:-1]',
        '    return text[:-1] if text.endswith("\\n") else text',
    ),
    (
        VERIFY,
        "body が無ければ空の本文として扱う",
        '        raise VerifyError("応答に body がありません。ドキュメントを読み取れていません")',
        "        return []",
    ),
    (
        VERIFY,
        "content が無ければ空の本文として扱う",
        '        raise VerifyError('
        '"応答に body.content がありません。ドキュメントを読み取れていません")',
        "        return []",
    ),
    (
        VERIFY,
        "タイトルが返らなければ一致扱いにする",
        '    actual_title = document.get("title")',
        '    actual_title = document.get("title", expected_title)',
    ),
    (
        VERIFY,
        "ドキュメント ID が返らなければ一致扱いにする",
        '    actual_id = document.get("documentId")',
        '    actual_id = document.get("documentId", expected_document_id)',
    ),
    (
        VERIFY,
        "本文を自分自身と比べる（トートロジー）",
        "body_text is not None and body_text == expected_text",
        "body_text is not None and body_text == body_text",
    ),
    (
        VERIFY,
        "文字数の照合をやめる",
        "body_text is not None and len(body_text) == len(expected_text)",
        "True",
    ),
    (
        VERIFY,
        "段落数の照合をやめる",
        "            actual_paragraphs == expected_paragraphs,",
        "            True,",
    ),
    (
        VERIFY,
        "期待する段落数を数え違える",
        'expected_paragraphs = expected_text.count("\\n") + 1',
        'expected_paragraphs = expected_text.count("\\n")',
    ),
    (
        VERIFY,
        "all_ok が常に True",
        "    return bool(checks) and all(check.ok for check in checks)",
        "    return True",
    ),
    (
        VERIFY,
        "照合ゼロ件でも「全部一致」にする",
        "    return bool(checks) and all(check.ok for check in checks)",
        "    return all(check.ok for check in checks)",
    ),
    (
        VERIFY,
        "format_checks が全部 OK と印字する",
        "f\"{'OK ' if c.ok else 'NG '} {c.label}",
        "f\"{'OK '} {c.label}",
    ),
    (VERIFY, "食い違っても 0 を返す", "    return 0 if all_ok(checks) else 1", "    return 0"),
    (
        VERIFY,
        "sectionBreak も段落として数える",
        '    return sum(1 for element in _content(document) if "paragraph" in element)',
        "    return sum(1 for element in _content(document))",
    ),
    (
        VERIFY,
        "段落の最初の textRun しか読まない",
        'for run in paragraph.get("elements", []):',
        'for run in paragraph.get("elements", [])[:1]:',
    ),
    (
        VERIFY,
        "エラーにドキュメント ID を残さない",
        'f"[{status}] ドキュメントを読み取れませんでした（ID: {document_id}）"',
        'f"[{status}] ドキュメントを読み取れませんでした"',
    ),
    (
        VERIFY,
        "タイトルの既定を create_doc と別の規則にする",
        "expected_title = create_doc.resolve_title(args.title, args.text_file)",
        "expected_title = args.title or create_doc.DEFAULT_TITLE",
    ),
    (
        VERIFY,
        "期待値の改行を正規化しない",
        "expected_text = create_doc.resolve_text(args.text, args.text_file)",
        "expected_text = args.text if args.text is not None else "
        'Path(args.text_file).read_text(encoding="utf-8")',
    ),
    (
        VERIFY,
        "テキストを確定する前に API へ繋ぐ",
        "        expected_text = create_doc.resolve_text(args.text, args.text_file)\n"
        "        expected_title = create_doc.resolve_title(args.title, args.text_file)\n"
        "        service = factory(args)",
        "        service = factory(args)\n"
        "        expected_text = create_doc.resolve_text(args.text, args.text_file)\n"
        "        expected_title = create_doc.resolve_title(args.title, args.text_file)",
    ),
]


def read_source(path: Path) -> str:
    """改行コードを変換せずに読む。

    既定の text mode は CRLF を LF に読み替え、書き戻すとき OS の既定
    （Windows なら CRLF）に直す。素直に read/write すると、書き換えていない
    ファイルの改行コードだけが静かに入れ替わる。newline="" で素通しにする。
    """
    return path.read_text(encoding="utf-8", newline="")


def write_source(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def _install_restore_guard() -> None:
    """中断されても元へ戻せるように、開始時の中身を覚えておく。"""
    originals = {path: read_source(path) for path in (CREATE, VERIFY)}

    def restore() -> None:
        for path, text in originals.items():
            if read_source(path) != text:
                write_source(path, text)
                print(f"! 中断されたため {path.name} を元に戻した")

    atexit.register(restore)


def run_tests() -> int:
    """テストを走らせて、失敗とエラーの合計件数を返す。"""
    proc = subprocess.run(
        [str(PYTHON), "-m", "pytest", str(TESTS), "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    failed = re.search(r"(\d+) failed", tail)
    errors = re.search(r"(\d+) error", tail)
    return (int(failed.group(1)) if failed else 0) + (int(errors.group(1)) if errors else 0)


def main() -> int:
    _install_restore_guard()

    if run_tests():
        print("! 壊す前からテストが落ちている。先にそちらを直すこと")
        return 2

    survivors: list[str] = []
    print(f"{'#':>3}  {'落ちた件数':>10}  対象  壊した内容")
    print("-" * 78)

    for index, (path, description, old, new) in enumerate(MUTATIONS, start=1):
        original = read_source(path)
        occurrences = original.count(old)
        if occurrences != 1:
            # 置換できないミューテーションは「守られている証拠」にならない。
            # 素通りと同じ扱いにして必ず目に入れる。
            print(f"{index:>3}  {'置換不能':>10}  {path.name}  {description}（一致 {occurrences} 件）")
            survivors.append(f"{index}. {description}（置換できなかった）")
            continue

        write_source(path, original.replace(old, new, 1))
        try:
            caught = run_tests()
        finally:
            write_source(path, original)

        print(f"{index:>3}  {caught:>10}  {path.name}  {description}{'' if caught else '  ← 素通り'}")
        if not caught:
            survivors.append(f"{index}. {description}")

    print("-" * 78)
    if survivors:
        print(f"素通りが {len(survivors)} 件:")
        for line in survivors:
            print(f"  - {line}")
        return 1

    print(f"素通りゼロ（{len(MUTATIONS)} か所）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
