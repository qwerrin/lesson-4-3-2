#!/usr/bin/env python3
"""notify_schedule.py を1か所ずつ壊して、テストが落ちることを確かめる。

**テストが通っていることは、守られていることの証拠にならない。**
課題9では、主題そのものである「確認できないことを出す」行が素通りしていた
（2つの assert が両方とも、無関係な場所の文字列で満たされていた）。

この課題で守りたいのは「**エラーにならず、件数だけ静かに減る**」失敗である。
0 件が正常値でありうるので、減っても「予定が無い日」と見分けが付かない。
だから件数に関わる分岐は、壊したら必ず落ちる状態にしておく。

使い方::

    .venv\\Scripts\\python.exe task10\\line\\tools\\mutate.py

リポジトリを一時ディレクトリへ写し、**写した側だけ**を壊す。
成果物には触らないので、途中で強制終了しても壊れたまま残らない。

**置換先が見つからない（NOT FOUND）は素通りと同じ扱いにする。**
実装を直して壊しかたを直し忘れると、何も壊さずに全部通って
「穴ゼロ」と出てしまうため。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

NOTIFY = "task10/line/notify_schedule.py"
AUTH = "common/line_auth.py"

IGNORE = shutil.ignore_patterns(
    ".venv", ".git", "__pycache__", ".pytest_cache", "docs", "*.png", "node_modules"
)

# common/ を壊すので、**課題9のテストも一緒に回す**。
# 共有部品に足したものが既存を壊していないことを、ここでも確かめる。
TEST_PATHS = ("task10/line/tests", "common/tests")

# (対象ファイル, 壊した内容, 置換前, 置換後)
MUTATIONS: list[tuple[str, str, str, str]] = [
    # ==================================== 問い合わせパラメータ：静かに減る側
    (
        NOTIFY,
        "繰り返し予定を展開しない（singleEvents を落とす）",
        '"singleEvents": True,',
        '"singleEvents": False,',
    ),
    (
        NOTIFY,
        "開始時刻順の指定を落とす",
        '"orderBy": "startTime",',
        '"orderBy": "updated",',
    ),
    (
        NOTIFY,
        "問い合わせ窓の下限を渡さない",
        '"timeMin": time_min,',
        '"timeMin": "",',
    ),
    (
        NOTIFY,
        "問い合わせ窓の上限を渡さない",
        '"timeMax": time_max,',
        '"timeMax": "",',
    ),
    # ================================================ 問い合わせ窓：1日ずれる
    (
        NOTIFY,
        "窓の幅を1日から0日にする（その日の予定が1件も入らない）",
        "    end = start + timedelta(days=1)",
        "    end = start + timedelta(days=0)",
    ),
    (
        NOTIFY,
        "タイムゾーンを付けない（RFC3339 のオフセットが消える）",
        "    start = datetime.combine(target_date, time.min, tzinfo=tz)",
        "    start = datetime.combine(target_date, time.min)",
    ),
    # ============================================================== 対象日
    (
        NOTIFY,
        "明示指定を無視して常に実行日を使う",
        "    if explicit is not None:",
        "    if False:",
    ),
    (
        NOTIFY,
        "対象日を翌日にずらす",
        "    return now.date()",
        "    return now.date() + timedelta(days=1)",
    ),
    # ============================================ 予定の取り出し：静かに減る側
    (
        NOTIFY,
        "終日予定を落とす（start.date を見ない）",
        '        if "date" in start:',
        "        if False:",
    ),
    (
        NOTIFY,
        "時刻付き予定を落とす（start.dateTime を見ない）",
        '        elif "dateTime" in start:',
        "        elif False:",
    ),
    (
        NOTIFY,
        "start が未知の形の予定を捨てる",
        "            events.append(\n                Event(summary=summary, all_day=False, start_label=UNKNOWN_TIME_LABEL)\n            )",
        "            pass",
    ),
    (
        NOTIFY,
        "タイトルの無い予定を空文字で通す（行が空になり0件と紛れる）",
        'NO_TITLE = "(タイトルなし)"',
        'NO_TITLE = ""',
    ),
    (
        NOTIFY,
        "終日の目印を消す（時刻付きと区別できなくなる）",
        'ALL_DAY_LABEL = "終日"',
        'ALL_DAY_LABEL = ""',
    ),
    (
        NOTIFY,
        "items が無いときに落ちる（0件を正常値として扱わない）",
        '    for item in payload.get("items") or []:',
        '    for item in payload["items"]:',
    ),
    # ================================================================ 本文
    (
        NOTIFY,
        "0件のとき何も言わない（予定なしと動いていないが区別できなくなる）",
        "    if not events:",
        "    if False:",
    ),
    (
        NOTIFY,
        "本文から対象日を消す（いつの予定か分からなくなる）",
        '    header = f"{target_date.isoformat()} の予定"',
        '    header = "予定"',
    ),
    (
        NOTIFY,
        "予定の見出しだけ出して時刻を落とす",
        '    lines = [f"- {event.start_label} {event.summary}" for event in events]',
        '    lines = [f"- {event.summary}" for event in events]',
    ),
    (
        NOTIFY,
        "時刻の整形をやめて生の文字列を出す",
        '        return datetime.fromisoformat(raw).strftime("%H:%M")',
        "        return raw",
    ),
    # ================================ 送信前ガード：宛先（common/line_auth.py）
    (
        AUTH,
        "404 を見落とす（未友だち・ブロックを検出できなくなる）",
        '    if getattr(response, "status_code", None) == 404:',
        "    if False:",
    ),
    (
        AUTH,
        "404 なのに「届く」と答える",
        "            reachable=False,",
        "            reachable=True,",
    ),
    (
        AUTH,
        "届かない理由を丸ごと空にする（止めた理由が利用者に伝わらない）",
        '            reason=(\n                "この宛先はプロフィールを返しませんでした（404）。"\n                "友だち追加されていないか、ブロックされています。"\n                "push はこの相手にも 200 を返すため、送信前に止めます。"\n            ),',
        '            reason="",',
    ),
    (
        AUTH,
        "404 以外のエラーを素通りさせる（401 が「届かない」に化ける）",
        "            profile=None,\n        )\n\n    raise_for_line_error(response, *secrets)",
        "            profile=None,\n        )\n\n    pass",
    ),
    (
        AUTH,
        "プロフィールの userId が空でも通す（宛先を確かめずに進む）",
        "    if not profile_user_id:",
        "    if False:",
    ),
    # ================================ 送信前ガード：通数（common/line_auth.py）
    (
        AUTH,
        "上限の種別を見ない（無制限を上限ありとして扱う）",
        '    limited = str(payload.get("type") or "") == "limited"',
        "    limited = True",
    ),
    (
        AUTH,
        "上限が無いときに 0 とみなす（無制限が「使い切った」に化ける）",
        "    limit = int(raw_limit) if limited and raw_limit is not None else None",
        "    limit = int(raw_limit) if limited and raw_limit is not None else 0",
    ),
    (
        AUTH,
        "残数の「無制限」を 0 で返す",
        "    if not quota.limited or quota.limit is None:\n        return None",
        "    if not quota.limited or quota.limit is None:\n        return 0",
    ),
    (
        AUTH,
        "残数の負値を 0 に丸める（超過と使い切りが同じ表示になる）",
        "    return quota.limit - consumption",
        "    return max(0, quota.limit - consumption)",
    ),
    (
        AUTH,
        "totalUsage が無くても中断しない",
        "    if raw_usage is None:",
        "    if False:",
    ),
]


def run_tests(work: Path) -> bool:
    """写した側でテストを回す。1件でも落ちたら True。"""
    proc = subprocess.run(
        [str(PYTHON), "-m", "pytest", *TEST_PATHS, "-x", "-q", "--no-header"],
        cwd=work,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode != 0


def main() -> int:
    if not PYTHON.exists():
        print(f"仮想環境の Python が見つかりません: {PYTHON}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(ROOT, work, ignore=IGNORE)

        if run_tests(work):
            print("壊す前からテストが落ちています。先にそちらを直してください。", file=sys.stderr)
            return 1

        killed: list[str] = []
        survived: list[str] = []
        not_found: list[str] = []

        for index, (target, label, before, after) in enumerate(MUTATIONS, start=1):
            path = work / target
            # 復元用にはバイトをそのまま持つ（newline="" は改行を変換しない）。
            original = path.read_text(encoding="utf-8", newline="")

            # **照合と書き込みは LF に正規化した文字列で行う。**
            # このリポジトリは core.autocrlf=true で、チェックアウトすると
            # .py は CRLF になる。newline="" のまま `\n` を含むパターンを探すと
            # **複数行のパターンは構造的に一度もマッチしない**——そして
            # 「置換先なし」は素通りと同じ扱いなので、壊し方が悪いのか
            # 照合器が壊れているのか区別が付かないまま数字だけ出る。
            # 課題9で実際に4件がこれで NOT FOUND になった。
            haystack = original.replace("\r\n", "\n")

            if before not in haystack:
                not_found.append(f"{target}: {label}")
                print(f"[{index:3}/{len(MUTATIONS)}] NOT FOUND  {label}")
                continue

            path.write_text(
                haystack.replace(before, after, 1), encoding="utf-8", newline=""
            )
            failed = run_tests(work)
            path.write_text(original, encoding="utf-8", newline="")

            if failed:
                killed.append(label)
                print(f"[{index:3}/{len(MUTATIONS)}] kill       {label}")
            else:
                survived.append(f"{target}: {label}")
                print(f"[{index:3}/{len(MUTATIONS)}] SURVIVED   {label}")

    print()
    print(f"kill {len(killed)} / SURVIVED {len(survived)} / NOT FOUND {len(not_found)}"
          f"  （全 {len(MUTATIONS)} 件）")

    if survived:
        print()
        print("素通り（テストが守っていない）:")
        for item in survived:
            print(f"  - {item}")
    if not_found:
        print()
        print("置換先が見つからない（壊しかたが実装とずれている）:")
        for item in not_found:
            print(f"  - {item}")

    return 0 if not survived and not not_found else 1


if __name__ == "__main__":
    raise SystemExit(main())
