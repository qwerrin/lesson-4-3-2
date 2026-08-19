"""課題9の README と実体を機械照合する。

    .venv\\Scripts\\python.exe task9\\tools\\check_docs.py

文章は目で読んでも合っているように見える。**数字とコードの食い違いは目視では出ない。**
課題8では「スクショを撮り直して ID がズレ、文章だけ古いまま残った」を踏んだ。

このツールが確かめること
------------------------------------------------------------------

1. README のテスト件数が pytest の実測と一致する
2. README の実測値が ``results.json`` と一致する
3. **追跡されるファイルに本物の資格情報が入っていない**
4. ``.env.example`` の変数名が ``common/line_auth.py`` の定数と一致する
5. README が名前を出しているファイルが実在する

**検査対象を環境変数から取らない。**
課題5・課題8で2回踏んだ形がこれである。``os.environ["USERNAME"]`` のような値は
シェル経由で変わり、**空なら黙って0件を「問題なし」として表示する**。
本物の資格情報は ``.env`` から読み、**読めなければ検査を成功させずに落とす**。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import env_file, line_auth  # noqa: E402

TASK = ROOT / "task9"
README = TASK / "README.md"
ROOT_README = ROOT / "README.md"
RESULTS = TASK / "results.json"
ENV_EXAMPLE = ROOT / ".env.example"

PY_EXE = ROOT / ".venv/Scripts/python.exe"

# README の件数表に出す対象。**ここに並べたものだけを照合する**ので、
# 表に行を足したらここにも足す（足し忘れは下の「表の行数」検査で捕まる）。
TEST_FILES = {
    "common/tests/test_env_file.py": None,
    "common/tests/test_line_auth.py": None,
    "task9/tests/test_send_push.py": None,
    "task9/tests/test_verify_push.py": None,
}


class Failure(Exception):
    """検査を始められない。**不一致とは区別する。**"""


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

    added_row = re.search(r"課題9で足したぶん\*\*\s*\|\s*\*\*(\d+)\*\*", readme)
    if added_row:
        check(
            out,
            int(added_row.group(1)) == total_added,
            f"課題9で足したぶん: README={added_row.group(1)} 実測={total_added}",
        )
    else:
        check(out, False, "README に「課題9で足したぶん」の行が無い")

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
    if not RESULTS.is_file():
        # **無いことを「一致した」に倒さない。** 記録が無いなら照合していない。
        check(out, False, f"{RESULTS.name} が無いので実測値を照合できない")
    else:
        record = json.loads(RESULTS.read_text(encoding="utf-8"))
        pairs = [
            ("message ID", record["message_id"]),
            ("x-line-request-id", record["request_id"]),
            ("basicId", record["bot"]["basic_id"]),
            ("bot の userId", record["bot"]["user_id"]),
        ]
        for label, value in pairs:
            check(out, value in readme, f"README に {label} `{value}` がある")

        usage = f"`{record['usage_before']}` → `{record['usage_after']}`"
        check(out, usage in readme, f"README の通数が記録と一致（{usage}）")

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
    # 新規ファイルはまだ追跡されていないので、課題9の成果物も明示的に足す。
    targets = {*tracked, "task9/README.md", "README.md", ".env.example",
               "task9/results.json", "task9/send_push.py", "task9/verify_push.py",
               "common/line_auth.py", "common/env_file.py"}

    leaked: list[str] = []
    for rel in sorted(targets):
        path = ROOT / rel
        if not path.is_file() or path.suffix in {".png", ".jpg", ".pyc"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for secret, name in ((token, "チャネルアクセストークン"), (user_id, "宛先ユーザーID")):
            if secret in text:
                leaked.append(f"{rel} に{name}")

    check(out, not leaked, f"資格情報の漏れ: {len(leaked)} 件" + (f" -> {leaked}" if leaked else ""))

    # --- 4. .env.example の変数名 --------------------------------------
    example = ENV_EXAMPLE.read_text(encoding="utf-8") if ENV_EXAMPLE.is_file() else ""
    for name in (line_auth.CHANNEL_ACCESS_TOKEN_ENV, line_auth.USER_ID_ENV):
        check(out, f"{name}=" in example, f".env.example に {name}= がある")
        check(out, name in readme, f"README に {name} がある")

    # --- 5. README が名前を出すファイルの実在 ---------------------------
    for rel in ("task9/send_push.py", "task9/verify_push.py",
                "common/line_auth.py", "common/env_file.py"):
        check(out, f"`{rel}`" in readme, f"README が {rel} に触れている")
        check(out, (ROOT / rel).is_file(), f"{rel} が実在する")

    # --- 6. README に書いたコマンドが壊れていないか ----------------------
    # **2026-08-19 に実際に踏んだ。** README を書き換えるスクリプトの中で
    # ``task9\tools`` と書いたところ、``\t`` がタブ文字に化けて
    # ``task9<TAB>ools\mutate.py`` になった。同じ行の ``\S`` は「無効なエスケープ」で
    # 警告が出たのに、**``\t`` は有効なので黙って変換された**。
    # **警告が出た側は無事で、出なかった側が壊れる。** 目視では見分けが付かない。
    # （この検査を書いている最中にも同じ形で踏んだ。だから検査として残す。）
    check(out, "\t" not in readme, "README にタブ文字が無い（コマンド行の破損跡）")

    # コマンド例が指すスクリプトが実在するか。壊れたパスはここで落ちる。
    for raw in re.findall(r"python\.exe\s+(\S+\.py)", readme):
        rel = raw.replace("\\", "/")
        check(out, (ROOT / rel).is_file(), f"README のコマンドが指す {rel} が実在する")

    # --- 7. README が参照する画像の実在 ----------------------------------
    # **「表に書いたのに置き忘れた」を捕まえる。** 課題8では
    # 「スクショを撮り直して ID がズレ、文章だけ古いまま残った」を踏んだ。
    # 名前を書いただけで実体が無い状態は、その一歩手前である。
    referenced = re.findall(r"`(docs/[^`]+\.png)`", readme)
    check(out, len(referenced) > 0, "README がスクリーンショットに触れている")
    for rel in sorted(set(referenced)):
        check(out, (TASK / rel).is_file(), f"README が参照する {rel} が実在する")

    # --- 8. ルート README の一覧 ----------------------------------------
    root_text = ROOT_README.read_text(encoding="utf-8")
    check(out, "| 9 | LINE Messaging API |" in root_text, "ルート README に課題9の行がある")
    check(out, "未着手 |" not in root_text.split("| 9 |")[1].split("\n")[0],
          "課題9の行が「未着手」のままになっていない")

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
