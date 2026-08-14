"""task4 と common/zoom_auth.py を1か所ずつ壊して、テストが落ちることを確認する。

通っているテストの数は、守られている範囲を意味しない。
落ちなかった行は「テストが見ていない場所」なので、そこだけ手当てする。

壊しかたを足すのは**コードを書いた直後**。まとめて最後にやると穴が出て、直後にやると出ない。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task4\\tools\\mutate.py

**このスクリプトはソースファイルを一時的に書き換える。**
1件ごとに元へ戻し、Ctrl-C など通常の中断なら atexit でも戻す。

ただし **atexit は強制終了では走らない**。2026-08-14 に実際にそれで
verify_meeting.py が `if False:` のまま残り、直後の pytest が1件落ちた。
このとき `git checkout` は効かない——**未コミットの新規ファイルは checkout の
対象にならない**ので、git 管理下にある前提の復旧手順は成立しない。

そこで開始時に `.mutate_backup/` へ控えを取り、次回の起動時に必ず突き合わせて
戻す。復旧を「人が思い出して打つコマンド」ではなく、道具側の責任にしている。
"""

from __future__ import annotations

import atexit
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

AUTH = ROOT / "common" / "zoom_auth.py"
CREATE = ROOT / "task4" / "create_meeting.py"
VERIFY = ROOT / "task4" / "verify_meeting.py"

TARGETS = (AUTH, CREATE, VERIFY)

TEST_DIRS = [d for d in (ROOT / "task4" / "tests", ROOT / "common" / "tests") if d.is_dir()]

# 強制終了に備えた控えの置き場。正常終了のたびに空になる。
BACKUP_DIR = Path(__file__).resolve().parent / ".mutate_backup"

