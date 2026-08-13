"""task3 と common のコードを1か所ずつ壊して、テストが落ちることを確認する。

通っているテストの数は、守られている範囲を意味しない。
落ちなかった行は「テストが見ていない場所」なので、そこだけ手当てする。

課題2のものとの違いは、**共有モジュール（common/google_auth.py）も対象に入れた**こと。
共有した以上、ここが壊れれば全部の課題が壊れる。テストが無いまま共有すると、
壊しても誰も気づかない状態になる。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task3\\tools\\mutate.py

**このスクリプトはソースファイルを一時的に書き換える。**
1件ごとに元へ戻し、途中で中断された場合も atexit で戻す。強制終了で壊れたまま
残ったときは次で戻せる::

    git checkout -- task3/create_meet.py task3/verify_meet.py common/google_auth.py
"""

from __future__ import annotations

import atexit
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
TEST_DIRS = [ROOT / "task3" / "tests", ROOT / "common" / "tests"]

CREATE = ROOT / "task3" / "create_meet.py"
VERIFY = ROOT / "task3" / "verify_meet.py"
AUTH = ROOT / "common" / "google_auth.py"

TARGETS = (CREATE, VERIFY, AUTH)

# (対象ファイル, 壊した内容, 置換前, 置換後)
MUTATIONS: list[tuple[Path, str, str, str]] = [
    # ------------------------------------------------------------ create_meet.py
    (
        CREATE,
        "読み取り専用スコープを要求する（作成できない）",
        'SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/meetings.space.created",)',
        'SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/meetings.space.readonly",)',
    ),
    (
        CREATE,
        "参加リンクの基底 URL を変える",
        'MEET_BASE_URL = "https://meet.google.com/"',
        'MEET_BASE_URL = "https://meet.google.com/call/"',
    ),
    (
        CREATE,
        "未指定を意味する値をアクセス種別として通す",
        'ACCESS_TYPES: tuple[str, ...] = ("OPEN", "TRUSTED", "RESTRICTED")',
        'ACCESS_TYPES: tuple[str, ...] = ("OPEN", "TRUSTED", "RESTRICTED", "ACCESS_TYPE_UNSPECIFIED")',
    ),
    (
        CREATE,
        "アクセス種別の検証をやめる",
        "    if normalized not in ACCESS_TYPES:",
        "    if False:",
    ),
    (
        CREATE,
        "アクセス種別を大文字に揃えない",
        "    normalized = value.strip().upper()",
        "    normalized = value.strip()",
    ),
    (
        CREATE,
        "アクセス種別の前後の空白を落とさない",
        "    normalized = value.strip().upper()\n",
        "    normalized = value.upper()\n",
    ),
    (
        CREATE,
        "未指定でも config を送る",
        "    if access_type is None:\n        return {}",
        "    if False:\n        return {}",
    ),
    (
        CREATE,
        "config に accessType 以外も混ぜる",
        'return {"config": {"accessType": access_type}}',
        'return {"config": {"accessType": access_type, "moderation": "OFF"}}',
    ),
    (
        CREATE,
        "会議コードが空でもリンクを組む",
        "    if not meeting_code:",
        "    if False:",
    ),
    (
        CREATE,
        "スペース名が返らなくても成功にする",
        '    _require(space, "name", "スペース名")\n',
        "",
    ),
    (
        CREATE,
        "参加リンクが返らなくても成功にする",
        '    _require(space, "meetingUri", "参加リンク")\n',
        "",
    ),
    (
        CREATE,
        "会議コードが返らなくても成功にする",
        '    _require(space, "meetingCode", "会議コード")\n',
        "",
    ),
    (
        CREATE,
        "空文字を「返ってきた」とみなす",
        "    value = space.get(key)\n    if not value:",
        "    value = space.get(key)\n    if value is None:",
    ),
    (
        CREATE,
        "API 未有効化の案内を出さない",
        "    if status == 403 and _looks_like_api_disabled(detail):",
        "    if False:",
    ),
    (
        CREATE,
        "権限不足の案内を出さない",
        "    if status == 403 and _looks_like_scope_problem(detail):",
        "    if False:",
    ),
    (
        CREATE,
        "403 の原因からアカウントの種類を落とす",
        "            \"  3. 使っている Google アカウントの種類が Meet API に対応していない\\n\"\n",
        "",
    ),
    (
        CREATE,
        "404 の案内を出さない",
        "    if status == 404:",
        "    if False:",
    ),
    (
        CREATE,
        "エラーからステータスコードを落とす",
        'return MeetError(f"Meet API の呼び出しに失敗しました（{status}）。\\n応答: {detail}")',
        'return MeetError(f"Meet API の呼び出しに失敗しました。\\n応答: {detail}")',
    ),
    (
        CREATE,
        "main が結果を印字しない",
        "    print(format_result(space))\n",
        "",
    ),
    (
        CREATE,
        "失敗しても 0 を返す",
        "    except (MeetError, google_auth.AuthError) as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 1",
        "    except (MeetError, google_auth.AuthError) as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 0",
    ),
    (
        CREATE,
        "既定の資格情報パスを絶対パスにする",
        'parser.add_argument("--credentials", default="credentials.json"',
        'parser.add_argument("--credentials", default="C:/Users/sprin/credentials.json"',
    ),
    (
        CREATE,
        "既定のトークンパスを絶対パスにする",
        'parser.add_argument("--token", default="token.json", help="トークンの保存先")\n'
        "    return parser.parse_args(argv)\n\n\ndef _default_service_factory",
        'parser.add_argument("--token", default="C:/Users/sprin/token.json", help="トークンの保存先")\n'
        "    return parser.parse_args(argv)\n\n\ndef _default_service_factory",
    ),
    (
        CREATE,
        "送る内容を確定する前に API へ繋ぐ",
        "        body = build_space_body(resolve_access_type(args.access_type))\n"
        "    except MeetError as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 1\n"
        "\n"
        "    try:\n"
        "        service = factory(args)\n",
        "        service = factory(args)\n"
        "        body = build_space_body(resolve_access_type(args.access_type))\n"
        "    except MeetError as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 1\n"
        "\n"
        "    try:\n",
    ),
    # ------------------------------------------------------------ verify_meet.py
    (
        VERIFY,
        "会議コードの形を何でも通す",
        'MEETING_CODE_PATTERN = re.compile(r"^[a-z]+-[a-z]+-[a-z]+$")',
        'MEETING_CODE_PATTERN = re.compile(r".*")',
    ),
    (
        VERIFY,
        "会議コードの形を部分一致で見る",
        "    return MEETING_CODE_PATTERN.fullmatch(code) is not None",
        "    return MEETING_CODE_PATTERN.search(code) is not None",
    ),
    (
        VERIFY,
        "アクセス種別が返らなければ一致扱いにする",
        "                actual_access is not None and actual_access == expected_access_type,",
        "                actual_access is None or actual_access == expected_access_type,",
    ),
    (
        VERIFY,
        "スペース名が返らなければ一致扱いにする",
        "            actual_name is not None and actual_name == expected_name,",
        "            actual_name is None or actual_name == expected_name,",
    ),
    (
        VERIFY,
        "参加リンクが返らなければ一致扱いにする",
        "            actual_uri is not None and actual_uri == expected_uri,",
        "            actual_uri is None or actual_uri == expected_uri,",
    ),
    (
        VERIFY,
        "リンクとコードの整合を自分自身と比べる（トートロジー）",
        '        built = f"{create_meet.MEET_BASE_URL}{actual_code}"',
        "        built = actual_uri",
    ),
    (
        VERIFY,
        "会議が始まっていても OK にする",
        '            "会議はまだ始まっていない",\n            not active,',
        '            "会議はまだ始まっていない",\n            True,',
    ),
    (
        VERIFY,
        "config が dict でなくても読もうとする",
        "    if not isinstance(config, dict):\n        return None",
        "    if False:\n        return None",
    ),
    (
        VERIFY,
        "all_ok が常に True",
        "    return all(check.ok for check in checks)",
        "    return True",
    ),
    (
        VERIFY,
        "照合ゼロ件でも「全部一致」にする",
        "    if not checks:\n        return False",
        "    if False:\n        return False",
    ),
    (
        VERIFY,
        "format_checks が全部 OK と印字する",
        '        mark = "OK" if check.ok else "NG"',
        '        mark = "OK"',
    ),
    (
        VERIFY,
        "format_checks が詳細を落とす",
        "        if check.detail:\n            line += f\"  {check.detail}\"\n",
        "",
    ),
    (
        VERIFY,
        "食い違っても 0 を返す",
        "    return 0 if all_ok(checks) else 1",
        "    return 0",
    ),
    (
        VERIFY,
        "エラーにスペース名を残さない",
        'f"スペースが見つかりません（{status}）: {name}\\n"',
        'f"スペースが見つかりません（{status}）\\n"',
    ),
    (
        VERIFY,
        "読み取り失敗からステータスコードを落とす",
        'f"スペースを読み取れませんでした（{status}）: {name}\\n応答: {detail}"',
        'f"スペースを読み取れませんでした: {name}\\n応答: {detail}"',
    ),
    (
        VERIFY,
        "期待値を確定する前に API へ繋ぐ",
        "        expected_access_type = create_meet.resolve_access_type(args.access_type)\n"
        "    except create_meet.MeetError as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 1\n"
        "\n"
        "    try:\n"
        "        service = factory(args)\n",
        "        service = factory(args)\n"
        "        expected_access_type = create_meet.resolve_access_type(args.access_type)\n"
        "    except create_meet.MeetError as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 1\n"
        "\n"
        "    try:\n",
    ),
    # ------------------------------------------------------------ common/google_auth.py
    (
        AUTH,
        "保存済みトークンの権限を確認しない",
        "    if credentials is not None and not credentials.has_scopes(wanted):",
        "    if False:",
    ),
    (
        AUTH,
        "スコープが空でも進む",
        "    if not wanted:",
        "    if False:",
    ),
    (
        AUTH,
        "credentials.json が無くても進む",
        "    if not credentials_path.exists():",
        "    if False:",
    ),
    (
        AUTH,
        "リフレッシュしたトークンを保存し直さない",
        "            refresher(credentials)\n            save_token(token_path, credentials)",
        "            refresher(credentials)",
    ),
    (
        AUTH,
        "取り直したトークンを保存しない",
        "    credentials = flow.run_local_server(port=0)\n    save_token(token_path, credentials)",
        "    credentials = flow.run_local_server(port=0)",
    ),
    (
        AUTH,
        "flow に要求スコープを渡さない",
        "    flow = flow_factory(str(credentials_path), wanted)",
        "    flow = flow_factory(str(credentials_path), [])",
    ),
    (
        AUTH,
        "壊れたトークンを読めたことにする",
        "    except (ValueError, UnicodeDecodeError):\n        return None",
        "    except ():\n        return None",
    ),
    (
        AUTH,
        "保存先の親ディレクトリを作らない",
        "    token_path.parent.mkdir(parents=True, exist_ok=True)\n",
        "",
    ),
]


def read_source(path: Path) -> str:
    """改行コードを変換せずに読む。

    既定の text mode は CRLF を LF に読み替え、書き戻すとき OS の既定に直す。
    素直に read/write すると、書き換えていないファイルの改行コードだけが静かに変わる。
    """
    return path.read_text(encoding="utf-8", newline="")


def write_source(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def _install_restore_guard() -> None:
    """中断されても元へ戻せるように、開始時の中身を覚えておく。"""
    originals = {path: read_source(path) for path in TARGETS}

    def restore() -> None:
        for path, text in originals.items():
            if read_source(path) != text:
                write_source(path, text)
                print(f"! 中断されたため {path.name} を元に戻した")

    atexit.register(restore)


def run_tests() -> int:
    """テストを走らせて、失敗とエラーの合計件数を返す。"""
    proc = subprocess.run(
        [str(PYTHON), "-m", "pytest", *[str(d) for d in TEST_DIRS], "-q", "--no-header",
         "-p", "no:cacheprovider"],
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
    print("-" * 82)

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

    print("-" * 82)
    if survivors:
        print(f"素通りが {len(survivors)} 件:")
        for line in survivors:
            print(f"  - {line}")
        return 1

    print(f"素通りゼロ（{len(MUTATIONS)} か所）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
