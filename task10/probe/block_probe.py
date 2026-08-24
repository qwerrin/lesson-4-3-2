#!/usr/bin/env python3
"""ブロックされた相手に ``GET /v2/bot/profile/{userId}`` が何を返すかを実測する。

課題10（LINE）の記事に、こう書いて出していない宿題がある。

    ブロックしたユーザーに対して、プロフィール取得が何を返すかを実際に測る。
    公式の 404 の説明にブロックが含まれるかを確認しきれず、未確認のまま残っています。

公式リファレンスの 404 は「ユーザーIDが存在しない」「プロフィールの取得に
同意していない」「友だち追加していない」の3つを並べていて、**ブロックが
そこに入るかどうかは書かれていない**。書かれていないものを推測で埋めない。

===================    ====================================================
確かめること            ブロック中と解除後で ``/v2/bot/profile`` の応答が変わるか
物差しをどこから取るか  **同じ宛先を2つの状態で測って突き合わせる**
===================    ====================================================

**1回では決まらない。** ブロック中に 404 が返っても、それが

1. ブロックのせいで 404 になった
2. **元から 404 だった**（同意していない・友だちでない）

のどちらかは分からない。解除後にもう一度測って 200 になって初めて 1 が決まる。
課題10で踏んだ「0 件が正常値の処理は、別経路で数えるまで動いたと言えない」と
同じ形である。

**送信は一切しない。** この経路は GET だけで、無料枠を1通も消費しない。

秘密は ``.env`` から読む（**環境変数には置かない**）。課題7で、環境変数に
置いたトークンが隣で常駐している別のシステムの起動条件になっていて、
cron を2日間止めた。名前空間は共有されている。
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common import env_file, line_auth  # noqa: E402

#: 「届くか」の答えとして読んでよい状態コード。
#:
#: **これ以外は答えではない。** 401 はトークン、429 は制限の話で、
#: 相手が届くかどうかを1文字も言っていない。数字が返ってきたことを
#: 「測れた」と読むと、トークン切れを「ブロックされている」と報告する。
ANSWER_CODES = (200, 404)


def shown_path(path: str | Path) -> str:
    """画面に出すためのパス。**絶対パスを出さない。**

    実行画面も提出物になる。``task10/discord/relay_uploads.py`` の同名関数と
    同じ理由・同じ規則（実機で利用者の名前を含む絶対パスが画面に出ていた）。
    LINE 側の探り道具から Discord 課題の実装を import すると課題どうしが
    依存し合うので、ここに置く。
    """
    target = Path(path)
    try:
        return target.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return target.name


def fingerprint(value: str) -> str:
    """宛先を、値を出さずに**同一性だけ**比べられる形にする。

    LINE のユーザーIDそのものは記録にも画面にも出さない。だが
    「2回とも同じ相手を測ったか」は確かめる必要がある。
    """
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def decide(blocked: Mapping[str, Any], unblocked: Mapping[str, Any]) -> tuple[str, str]:
    """2回の実測から結論を出す。**分からないときは分からないと言う。**

    返すのは ``(判定コード, 説明)``。``inconclusive`` で始まるものは
    **記事に「実測した」として書いてはいけない**。
    """
    if blocked.get("bot_user_id") != unblocked.get("bot_user_id"):
        return (
            "inconclusive_different_bot",
            "2回の実測でボット（チャネル）が違う。同じチャネルで測り直すこと。",
        )

    if blocked.get("user_fingerprint") != unblocked.get("user_fingerprint"):
        return (
            "inconclusive_different_target",
            "2回の実測で宛先が違う。別の相手を比べても、ブロックの影響は分からない。",
        )

    b = blocked.get("status")
    u = unblocked.get("status")

    unusable = [code for code in (b, u) if code not in ANSWER_CODES]
    if unusable:
        joined = "・".join(str(code) for code in unusable)
        return (
            "inconclusive_unexpected_status",
            "「届くか」の答えでない状態コードが混ざっている: "
            + joined
            + "。401 は資格情報、429 は制限の話で、宛先については何も言っていない。",
        )

    if b == 404 and u == 200:
        return (
            "block_causes_404",
            # **順序を断定しない。** この関数が見ているのは2件の状態コードだけで、
            # 対照がブロックの前に測ったものか後に測ったものかは知らない。
            # 「解除後は 200」と書いていたが、ブロック前の対照と突き合わせた
            # ときに嘘になった（2026-08-24 に実際に出した）。
            "ブロック中は 404、ブロックしていないときは 200。"
            "**ブロックされた相手に対して /v2/bot/profile は 404 を返す。**"
            "公式が並べている3つの理由に加えて、ブロックでも 404 になる。",
        )

    if b == 200 and u == 200:
        return (
            "block_does_not_cause_404",
            "ブロック中も解除後も 200。**ブロックでは 404 にならない。**"
            "この宛先については、404 の原因からブロックを外してよい。",
        )

    if b == 404 and u == 404:
        return (
            "inconclusive_both_404",
            "解除後も 404 のまま。**この宛先は元から届かない。**"
            "ブロックの影響は測れていない。"
            "公式が並べている3つの理由のどれかが効いている。",
        )

    return (
        "inconclusive_reversed",
        "ブロック中が 200、解除後が 404。順序かラベルを取り違えている疑いがある。"
        "測り直すこと。",
    )


def measure(label: str, *, env_path: Path) -> dict[str, Any]:
    """1回ぶんの実測。**GET しかしない。**"""
    env = env_file.load(env_path)
    token = line_auth.read_channel_access_token(env)
    user_id = line_auth.read_user_id(env)
    session = line_auth.build_session(token)
    secrets = (token, user_id)

    # 対照。**トークンが生きていて、どのチャネルで測っているか**をここで固定する。
    # これを取らずに 404 を見ると、「相手が届かない」と「こちらが壊れている」が
    # 見分けられない。
    bot = line_auth.fetch_bot_info(session, secrets=secrets)

    response = session.get(line_auth.API_BASE + "/v2/bot/profile/" + user_id)
    status = getattr(response, "status_code", None)

    try:
        payload = response.json()
    except Exception:
        payload = None

    body_keys = sorted(payload.keys()) if isinstance(payload, dict) else []
    display_name = ""
    if isinstance(payload, dict):
        display_name = str(payload.get("displayName") or "")

    # 実装が結論として何を言うか。**記事が引用しているのはこの文言**なので、
    # 生の状態コードと並べて記録する。
    guard_reachable: bool | None = None
    guard_reason = ""
    try:
        reachability = line_auth.fetch_profile(session, user_id, secrets=secrets)
        guard_reachable = reachability.reachable
        guard_reason = reachability.reason
    except line_auth.LineError as exc:
        guard_reason = line_auth.redact(str(exc), *secrets)

    return {
        "label": label,
        "measured_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "endpoint": "GET /v2/bot/profile/{userId}",
        "bot_user_id": bot.user_id,
        "bot_basic_id": bot.basic_id,
        "user_fingerprint": fingerprint(user_id),
        "status": status,
        "body_keys": body_keys,
        "has_user_id": bool(isinstance(payload, dict) and payload.get("userId")),
        "has_display_name": bool(display_name),
        # 値そのものは出さない。**公開リポジトリに置くスクリーンショットに写る。**
        "display_name_len": len(display_name),
        "guard_reachable": guard_reachable,
        "guard_reason": guard_reason,
    }


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ブロック中と解除後で /v2/bot/profile の応答が変わるかを実測する")
    parser.add_argument(
        "--label",
        default=None,
        help="この実行の目印（blocked / unblocked）。--compare のときは不要",
    )
    parser.add_argument("--json-out", default=None, help="結果の保存先")
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BLOCKED_JSON", "UNBLOCKED_JSON"),
        default=None,
        help="2回ぶんの結果を突き合わせて結論を出す（測定はしない）",
    )
    args = parser.parse_args(argv)

    if args.compare:
        blocked_path, unblocked_path = (Path(p) for p in args.compare)
        blocked, unblocked = _load(blocked_path), _load(unblocked_path)
        verdict, why = decide(blocked, unblocked)
        print("[突き合わせ]")
        for record, path in ((blocked, blocked_path), (unblocked, unblocked_path)):
            label = str(record.get("label", "?"))
            print(
                "  " + label.ljust(10)
                + " status=" + str(record.get("status"))
                + "  (" + shown_path(path) + ")"
            )
        print("[判定] " + verdict)
        print("  " + why)
        if verdict.startswith("inconclusive"):
            print("  **記事に「実測した」と書かないこと。**")
            return 1
        return 0

    if not args.label:
        parser.error("--label が要ります（blocked / unblocked）")

    record = measure(args.label, env_path=_REPO_ROOT / env_file.ENV_FILENAME)
    print("[実測] label=" + str(record["label"]) + "  status=" + str(record["status"]))
    print("  bot=" + str(record["bot_basic_id"]) + "  宛先=" + str(record["user_fingerprint"]))
    print("  body のキー: " + (", ".join(record["body_keys"]) or "(なし)"))
    print("  実装の結論: reachable=" + str(record["guard_reachable"]))
    if record["status"] not in ANSWER_CODES:
        print("  **これは「届くか」の答えではない。** 資格情報か制限を先に直すこと。")

    if args.json_out:
        out = Path(args.json_out)
    else:
        out = Path(__file__).with_name("block_probe_" + str(record["label"]) + ".json")
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  保存: " + shown_path(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
