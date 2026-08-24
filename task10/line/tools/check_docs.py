"""課題10（LINE）の README と実体を機械照合する。

    .venv/Scripts/python.exe task10/line/tools/check_docs.py

文章は目で読んでも合っているように見える。**数字とコードの食い違いは目視では出ない。**

課題9から持ち越した宿題
------------------------------------------------------------------

課題9の check_docs.py は最後に「照合 38 項目 / NG 0 件」と出していたが、
**その 38 が正しいかは誰も確かめていなかった**。検査を1つ足せば 39 になるのに、
README とスクリーンショットは 38 のまま残る。

課題9で入れなかった理由は「足すと 39 項目になり、**提出済みのスクショの 38 と
食い違う**」だった。順序として仕方がなかったが、**そのまま忘れれば同じ穴が残る**。
だから課題10では最初から入れてある（``self_count_check``）。

このツールが確かめること
------------------------------------------------------------------

1. README のテスト件数が pytest の実測と一致する
2. README の実測値が ``results.json`` と一致する
3. **追跡されるファイルに本物の資格情報が入っていない**
4. ``.env.example`` の変数名が ``common/line_auth.py`` の定数と一致する
5. README が名前を出しているファイルが実在する
6. README のコマンドが壊れていない（タブ化・存在しないパス）
7. README が参照する画像が実在する
8. ルート README の課題10の行が最新である
9. **README が名乗る照合項目数が、実際の項目数と一致する**

**検査対象を環境変数から取らない。**
``os.environ["USERNAME"]`` のような値はシェル経由で変わり、**空なら黙って
0 件を「問題なし」として表示する**。本物の資格情報は ``.env`` から読み、
**読めなければ検査を成功させずに落とす**。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import env_file, line_auth  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notify_schedule  # noqa: E402

TASK = ROOT / "task10" / "line"
README = TASK / "README.md"
ROOT_README = ROOT / "README.md"
RESULTS = TASK / "results.json"
ENV_EXAMPLE = ROOT / ".env.example"

PY_EXE = ROOT / ".venv/Scripts/python.exe"

#: README の件数表に出す対象。**ここに並べたものだけを照合する**ので、
#: 表に行を足したらここにも足す。
TEST_FILES = (
    "task10/line/tests/test_notify_schedule.py",
    "task10/line/tests/test_verify_notify.py",
    "task10/line/tests/test_check_docs.py",
    "common/tests/test_line_auth.py",
    # ブロック実測の判定（2026-08-24 に追加）。**判定がそのまま記事に載る**ので、
    # 他のテストと同じように件数を README と突き合わせる。
    "task10/probe/tests/test_block_probe.py",
)

#: README が名乗る照合項目数。``照合 **42** 項目`` の形で書く。
SELF_COUNT_PATTERN = r"照合\s*\*\*(\d+)\*\*\s*項目"


class Failure(Exception):
    """検査を始められない。**不一致とは区別する。**"""


# ------------------------------------------------- 自分の項目数を自分で検査する


def read_stated_count(readme: str) -> int | None:
    """README が名乗っている照合項目数を読む。無ければ ``None``。

    **無いのを 0 と読まない。** 0 は「1件も検査していない」という正当な値で、
    「書いていない」とは別の意味になる。
    """
    match = re.search(SELF_COUNT_PATTERN, readme)
    return int(match.group(1)) if match else None


def self_count_check(readme: str, checks_so_far: int) -> tuple[bool, str]:
    """README の項目数と実際の項目数を突き合わせる。

    **この検査自身を 1 つ足す。** 最後に積まれるので ``checks_so_far`` には
    自分が入っていない。+1 を忘れると README には常に1つ少ない数を書くことになり、
    しかも**その状態で一致してしまう**——ずれた物差しどうしが噛み合う。
    """
    actual = checks_so_far + 1
    stated = read_stated_count(readme)

    if stated is None:
        return False, (
            f"README が照合項目数を名乗っていない（実測={actual}）。"
            "「照合 **N** 項目」の形で書く"
        )
    return stated == actual, f"照合項目数: README={stated} 実測={actual}"


# ------------------------------------------------------------------ 件数を数える


def collect_count(target: str) -> int:
    """pytest に数えさせる。**README の数字を物差しにしない。**"""
    result = subprocess.run(
        [str(PY_EXE), "-m", "pytest", target, "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    match = re.search(r"^(\d+) tests collected", result.stdout or "", re.MULTILINE)
    if not match:
        raise Failure(f"{target} のテスト件数を数えられませんでした。")
    return int(match.group(1))


def check(results: list[tuple[bool, str]], ok: bool, label: str) -> None:
    results.append((ok, label))


def main() -> int:
    if not README.is_file():
        raise Failure(f"README がありません: {README}")
    readme = README.read_text(encoding="utf-8")

    out: list[tuple[bool, str]] = []

    # --- 1. テスト件数 -------------------------------------------------
    total_added = 0
    for target in TEST_FILES:
        actual = collect_count(target)
        total_added += actual
        row = re.search(
            rf"^\|\s*`{re.escape(target)}`\s*\|\s*(\d+)\s*\|", readme, re.MULTILINE
        )
        if not row:
            check(out, False, f"README に {target} の行が無い")
            continue
        stated = int(row.group(1))
        check(out, stated == actual, f"件数 {target}: README={stated} 実測={actual}")

    added_row = re.search(r"この課題が触るぶん\*\*\s*\|\s*\*\*(\d+)\*\*", readme)
    if added_row:
        check(
            out,
            int(added_row.group(1)) == total_added,
            f"この課題が触るぶん: README={added_row.group(1)} 実測={total_added}",
        )
    else:
        check(out, False, "README に「この課題が触るぶん」の行が無い")

    whole = collect_count(".")
    whole_row = re.search(r"リポジトリ全体\s*\|\s*(\d+)\s*\|", readme)
    if whole_row:
        check(
            out,
            int(whole_row.group(1)) == whole,
            f"リポジトリ全体: README={whole_row.group(1)} 実測={whole}",
        )
    else:
        check(out, False, "README に「リポジトリ全体」の行が無い")

    # --- 2. 実測値 ↔ results.json --------------------------------------
    # **記録の有無で項目数を変えない。** 変えると README が名乗る項目数が
    # 実行状況で動き、項目9（自己検査）が「いつ数えたか」に依存してしまう。
    # 記録が無いなら、同じ項目が同じ数だけ NG として並ぶ。
    record = json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.is_file() else None

    def from_record(getter):
        """記録から値を取る。**無いことを「一致した」に倒さない。**"""
        if record is None:
            return None
        try:
            return getter(record)
        except (KeyError, TypeError):
            return None

    for label, getter in (
        ("message ID", lambda r: r["message_id"]),
        ("basicId", lambda r: r["bot"]["basic_id"]),
        ("bot の userId", lambda r: r["bot"]["user_id"]),
        ("対象日", lambda r: r["target_date"]),
    ):
        value = from_record(getter)
        shown = value if value is not None else "記録が無い"
        check(out, value is not None and str(value) in readme,
              f"README に {label} `{shown}` がある")

    before = from_record(lambda r: r["usage_before"])
    after = from_record(lambda r: r["usage_after"])
    usage = f"`{before}` → `{after}`"
    check(out, before is not None and usage in readme,
          f"README の通数が記録と一致（{usage}）")

    # 記録は public リポジトリに入る。伏せ忘れをここでも捕まえる。
    masked = str(from_record(lambda r: r["to_masked"]) or "")
    check(out, bool(masked) and ("…" in masked or "..." in masked),
          "記録の宛先が伏せられている")

    # --- 3. 資格情報が漏れていないか -----------------------------------
    # **.env から本物を読む。読めなければ落とす**（環境変数から取らない）。
    try:
        env = env_file.load(ROOT / env_file.ENV_FILENAME)
        token = line_auth.read_channel_access_token(env)
        user_id = line_auth.read_user_id(env)
    except (env_file.EnvFileError, line_auth.AuthError) as error:
        raise Failure(
            f"{env_file.ENV_FILENAME} を読めないので漏れの検査ができません。\n"
            f"**検査していない状態を「問題なし」と表示しないため、ここで止めます。**\n{error}"
        ) from error

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    ).stdout.split()
    # 新規ファイルはまだ追跡されていないので、課題10の成果物も明示的に足す。
    targets = {
        *tracked,
        "task10/line/README.md",
        "task10/line/notify_schedule.py",
        "task10/line/verify_notify.py",
        "task10/line/tools/check_docs.py",
        "task10/line/tools/mutate.py",
        "task10/line/results.json",
        "README.md",
        ".env.example",
        "common/line_auth.py",
    }

    leaked: list[str] = []
    for rel in sorted(targets):
        path = ROOT / rel
        if not path.is_file() or path.suffix in {".png", ".jpg", ".pyc"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for secret, name in (
            (token, "チャネルアクセストークン"),
            (user_id, "宛先ユーザーID"),
        ):
            if secret in text:
                leaked.append(f"{rel} に{name}")

    check(
        out,
        not leaked,
        f"資格情報の漏れ: {len(leaked)} 件" + (f" -> {leaked}" if leaked else ""),
    )

    # トークンのファイルが追跡されていないこと。**課題3の token.json と分ける**
    # 判断が効いているかを、名前ではなく git の答えで確かめる。
    ignored = subprocess.run(
        ["git", "check-ignore", "task10/line/token-calendar.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    check(out, ignored.returncode == 0, "token-calendar.json が .gitignore に該当する")

    # --- 4. .env.example の変数名 --------------------------------------
    example = ENV_EXAMPLE.read_text(encoding="utf-8") if ENV_EXAMPLE.is_file() else ""
    for name in (line_auth.CHANNEL_ACCESS_TOKEN_ENV, line_auth.USER_ID_ENV):
        check(out, f"{name}=" in example, f".env.example に {name}= がある")
        check(out, name in readme, f"README に {name} がある")

    # --- 5. README が名前を出すファイルの実在 ---------------------------
    for rel in (
        "task10/line/notify_schedule.py",
        "task10/line/verify_notify.py",
        "task10/line/tools/check_docs.py",
        "task10/line/tools/mutate.py",
        "common/line_auth.py",
    ):
        check(out, f"`{rel}`" in readme, f"README が {rel} に触れている")
        check(out, (ROOT / rel).is_file(), f"{rel} が実在する")

    # 要求するスコープが README とコードで一致していること。**書き込み権限を
    # うっかり増やしたら、文章が古いまま残る**のではなく、ここで落ちる。
    for scope in notify_schedule.CALENDAR_SCOPES:
        check(out, scope in readme, f"README が要求スコープ {scope} を書いている")
    check(
        out,
        len(notify_schedule.CALENDAR_SCOPES) == 1,
        f"要求スコープが1つだけ（実測={len(notify_schedule.CALENDAR_SCOPES)}）",
    )

    # --- 6. README に書いたコマンドが壊れていないか ----------------------
    # **2026-08-19 に実際に踏んだ。** README を書き換えるスクリプトの中で
    # ``task9\tools`` と書いたところ、``\t`` がタブ文字に化けた。同じ行の
    # ``\S`` は「無効なエスケープ」で警告が出たのに、**``\t`` は有効なので
    # 黙って変換された**。**警告が出た側は無事で、出なかった側が壊れる。**
    check(out, "\t" not in readme, "README にタブ文字が無い（コマンド行の破損跡）")

    for raw in re.findall(r"python\.exe\s+(\S+\.py)", readme):
        rel = raw.replace("\\", "/")
        check(out, (ROOT / rel).is_file(), f"README のコマンドが指す {rel} が実在する")

    # --- 7. README が参照する画像の実在 ----------------------------------
    referenced = re.findall(r"`(docs/[^`]+\.png)`", readme)
    check(out, len(referenced) > 0, "README がスクリーンショットに触れている")
    for rel in sorted(set(referenced)):
        check(out, (TASK / rel).is_file(), f"README が参照する {rel} が実在する")

    # --- 8. ルート README の一覧 ----------------------------------------
    root_text = ROOT_README.read_text(encoding="utf-8")
    check(out, "| 10 |" in root_text, "ルート README に課題10の行がある")
    check(
        out,
        "未着手 |" not in root_text.split("| 10 |")[1].split("\n")[0],
        "課題10の行が「未着手」のままになっていない",
    )

    # --- 9. 自分の項目数 -------------------------------------------------
    # **必ず最後に積む。** ここまでの件数に自分を1つ足したものが正しい答え。
    ok, label = self_count_check(readme, len(out))
    check(out, ok, label)

    # --- 出力 -----------------------------------------------------------
    ng = 0
    for ok, label in out:
        print(f"  [{'OK' if ok else 'NG'}] {label}")
        ng += 0 if ok else 1

    print()
    print(f"照合 {len(out)} 項目 / NG {ng} 件")
    return 0 if ng == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except Failure as error:
        print(f"検査を実行できません: {error}")
        raise SystemExit(2) from error
