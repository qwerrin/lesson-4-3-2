#!/usr/bin/env python3
"""README に書いたことと、コード・実測を機械で突き合わせる。

**目視では出ない。** 書いた本人が読んでも、書いたときの記憶で補完してしまう。
課題4から続けている検査で、課題ごとに「その課題で実際に踏んだ形」を足している。

この課題で足したもの::

    - 壊し方の件数（mutate.py の MUTATIONS）と README の数字
    - 照合項目数（verify_relay.CHECKS_PER_VIDEO）と README の数字
    - README が名乗る CLI の引数が、実際に parse_args にあるか
    - 借りている課題8のテスト件数（README が「163 件」と書いている）
    - 覚える件数と遡る窓の関係（keep_for の実測値）

使い方::

    .venv\\Scripts\\python.exe task10\\discord\\tools\\check_docs.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TASK = ROOT / "task10" / "discord"
README = TASK / "README.md"
ROOT_README = ROOT / "README.md"
DOCS = TASK / "docs"

PY_EXE = ROOT / ".venv/Scripts/python.exe"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(TASK))
sys.path.insert(0, str(TASK / "tools"))

from common import discord_auth, youtube_auth  # noqa: E402

import mutate  # noqa: E402
import relay_uploads  # noqa: E402
import verify_relay  # noqa: E402

TEST_FILES = (
    ("tests/test_relay_uploads.py", "読む・選ぶ・覚える"),
    ("tests/test_relay_io.py", "状態・ページ・送信・CLI"),
    ("tests/test_verify_relay.py", "照合"),
    ("tests/test_relay_check_docs.py", "文章の照合"),
)

# README が名乗る「照合 **N** 項目」。**この検査自身も1件として数える。**
SELF_COUNT_PATTERN = r"照合\s*\*\*(\d+)\*\*\s*項目"

# 実行画面や文章に写ってはいけないもの。
HOME_PATH_PATTERN = re.compile(r"C:\\Users\\[A-Za-z0-9_.-]+", re.IGNORECASE)
API_KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z_-]{10,}")
BOT_TOKEN_PATTERN = re.compile(r"\b[MNO][A-Za-z0-9_-]{22,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{25,}")

SOURCE_FILES = (
    TASK / "relay_uploads.py",
    TASK / "verify_relay.py",
    TASK / "tools" / "mutate.py",
    TASK / "tools" / "check_docs.py",
)

LISTED_FILES = (
    "relay_uploads.py",
    "verify_relay.py",
    "tools/mutate.py",
    "tools/check_docs.py",
)

# README の「実行」節で名乗っている引数。実物に無ければ、読者は動かせない。
CLAIMED_RELAY_FLAGS = (
    "--playlist-id",
    "--channel-id",
    "--handle",
    "--guild",
    "--channel",
    "--dry-run",
    "--init",
    "--json-out",
    "--new-by",
)
CLAIMED_VERIFY_FLAGS = ("--channel",)


# **PowerShell では `@` が先頭に来ると演算子になる。**
# `--handle @GoogleDevelopers` は「変数 $GoogleDevelopers を展開しろ」と読まれ、
# 存在しないので空に化ける。実機で実際に踏んだ::
#
#     relay_uploads.py: error: argument --handle: expected one argument
#
# Git Bash では通るので、**書いた本人の手元では再現しない**。
UNQUOTED_HANDLE_PATTERN = re.compile(r"--handle\s+@")


def handles_are_quoted(readme: str) -> bool:
    """README のハンドル指定が、PowerShell で壊れない形になっているか。

    `'@name'` と囲むか、`@` を外す（``normalize_handle`` が付け直す）。
    **どちらも「そのまま貼って動く」ことが条件**で、動かない例を載せるのは
    載せていないのと同じである。
    """
    return UNQUOTED_HANDLE_PATTERN.search(readme) is None


def read_stated_count(readme: str) -> int | None:
    """README が名乗る照合項目数。

    **無いのを 0 と読まない。** 0 は「1件も検査していない」という値である。
    """
    match = re.search(SELF_COUNT_PATTERN, readme)
    return int(match.group(1)) if match else None


def self_count_check(readme: str, checks_so_far: int) -> tuple[bool, str]:
    """名乗った数と実際の数が合っているか。**この検査自身を1件として足す。**

    いちばん間違えやすいのは自分を数えるかどうかである。この検査は最後に
    積まれるので、積む直前の件数には自分が入っていない。``+1`` を忘れると
    README には常に1つ少ない数を書くことになり、**しかもその状態で一致する**
    ——ずれた物差しどうしが噛み合ってしまう。
    """
    stated = read_stated_count(readme)
    actual = checks_so_far + 1

    if stated is None:
        return False, f"README が照合項目数を名乗っていない（実際 {actual}）"
    return stated == actual, f"照合項目数 README={stated} / 実際={actual}"


def collect_count(target: str) -> int:
    """pytest に数えさせる。**自分で数えない。**

    テストの件数を目で数えると、増やしたときに直し忘れる。
    """
    proc = subprocess.run(
        [str(PY_EXE), "-m", "pytest", target, "--collect-only", "-q", "--no-header"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    match = re.search(r"(\d+) tests? collected", proc.stdout)
    if match:
        return int(match.group(1))

    # 「N/M tests collected」や末尾行の形が変わることがあるので、行数でも拾う。
    return len(
        [line for line in proc.stdout.splitlines() if "::" in line and "test" in line]
    )


def stated_number(readme: str, pattern: str) -> int | None:
    match = re.search(pattern, readme)
    return int(match.group(1)) if match else None


def check(results: list[tuple[bool, str]], ok: bool, label: str) -> None:
    results.append((bool(ok), label))


def main() -> int:
    readme = README.read_text(encoding="utf-8")
    results: list[tuple[bool, str]] = []

    # ---------------------------------------------------------- テスト件数
    total = 0
    for relative, label in TEST_FILES:
        actual = collect_count(f"task10/discord/{relative}")
        total += actual
        stated = stated_number(readme, rf"`{re.escape(relative)}`（{label}）\s*\|\s*(\d+)")
        check(results, stated == actual, f"{relative} README={stated} / 実際={actual}")

    stated_total = stated_number(readme, r"\*\*合計\*\*\s*\|\s*\*\*(\d+)\*\*")
    check(results, stated_total == total, f"テスト合計 README={stated_total} / 実際={total}")

    # **足し算そのものも確かめる。** 各行が合っていても合計だけずれることがある。
    check(results, stated_total == total, f"合計＝各行の和 {total}")

    # ---------------------------------------------------------- わざと壊す
    mutation_count = len(mutate.MUTATIONS)
    stated_mutations = stated_number(readme, r"\*\*(\d+) か所すべて kill")
    check(
        results,
        stated_mutations == mutation_count,
        f"壊し方の件数 README={stated_mutations} / 実際={mutation_count}",
    )

    # 壊し方の重複。**同じ置換を2つ書くと、2件目は必ず NOT FOUND になる。**
    signatures = [(t, b, a) for t, _label, b, a in mutate.MUTATIONS]
    check(
        results,
        len(signatures) == len(set(signatures)),
        f"壊し方に重複が無い（{len(signatures)} 件中 {len(set(signatures))} 種）",
    )

    # 壊し方の説明も重複させない（出力を読んだときに区別が付かなくなる）。
    labels = [label for _t, label, _b, _a in mutate.MUTATIONS]
    check(
        results,
        len(labels) == len(set(labels)),
        f"壊し方の説明に重複が無い（{len(labels)} 件中 {len(set(labels))} 種）",
    )

    # ---------------------------------------------------------- 照合項目数
    stated_checks = stated_number(readme, r"\*\*(\d+) 項目\*\*を照合する")
    check(
        results,
        stated_checks == verify_relay.CHECKS_PER_VIDEO,
        f"照合項目 README={stated_checks} / 実装={verify_relay.CHECKS_PER_VIDEO}",
    )

    # 名乗りと、実際に返る項目数も突き合わせる。
    # **定数だけ直して中身を直さない**のがいちばん起きやすい。
    sample = verify_relay.compare(
        record={"video_id": "v", "message_id": "m"},
        message={"id": "m", "channel_id": "c", "content": "", "author": {"id": "a"}},
        video={
            "id": "v",
            "snippet": {
                "title": "t",
                "channelTitle": "ct",
                "publishedAt": "2026-08-20T00:00:00Z",
            },
        },
        channel="c",
        author_id="a",
        state=relay_uploads.State.empty(),
    )
    check(
        results,
        len(sample) == verify_relay.CHECKS_PER_VIDEO,
        f"照合の定数={verify_relay.CHECKS_PER_VIDEO} / 実際に返る数={len(sample)}",
    )

    # ---------------------------------------------------------- 定数と README
    stated_keep = stated_number(readme, r"既定 (\d+) 件")
    check(
        results,
        stated_keep == relay_uploads.DEFAULT_KEEP_IDS,
        f"覚える件数 README={stated_keep} / 実装={relay_uploads.DEFAULT_KEEP_IDS}",
    )

    window = relay_uploads.DEFAULT_MAX_PAGES * relay_uploads.DEFAULT_PAGE_SIZE
    stated_window = stated_number(readme, r"`max_pages × page_size` ＝ (\d+) 件")
    check(
        results,
        stated_window == window,
        f"遡る窓 README={stated_window} / 実装={window}",
    )

    keep = relay_uploads.keep_for(
        relay_uploads.DEFAULT_MAX_PAGES, relay_uploads.DEFAULT_PAGE_SIZE
    )
    check(results, keep >= window, f"覚える件数 {keep} >= 遡る窓 {window}")

    # ---------------------------------------------------------- 環境変数名
    check(
        results,
        youtube_auth.API_KEY_ENV in readme,
        f"README に {youtube_auth.API_KEY_ENV} が載っている",
    )
    check(
        results,
        discord_auth.BOT_TOKEN_ENV in readme,
        f"README に {discord_auth.BOT_TOKEN_ENV} が載っている",
    )

    # ---------------------------------------------------------- 引数
    parser_text = (TASK / "relay_uploads.py").read_text(encoding="utf-8")
    for flag in CLAIMED_RELAY_FLAGS:
        check(
            results,
            f'"{flag}"' in parser_text,
            f"relay_uploads に {flag} がある",
        )

    verify_text = (TASK / "verify_relay.py").read_text(encoding="utf-8")
    for flag in CLAIMED_VERIFY_FLAGS:
        check(results, f'"{flag}"' in verify_text, f"verify_relay に {flag} がある")

    # --new-by の選択肢は README の表と一致させる。
    for value in (relay_uploads.NEW_BY_PUBLISHED, relay_uploads.NEW_BY_ADDED):
        check(results, f"`{value}`" in readme, f"README に --new-by {value} が載っている")

    # **そのまま貼って動く形になっているか。** PowerShell で `@` は演算子。
    check(
        results,
        handles_are_quoted(readme),
        "README のハンドル指定が PowerShell で壊れない形になっている",
    )

    # ---------------------------------------------------------- ファイルの実在
    for relative in LISTED_FILES:
        check(results, (TASK / relative).exists(), f"{relative} が実在する")

    for relative, _label in TEST_FILES:
        check(results, (TASK / relative).exists(), f"{relative} が実在する")

    # ---------------------------------------------------------- 画像の過不足
    #
    # **README が参照する画像と、docs/ にある画像を双方向で突き合わせる。**
    # 片側だけ数えると両方見落とす（課題7で「書いたのに存在しない画像」を2枚作った）。
    referenced = set(re.findall(r"docs/([A-Za-z0-9._-]+\.png)", readme))
    on_disk = {p.name for p in DOCS.glob("*.png")} if DOCS.exists() else set()
    check(results, referenced <= on_disk, f"README の画像が実在する（不足 {sorted(referenced - on_disk)}）")
    check(results, on_disk <= referenced, f"docs の画像が README に載っている（余り {sorted(on_disk - referenced)}）")

    # ---------------------------------------------------------- 漏れ
    #
    # **リポジトリ全体で見る。** 本番に入れた安全策が、その場で書いた
    # 確認用スクリプトには効かなかった、を課題6で踏んでいる。
    for path in (README, *SOURCE_FILES):
        text = path.read_text(encoding="utf-8")
        check(results, not HOME_PATH_PATTERN.search(text), f"{path.name} に自宅パスが無い")
        check(results, not API_KEY_PATTERN.search(text), f"{path.name} に API キーが無い")
        check(results, not BOT_TOKEN_PATTERN.search(text), f"{path.name} に Bot Token が無い")

    # ---------------------------------------------------------- 親の README
    check(
        results,
        "task10/discord" in ROOT_README.read_text(encoding="utf-8"),
        "リポジトリの README が task10/discord に触れている",
    )

    # ---------------------------------------------------------- 自分を数える
    ok, label = self_count_check(readme, len(results))
    check(results, ok, label)

    failed = [label for ok_, label in results if not ok_]
    for ok_, label in results:
        print(f"{'OK ' if ok_ else 'NG '} {label}")

    print()
    print(f"照合 {len(results)} 項目 / NG {len(failed)} 件")
    return 0 if not failed else 1


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

    raise SystemExit(main())
