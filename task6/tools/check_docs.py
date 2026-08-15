"""README に書いてあることを、コードと実測に突き合わせる。

目視では出ない種類のズレだけを見る。文章の良し悪しは扱わない。

**課題6で範囲を広げた。** 課題4で作り、課題5で「ソースも見る」まで広げたが、
見ていたのは `task5/**` だけだった。同じ漏れが課題1・2・4に残っていても
気づけない状態だったので、漏れの検査を**リポジトリ全体**に掛ける
（`git ls-files` ＋ 未追跡で無視されていないファイル＝これから入るもの）。

課題6で新しく要るもの:

- **API キーらしき文字列がリポジトリのどこにも無いか**。API キーは URL の
  クエリに載るので、エラー出力・ログ・スクリーンショットから紛れ込みやすい
- **動画IDが実行結果と一致するか**。課題5の「メッセージIDが1種類だけ」に当たる。
  検索は毎回結果が変わるので、README と実際の実行結果がずれやすい。
  こちらは `results.json`（実行の成果物）を物差しにして突き合わせる

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task6\\tools\\check_docs.py

NG が1件でもあれば終了コード 1。
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
README = ROOT / "task6" / "README.md"
DOCS_DIR = ROOT / "task6" / "docs"
RESULTS = ROOT / "task6" / "results.json"

TEST_PATHS = ("task6/tests", "common/tests/test_youtube_auth.py")

# 文章に出てくる視聴 URL。動画 ID をここから取り出す。
WATCH_URL_PATTERN = re.compile(r"https://www\.youtube\.com/watch\?v=([\w-]+)")

# YouTube の動画 ID は 11 文字。
VIDEO_ID_SHAPE = re.compile(r"\A[A-Za-z0-9_-]{11}\Z")

# **Google の API キーの形。** AIza で始まる 39 文字。
# 本物がリポジトリに入ったら、この検査だけが気づける。
# テストのダミー鍵はこの形を**わざと避けてある**ので、除外リストは持たない
# （除外リストを持つと、本物を入れたときも同じ言い訳で通してしまう）。
API_KEY_PATTERN = re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")

# 実測と突き合わせるのは、**太字で書かれた数**だけにする。
# 地の文と、現状についての主張は別物。太字を主張の印として使う（課題4から同じ約束）。
CLAIM_PATTERN = re.compile(r"\*\*(\d+)\s*(?:件|か所)\*\*")

# README が触れてよいファイル。ここに無いパスを書いていたら NG にする。
PATH_PATTERN = re.compile(r"(?:task6|common)[\\/][\w./\\-]+\.py")

IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\((docs/[\w.-]+\.png)\)")

# 公開リポジトリに置くページなので、自宅のパスや利用者名を写さない。
#
# **プレースホルダは除外する。** リポジトリ全体に広げた初回（2026-08-16）、
# 4件ヒットして全部が誤検出だった——「公開する画面に `C:\\Users\\...` を写さない」
# という注意書き（3件）と、`C:/Users/example/` というミューテーション用の偽値（1件）。
# 本物の利用者名は1件も無い。
#
# 毎回鳴る4件を放置すると検査そのものが読まれなくなるので、
# **後ろに続くのが明らかな伏せ字なら見逃す**。危ないのは実在の名前が続くときで、
# それは下の「利用者名」の検査が別に見ている（そちらは伏せられない）。
_PLACEHOLDER_AFTER_HOME = r"(?!\.\.\.|example|<|USERNAME|username|ユーザー名)"
HOME_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]Users[\\/]" + _PLACEHOLDER_AFTER_HOME),
    re.compile(r"/home/" + _PLACEHOLDER_AFTER_HOME + r"\w"),
    re.compile(r"(?:^|\s)cd\s+~[\\/]"),
)

# 実在しうるメールアドレスを公開ページに置かない（収集される）。
# RFC 2606 が予約していて実在しない example.com / .net / .org だけ通す。
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RESERVED_DOMAINS = ("example.com", "example.net", "example.org")


def _current_user() -> str:
    """利用者名を求める。**ホームディレクトリの実物から取る。**

    環境変数 USERNAME は実測すると値が違うことがある（2026-08-15・Git Bash 経由で
    3文字と5文字の食い違い）。短く切れた名前で部分一致を掛けると、ふつうの単語に誤爆する。

    **この docstring に実際の名前を書かないこと。** 課題5で一度書いて、
    下の「リポジトリ全体の漏れ」検査に自分で引っ掛かった。
    """
    try:
        name = Path.home().name
    except (RuntimeError, OSError):
        name = ""
    return name or os.environ.get("USERNAME") or os.environ.get("USER") or ""


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
    sys.path.insert(0, str(ROOT / "task6" / "tools"))
    import mutate  # noqa: PLC0415

    return len(mutate.MUTATIONS)


def _task6_modules():
    sys.path.insert(0, str(ROOT / "task6"))
    sys.path.insert(0, str(ROOT))
    import search_videos  # noqa: PLC0415

    from common import youtube_auth  # noqa: PLC0415

    return search_videos, youtube_auth


def repo_files() -> list[Path]:
    """これから公開されるファイルを列挙する。

    追跡済み（`--cached`）＋ 未追跡で無視されていないもの（`--others
    --exclude-standard`）。**「手元で無視されている」と「リモートに無い」は別**
    なので、無視されていないものは全部これから入るものとして扱う。
    """
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit(f"git ls-files に失敗した:\n{proc.stderr}")

    paths = []
    for line in proc.stdout.splitlines():
        name = line.strip()
        if not name:
            continue
        path = ROOT / name
        if path.is_file():
            paths.append(path)
    return paths


def _readable(path: Path) -> str | None:
    """テキストとして読めれば中身、読めなければ None（画像など）。"""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def recorded_video_ids() -> set[str] | None:
    """実行の成果物（results.json）に記録されている動画 ID。

    README の物差しにする。**文章の中の値どうしを比べない**——実行結果を
    外から持ってきて突き合わせる。
    """
    if not RESULTS.exists():
        return None
    try:
        payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None

    search_videos, _ = _task6_modules()
    ids = set()
    for record in payload.get("videos", []):
        video_id = search_videos.video_id_from_url(record.get("url"))
        if video_id:
            ids.add(video_id)
    return ids


def check(text: str) -> list[Result]:
    search_videos, youtube_auth = _task6_modules()
    results: list[Result] = []

    # 1. 文章に出てくる動画 ID が、実際の実行結果と一致する
    #    検索は実行のたびに結果が変わる。撮り直した実行画面と文章がずれても、
    #    どちらも「それらしい」ので目では気づけない（課題5でスクショがずれた形）。
    in_text = set(WATCH_URL_PATTERN.findall(text))
    recorded = recorded_video_ids()
    if recorded is None:
        results.append(Result("動画IDが実行結果と一致", False, f"{RESULTS} が無いか読めない"))
    else:
        extra = sorted(in_text - recorded)
        absent = sorted(recorded - in_text)
        results.append(
            Result(
                "動画IDが実行結果と一致",
                not extra and not absent,
                (
                    f"実行結果に無い: {extra} / 文章に無い: {absent}"
                    if (extra or absent)
                    else f"{len(in_text)} 件"
                ),
            )
        )

    # 2. 動画 ID の形が正しい（11文字）
    malformed = sorted({v for v in in_text if not VIDEO_ID_SHAPE.match(v)})
    results.append(
        Result(
            "動画IDの形式が正しい",
            not malformed,
            f"11文字でない: {malformed}" if malformed else f"{len(in_text)} 件",
        )
    )

    # 3. 視聴 URL の形がコードと一致する
    results.append(
        Result(
            "視聴URLの形がコードと一致",
            search_videos.WATCH_URL_PREFIX in text,
            f"文章に無い: {search_videos.WATCH_URL_PREFIX}"
            if search_videos.WATCH_URL_PREFIX not in text
            else search_videos.WATCH_URL_PREFIX,
        )
    )

    # 4. 環境変数名がコードと一致する
    #    コードの定数だけ変えて README を直し忘れると、書いてある手順で動かない。
    results.append(
        Result(
            "環境変数名がコードと一致",
            youtube_auth.API_KEY_ENV in text,
            f"文章に無い: {youtube_auth.API_KEY_ENV}"
            if youtube_auth.API_KEY_ENV not in text
            else youtube_auth.API_KEY_ENV,
        )
    )

    # 5. 1日の上限がコードと一致する
    limit = str(search_videos.DAILY_SEARCH_CALL_LIMIT)
    results.append(
        Result(
            "1日の呼び出し上限がコードと一致",
            limit in text,
            f"文章に無い: {limit}" if limit not in text else f"{limit} 回",
        )
    )

    # 6. 太字で主張している数が実測と一致
    tests = collected_test_count()
    mutations = mutation_count()
    truths = {
        "課題6のテスト": tests,
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

    # 7. 肝心の数がそもそも書いてあるか
    #    「全部が古い」なら 6 は通ってしまう（古い数だけが並ぶので相手がいない）。
    must_appear = {"テスト件数": tests, "壊した箇所": mutations}
    absent_claims = {label: n for label, n in must_appear.items() if n not in claimed}
    results.append(
        Result(
            "実測値が太字で書かれている",
            not absent_claims,
            f"書かれていない: {absent_claims}" if absent_claims else f"{must_appear}",
        )
    )

    # 8. README に自宅のパスや利用者名が写っていない
    leaks = _home_path_hits(text)
    results.append(
        Result(
            "READMEに自宅のパスや利用者名が無い",
            not leaks,
            f"見つかった: {', '.join(leaks)}" if leaks else "",
        )
    )

    # 9. README に実在しうるメールアドレスが無い
    addresses = _real_email_hits(text)
    results.append(
        Result(
            "READMEに実在しうるメールアドレスが無い",
            not addresses,
            f"見つかった: {', '.join(addresses)}" if addresses else "",
        )
    )

    # 10. 貼った画像が実在し、撮った画像が貼り忘れられていない
    referenced = set(IMAGE_PATTERN.findall(text))
    on_disk = {f"docs/{p.name}" for p in DOCS_DIR.glob("*.png")} if DOCS_DIR.is_dir() else set()
    broken = sorted(referenced - on_disk)
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

    # 11. **リポジトリ全体**に実名・自宅のパス・実アドレスが無い（課題6で格上げ）
    #
    #     課題5までは自分の課題フォルダしか見ていなかった。同じ漏れが
    #     課題1・2・4に残っていても検出できない状態だったので、全体に掛ける。
    files = repo_files()
    skipped = 0
    source_leaks: list[str] = []
    for path in files:
        body = _readable(path)
        if body is None:
            skipped += 1
            continue
        found = _home_path_hits(body) + _real_email_hits(body)
        if found:
            source_leaks.append(f"{path.relative_to(ROOT).as_posix()}: {', '.join(found)}")
    results.append(
        Result(
            "リポジトリ全体に実名・パス・実アドレスが無い",
            not source_leaks,
            " / ".join(source_leaks)
            if source_leaks
            else f"{len(files) - skipped} ファイル（画像など {skipped} 件は読めないので見ていない）",
        )
    )

    # 12. **リポジトリ全体**に API キーらしき文字列が無い（課題6で追加）
    #
    #     API キーは URL のクエリに載るので、エラー出力やログを貼った拍子に
    #     紛れ込みやすい。コードの側は redact() で伏せているが、
    #     手で貼り付けた文章までは守れない。
    key_leaks: list[str] = []
    for path in files:
        body = _readable(path)
        if body is None:
            continue
        if API_KEY_PATTERN.search(body):
            key_leaks.append(path.relative_to(ROOT).as_posix())
    results.append(
        Result(
            "リポジトリ全体にAPIキーらしき文字列が無い",
            not key_leaks,
            f"見つかった: {', '.join(key_leaks)}" if key_leaks else f"{len(files) - skipped} ファイル",
        )
    )

    # 13. 参照しているファイルが実在する
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

    # 見ていない範囲を黙って隠さない。「すべて一致」だけを出す道具は、
    # 検査していない場所まで保証しているように読める（課題5の教訓）。
    print("\n（太字でない数字は検査していない。現状の主張は太字で書くこと）")
    print("（画像は枚数と実在しか見ていない。写っている値・アドレス・実名・APIキーは目視で確認すること）")
    print("（キーワードと検索結果の中身の妥当性は検査していない。実行画面と照らすこと）")

    if not all(r.ok for r in results):
        print("食い違いがあります。README を直すか、この検査の期待値を疑うこと。")
        return 1

    print("すべて一致しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
