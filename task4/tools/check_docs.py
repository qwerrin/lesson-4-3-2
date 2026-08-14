"""README に書いてあることを、コードと実測に突き合わせる。

目視では出ない種類のズレだけを見る。文章の良し悪しは扱わない。

見るもの:

1. **会議 ID が1種類だけか** — 課題1で、実行画面と照合画面が別のファイルを指したまま
   並びかけた。作り直すたびに ID が変わるので、貼り替え漏れが起きやすい。
   README に2つ以上の会議 ID が出てきたら、どちらかが古い。
2. **スコープ名がコードと一致するか** — `meeting:write:admin` と
   `meeting:write:meeting:admin` は別物。文章側だけ旧名のまま残ると、
   読んだ人が Marketplace で違うものを追加する。
3. **件数が実測と一致するか** — テスト件数・壊した箇所の数は増減する。
   文章の数字だけ古くなっても、誰も落ちない。
4. **参照しているファイルが実在するか** — 名前を変えたときに文章が置き去りになる。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task4\\tools\\check_docs.py

NG が1件でもあれば終了コード 1。
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
README = ROOT / "task4" / "README.md"

TEST_PATHS = ("task4/tests", "common/tests/test_zoom_auth.py")

# Zoom の会議 ID は10〜11桁の数字。日付（2026）や桁の少ない数字を拾わない幅にする。
MEETING_ID_PATTERN = re.compile(r"\b\d{10,11}\b")

# 文章中のスコープらしき文字列。granular も classic も拾う。
SCOPE_PATTERN = re.compile(r"\bmeeting:[a-z_]+(?::[a-z_]+)*\b")

# コードは要求していないが、説明のために出す名前。
# ここを増やすほど検査は弱くなるので、理由を書けるものだけ入れる。
MENTIONED_ON_PURPOSE = {
    # `:admin` 付きと前方一致するが別物、という説明の反例として出している。
    "meeting:read:meeting",
}

# 実測と突き合わせるのは、**太字で書かれた数**だけにする。
#
# 最初は「N件」を全部拾っていたが、「スコープ0件」「pytest が1件だけ落ちた」
# 「151件と書いていたが実際は153件だった」まで主張として扱ってしまった。
# **地の文と、現状についての主張は別物**。太字を主張の印として使い、
# 主張だけを検査する。地の文は見ない（見ないことを出力にも書く）。
CLAIM_PATTERN = re.compile(r"\*\*(\d+)\s*(?:件|か所)\*\*")

# README が触れてよいファイル。ここに無いパスを書いていたら NG にする。
PATH_PATTERN = re.compile(r"(?:task4|common)[\\/][\w./\\-]+\.py")


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


def collected_test_count() -> int:
    """pytest に数えさせる。文章側の数字を写さない。"""
    out = _run(["-m", "pytest", *TEST_PATHS, "--collect-only", "-q", "-p", "no:cacheprovider"])
    matched = re.search(r"(\d+)\s+tests? collected", out)
    if not matched:
        raise SystemExit(f"テスト件数を数えられなかった:\n{out[-500:]}")
    return int(matched.group(1))


def total_test_count() -> int:
    out = _run(["-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"])
    matched = re.search(r"(\d+)\s+tests? collected", out)
    if not matched:
        raise SystemExit(f"全体のテスト件数を数えられなかった:\n{out[-500:]}")
    return int(matched.group(1))


def mutation_count() -> int:
    """mutate.py を実行せずに、定義されている壊しかたの数だけ読む。"""
    sys.path.insert(0, str(ROOT / "task4" / "tools"))
    import mutate  # noqa: PLC0415

    return len(mutate.MUTATIONS)


def code_scopes() -> set[str]:
    """コードが実際に要求しているスコープ。README の物差しにする。"""
    sys.path.insert(0, str(ROOT / "task4"))
    sys.path.insert(0, str(ROOT))
    import create_meeting  # noqa: PLC0415
    import verify_meeting  # noqa: PLC0415

    return set(create_meeting.SCOPES) | set(verify_meeting.SCOPES)


def check(text: str) -> list[Result]:
    results: list[Result] = []

    # 1. 会議 ID は1種類だけ
    ids = sorted(set(MEETING_ID_PATTERN.findall(text)))
    results.append(
        Result(
            "会議IDが1種類だけ",
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
            f"コードに無い表記: {', '.join(unknown)}" if unknown else f"{', '.join(found)}",
        )
    )

    # 3. 太字で主張している数が実測と一致
    tests = collected_test_count()
    mutations = mutation_count()
    truths = {
        "課題4関連のテスト": tests,
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

    # 4. 肝心の数がそもそも書いてあるか
    # 「全部が古い」なら 3 は通ってしまう（古い数だけが並ぶので突き合わせる相手がいない）。
    # 実測値が最低1回は太字で出ていることを別に見る。
    must_appear = {"テスト件数": tests, "壊した箇所": mutations}
    absent = {label: n for label, n in must_appear.items() if n not in claimed}
    results.append(
        Result(
            "実測値が太字で書かれている",
            not absent,
            f"書かれていない: {absent}" if absent else f"{must_appear}",
        )
    )

    # 4. 参照しているファイルが実在する
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

    if not all(r.ok for r in results):
        print("食い違いがあります。README を直すか、この検査の期待値を疑うこと。")
        return 1

    print("すべて一致しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
