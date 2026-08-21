"""課題10: 予定の通知を照合する。

    .venv/Scripts/python.exe task10/line/verify_notify.py
    .venv/Scripts/python.exe task10/line/verify_notify.py --local-only
    .venv/Scripts/python.exe task10/line/verify_notify.py --no-calendar

**課題9から引き継ぐ主題は「確認できないことを、確認できないと言う」。**

課題9の verify_push.py はこう締めていた——LINE には bot が送ったテキストを
読み返す API が無いので、全部 OK と出してもそれは文面が届いたことを意味しない。
だから注記を**合格のときにも必ず出す**。

課題10で照合の相手が2つ増えた。

============================== ================================================
照合の相手                      何を言えるか
============================== ================================================
``/v2/bot/info``                意図したチャネルを叩いた（課題9から）
``quota/consumption``           送信対象として数えられた（課題9から）
``/v2/bot/profile/{userId}``    **いまも届く状態にある**（課題10）
Google カレンダー                 **対象日の件数が記録と合う**（課題10）
============================== ================================================

増えたぶん、**新しい限界も増えた**。

カレンダーの照合が読むのは「いま」のカレンダーであって、送信した瞬間の
ものではない。送信後に予定を1件足せば、実装が正しくても不一致になる。
profile の照合も同じで、「いま届く」は「送った時に届いた」ではない。

**限界が増えたら注記も増やす。** 検査を足したぶんだけ「確認した」の範囲が
広がったように見えるのが、いちばん危ない。

**遠隔検査は必ず API を叩き直す。** 記録の中の値どうしを比べると
「自分で書いた値を自分で確かめる」トートロジーになる（課題4・6・7・8で
繰り返し踏んだ形）。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _extra_path in (_REPO_ROOT, Path(__file__).resolve().parent):
    if str(_extra_path) not in sys.path:
        sys.path.insert(0, str(_extra_path))

from common import env_file, google_auth, line_auth  # noqa: E402

import notify_schedule  # noqa: E402

# 宛先の伏せ方を記録側とそろえるため、課題9の実装をそのまま使う。
# **別々に実装すると、伏せ方が食い違って毎回不一致になる。**
sys.path.insert(0, str(_REPO_ROOT / "task9"))

import send_push  # noqa: E402

ROOT = _REPO_ROOT
DEFAULT_RESULTS = notify_schedule.DEFAULT_RESULTS

CONSUMPTION_PATH = "/v2/bot/message/quota/consumption"
QUOTA_PATH = "/v2/bot/message/quota"

#: 記録に無ければ照合を始めない。**欠けた項目を「無いので OK」にしない。**
REQUIRED_KEYS = (
    "bot",
    "to_masked",
    "target_date",
    "event_count",
    "text",
    "message_id",
    "usage_before",
    "usage_after",
    "remaining",
)

#: 本文の中で「予定1件」を表す行の印。build_message と対になっている。
EVENT_LINE_PREFIX = "- "


class VerifyError(Exception):
    """照合を始められない失敗。**検査の不合格とは区別する。**"""


@dataclass
class Check:
    """検査1件。``expected`` と ``actual`` を必ず両方持つ。

    片方だけ出すと「何と比べて OK なのか」が読めない（課題8「証拠は貼る
    だけでは主張にならない」）。
    """

    label: str
    expected: Any
    actual: Any
    ok: bool


def _compare(label: str, expected: Any, actual: Any) -> Check:
    return Check(label=label, expected=expected, actual=actual, ok=expected == actual)


def _is_int(value: Any) -> bool:
    """bool を整数として通さない。``True`` は ``1`` と等しく、素朴な検査を素通りする。"""
    return isinstance(value, int) and not isinstance(value, bool)


# ------------------------------------------------------------------ 記録を読む


def load_results(path: str | Path) -> dict:
    """``notify_schedule.py`` が書いた記録を読む。

    **欠けた項目を「無いので OK」にしない。** 項目が無いまま検査を組むと、
    その検査は必ず通る（比べる相手がいないので）。
    """
    target = Path(path)

    if not target.is_file():
        raise VerifyError(
            f"送信の記録が見つかりません: {target}\n"
            "先に notify_schedule.py を実行してください。"
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
            "notify_schedule.py を実行し直してください。"
        )

    if not isinstance(payload["bot"], dict):
        raise VerifyError("記録の bot が辞書ではありません。")

    return payload


def count_event_lines(text: str) -> int:
    """本文の中の予定の行数を数える。

    見出しと空行は数えない。**本文の組み立てが壊れると行だけ静かに消える**
    ので、件数と突き合わせられるようにしておく。
    """
    return sum(1 for line in text.splitlines() if line.startswith(EVENT_LINE_PREFIX))


# ------------------------------------------------------------------ 手元の検査


def build_local_checks(record: dict) -> list[Check]:
    """記録だけで確かめられることを検査する。ネットワークを使わない。"""
    bot = record.get("bot") or {}
    before = record.get("usage_before")
    after = record.get("usage_after")

    delta: Any = after - before if _is_int(before) and _is_int(after) else "整数ではない"

    text = str(record.get("text") or "")
    target_date = str(record.get("target_date") or "")
    message_id = str(record.get("message_id") or "")
    basic_id = str(bot.get("basic_id") or "")
    masked = str(record.get("to_masked") or "")
    remaining = record.get("remaining")

    return [
        # **「増えた」ではなく「1 増えた」で見る。** 多ければ良いわけではない。
        _compare("通数の増分", 1, delta),
        _compare(
            "message_id が数字のみ",
            True,
            bool(message_id) and message_id.isdigit(),
        ),
        _compare("basicId が @ で始まる", True, basic_id.startswith("@")),
        # 届いた時刻と対象日はズレうる。本文に日付が無いと、受け取った側は
        # 「いつの予定か」を届いた時刻から推測することになる。
        _compare(
            "本文に対象日がある", True, bool(target_date) and target_date in text
        ),
        _compare("予定の行数と件数", record.get("event_count"), count_event_lines(text)),
        # 記録は public リポジトリに入る。伏せ忘れを検査で捕まえる。
        _compare(
            "宛先が伏せられている",
            True,
            bool(masked) and ("…" in masked or "..." in masked),
        ),
        # **null（無制限）を不合格にしない。** 0 と混ぜると真逆の判定になる。
        _compare(
            "残数が読める形", True, remaining is None or _is_int(remaining)
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
    to: str,
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
    if _is_int(current) and _is_int(after):
        usage_ok = current >= after
        usage_actual = current
    else:
        usage_ok = False
        usage_actual = "整数として読めない"

    # 課題10で足した検査。**送る前に見たものを、送ったあとにもう一度見る。**
    reachability = line_auth.fetch_profile(session, to, base=base, secrets=secrets)

    checks = [
        _compare("basicId（API と記録）", bot.get("basic_id"), info.basic_id),
        _compare("bot の userId（API と記録）", bot.get("user_id"), info.user_id),
        Check(
            label="いま数えた通数",
            expected=f"{after} 以上",
            actual=usage_actual,
            ok=usage_ok,
        ),
        # 記録した宛先と、いま .env にある宛先が同じか。**伏せ字どうしで比べる。**
        # 違っていれば、別の宛先の記録を別の宛先で照合していることになる。
        _compare(
            "宛先（.env と記録）",
            record.get("to_masked"),
            send_push.mask_destination(to),
        ),
        Check(
            label="いまも宛先に届く",
            expected=True,
            actual=reachability.reachable or reachability.reason,
            ok=reachability.reachable,
        ),
    ]

    # 上限は検査ではなく情報。失敗にはしないが、枠が見えると判断しやすい。
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


# ------------------------------------------------------------- カレンダーの照合


def build_calendar_checks(service, record: dict) -> list[Check]:
    """カレンダーを読み直して、対象日の件数が記録と合うかを見る。

    **記録に書いた対象日で引く。** 今日で引くと、翌日以降は必ずズレる。

    一致しないことは実装の誤りとは限らない。この検査が読むのは「いま」の
    カレンダーで、送信した瞬間のものではないため。**その限界は
    unverifiable_notes() に書いてある。**
    """
    raw = str(record.get("target_date") or "")
    try:
        target_date = date.fromisoformat(raw)
    except ValueError as error:
        # **生の ValueError を外へ出さない。** 記録が壊れているという話なのに、
        # スタックトレースだけが出ると原因がカレンダー側に見える。
        raise VerifyError(
            f"記録の target_date を日付として読めません: {raw!r}\n"
            "notify_schedule.py を実行し直してください。"
        ) from error
    params = notify_schedule.build_list_params(
        target_date, notify_schedule.DEFAULT_TIMEZONE
    )
    payload = notify_schedule.fetch_all_events(service, params)
    events = notify_schedule.extract_events(payload)

    return [
        _compare("カレンダーの件数（いま）", record.get("event_count"), len(events)),
    ]


# ------------------------------------------------------------ 確認できないこと


def unverifiable_notes() -> tuple[str, ...]:
    """**確かめていないことの一覧。空にできない作りにしてある。**

    空にできると、いつか空になって「全部確認した」に見える。課題10では
    検査を2つ足したので、**足した検査の限界も足してある**。
    """
    return (
        "本文が届いたかどうか。LINE には bot が送ったテキストを読み返す API が無い"
        "（GET /v2/bot/message/{messageId}/content はユーザーが送った画像・動画・音声専用）。"
        "→ 自分の LINE を目で見て確かめる。",
        "通数の増分は「1通ぶん送信対象になった」までしか言わない。"
        "どの文面が数えられたかは言わない。",
        "相手が実際に読んだかどうか。既読の有無を取る API は使っていない。",
        "カレンダーの照合が見ているのは「いま」の予定であって、送信した瞬間のもの"
        "ではない。送信後に予定を足し引きすれば、実装が正しくても不一致になる。",
        "送信前ガードが「届く」と答えたことは、届いたことを意味しない。"
        "profile が 200 を返した後にブロックされる余地は残る。",
    )


# ------------------------------------------------------------------ 出力


def all_ok(checks: Sequence[Check]) -> bool:
    return all(check.ok for check in checks)


def format_report(*check_groups: Sequence[Check]) -> str:
    """検査結果と「確認できないこと」を1枚にする。

    **注記は合否に関わらず必ず出す。** ここが課題9から継承した主題。
    """
    lines: list[str] = []
    for checks in check_groups:
        for check in checks:
            mark = "OK" if check.ok else "NG"
            lines.append(
                f"  [{mark}] {check.label}: 期待={check.expected!r} 実際={check.actual!r}"
            )

    lines.append("")
    lines.append("  --- この検査で確認できないこと ---")
    for note in unverifiable_notes():
        lines.append(f"  ・{note}")

    return "\n".join(lines)


# ------------------------------------------------------------------ 実行


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="課題10の送信記録を照合する")
    parser.add_argument(
        "--results", default=DEFAULT_RESULTS, help="notify_schedule.py が書いた記録"
    )
    parser.add_argument(
        "--env",
        default=str(ROOT / env_file.ENV_FILENAME),
        help=f"LINE の資格情報が入った {env_file.ENV_FILENAME}",
    )
    parser.add_argument(
        "--credentials",
        default=notify_schedule.DEFAULT_CREDENTIALS,
        help="Google の OAuth クライアント",
    )
    parser.add_argument(
        "--token",
        default=notify_schedule.DEFAULT_TOKEN,
        help="カレンダー専用のトークン",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="API を叩かず、記録だけで確かめられることを検査する",
    )
    parser.add_argument(
        "--no-calendar",
        action="store_true",
        help="カレンダーを読まない（同意画面を開かずに LINE 側だけ照合する）",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: Callable | None = None,
    service_factory: Callable | None = None,
) -> int:
    args = parse_args(argv)
    record = load_results(args.results)

    groups: list[Sequence[Check]] = [build_local_checks(record)]

    if not args.local_only:
        env = env_file.load(args.env)
        token = line_auth.read_channel_access_token(env)
        to = line_auth.read_user_id(env)
        session = (
            session_factory(token)
            if session_factory
            else line_auth.build_session(token)
        )
        groups.append(build_remote_checks(session, record, to, secrets=(token,)))

        if not args.no_calendar:
            service = (
                service_factory()
                if service_factory
                else notify_schedule.build_service(
                    google_auth.load_credentials(
                        args.credentials, args.token, notify_schedule.CALENDAR_SCOPES
                    )
                )
            )
            groups.append(build_calendar_checks(service, record))

    print(format_report(*groups))

    ok = all(all_ok(group) for group in groups)
    print()
    print(
        "照合結果:",
        "すべて一致（ただし上の「確認できないこと」を読むこと）" if ok else "不一致あり",
    )
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except (
        VerifyError,
        notify_schedule.NotifyError,
        line_auth.LineError,
        env_file.EnvFileError,
        google_auth.AuthError,
    ) as error:
        print(f"失敗: {error}")
        raise SystemExit(1) from error
