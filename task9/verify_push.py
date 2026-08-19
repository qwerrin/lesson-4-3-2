"""課題9: 送信の記録を照合する。

    .venv\\Scripts\\python.exe task9\\verify_push.py
    .venv\\Scripts\\python.exe task9\\verify_push.py --local-only

**このモジュールのいちばん重要な仕事は「確認できないことを確認できないと言う」こと。**

課題1〜8の verify は、送った文字列を別経路で取り直して突き合わせていた。
LINE にはその経路が無い。だから全部 OK と出しても、それは
**送った文面が届いたことを意味しない**。

確かめているのは次の2つだけである。

============================ ==============================================
検査                          何を言えるか
============================ ==============================================
通数が 1 増えた                LINE が**送信対象として数えた**
``basicId`` / ``userId`` 一致  **意図したチャネル**を叩いた
============================ ==============================================

文面が届いたことは、**人が自分の LINE を見て確かめるしかない**。
そのことを検査結果に常に併記する。合格のときに消えると、読み手が見るのは
いつも「すべて一致しました」になり、確かめていないことが確かめた顔で残る。

**遠隔検査は必ず API を叩き直す。** 記録の中の値どうしを比べると
「自分で書いた値を自分で確かめる」トートロジーになる（課題4・6・7・8で
繰り返し踏んだ形）。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

# common/ を import する前にリポジトリのルートを通す。**この順番でないと動かない**——
# スクリプトとして直接実行すると sys.path の先頭は task9/ になるため、
# 関数の中で足しても遅い（module 直下の import 文が先に走る）。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common import env_file, line_auth  # noqa: E402

ROOT = _REPO_ROOT
DEFAULT_RESULTS = str(Path(__file__).resolve().parent / "results.json")

CONSUMPTION_PATH = "/v2/bot/message/quota/consumption"
QUOTA_PATH = "/v2/bot/message/quota"

REQUIRED_KEYS = (
    "bot",
    "to_masked",
    "text",
    "message_id",
    "usage_before",
    "usage_after",
)


class VerifyError(Exception):
    """照合を始められない失敗。検査の不合格とは区別する。"""


@dataclass
class Check:
    """検査1件。``expected`` と ``actual`` を必ず両方持つ。

    片方だけ出すと「何と比べて OK なのか」が読めない。課題8で
    「証拠は貼るだけでは主張にならない」を踏んだので、値を並べて出す。
    """

    label: str
    expected: Any
    actual: Any
    ok: bool


def _compare(label: str, expected: Any, actual: Any) -> Check:
    return Check(label=label, expected=expected, actual=actual, ok=expected == actual)


# ------------------------------------------------------------------ 記録を読む


def load_results(path: str | Path) -> dict:
    """``send_push.py`` が書いた記録を読む。

    **欠けた項目を「無いので OK」にしない。** 項目が無いまま検査を組むと、
    その検査は必ず通る（比べる相手がいないので）。
    """
    target = Path(path)

    if not target.is_file():
        raise VerifyError(
            f"送信の記録が見つかりません: {target}\n"
            "先に send_push.py を実行してください。"
        )

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerifyError(f"記録を読めませんでした: {target}\n{error}") from error

    if not isinstance(payload, dict):
        raise VerifyError(f"記録の形が想定と違います（辞書ではありません）: {target}")

    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        raise VerifyError(
            f"記録に必要な項目がありません: {', '.join(missing)}\n"
            "send_push.py を実行し直してください。"
        )

    if not isinstance(payload["bot"], dict):
        raise VerifyError("記録の bot が辞書ではありません。")

    return payload


# ------------------------------------------------------------------ 手元の検査


def build_local_checks(record: dict) -> list[Check]:
    """記録だけで確かめられることを検査する。ネットワークを使わない。"""
    bot = record.get("bot") or {}
    before = record.get("usage_before")
    after = record.get("usage_after")

    delta: Any
    if isinstance(before, int) and isinstance(after, int) and not isinstance(
        before, bool
    ) and not isinstance(after, bool):
        delta = after - before
    else:
        delta = "整数ではない"

    message_id = str(record.get("message_id") or "")
    basic_id = str(bot.get("basic_id") or "")
    masked = str(record.get("to_masked") or "")

    return [
        # **「増えた」ではなく「1 増えた」で見る。** 多ければ良いわけではない。
        _compare("通数の増分", 1, delta),
        _compare(
            "message_id が数字のみ",
            True,
            bool(message_id) and message_id.isdigit(),
        ),
        _compare("basicId が @ で始まる", True, basic_id.startswith("@")),
        _compare("chatMode が記録されている", True, bool(bot.get("chat_mode"))),
        _compare("本文が空でない", True, bool(str(record.get("text") or "").strip())),
        # 記録は public リポジトリに入る。伏せ忘れを検査で捕まえる。
        _compare(
            "宛先が伏せられている",
            True,
            bool(masked) and ("…" in masked or "..." in masked),
        ),
    ]


# ------------------------------------------------------------------ 遠隔の検査


def _json_of(response) -> Any:
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return None


def build_remote_checks(
    session,
    record: dict,
    *,
    base: str = line_auth.API_BASE,
    secrets: tuple = (),
) -> list[Check]:
    """API を叩き直して、記録が指すチャネルと一致するかを確かめる。

    通数は「一致」ではなく「**記録の値以上**」で見る。月内で単調に増えるので、
    送信から検証までの間に別の送信が入っても正しく通る。減っていたら、
    記録か対象チャネルのどちらかが違う。
    """
    bot = record.get("bot") or {}

    info = line_auth.fetch_bot_info(session, base=base, secrets=secrets)

    response = session.get(base + CONSUMPTION_PATH)
    line_auth.raise_for_line_error(response, *secrets)
    payload = _json_of(response)
    current = payload.get("totalUsage") if isinstance(payload, dict) else None

    after = record.get("usage_after")
    if isinstance(current, int) and not isinstance(current, bool) and isinstance(
        after, int
    ):
        usage_ok = current >= after
        usage_actual = current
    else:
        usage_ok = False
        usage_actual = "整数として読めない"

    checks = [
        _compare("basicId（API と記録）", bot.get("basic_id"), info.basic_id),
        _compare("bot の userId（API と記録）", bot.get("user_id"), info.user_id),
        Check(
            label="いま数えた通数",
            expected=f"{after} 以上",
            actual=usage_actual,
            ok=usage_ok,
        ),
    ]

    # 上限は検査ではなく情報。失敗にはしないが、200通の枠が見えると判断しやすい。
    quota_response = session.get(base + QUOTA_PATH)
    line_auth.raise_for_line_error(quota_response, *secrets)
    quota = _json_of(quota_response)
    if isinstance(quota, dict):
        checks.append(
            Check(
                label="今月の上限（参考）",
                expected="—",
                actual=f"{quota.get('type')} / {quota.get('value')}",
                ok=True,
            )
        )

    return checks


# ------------------------------------------------------------------ 確認できないこと


def unverifiable_notes() -> tuple[str, ...]:
    """**確かめていないことの一覧。空にできない作りにしてある。**

    空にできると、いつか空になって「全部確認した」に見える。
    LINE に読み返す API が無い以上、この一覧が消えることはない。
    """
    return (
        "本文が届いたかどうか。LINE には bot が送ったテキストを読み返す API が無い"
        "（GET /v2/bot/message/{messageId}/content はユーザーが送った画像・動画・音声専用）。"
        "→ 自分の LINE を目で見て確かめる。",
        "通数の増分は「1通ぶん送信対象になった」までしか言わない。"
        "どの文面が数えられたかは言わない。",
        "相手が実際に読んだかどうか。既読の有無を取る API は使っていない。",
    )


# ------------------------------------------------------------------ 出力


def all_ok(checks: Sequence[Check]) -> bool:
    return all(check.ok for check in checks)


def format_report(*check_groups: Sequence[Check]) -> str:
    """検査結果と「確認できないこと」を1枚にする。

    **注記は合否に関わらず必ず出す。** ここが課題9の主題。
    """
    lines: list[str] = []
    for checks in check_groups:
        for check in checks:
            mark = "OK" if check.ok else "NG"
            lines.append(f"  [{mark}] {check.label}: 期待={check.expected!r} 実際={check.actual!r}")

    lines.append("")
    lines.append("  --- この検査で確認できないこと ---")
    for note in unverifiable_notes():
        lines.append(f"  ・{note}")

    return "\n".join(lines)


# ------------------------------------------------------------------ 実行


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="課題9の送信記録を照合する")
    parser.add_argument(
        "--results", default=DEFAULT_RESULTS, help="send_push.py が書いた記録"
    )
    parser.add_argument(
        "--env",
        default=str(ROOT / env_file.ENV_FILENAME),
        help=f"資格情報の {env_file.ENV_FILENAME}",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="API を叩かず、記録だけで確かめられることを検査する",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: Callable | None = None,
) -> int:
    args = parse_args(argv)
    record = load_results(args.results)

    groups: list[Sequence[Check]] = [build_local_checks(record)]

    if not args.local_only:
        env = env_file.load(args.env)
        token = line_auth.read_channel_access_token(env)
        session = (
            session_factory(token)
            if session_factory
            else line_auth.build_session(token)
        )
        groups.append(build_remote_checks(session, record, secrets=(token,)))

    print(format_report(*groups))

    ok = all(all_ok(group) for group in groups)
    print()
    print("照合結果:", "すべて一致（ただし上の「確認できないこと」を読むこと）" if ok else "不一致あり")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except (VerifyError, line_auth.LineError, env_file.EnvFileError) as error:
        print(f"失敗: {error}")
        raise SystemExit(1) from error
