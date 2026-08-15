"""README に書いてあることを、コードと実測に突き合わせる。

目視では出ない種類のズレだけを見る。文章の良し悪しは扱わない。

見るもの:

1. **メッセージ ID が1種類だけか** — 送るたびに変わる。実行画面と照合画面が
   別のメールを指したまま並ぶと、読んだ人には見分けがつかない。
2. **スコープ名がコードと一致するか** — `gmail.send` と `gmail.readonly` は
   別物で、しかも README では「使わなかった候補」も並べる。
   コードが要求していない名前を、断りなく書いていないか見る。
3. **トークンのファイル名がコードと一致するか** — 課題5固有。
   設計の途中で `token.json` から送信用・読み取り用の2本に分けたので、
   文章側だけ古い名前で残りやすい。
4. **件数が実測と一致するか** — テスト件数・壊した箇所の数は増減する。
   文章の数字だけ古くなっても、誰も落ちない。
5. **参照しているファイルが実在するか** — 名前を変えたときに文章が置き去りになる。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task5\\tools\\check_docs.py

NG が1件でもあれば終了コード 1。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
README = ROOT / "task5" / "README.md"

TEST_PATHS = ("task5/tests",)

# Gmail のメッセージ ID は16桁の16進。日付や桁の少ない数字を拾わない幅にする。
MESSAGE_ID_PATTERN = re.compile(r"\b[0-9a-f]{16}\b")

# 文章中のスコープらしき文字列。gmail.* と、全権限を表す mail.google.com/ を拾う。
SCOPE_PATTERN = re.compile(r"https://(?:www\.googleapis\.com/auth/gmail\.[a-z]+|mail\.google\.com/)")

# コードは要求していないが、説明のために出す名前。
# ここを増やすほど検査は弱くなるので、理由を書けるものだけ入れる。
MENTIONED_ON_PURPOSE = {
    # send が受け付けるスコープの一覧として出す（採らなかった候補）。
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://mail.google.com/",
    # 読み返しの候補として検討し、本文が読めないので採らなかった。
    "https://www.googleapis.com/auth/gmail.metadata",
}

# 文章に出てくるトークンのファイル名。
TOKEN_PATTERN = re.compile(r"task5/token-[\w-]+\.json")

# 実測と突き合わせるのは、**太字で書かれた数**だけにする。
# 地の文と、現状についての主張は別物。太字を主張の印として使う（課題4と同じ約束）。
CLAIM_PATTERN = re.compile(r"\*\*(\d+)\s*(?:件|か所)\*\*")

# README が触れてよいファイル。ここに無いパスを書いていたら NG にする。
PATH_PATTERN = re.compile(r"(?:task5|common)[\\/][\w./\\-]+\.py")

# 貼った画像。**出力が何枚に分かれるかは撮るまで分からない**ので、
# 文章側に書いた枚数と実物がズレる。2026-08-15 に実際にズレた
# （`05-tests.png` と書いていたが、実物は 1〜4 の4枚だった）。
# 壊れた画像リンクは、GitHub 上で見るまで気づけない。
IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\((docs/[\w.-]+\.png)\)")
DOCS_DIR = ROOT / "task5" / "docs"

# 公開リポジトリに置くページなので、自宅のパスや利用者名を写さない。
#
# コードのほうは既に対策してある（`--credentials` の既定を相対パスにしてある）が、
# **文章側には同じ歯止めが無かった**。2026-08-15 に、チャットの実行ボタン用に
# 書いた `cd ~/Documents/life/...` 付きのコマンドを、そのまま README に
# 貼っていた。実行例はスクリーンショットに撮るので、コマンドに混ざると画像にも残る。
HOME_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]Users[\\/]"),
    re.compile(r"/home/\w"),
    re.compile(r"(?:^|\s)cd\s+~[\\/]"),
)


def _current_user() -> str:
    """利用者名を求める。**ホームディレクトリの実物から取る。**

    最初は `USERNAME` 環境変数を読んでいたが、実測すると値が違った
    （2026-08-15・Git Bash 経由の実行で、環境変数は3文字・実際のホームは5文字）。
    短く切れた名前で部分一致を掛けると、`string` のようなふつうの単語に誤爆する。
    ホームのディレクトリ名は実在するパスなので、こちらのほうが権威がある。

    **この docstring に実際の名前を書かないこと。** 一度書いて、下の
    「ソースにも漏れが無いか」の検査に自分で引っ掛かった。
    """
    try:
        name = Path.home().name
    except (RuntimeError, OSError):
        name = ""
    return name or os.environ.get("USERNAME") or os.environ.get("USER") or ""


# 実在しうるメールアドレスを公開ページに置かない（収集される）。
# RFC 2606 が予約していて実在しない example.com / .net / .org だけ通す。
#
# 2026-08-15、実際に自分のアドレスを README に6か所・テストに6か所書いていた。
# 課題1〜4 のテストは `nana@example.com` を使っていたので、**課題5だけ
# 既存の約束を破っていた**ことになる。人が気づいたので、機械にも見せる。
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RESERVED_DOMAINS = ("example.com", "example.net", "example.org")


def _real_email_hits(text: str) -> list[str]:
    hits = [
        address
        for address in EMAIL_PATTERN.findall(text)
        if not address.lower().endswith(RESERVED_DOMAINS)
    ]
    return sorted(set(hits))


def _home_path_hits(text: str) -> list[str]:
    hits = [m.group(0).strip() for p in HOME_PATH_PATTERNS for m in p.finditer(text)]
    # 利用者名はソースに書かない（書いたらこのファイル自体が漏らす）。実行時に求める。
    # 4文字未満は部分一致の誤爆が大きすぎるので見ない（見ないことは出力に書く）。
    name = _current_user()
    if len(name) >= 4 and name.lower() in text.lower():
        hits.append("利用者名")
    return sorted(set(hits))


@dataclass(frozen=True)
class Result:
    label: str
    ok: bool
    detail: str = ""


def _run(args: list[str]) -> str:
    proc = subprocess.run(
        [str(PYTHON), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout


def _collected(paths: list[str]) -> int:
    out = _run(["-m", "pytest", *paths, "--collect-only", "-q", "-p", "no:cacheprovider"])
    matched = re.search(r"(\d+)\s+tests? collected", out)
    if not matched:
        raise SystemExit(f"テスト件数を数えられなかった:\n{out[-500:]}")
    return int(matched.group(1))


def collected_test_count() -> int:
    """pytest に数えさせる。文章側の数字を写さない。"""
    return _collected(list(TEST_PATHS))


def total_test_count() -> int:
    return _collected([])


def mutation_count() -> int:
    """mutate.py を実行せずに、定義されている壊しかたの数だけ読む。"""
    sys.path.insert(0, str(ROOT / "task5" / "tools"))
    import mutate  # noqa: PLC0415

    return len(mutate.MUTATIONS)


def _task5_modules():
    sys.path.insert(0, str(ROOT / "task5"))
    sys.path.insert(0, str(ROOT))
    import send_mail  # noqa: PLC0415
    import verify_mail  # noqa: PLC0415

    return send_mail, verify_mail


def code_scopes() -> set[str]:
    """コードが実際に要求しているスコープ。README の物差しにする。"""
    send_mail, verify_mail = _task5_modules()
    return set(send_mail.SCOPES) | set(verify_mail.SCOPES)


def code_tokens() -> set[str]:
    send_mail, verify_mail = _task5_modules()
    return {send_mail.DEFAULT_TOKEN, verify_mail.DEFAULT_TOKEN}


def check(text: str) -> list[Result]:
    results: list[Result] = []

    # 1. メッセージ ID は1種類だけ
    ids = sorted(set(MESSAGE_ID_PATTERN.findall(text)))
    results.append(
        Result(
            "メッセージIDが1種類だけ",
            len(ids) <= 1,
            f"見つかった ID: {', '.join(ids) if ids else '(なし)'}",
        )
    )

    # 2. スコープ名がコードと一致
    known = code_scopes() | MENTIONED_ON_PURPOSE
    found = sorted(set(SCOPE_PATTERN.findall(text)))
    unknown = [scope for scope in found if scope not in known]
    results.append(
        Result(
            "スコープ名がコードと一致",
            not unknown,
            f"コードに無い表記: {', '.join(unknown)}" if unknown else f"{len(found)} 種類",
        )
    )

    # 2b. 要求しているスコープが両方とも文章に出ているか。
    #     「余計なものが無い」だけだと、書き落としを見逃す。
    required = code_scopes()
    absent_scopes = sorted(required - set(found))
    results.append(
        Result(
            "要求しているスコープが文章に出ている",
            not absent_scopes,
            f"書かれていない: {', '.join(absent_scopes)}" if absent_scopes else f"{', '.join(sorted(required))}",
        )
    )

    # 3. トークンのファイル名がコードと一致
    tokens_in_code = code_tokens()
    tokens_in_text = set(TOKEN_PATTERN.findall(text))
    wrong = sorted(tokens_in_text - tokens_in_code)
    missing_tokens = sorted(tokens_in_code - tokens_in_text)
    results.append(
        Result(
            "トークンのファイル名がコードと一致",
            not wrong and not missing_tokens,
            (
                f"コードに無い名前: {wrong} / 書かれていない: {missing_tokens}"
                if (wrong or missing_tokens)
                else ", ".join(sorted(tokens_in_code))
            ),
        )
    )

    # 4. 太字で主張している数が実測と一致
    tests = collected_test_count()
    mutations = mutation_count()
    truths = {
        "課題5のテスト": tests,
        "リポジトリ全体のテスト": total_test_count(),
        "壊した箇所": mutations,
    }
    allowed = set(truths.values())
    claimed = sorted({int(n) for n in CLAIM_PATTERN.findall(text)})
    stale = [n for n in claimed if n not in allowed]
    results.append(
        Result(
            "太字で主張している数が実測と一致",
            not stale,
            f"実測に無い数: {stale} / 実測: {truths}" if stale else f"主張: {claimed} / 実測: {truths}",
        )
    )

    # 5. 肝心の数がそもそも書いてあるか
    # 「全部が古い」なら 4 は通ってしまう（古い数だけが並ぶので突き合わせる相手がいない）。
    must_appear = {"テスト件数": tests, "壊した箇所": mutations}
    absent = {label: n for label, n in must_appear.items() if n not in claimed}
    results.append(
        Result(
            "実測値が太字で書かれている",
            not absent,
            f"書かれていない: {absent}" if absent else f"{must_appear}",
        )
    )

    # 6. 自宅のパスや利用者名が写っていない
    leaks = _home_path_hits(text)
    results.append(
        Result(
            "自宅のパスや利用者名が写っていない",
            not leaks,
            f"見つかった: {', '.join(leaks)}" if leaks else "",
        )
    )

    # 7. 実在しうるメールアドレスが書かれていない
    addresses = _real_email_hits(text)
    results.append(
        Result(
            "実在しうるメールアドレスが無い",
            not addresses,
            f"見つかった: {', '.join(addresses)}" if addresses else "",
        )
    )

    # 8. 貼った画像が実在し、撮った画像が貼り忘れられていない
    referenced = {m for m in IMAGE_PATTERN.findall(text)}
    on_disk = {f"docs/{p.name}" for p in DOCS_DIR.glob("*.png")} if DOCS_DIR.is_dir() else set()
    broken = sorted(referenced - on_disk)
    # 撮ったのに貼っていない画像も見る。片方向だけだと、
    # 分割して増えた2枚目以降が黙って落ちる。
    unused = sorted(on_disk - referenced)
    results.append(
        Result(
            "貼った画像と撮った画像が一致",
            not broken and not unused,
            (
                f"存在しない参照: {broken} / 貼られていない画像: {unused}"
                if (broken or unused)
                else f"{len(referenced)} 枚"
            ),
        )
    )

    # 9. README 以外のソースにも、実名・自宅のパス・実アドレスが無い
    #
    # README だけ見ていると、**この検査を書いたファイル自身**が漏らす。
    # 2026-08-15、利用者名を検出する関数の docstring に実際の名前を書いていて、
    # 手で grep するまで気づかなかった。公開されるのは README だけではない。
    source_leaks: list[str] = []
    for path in sorted((ROOT / "task5").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        body = path.read_text(encoding="utf-8")
        found = _home_path_hits(body) + _real_email_hits(body)
        if found:
            source_leaks.append(f"{path.relative_to(ROOT).as_posix()}: {', '.join(found)}")
    results.append(
        Result(
            "ソースにも実名・パス・実アドレスが無い",
            not source_leaks,
            " / ".join(source_leaks) if source_leaks else "task5/**/*.py",
        )
    )

    # 10. 参照しているファイルが実在する
    missing = sorted(
        {p for p in PATH_PATTERN.findall(text) if not (ROOT / p.replace("\\", "/")).exists()}
    )
    results.append(
        Result(
            "参照しているファイルが実在する",
            not missing,
            f"見つからない: {', '.join(missing)}" if missing else "",
        )
    )

    return results


def main() -> int:
    if not README.exists():
        print(f"README が無い: {README}")
        return 2

    results = check(README.read_text(encoding="utf-8"))

    for result in results:
        mark = "OK" if result.ok else "NG"
        line = f"[{mark}] {result.label}"
        if result.detail:
            line += f"\n       {result.detail}"
        print(line)

    # 見ていない範囲を黙って隠さない。太字でない数字は地の文として素通りする。
    print("\n（太字でない数字は検査していない。現状の主張は太字で書くこと）")
    # **画像の中身は誰も検査していない。** 枚数と実在しか見ていない。
    # 2026-08-15、文章のほうは「メッセージIDが1種類だけ」を通っていたのに、
    # 実行画面と照合画面のスクリーンショットが別のメールを指していた。
    # アドレス・実名・パスの写り込みも同じで、人が目で見るしかない。
    print("（画像は枚数と実在しか見ていない。写っている値・アドレス・実名は目視で確認すること）")

    if not all(r.ok for r in results):
        print("食い違いがあります。README を直すか、この検査の期待値を疑うこと。")
        return 1

    print("すべて一致しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
