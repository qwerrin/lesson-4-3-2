"""task7 の README とコードを機械照合する。

文章は目で読んでも合っているように見える。数を数え直したり、コードの定数と
突き合わせたりは機械にしかできない（課題4で作って以降、毎回ここで食い違いが出る）。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task7\\tools\\check_docs.py

**この道具が見ていない範囲がある。** 画像は「枚数と実在」しか見ていないので、
中身が正しいかは人が見るしかない。出力の最後にそう書いてある。
「すべて一致しました」だけを出す道具は、検査していない場所まで保証しているように読める。

課題6から引き継いだ検査に、Slack 固有のものを足した。
------------------------------------------------------------------

- **Bot Token の漏れ**（`xoxb-` / `xoxp-`）。API キーと違って URL には載らないが、
  ソースや文章に貼れば同じように漏れる
- **README に書いた実測値が、実装の関数と整合しているか**。
  「送った本文」と「返った本文」を README から取り出し、
  ``post_message.escape_for_slack()`` を通して一致するかを確かめる。
  **文章に書いた実測値がコードの挙動と食い違っていたら、どちらかが嘘になる**
- **スコープ名・エラーコード・コマンドのオプション名**を実装から取って突き合わせる
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

README = ROOT / "task7" / "README.md"
RESULTS = ROOT / "task7" / "results.json"
DOCS_DIR = ROOT / "task7" / "docs"

POST = ROOT / "task7" / "post_message.py"
VERIFY = ROOT / "task7" / "verify_message.py"
AUTH = ROOT / "common" / "slack_auth.py"

TEST_PATHS = ("task7", "common/tests/test_slack_auth.py")

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "task7"))

import post_message  # noqa: E402
import verify_message  # noqa: E402
from common import slack_auth  # noqa: E402

sys.path.insert(0, str(ROOT / "task7" / "tools"))
import mutate  # noqa: E402


# **Slack のトークンの形。** xox[bpasr]- のあとに数字が続く。
# テストのダミーは `xoxb-DUMMY-...` で**わざとこの形を避けてある**ので、
# 除外リストは持たない（除外リストを持つと、本物を入れたときも同じ言い訳で通る）。
SLACK_TOKEN_PATTERN = re.compile(r"\bxox[bpasr]-\d[\w-]{10,}\b")

# Google の API キー（課題6から引き継ぎ）。AIza で始まる 39 文字。
API_KEY_PATTERN = re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")

# 実測と突き合わせるのは、**太字で書かれた数**だけにする。
# 地の文と、現状についての主張は別物（課題4から同じ約束）。
#
# 太字ブロックを丸ごと取ってから中の数を拾う。`**74件**` のように数だけを
# 太字にする書き方に限定すると、`**74か所すべてでテストが落ちた**` のような
# **文ごと太字にした主張を1つも拾えない**（最初の実装がそれで4件を取りこぼした）。
BOLD_PATTERN = re.compile(r"\*\*([^*]+)\*\*")

# コマンドのオプションを拾う対象。README 全体から拾うと、表の区切り線（`---`）や
# pytest の `--no-header` まで「実装に無いオプション」として鳴る。
CODE_BLOCK_PATTERN = re.compile(r"```[\w]*\n(.*?)```", re.S)

# README が触れてよいファイル。ここに無いパスを書いていたら NG にする。
PATH_PATTERN = re.compile(r"(?:task7|common)[\\/][\w./\\-]+\.py")

IMAGE_TABLE_PATTERN = re.compile(r"`(docs/[\w.-]+\.png)`")

# 実測値の記録（README の「実測でわかったこと」）。
SENT_PATTERN = re.compile(r"送った本文 : '([^']*)'")
GOT_PATTERN = re.compile(r"返った本文 : '([^']*)'")

# 実装の引数定義を読む。
OPTION_PATTERN = re.compile(r'add_argument\(\s*\n?\s*"(--[\w-]+)"')
README_OPTION_PATTERN = re.compile(r"(--[\w-]+)")

_PLACEHOLDER_AFTER_HOME = r"(?!\.\.\.|example|<|USERNAME|username|ユーザー名)"
HOME_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]Users[\\/]" + _PLACEHOLDER_AFTER_HOME),
    re.compile(r"/home/" + _PLACEHOLDER_AFTER_HOME + r"\w"),
    re.compile(r"(?:^|\s)cd\s+~[\\/]"),
)

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RESERVED_DOMAINS = ("example.com", "example.net", "example.org")


@dataclass
class Result:
    label: str
    ok: bool
    detail: str = ""


def _current_user() -> str:
    """利用者名を求める。**ホームディレクトリの実物から取る。**

    **この docstring に実際の名前を書かないこと。** 課題5で一度書いて、
    下の「リポジトリ全体の漏れ」検査に自分で引っ掛かった。
    """
    try:
        name = Path.home().name
    except (RuntimeError, OSError):
        name = ""
    return name or os.environ.get("USERNAME") or os.environ.get("USER") or ""


def _run(args: list[str]) -> str:
    """子プロセスの出力を読む。

    **encoding を明示する。** 既定は Windows だと cp932 になり、テスト名に
    使っている日本語（UTF-8）が混ざった時点で UnicodeDecodeError で落ちる。
    しかも例外はリーダースレッドの中で出るので、``stdout`` が None のまま
    返ってきて、**本当の原因から遠いところ**（None + str）で落ちる。
    """
    proc = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return (proc.stdout or "") + (proc.stderr or "")


def _collected(paths: list[str]) -> int:
    out = _run([str(PYTHON), "-m", "pytest", *paths, "--collect-only", "-q", "--no-header"])
    found = re.search(r"(\d+)\s+tests?\s+collected", out)
    return int(found.group(1)) if found else -1


def repo_files() -> list[Path]:
    """**これから公開されるファイル**を列挙する（課題6で全体に格上げした）。

    追跡済み＋未追跡（ただし .gitignore を尊重）。検査対象を README だけに
    絞っていると、道具は自分の外側しか見ない状態になる。
    """
    out = _run(["git", "ls-files", "--cached", "--others", "--exclude-standard"])
    paths = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        path = ROOT / line
        if path.is_file():
            paths.append(path)
    return paths


def _readable(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _real_email_hits(text: str) -> list[str]:
    hits = [
        address
        for address in EMAIL_PATTERN.findall(text)
        if not address.lower().endswith(RESERVED_DOMAINS)
    ]
    return sorted(set(hits))


def _home_path_hits(text: str) -> list[str]:
    return sorted({m.group(0).strip() for p in HOME_PATH_PATTERNS for m in p.finditer(text)})


def _source_options(path: Path) -> set[str]:
    return set(OPTION_PATTERN.findall(path.read_text(encoding="utf-8")))


def _claimed_numbers(text: str) -> set[int]:
    numbers: set[int] = set()
    for bold in BOLD_PATTERN.findall(text):
        numbers.update(int(n) for n in re.findall(r"\d+", bold))
    return numbers


def _readme_command_options(text: str) -> set[str]:
    """この課題のスクリプトを呼んでいるコードブロックだけからオプションを拾う。"""
    options: set[str] = set()
    for block in CODE_BLOCK_PATTERN.findall(text):
        if "post_message.py" in block or "verify_message.py" in block:
            options.update(README_OPTION_PATTERN.findall(block))
    return options


def check(text: str) -> list[Result]:
    results: list[Result] = []

    def add(label, ok, detail=""):
        results.append(Result(label, ok, detail))

    # ---------------------------------------------------------- 数の主張
    claimed = _claimed_numbers(text)

    task7_tests = _collected(["task7"])
    auth_tests = _collected(["common/tests/test_slack_auth.py"])
    total_tests = _collected([])
    mutations = len(mutate.MUTATIONS)

    # 個別の件数（97 / 45 / 1003）は下の表の検査が見ている。ここで見るのは
    # 「文章で強調して主張している数」だけにする。
    for label, value in (
        ("課題7ぶんの合計", task7_tests + auth_tests),
        ("ミューテーション件数", mutations),
    ):
        add(f"{label}が README の太字にある（実測 {value}）", value in claimed, f"実測 {value}")

    # 表の中の数（太字にしていない）も突き合わせる。
    add(
        f"README のテスト表と実測が一致（task7={task7_tests} / auth={auth_tests} / 全体={total_tests}）",
        f"| {task7_tests} |" in text and f"| {auth_tests} |" in text and f"| {total_tests} |" in text,
        f"実測 task7={task7_tests} auth={auth_tests} 全体={total_tests}",
    )

    # ---------------------------------------------------------- 実装との整合
    scopes = set(post_message.SCOPES) | set(verify_message.SCOPES)
    missing_scopes = sorted(scope for scope in scopes if scope not in text)
    add("実装のスコープが README に書かれている", not missing_scopes, " / ".join(missing_scopes))

    # README に出てくるスコープらしき語が、実装の定数に無いものを含んでいないか。
    for scope in ("chat:write", "channels:history"):
        add(f"README の {scope} が実装の定数にある", scope in scopes)

    hinted = set(post_message._ERROR_HINTS)
    quoted_codes = {code for code in hinted if code in text}
    add(
        "README が触れているエラーコードは実装が知っている",
        bool(quoted_codes),
        f"README に出ている: {', '.join(sorted(quoted_codes)) or '(なし)'}",
    )
    add("not_in_channel の案内が実装にある", "not_in_channel" in hinted)

    # コマンドのオプション名。README の実行例が実装と食い違っていたら動かない。
    readme_options = _readme_command_options(text)
    known_options = _source_options(POST) | _source_options(VERIFY)
    unknown = sorted(readme_options - known_options)
    add("README のコマンドのオプションが実装にある", not unknown, " / ".join(unknown))

    # ---------------------------------------------------------- 実測値の整合
    sent = SENT_PATTERN.search(text)
    got = GOT_PATTERN.search(text)
    if sent and got:
        converted = post_message.escape_for_slack(sent.group(1))
        add(
            "README の「送った本文→返った本文」が escape_for_slack と一致する",
            converted == got.group(1),
            "" if converted == got.group(1) else f"変換すると {converted!r}",
        )
        add(
            "README の実測値は「そのままでは一致しない」例になっている",
            sent.group(1) != got.group(1),
            "変換前後が同じ値だと、この罠の説明が成り立たない",
        )
    else:
        add("README に送信前後の本文が載っている", False, "実測値のブロックが見つからない")

    if RESULTS.exists():
        record = json.loads(RESULTS.read_text(encoding="utf-8"))
        for key in ("channel", "ts", "permalink", "posted_by"):
            value = record.get(key, "")
            add(f"results.json の {key} が README に載っている", value in text, value)

        ts = record.get("ts", "")
        permalink = record.get("permalink", "")
        expected_tail = "p" + ts.replace(".", "")
        add(
            "permalink の末尾が ts から導ける形になっている",
            permalink.endswith(expected_tail),
            f"期待する末尾 {expected_tail}",
        )
        add(
            "results.json のチャンネルとリンクの指す先が同じ",
            record.get("channel", "") in permalink,
        )
    else:
        add("results.json がある", False, str(RESULTS))

    # ---------------------------------------------------------- パスと画像
    referenced = sorted(set(PATH_PATTERN.findall(text)))
    missing_paths = [p for p in referenced if not (ROOT / p.replace("\\", "/")).exists()]
    add("README が挙げた .py が実在する", not missing_paths, " / ".join(missing_paths))

    listed = sorted(set(IMAGE_TABLE_PATTERN.findall(text)))
    actual = sorted(f"docs/{p.name}" for p in DOCS_DIR.glob("*.png")) if DOCS_DIR.is_dir() else []

    # 表はレンジ表記（`01-a.png` 〜 `04-d.png`）を含むので、載っている名前が
    # 実在することだけを見る。実在するのに載っていないものは別に報告する。
    absent = [name for name in listed if name not in actual]
    add("README が挙げた画像が実在する", not absent, " / ".join(absent))
    add("docs/ に画像がある", bool(actual), f"{len(actual)} 枚")

    # ---------------------------------------------------------- 漏れ（リポジトリ全体）
    user = _current_user()
    leaked_user: list[str] = []
    leaked_token: list[str] = []
    leaked_key: list[str] = []
    leaked_home: list[str] = []
    leaked_mail: list[str] = []

    for path in repo_files():
        body = _readable(path)
        if body is None:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if user and user in body:
            leaked_user.append(rel)
        if SLACK_TOKEN_PATTERN.search(body):
            leaked_token.append(rel)
        if API_KEY_PATTERN.search(body):
            leaked_key.append(rel)
        if _home_path_hits(body):
            leaked_home.append(rel)
        if _real_email_hits(body):
            leaked_mail.append(rel)

    add("利用者名がどこにも無い", not leaked_user, " / ".join(leaked_user))
    add("Slack のトークンがどこにも無い", not leaked_token, " / ".join(leaked_token))
    add("API キーがどこにも無い", not leaked_key, " / ".join(leaked_key))
    add("自宅のパスがどこにも無い", not leaked_home, " / ".join(leaked_home))
    add("実在しうるメールアドレスがどこにも無い", not leaked_mail, " / ".join(leaked_mail))

    return results


def main() -> int:
    if not README.exists():
        print(f"README が見つかりません: {README}", file=sys.stderr)
        return 1

    results = check(README.read_text(encoding="utf-8"))

    for result in results:
        mark = "OK" if result.ok else "NG"
        line = f"[{mark}] {result.label}"
        if result.detail:
            line += f"  — {result.detail}"
        print(line)

    ng = [r for r in results if not r.ok]
    print()
    print(f"{len(results)} 項目 / NG {len(ng)} 件")
    # **見ていない範囲を明示する。** 画像の中身は機械に見えない。
    print("この検査が見ていないもの: 画像の中身（枚数と実在しか見ていない）/ 文章の意味の正しさ")

    return 1 if ng else 0


if __name__ == "__main__":
    raise SystemExit(main())