# (対象ファイル, 壊した内容, 置換前, 置換後)
MUTATIONS: list[tuple[Path, str, str, str]] = [
    # ------------------------------------------------- read_credentials
    (
        AUTH,
        "値の前後の空白を落とさない",
        '        value = (env.get(name) or "").strip()',
        '        value = env.get(name) or ""',
    ),
    (
        AUTH,
        "空文字や空白だけを「設定済み」とみなす",
        "        if value:\n            values[name] = value",
        "        if value is not None:\n            values[name] = value",
    ),
    (
        AUTH,
        "欠けている変数を1つ目しか報告しない",
        '            "Zoom の資格情報が設定されていません: " + " / ".join(missing) + "\\n"',
        '            "Zoom の資格情報が設定されていません: " + missing[0] + "\\n"',
    ),
    (
        AUTH,
        "エラーに環境変数の中身ごと載せる（シークレット漏れ）",
        '            "Zoom の資格情報が設定されていません: " + " / ".join(missing) + "\\n"\n',
        '            "Zoom の資格情報が設定されていません: " + str(dict(env)) + "\\n"\n',
    ),
    (
        AUTH,
        "足りない資格情報があっても進む",
        "    if missing:\n        # シークレットの値そのものは絶対に載せない。",
        "    if False:\n        # シークレットの値そのものは絶対に載せない。",
    ),
    # ------------------------------------------------- basic_auth_header
    (
        AUTH,
        "コロン以外で連結する",
        '    raw = f"{client_id}:{client_secret}".encode("utf-8")',
        '    raw = f"{client_id}/{client_secret}".encode("utf-8")',
    ),
    (
        AUTH,
        "76文字で折り返す base64 を使う",
        '    return "Basic " + base64.b64encode(raw).decode("ascii")',
        '    return "Basic " + base64.encodebytes(raw).decode("ascii")',
    ),
    (
        AUTH,
        "Basic の接頭辞を付けない",
        '    return "Basic " + base64.b64encode(raw).decode("ascii")',
        '    return base64.b64encode(raw).decode("ascii")',
    ),
    # ------------------------------------------------- fetch_access_token
    (
        AUTH,
        "トークンの取得先を別ホストにする",
        'TOKEN_URL = "https://zoom.us/oauth/token"',
        'TOKEN_URL = "https://api.zoom.us/oauth/token"',
    ),
    (
        AUTH,
        "grant_type を client_credentials にする",
        '            "grant_type": "account_credentials",',
        '            "grant_type": "client_credentials",',
    ),
    (
        AUTH,
        "account_id を送らない",
        '            "account_id": credentials.account_id,\n',
        "",
    ),
    (
        AUTH,
        "Basic 認証ヘッダを付けない",
        '        headers={\n            "Authorization": basic_auth_header(\n'
        "                credentials.client_id, credentials.client_secret\n"
        "            )\n        },",
        "        headers={},",
    ),
    (
        AUTH,
        "タイムアウトを渡さない",
        "        timeout=timeout,\n",
        "",
    ),
    (
        AUTH,
        "タイムアウトを0にする",
        "TIMEOUT_SECONDS = 30",
        "TIMEOUT_SECONDS = 0",
    ),
    (
        AUTH,
        "HTTP エラーを見ない",
        "    if not response.ok:",
        "    if False:",
    ),
    (
        AUTH,
        "エラーからステータスコードを落とす",
        '            f"Zoom のトークン取得に失敗しました（HTTP {response.status_code}）: "',
        '            f"Zoom のトークン取得に失敗しました: "',
    ),
    (
        AUTH,
        "未有効化の案内を出さない",
        '    if "disabled" not in detail.lower():',
        "    if True:",
    ),
    (
        AUTH,
        "原因を問わず未有効化の案内を出す",
        '    if "disabled" not in detail.lower():\n        return ""',
        '    if False:\n        return ""',
    ),
    (
        AUTH,
        "相手が言っている理由を載せない",
        '        for key in ("reason", "message", "error_description", "error"):',
        "        for key in ():",
    ),
    (
        AUTH,
        "JSON でない成功応答を素通りさせる",
        "    if payload is None:",
        "    if False:",
    ),
    (
        AUTH,
        "JSON の解析失敗を握りつぶさない（生の例外が外に出る）",
        "    except ValueError:\n        return None",
        "    except ():\n        return None",
    ),
    (
        AUTH,
        "dict でない JSON も payload として扱う",
        "    return payload if isinstance(payload, dict) else None",
        "    return payload",
    ),
    (
        AUTH,
        "access_token が空文字でも成功にする",
        '    value = str(payload.get("access_token") or "").strip()\n    if not value:',
        '    value = str(payload.get("access_token") or "").strip()\n    if value is None:',
    ),
    (
        AUTH,
        "access_token が無ければ既定値で埋める",
        '    value = str(payload.get("access_token") or "").strip()',
        '    value = str(payload.get("access_token") or "DUMMY").strip()',
    ),
    (
        AUTH,
        "スコープをカンマで割る（空白区切りを割れない）",
        '    scopes = tuple(str(payload.get("scope") or "").split())',
        '    scopes = tuple(str(payload.get("scope") or "").split(","))',
    ),
    (
        AUTH,
        "有効期限を読まず 0 で固定する",
        '        expires_in=int(payload.get("expires_in") or 0),',
        "        expires_in=0,",
    ),
    (
        AUTH,
        "api_url を応答から読まず既定値で固定する",
        '        api_url=str(payload.get("api_url") or DEFAULT_API_URL),',
        "        api_url=DEFAULT_API_URL,",
    ),
    # ------------------------------------------------- require_scopes
    (
        AUTH,
        "要求スコープが空でも通す",
        "    if not wanted:",
        "    if False:",
    ),
    (
        AUTH,
        "権限が足りなくても通す",
        "    missing = [scope for scope in wanted if scope not in granted]\n    if missing:",
        "    missing = [scope for scope in wanted if scope not in granted]\n    if False:",
    ),
    (
        AUTH,
        "足りている権限まで名指しする",
        "    missing = [scope for scope in wanted if scope not in granted]",
        "    missing = list(wanted)",
    ),
    (
        AUTH,
        "権限の判定を部分一致にする",
        "    missing = [scope for scope in wanted if scope not in granted]",
        "    missing = [scope for scope in wanted if not any(scope in g for g in granted)]",
    ),
    # ------------------------------------------------- create_meeting.py
    (
        CREATE,
        "読み取りだけのスコープを要求する（作成できない）",
        'SCOPES: tuple[str, ...] = ("meeting:write:meeting:admin",)',
        'SCOPES: tuple[str, ...] = ("meeting:read:meeting:admin",)',
    ),
    (
        CREATE,
        "即時会議として作る（読み返す余地が無くなる）",
        "MEETING_TYPE_SCHEDULED = 2",
        "MEETING_TYPE_SCHEDULED = 1",
    ),
    (
        CREATE,
        "空の議題を既定値に化かす",
        "    if topic is None:\n        topic = DEFAULT_TOPIC\n    topic = topic.strip()",
        "    topic = (topic or DEFAULT_TOPIC).strip()",
    ),
    (
        CREATE,
        "議題の前後の空白を落とさない",
        "    topic = topic.strip()\n",
        "",
    ),
    (
        CREATE,
        "所要時間の検証をやめる",
        "    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:",
        "    if False:",
    ),
    (
        CREATE,
        "所要時間0を通す",
        "or duration <= 0:",
        "or duration < 0:",
    ),
    (
        CREATE,
        "開始時刻の書式を何でも通す",
        'START_TIME_PATTERN = re.compile(r"\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z?")',
        'START_TIME_PATTERN = re.compile(r".*")',
    ),
    (
        CREATE,
        "開始時刻の書式を部分一致で見る",
        "        if not START_TIME_PATTERN.fullmatch(start_time):",
        "        if not START_TIME_PATTERN.search(start_time):",
    ),
    (
        CREATE,
        "空のパスワード指定を未指定に倒す",
        "        password = password.strip()\n        if not password:",
        "        password = password.strip()\n        if False:",
    ),
    (
        CREATE,
        "参加リンクの照合パスをゆるめる",
        'JOIN_PATH = "/j/"',
        'JOIN_PATH = "/"',
    ),
    (
        CREATE,
        "必須項目の確認をやめる",
        "    for key, label in REQUIRED_FIELDS:",
        "    for key, label in ():",
    ),
    (
        CREATE,
        "パスワードを必須から外す",
        '    ("password", "パスワード"),\n',
        "",
    ),
    (
        CREATE,
        "会議IDを必須から外す",
        '    ("id", "会議ID"),\n',
        "",
    ),
    (
        CREATE,
        "参加リンクを必須から外す",
        '    ("join_url", "参加リンク"),\n',
        "",
    ),
    (
        CREATE,
        "空文字を「返ってきた」とみなす",
        '        if value is None or str(value).strip() == "":',
        "        if value is None:",
    ),
    (
        CREATE,
        "パスワードが無いときの原因を案内しない",
        '            if key == "password":',
        "            if False:",
    ),
    (
        CREATE,
        "参加リンクと会議IDの照合をやめる",
        '    if f"{JOIN_PATH}{meeting_id}" not in join_url:',
        "    if False:",
    ),
    (
        CREATE,
        "ホストを決め打ちにする（地域別URLを無視）",
        "    return f\"{api_base.rstrip('/')}/v2/users/me/meetings\"",
        '    return "https://api.zoom.us/v2/users/me/meetings"',
    ),
    (
        CREATE,
        "ベアラートークンを付けない",
        '            "Authorization": f"Bearer {access_token}",',
        '            "Authorization": "",',
    ),
    (
        CREATE,
        "タイムアウトを渡さない",
        "        timeout=timeout,\n",
        "",
    ),
    (
        CREATE,
        "HTTP エラーを見ない",
        "    if not response.ok:\n        raise _error_for(response, payload)",
        "    if False:\n        raise _error_for(response, payload)",
    ),
    (
        CREATE,
        "JSON でない成功応答を素通りさせる",
        "    if payload is None:\n        raise MeetingError(",
        "    if False:\n        raise MeetingError(",
    ),
    (
        CREATE,
        "エラーからステータスコードを落とす",
        '    message = f"会議の作成に失敗しました（HTTP {status}）: {detail}"',
        '    message = f"会議の作成に失敗しました: {detail}"',
    ),
    (
        CREATE,
        "相手が言っている理由を載せない",
        '        for key in ("message", "reason", "error"):',
        "        for key in ():",
    ),
    (
        CREATE,
        "権限不足のときスコープを案内しない",
        "    if status in (401, 403):",
        "    if False:",
    ),
    (
        CREATE,
        "回数制限のとき1日の上限を案内しない",
        "    elif status == 429:",
        "    elif False:",
    ),
    (
        CREATE,
        "開始用リンク（ホスト権限）も印字する",
        "        f\"  参加リンク: {meeting.get('join_url', '')}\",",
        "        f\"  参加リンク: {meeting.get('join_url', '')}\",\n"
        "        f\"  開始リンク: {meeting.get('start_url', '')}\",",
    ),
    (
        CREATE,
        "パスワードを印字しない",
        "        f\"  パスワード: {meeting.get('password', '')}\",\n",
        "",
    ),
    (
        CREATE,
        "会議IDを印字しない",
        "        f\"  会議ID    : {meeting.get('id', '')}\",\n",
        "",
    ),
    (
        CREATE,
        "送る内容を確定する前に API へ繋ぐ",
        "    try:\n        body = build_meeting_body(",
        "    zoom_auth.fetch_access_token(zoom_auth.read_credentials(os.environ))\n"
        "    try:\n        body = build_meeting_body(",
    ),
    (
        CREATE,
        "失敗しても 0 を返す",
        "    except (MeetingError, zoom_auth.AuthError) as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 1\n"
        "\n"
        "    print(format_result(meeting))",
        "    except (MeetingError, zoom_auth.AuthError) as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 0\n"
        "\n"
        "    print(format_result(meeting))",
    ),
    (
        CREATE,
        "結果を印字しない",
        "    print(format_result(meeting))\n",
        "",
    ),
    # ------------------------------------------------- verify_meeting.py
    (
        VERIFY,
        "確認なのに書き込みスコープを要求する",
        'SCOPES: tuple[str, ...] = ("meeting:read:meeting:admin",)',
        'SCOPES: tuple[str, ...] = ("meeting:write:meeting:admin",)',
    ),
    (
        VERIFY,
        "読み取りのつもりで本文を送る（書き換えうる）",
        '        headers={"Authorization": f"Bearer {access_token}"},',
        '        json={},\n        headers={"Authorization": f"Bearer {access_token}"},',
    ),
    (
        VERIFY,
        "タイムアウトを渡さない",
        "        timeout=timeout,\n",
        "",
    ),
    (
        VERIFY,
        "ホストを決め打ちにする（地域別URLを無視）",
        "    return f\"{api_base.rstrip('/')}/v2/meetings/{meeting_id}\"",
        '    return f"https://api.zoom.us/v2/meetings/{meeting_id}"',
    ),
    (
        VERIFY,
        "読み取り失敗から会議IDを落とす",
        'f"会議を読み取れませんでした（HTTP {response.status_code}）: 会議ID {meeting_id}\\n"',
        'f"会議を読み取れませんでした（HTTP {response.status_code}）\\n"',
    ),
    (
        VERIFY,
        "空文字を「返ってきた」とみなす",
        '    return value is not None and str(value).strip() != ""',
        "    return value is not None",
    ),
    (
        VERIFY,
        "会議IDが返らなければ一致扱いにする",
        "            _present(actual_id) and str(actual_id) == str(meeting_id),",
        "            actual_id is None or str(actual_id) == str(meeting_id),",
    ),
    (
        VERIFY,
        "パスワードが返らなければ期待値と一致扱いにする",
        "                _present(actual_password) and str(actual_password) == expected_password,",
        "                actual_password is None or str(actual_password) == expected_password,",
    ),
    (
        VERIFY,
        "議題が返らなければ一致扱いにする",
        "                _present(actual_topic) and str(actual_topic) == expected_topic,",
        "                actual_topic is None or str(actual_topic) == expected_topic,",
    ),
    (
        VERIFY,
        "種別が返らなければ一致扱いにする",
        "            actual_type is not None and actual_type == create_meeting.MEETING_TYPE_SCHEDULED,",
        "            actual_type is None or actual_type == create_meeting.MEETING_TYPE_SCHEDULED,",
    ),
    (
        VERIFY,
        "状態が返らなければ一致扱いにする",
        '            _present(actual_status) and str(actual_status) == "waiting",',
        '            actual_status is None or str(actual_status) == "waiting",',
    ),
    (
        VERIFY,
        "参加リンクの照合を応答のIDで行う（トートロジー）",
        '    expected_fragment = f"{create_meeting.JOIN_PATH}{meeting_id}"',
        "    expected_fragment = f\"{create_meeting.JOIN_PATH}{meeting.get('id')}\"",
    ),
    (
        VERIFY,
        "照合ゼロ件でも「全部一致」にする",
        "    if not checks:\n        return False",
        "    if False:\n        return False",
    ),
    (
        VERIFY,
        "all_ok が常に True",
        "    return all(check.ok for check in checks)",
        "    return True",
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
        '        if check.detail:\n            line += f"  {check.detail}"\n',
        "",
    ),
    (
        VERIFY,
        "食い違っても 0 を返す",
        "    if not all_ok(checks):",
        "    if False:",
    ),
    (
        VERIFY,
        "期待する議題を照合に渡さない",
        "        expected_topic=args.expect_topic,",
        "        expected_topic=None,",
    ),
    (
        VERIFY,
        "読み取りに失敗しても 0 を返す",
        "    except (create_meeting.MeetingError, zoom_auth.AuthError) as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 1",
        "    except (create_meeting.MeetingError, zoom_auth.AuthError) as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 0",
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


def _backup_path(path: Path) -> Path:
    """控えの置き場。対象が別ディレクトリでも名前が衝突しないようにする。"""
    return BACKUP_DIR / f"{path.parent.name}__{path.name}"


def restore_leftovers() -> int:
    """前回が強制終了していたら、ここで元に戻す。

    atexit はプロセスを強制終了されると走らない。控えをディスクに置いて、
    次回の起動時に必ず突き合わせる。戻した件数を返す。
    """
    restored = 0
    for path in TARGETS:
        backup = _backup_path(path)
        if not backup.exists():
            continue
        saved = read_source(backup)
        if read_source(path) != saved:
            write_source(path, saved)
            print(f"! 前回の中断で {path.name} が壊れたまま残っていたので元に戻した")
            restored += 1
        backup.unlink()
    return restored


def _install_restore_guard() -> None:
    """中断されても元へ戻せるように、開始時の中身を覚えておく。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    originals = {}
    for path in TARGETS:
        text = read_source(path)
        originals[path] = text
        write_source(_backup_path(path), text)

    def restore() -> None:
        for path, text in originals.items():
            if read_source(path) != text:
                write_source(path, text)
                print(f"! 中断されたため {path.name} を元に戻した")
            backup = _backup_path(path)
            if backup.exists():
                backup.unlink()

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
    # 控えの確認は、今回ぶんの控えを取る前にやる。順番が逆だと
    # 「壊れた状態」を正しい中身として保存してしまう。
    restore_leftovers()
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
