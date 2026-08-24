#!/usr/bin/env python3
"""``block_probe.decide`` をわざと壊して、テストが落ちることを確かめる。

**通っているテストは、守っている証拠にならない。** この判定の出力は
そのまま記事に載るので、判定を壊しても素通りするなら、記事に嘘を書ける。

``task10/line/tools/mutate.py`` と分けてあるのは、あちらが
``task10/line`` と ``common`` を対象にしていて、探り道具まで含めると
「この課題の実装をどれだけ守れているか」の数字がぼやけるため。

**コピー側でしか壊さない。** 成果物を書き換えると、途中で強制終了したときに
壊れたまま残る（``atexit`` は強制終了では走らない）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PY_EXE = _REPO_ROOT / ".venv" / "Scripts" / "python.exe"
TARGET = Path("task10") / "probe" / "block_probe.py"
TESTS = Path("task10") / "probe" / "tests" / "test_block_probe.py"

#: (説明, 置換前, 置換後)
#:
#: **「置換先なし」も失敗として数える。** 置換前の文字列が実装から消えていると、
#: 壊していないのに kill と同じ見た目になる（何も起きていないのに合格に見える）。
MUTATIONS = [
    (
        "対照の 200 を見ずに結論を出す",
        "if b == 404 and u == 200:",
        "if b == 404:",
    ),
    (
        "両方404を「ブロックのせい」と言う",
        '"inconclusive_both_404",',
        '"block_causes_404",',
    ),
    (
        "両方200を「ブロックのせい」と言う",
        '"block_does_not_cause_404",',
        '"block_causes_404",',
    ),
    (
        "答えでない状態コードを通す",
        "ANSWER_CODES = (200, 404)",
        "ANSWER_CODES = (200, 404, 401, 403, 429, 500)",
    ),
    (
        "状態コードの検査そのものを消す",
        "    if unusable:",
        "    if False:",
    ),
    (
        "宛先が違っても突き合わせる",
        'if blocked.get("user_fingerprint") != unblocked.get("user_fingerprint"):',
        "if False:",
    ),
    (
        "ボットが違っても突き合わせる",
        'if blocked.get("bot_user_id") != unblocked.get("bot_user_id"):',
        "if False:",
    ),
    (
        "逆転を判定不能にしない",
        '"inconclusive_reversed",',
        '"block_causes_404",',
    ),
    (
        "指紋を宛先ごとに変えない",
        'hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]',
        '"constant"',
    ),
    (
        "結論の説明に測定順を書き戻す",
        "ブロック中は 404、ブロックしていないときは 200。",
        "ブロック中は 404、解除後は 200。",
    ),
]


def _run_tests(cwd: Path) -> bool:
    """テストが通ったら True。

    **出力を文字列に直さない。** ``text=True`` を付けると環境のロケール
    （Windows では cp932）で復号しようとして、日本語のテスト名が出た瞬間に
    ``UnicodeDecodeError`` を投げる。ここで見ているのは終了コードだけなので、
    バイト列のまま捨てる。**読まないものを復号しない。**
    """
    proc = subprocess.run(
        [str(PY_EXE), "-m", "pytest", str(TESTS), "-q"],
        cwd=str(cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def main() -> int:
    survived: list[str] = []
    missing: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(
            _REPO_ROOT,
            work,
            ignore=shutil.ignore_patterns(
                ".venv", ".git", "__pycache__", ".pytest_cache"
            ),
        )
        original = (work / TARGET).read_text(encoding="utf-8")

        # **壊す前に通ることを確かめる。** ここが落ちていると、
        # 以降の kill は「壊したから落ちた」の証拠にならない。
        if not _run_tests(work):
            print("[中止] 壊す前のコピーでテストが落ちた。コピーが壊れている。")
            return 2
        print("[前提] 壊す前のコピー: テスト通過")

        for index, (label, before, after) in enumerate(MUTATIONS, 1):
            if before not in original:
                missing.append(label)
                print(f"[{index:>3}/{len(MUTATIONS)}] NOT FOUND  {label}")
                continue

            (work / TARGET).write_text(
                original.replace(before, after, 1), encoding="utf-8"
            )
            passed = _run_tests(work)
            (work / TARGET).write_text(original, encoding="utf-8")

            if passed:
                survived.append(label)
                print(f"[{index:>3}/{len(MUTATIONS)}] SURVIVED   {label}")
            else:
                print(f"[{index:>3}/{len(MUTATIONS)}] kill       {label}")

    killed = len(MUTATIONS) - len(survived) - len(missing)
    print()
    print(
        f"kill {killed} / SURVIVED {len(survived)} / NOT FOUND {len(missing)}"
        f"  （全 {len(MUTATIONS)} か所）"
    )
    return 0 if not survived and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
