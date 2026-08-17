"""task8 の README とコードを機械照合する。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task8\\tools\\check_docs.py

目で読んで気づけるのは誤字までで、**「README に書いた件数」と「実際に走る件数」**の
食い違いは目視では出ない。課題4で作って以来、課題を追うごとに検査を足している。

この課題で増えた検査
------------------------------------------------------------------

**Webhook URL の検出。** これがこの課題いちばんの危険物で、
``Authorization`` ヘッダを付けずに投稿できる＝**URL を知っている人は誰でも
そのチャンネルに投稿できる**。それなのに見た目が「ただの URL」なので、
スクリーンショットにも記事にも紛れ込みやすい。Bot Token と同じ強さで探す。

**検査は自分の外側しか見ない**（課題5の教訓）。だから対象は README だけでなく、
``git ls-files`` で数えた「**これから公開されるファイル**」全部にしてある。
この検査スクリプト自身もその中に入る。
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

README = ROOT / "task8" / "README.md"
DOCS_DIR = ROOT / "task8" / "docs"

SEND = ROOT / "task8" / "send_notification.py"
VERIFY = ROOT / "task8" / "verify_notification.py"
AUTH = ROOT / "common" / "discord_auth.py"
MUTATE = ROOT / "task8" / "tools" / "mutate.py"

TEST_FILES = {
    "task8/tests/test_send_notification.py": None,
    "task8/tests/test_verify_notification.py": None,
    "common/tests/test_discord_auth.py": None,
}

# ---------------------------------------------------------------- 危ないもの

# Discord の Bot Token。3 節をドットでつないだ形。
BOT_TOKEN_PATTERN = re.compile(
    r"\b[A-Za-z0-9_-]{24,30}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}\b"
)

# **この課題の主役。** id が数字で、そのあとにトークンが続くものだけを拾う。
# 説明用の `<id>` や `...` は数字ではないので当たらない。
WEBHOOK_URL_PATTERN = re.compile(
    r"https?://(?:[\w-]+\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/\d+/[\w-]+"
)

_PLACEHOLDER_AFTER_HOME = r"(?!\.\.\.|example|<|USERNAME|username|ユーザー名)"
HOME_PATH_PATTERNS = (
    re.compile(r"[Cc]:[\\/]Users[\\/]" + _PLACEHOLDER_AFTER_HOME + r"[\w.-]+"),
    re.compile(r"/home/" + _PLACEHOLDER_AFTER_HOME + r"[\w.-]+"),
)

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RESERVED_DOMAINS = ("example.com", "example.net", "example.org")

OPTION_PATTERN = re.compile(r'add_argument\(\s*\n?\s*"(--[\w-]+)"')
README_OPTION_PATTERN = re.compile(r"(--[\w-]+)")
IMAGE_PATTERN = re.compile(r"`(docs/[\w.-]+\.png)`|\((?:\./)?docs/([\w.-]+\.png)\)")
FILE_TABLE_PATTERN = re.compile(r"`((?:task8|common)/[\w./-]+\.py)`")


@dataclass
class Result:
    label: str
    ok: bool
    detail: str = ""


def _run(args: list[str]) -> str:
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    return proc.stdout + proc.stderr


def _collected(path: str) -> int:
    """pytest に数えさせる。**README の数字を pytest で裏取りする。**"""
    output = _run([str(PYTHON), "-m", "pytest", path, "--collect-only", "-q", "--no-header"])
    match = re.search(r"(\d+) tests? collected", output)
    return int(match.group(1)) if match else -1


def repo_files() -> list[Path]:
    """これから公開されるファイル。追跡中のものと、追跡予定のものの両方。"""
    output = _run(["git", "ls-files", "--cached", "--others", "--exclude-standard"])
    files = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.endswith(".png"):
            continue
        path = ROOT / line
        if path.is_file():
            files.append(path)
    return files


def _readable(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _current_user() -> str:
    """公開物から探す「利用者名」を決める。

    **ホームディレクトリ名を正とする。** 環境変数 ``USERNAME`` は当てにならない
    ——実測では、ホームディレクトリ名の**末尾3文字だけ**が ``USERNAME`` に入っていた
    （シェル経由で値が変わる）。短い誤った名前で探すと、ごく普通の英単語や
    リポジトリの URL にまで部分一致して、**本物の漏れがノイズに埋もれる**。

    なお、この docstring に実際の名前を書いた最初の版は、**この検査自身に
    引っかかった**。課題5の「検査は自分の外側しか見ない」の再演で、
    対象をリポジトリ全体に広げていたから捕まった。
    """
    name = Path.home().name.strip()
    return name or (os.environ.get("USERNAME") or os.environ.get("USER") or "").strip()


def _user_hits(text: str) -> list[str]:
    """利用者名を語境界つきで探す。

    部分一致で探すと ``rin`` が ``string`` に当たる。逆に、名前を特定できない
    ときに**黙って0件を返すと「検査した結果ゼロ」に化ける**ので、
    そのときは呼び出し側で NG にする（この関数は呼ばれない）。
    """
    user = _current_user()
    pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])")
    return pattern.findall(text)


def _real_email_hits(text: str) -> list[str]:
    """メールアドレスらしき文字列。

    **URL の認証情報部（``user:pass@host``）はメールアドレスではない。**
    形が同じなので素直に探すと必ず当たる。除外リストで個別に通すと
    「本物を入れたときも同じ言い訳で通る」ので、**構文として落とす**。
    """
    text = re.sub(r"://[^/\s@]*:[^/\s@]*@", "://", text)

    hits = []
    for found in EMAIL_PATTERN.findall(text):
        if any(found.lower().endswith(domain) for domain in RESERVED_DOMAINS):
            continue
        hits.append(found)
    return hits


def _scan_repo(label: str, finder) -> Result:
    """公開されるファイル全部を1つの検査に掛ける。"""
    hits: list[str] = []
    for path in repo_files():
        text = _readable(path)
        if text is None:
            continue
        for found in finder(text):
            hits.append(f"{path.relative_to(ROOT)}: {found}")
    return Result(label, not hits, "\n      ".join(hits[:10]))


def check() -> list[Result]:
    results: list[Result] = []
    text = README.read_text(encoding="utf-8")

    # ---------------------------------------------------------- 1. 実在するか
    missing = [name for name in FILE_TABLE_PATTERN.findall(text) if not (ROOT / name).exists()]
    results.append(
        Result("README に挙げたファイルが実在する", not missing, f"存在しない: {missing}")
    )

    # ---------------------------------------------------------- 2. オプション名
    for label, source in (("送信", SEND), ("読み返し", VERIFY)):
        declared = set(OPTION_PATTERN.findall(source.read_text(encoding="utf-8")))
        # **1つのコードブロックに複数のコマンドが並ぶ**ので、呼び出し単位で切る。
        # ブロックごと見ると、送信の引数を読み返しのものと混ぜて数えてしまう。
        used: set[str] = set()
        for block in re.findall(r"```powershell\n(.*?)```", text, re.S):
            for segment in re.split(r"(?=\.venv\\Scripts\\python\.exe)", block):
                if f"task8\\{source.stem}.py" in segment:
                    used |= set(README_OPTION_PATTERN.findall(segment))
        unknown = used - declared
        results.append(
            Result(
                f"README の{label}コマンドの引数が実装にある",
                not unknown,
                f"実装に無い引数: {sorted(unknown)}",
            )
        )

    # ---------------------------------------------------------- 3. テストの件数
    total_claim = 0
    for path in TEST_FILES:
        actual = _collected(path)
        claimed = re.search(rf"`{re.escape(path)}` \| (\d+) \|", text)
        number = int(claimed.group(1)) if claimed else -1
        total_claim += max(number, 0)
        results.append(
            Result(
                f"件数が実測と一致: {path}",
                number == actual,
                f"README {number} / 実測 {actual}",
            )
        )

    claimed_sum = re.search(r"\*\*この課題の合計\*\* \| \*\*(\d+)\*\*", text)
    sum_number = int(claimed_sum.group(1)) if claimed_sum else -1
    results.append(
        Result(
            "この課題の合計が内訳の和と一致",
            sum_number == total_claim,
            f"README {sum_number} / 内訳の和 {total_claim}",
        )
    )

    repo_total = _collected(".")
    claimed_repo = re.search(r"リポジトリ全体 \| (\d+) \|", text)
    repo_number = int(claimed_repo.group(1)) if claimed_repo else -1
    results.append(
        Result(
            "リポジトリ全体の件数が実測と一致",
            repo_number == repo_total,
            f"README {repo_number} / 実測 {repo_total}",
        )
    )

    # ---------------------------------------------------------- 4. ミューテーション
    mutations = MUTATE.read_text(encoding="utf-8").count("\n    (\n        ")
    claimed_mutations = re.search(r"\*\*(\d+) か所・素通り 0 件", text)
    number = int(claimed_mutations.group(1)) if claimed_mutations else -1
    results.append(
        Result(
            "ミューテーションの件数が mutate.py と一致",
            number == mutations,
            f"README {number} / mutate.py {mutations}",
        )
    )

    # ---------------------------------------------------------- 5. 依存の版
    version = ""
    for line in _run([str(PYTHON), "-m", "pip", "show", "requests"]).splitlines():
        if line.startswith("Version:"):
            version = line.split(":", 1)[1].strip()
    results.append(
        Result(
            "README に書いた requests の版が実環境と一致",
            bool(version) and version in text,
            f"実環境 {version!r} が README に見つからない",
        )
    )

    # ---------------------------------------------------------- 6. 実装の定数と一致
    auth_source = AUTH.read_text(encoding="utf-8")
    results.append(
        Result(
            "README の User-Agent の形が実装と一致",
            'DiscordBot ($url, $versionNumber)' in text
            and 'USER_AGENT = "DiscordBot (' in auth_source,
            "README と discord_auth.USER_AGENT が食い違う",
        )
    )

    send_source = SEND.read_text(encoding="utf-8")
    results.append(
        Result(
            "メンション抑止の指定が README と実装で一致",
            'MENTIONS_SUPPRESSED = {"parse": []}' in send_source and "既定で全抑止" in text,
            "既定の抑止についての記述と実装が食い違う",
        )
    )

    # ---------------------------------------------------------- 7. 画像の過不足
    referenced = {a or b for a, b in IMAGE_PATTERN.findall(text)}
    on_disk = {f"docs/{p.name}" for p in DOCS_DIR.glob("*.png")} if DOCS_DIR.exists() else set()
    results.append(
        Result(
            "README の画像とファイルが過不足なく対応",
            referenced == on_disk,
            f"README だけ: {sorted(referenced - on_disk)} / ファイルだけ: {sorted(on_disk - referenced)}",
        )
    )

    # ---------------------------------------------------------- 8. 漏れ（リポジトリ全体）
    results.append(_scan_repo("Webhook URL が公開物に無い", WEBHOOK_URL_PATTERN.findall))
    results.append(_scan_repo("Bot Token らしき文字列が公開物に無い", BOT_TOKEN_PATTERN.findall))
    results.append(
        _scan_repo(
            "自宅のパスが公開物に無い",
            lambda body: [m for p in HOME_PATH_PATTERNS for m in p.findall(body)],
        )
    )
    results.append(_scan_repo("実在しうるメールアドレスが公開物に無い", _real_email_hits))

    # **名前が分からないときは「検査した結果ゼロ」ではなく NG にする。**
    # 黙って0件を返すと、検査していないことが「安全だった」に化ける。
    if _current_user():
        results.append(_scan_repo("利用者名が公開物に無い", _user_hits))
    else:
        results.append(
            Result("利用者名が公開物に無い", False, "利用者名を特定できないため検査していない")
        )

    # ---------------------------------------------------------- 9. 実測欄が埋まっているか
    # 見出しに日付などが付くので、行末までを許す。**見出しを完全一致で探すと、
    # 後ろに1文字足しただけで「節が無い」と読めてしまう**（実際に踏んだ）。
    measured = re.search(r"## 実測でわかったこと[^\n]*\n(.*?)(?=\n## )", text, re.S)
    body = (measured.group(1) if measured else "").strip()
    filled = bool(body) and not body.startswith("<!--")
    results.append(
        Result(
            "「実測でわかったこと」が埋まっている",
            filled,
            "実機を走らせるまで空のまま。ここが空の状態で提出しない",
        )
    )

    # ---------------------------------------------------------- 9.5 実測の ID の突合
    #
    # **課題5と課題8で2回、同じ事故を起こしている**——動作確認で1回走らせ、
    # あとからスクリーンショットを撮るためにもう一度走らせると、新しい
    # メッセージが作られて ID が変わる。結果ファイルは上書きされ、
    # 文章だけが古い ID のまま残る。どちらも「成功」の画面なので目視では気づけない。
    known: set[str] = set()
    results_missing: list[str] = []
    for name in ("results-bot.json", "results-webhook.json"):
        path = ROOT / "task8" / name
        if not path.exists():
            results_missing.append(name)
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        for key in ("message_id", "author_id", "guild", "channel"):
            value = str(record.get(key) or "")
            if value:
                known.add(value)

    measured_body = measured.group(1) if measured else ""
    in_readme = set(re.findall(r"\b\d{17,20}\b", measured_body))

    results.append(
        Result(
            "結果ファイルが2経路ぶんそろっている",
            not results_missing,
            f"見つからない: {results_missing}",
        )
    )
    results.append(
        Result(
            "結果ファイルの ID が README の実測に出ている",
            bool(known) and known <= in_readme,
            f"README に出ていない ID: {sorted(known - in_readme)}",
        )
    )
    results.append(
        Result(
            "README の実測に、結果ファイルに無い ID が無い",
            in_readme <= known,
            f"結果ファイルに無い ID: {sorted(in_readme - known)}（古い実行のものが残っていないか）",
        )
    )

    # ---------------------------------------------------------- 10. 未コミット
    dirty = [line for line in _run(["git", "status", "--porcelain"]).splitlines() if line.strip()]
    results.append(
        Result("未コミットの変更が無い", not dirty, f"{len(dirty)} 件の未コミット")
    )

    return results


def main() -> int:
    results = check()

    for result in results:
        mark = "OK" if result.ok else "NG"
        line = f"[{mark}] {result.label}"
        if not result.ok and result.detail:
            line += f"\n      {result.detail}"
        print(line)

    failed = [result for result in results if not result.ok]
    print(f"\n{len(results)} 項目 / NG {len(failed)} 件")

    # **検査していない場所まで保証しているように読める出力にしない**（課題5の教訓）。
    print(
        "この検査が見ていないもの: 画像の中身（枚数と実在しか見ていない）/ "
        "文章の意味 / スクリーンショットに写り込んだ文字。"
    )

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
