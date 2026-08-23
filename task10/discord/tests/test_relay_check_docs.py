"""tools/check_docs.py の、**自分の項目数を自分で検査する**部分のテスト。

課題9から持ち越した宿題で、課題10（LINE）から入れている。
検査を1つ足せば項目数が増えるのに、README とスクリーンショットは古い数のまま
残る——それを機械で捕まえる。

いちばん間違えやすいのは**自分自身を数えるかどうか**である。この検査は最後に
積まれるので、積む直前の件数には自分が入っていない。``+1`` を忘れると README には
常に1つ少ない数を書くことになり、**しかもその状態で一致する**——ずれた物差し
どうしが噛み合ってしまう。だから境界を1つずつ固定する。

> この課題では、実際に**テストの件数が2行とも間違っていた**のを検査が捕まえた
> （README 38/43 に対して実際は 33/48）。合計の 97 だけが合っていたので、
> 目で読んでも気づけない形だった。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_TASK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_TASK.parents[1]))
sys.path.insert(0, str(_TASK / "tools"))


def _load(name: str, path: Path):
    """**モジュールを名前ではなくパスで読む。**

    ``task10/line/tools/`` にも ``check_docs.py`` がある。名前で ``import`` すると
    **先に読まれたほうが ``sys.modules`` に居座る**ので、2番目のテストは
    自分のものではないモジュールを検査して——実装がほぼ同じなので——**通る**。

    実際に確かめた::

        1回目の import -> discord   （sys.path の先頭は discord/tools）
        2回目の import -> discord   （sys.path の先頭を line/tools にしても同じ）

    ファイル名を分けるだけでは足りない。テストファイルの衝突（pytest の
    ``import file mismatch``）は**エラーで止まる**ので気づけるが、
    こちらは**黙って通る**ぶん質が悪い。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_docs = _load("discord_check_docs", _TASK / "tools" / "check_docs.py")


def test_the_module_under_test_is_the_discord_one():
    """**取り違えていないことを、テスト自身が確かめる。**

    ここが無いと、将来この読み込みが名前解決に戻ったときに、
    残りのテストは全部通ったまま別モジュールを検査する。
    """
    assert Path(check_docs.__file__).parents[1].name == "discord"


def readme_saying(count):
    return f"# 課題10（Discord）\n\n照合 **{count}** 項目 / NG 0 件\n"


# ============================================================ 名乗りを読む


def test_the_stated_count_is_read():
    assert check_docs.read_stated_count(readme_saying(52)) == 52


def test_a_readme_without_the_count_reads_as_none():
    """**無いのを 0 と読まない。** 0 は「1件も検査していない」という値である。"""
    assert check_docs.read_stated_count("# 課題10\n\n何も書いていない\n") is None


# ============================================================ 自分を数える


def test_the_check_counts_itself():
    """積む直前が 51 件なら、README は 52 と名乗るのが正しい。

    **この検査自身が 52 件目になる。**
    """
    ok, _label = check_docs.self_count_check(readme_saying(52), 51)

    assert ok is True


def test_forgetting_to_count_itself_is_caught():
    """自分を数え忘れた README（51）は不一致になる。"""
    ok, _label = check_docs.self_count_check(readme_saying(51), 51)

    assert ok is False


def test_counting_itself_twice_is_caught():
    ok, _label = check_docs.self_count_check(readme_saying(53), 51)

    assert ok is False


def test_a_readme_that_never_states_the_count_fails():
    """**名乗っていないことを合格にしない。**"""
    ok, label = check_docs.self_count_check("# 何も書いていない\n", 51)

    assert ok is False
    assert "52" in label


# ============================================================ 秘密の検査


def test_a_home_path_is_detected():
    """実行画面も提出物なので、自宅パスが文章に混ざっていたら止める。"""
    assert check_docs.HOME_PATH_PATTERN.search(r"C:\Users\someone\Documents") is not None


def test_a_generic_windows_path_is_not_a_false_positive():
    assert check_docs.HOME_PATH_PATTERN.search(r".venv\Scripts\python.exe") is None


def test_an_api_key_shape_is_detected():
    """**キーそのものは書かない。** 形だけを持つダミーで確かめる。"""
    assert check_docs.API_KEY_PATTERN.search("AIzaSyDUMMY_not_a_real_key_0000") is not None


def test_a_plain_word_is_not_mistaken_for_an_api_key():
    assert check_docs.API_KEY_PATTERN.search("AIとAPIの話") is None


# ============================================================ シェルの違い


def test_an_unquoted_handle_is_caught():
    """**PowerShell では `@` が先頭に来ると演算子になる。**

    `--handle @GoogleDevelopers` は「変数を展開しろ」と読まれて空に化け、
    `argument --handle: expected one argument` で落ちる。
    Git Bash では通るので、**書いた本人の手元では再現しない**。
    """
    assert check_docs.handles_are_quoted("--handle @GoogleDevelopers") is False


def test_a_quoted_handle_passes():
    assert check_docs.handles_are_quoted("--handle '@GoogleDevelopers'") is True


def test_a_handle_without_the_at_sign_passes():
    """`@` を外す形も正解。`normalize_handle` が付け直す。"""
    assert check_docs.handles_are_quoted("--handle GoogleDevelopers") is True


def test_a_handle_mentioned_in_prose_is_not_flagged():
    """**説明文の中の `@名前` まで弾かない。** 弾くと書けなくなる。"""
    assert check_docs.handles_are_quoted("対象は @GoogleDevelopers である") is True
