"""task10/line/tools/check_docs.py の、**自分の項目数を自分で検査する**部分のテスト。

課題9から持ち越した宿題がこれである。課題9の check_docs.py は
「照合 38 項目 / NG 0 件」と最後に出していたが、**その 38 が正しいかは
誰も確かめていなかった**。検査を1つ足せば 39 になるのに、README とスクショは
38 のまま残る。

課題9で入れなかった理由は「足すと39項目になり、提出済みのスクショの38と
食い違う」だった。**課題10では最初から入れる**。

いちばん間違えやすいのは**自分自身を数えるかどうか**である。この検査は
最後に積まれるので、積む直前の件数には自分が入っていない。+1 を忘れると
README には常に1つ少ない数を書くことになり、しかも**その状態で一致する**
——ずれた物差しどうしが噛み合ってしまう。だから境界を1つずつ固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import check_docs  # noqa: E402


def readme_saying(count):
    return f"# 課題10\n\n照合 **{count}** 項目 / NG 0 件\n"


# ============================================================ 名乗りを読む


def test_the_stated_count_is_read():
    assert check_docs.read_stated_count(readme_saying(42)) == 42


def test_a_readme_without_the_count_reads_as_none():
    """**無いのを 0 と読まない。** 0 は「1件も検査していない」という値である。"""
    assert check_docs.read_stated_count("# 課題10\n\n何も書いていない\n") is None


# ============================================================ 自分を数える


def test_the_check_counts_itself():
    """積む直前が 41 件なら、README は 42 と名乗るのが正しい。

    **この検査自身が42件目になる。**
    """
    ok, _label = check_docs.self_count_check(readme_saying(42), 41)

    assert ok is True


def test_forgetting_to_count_itself_fails():
    """41 と名乗っていたら不一致。**ずれた物差しどうしを噛み合わせない。**"""
    ok, _label = check_docs.self_count_check(readme_saying(41), 41)

    assert ok is False


def test_counting_itself_twice_fails():
    ok, _label = check_docs.self_count_check(readme_saying(43), 41)

    assert ok is False


def test_a_readme_without_the_count_fails():
    """名乗っていないものを「一致した」にしない。"""
    ok, _label = check_docs.self_count_check("# 課題10\n", 41)

    assert ok is False


def test_the_label_shows_both_numbers():
    """何と何を比べたのかを出す。**片方だけでは主張にならない。**"""
    _ok, label = check_docs.self_count_check(readme_saying(41), 41)

    assert "41" in label
    assert "42" in label
