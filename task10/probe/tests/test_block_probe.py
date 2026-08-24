"""``block_probe.decide`` の判定を確かめる。

**この判定がそのまま記事に書かれる。** だから「測れていない」を
「測って否定できた」と取り違えない側に倒す。ここを間違えると、
課題10の記事Aに**確かめていないことを確かめたと書く**ことになる。

判定の材料は2回の実測（ブロック中／解除後）。片方だけでは
「元から 404 だった」のか「ブロックで 404 になった」のかが分かれない。
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROBE_DIR = Path(__file__).resolve().parent.parent
if str(_PROBE_DIR) not in sys.path:
    sys.path.insert(0, str(_PROBE_DIR))

import block_probe  # noqa: E402


BOT = "U-bot-1111"
WHO = "fingerprint-aaaa"


def run(status, *, bot=BOT, who=WHO, label="x"):
    return {"label": label, "status": status, "bot_user_id": bot, "user_fingerprint": who}


def test_ブロック中404_解除後200なら_ブロックが404を返すと決まる():
    verdict, why = block_probe.decide(run(404, label="blocked"), run(200, label="unblocked"))
    assert verdict == "block_causes_404"
    assert "ブロック" in why


def test_両方200なら_ブロックでは404にならないと決まる():
    verdict, why = block_probe.decide(run(200, label="blocked"), run(200, label="unblocked"))
    assert verdict == "block_does_not_cause_404"


def test_両方404なら_判定できない():
    """解除後も 404 なら、404 の原因は**ブロック以外**にもある。

    ここを ``block_causes_404`` に倒すと、元から届かない宛先を
    「ブロックのせいで届かない」と書くことになる。
    """
    verdict, why = block_probe.decide(run(404, label="blocked"), run(404, label="unblocked"))
    assert verdict == "inconclusive_both_404"


def test_逆転していたら_判定できない():
    verdict, why = block_probe.decide(run(200, label="blocked"), run(404, label="unblocked"))
    assert verdict.startswith("inconclusive")


def test_200と404以外が混ざったら_判定できない():
    """401 や 429 は「届くか」の答えではない。**資格情報や制限の話**。

    数字が返ってきたことを「測れた」と読むと、トークン切れを
    「ブロックされている」と報告することになる。
    """
    for bad in (401, 403, 429, 500):
        verdict, why = block_probe.decide(run(bad, label="blocked"), run(200, label="unblocked"))
        assert verdict.startswith("inconclusive"), bad
        assert str(bad) in why, bad


def test_宛先が違う2回は_突き合わせない():
    """**別の相手を測っていたら、比べても意味がない。**

    課題10（LINE）で「0 件が正常値の処理は、別経路で数えるまで動いたと
    言えない」を踏んだ。同じ形で、同じ宛先であることを先に確かめる。
    """
    verdict, why = block_probe.decide(
        run(404, who="fingerprint-aaaa"), run(200, who="fingerprint-bbbb")
    )
    assert verdict == "inconclusive_different_target"


def test_ボットが違う2回は_突き合わせない():
    verdict, why = block_probe.decide(run(404, bot="U-bot-1111"), run(200, bot="U-bot-2222"))
    assert verdict == "inconclusive_different_bot"


def test_指紋は宛先ごとに変わる():
    """**定数を返しても decide のテストは全部通る**（実測で確認）。

    指紋が宛先を区別できないと、``inconclusive_different_target`` が
    永久に出ず、別の相手を測った2回を平気で突き合わせる。
    """
    assert block_probe.fingerprint("U-aaa") != block_probe.fingerprint("U-bbb")


def test_指紋は同じ宛先なら毎回同じ():
    assert block_probe.fingerprint("U-aaa") == block_probe.fingerprint("U-aaa")


def test_指紋に宛先そのものを含めない():
    """ユーザーIDは記録にも画面にも出さない。**公開リポジトリに置く。**"""
    raw = "U0123456789abcdef0123456789abcdef"
    assert raw not in block_probe.fingerprint(raw)


def test_結論の説明が対照の測定順を断定しない():
    """``decide`` が見ているのは**状態コード2つだけ**。

    対照をブロックの前に測ったか後に測ったかは、この関数には分からない。
    「解除後は 200」と書いていて、ブロック前の対照と突き合わせたときに
    嘘になった（2026-08-24 に実際に出した）。
    """
    _, why = block_probe.decide(run(404, label="blocked"), run(200, label="unblocked-before"))
    assert "解除後" not in why
