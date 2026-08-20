"""task10/line/notify_schedule.py のテスト。

**課題10は「機能を追加する」ので、課題9と守るものが違う。**

課題9は「送れたことを、どう確かめるか」だった。課題10で足すのは
**送る前に確かめること**である。守るべき失敗は、どれも
「エラーにならず、静かに減る／届かない」形をしている。

============================== ================================================
守る失敗                        なぜエラーで気づけないか
============================== ================================================
未友だち・ブロックへの push      **HTTP 200 が返る。**判別できるのは profile の 404 だけ
繰り返し予定が展開されない       ``singleEvents`` を省いてもエラーは出ず、件数だけ減る
終日予定を取りこぼす             ``start.date`` と ``start.dateTime`` は別キー。
                                 片方だけ見ると 0 件に見える
``summary`` が無い予定を落とす   Google は ``summary`` を省略できる。落とすと件数が減る
============================== ================================================

**「0 件」が正常値でありうる**のがこの課題の厄介なところで、
件数が減る系の失敗は「予定が無い日」と見分けが付かない。
だから件数に関わる分岐は、ここで1つずつ固定する。

日付は**必ず外から渡す**。実行時刻に依存させると、日付が変わった瞬間に
テストが落ちるうえ、境界の挙動を確かめられない。
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notify_schedule  # noqa: E402

JST = timezone(timedelta(hours=9))


# ============================================================ 対象日を決める


@pytest.mark.parametrize(
    "hour,minute",
    [
        (0, 1),  # 日付が変わった直後に走らせる想定
        (12, 0),
        (23, 59),  # 同じ日の終わり
    ],
)
def test_target_date_is_the_day_of_now(hour, minute):
    """対象日は「実行した日」。時刻では変わらない。

    00:01 に走らせて当日ぶんを送る運用なので、時刻で日付が動くと
    **1日ずれた予定を送る**。ずれてもエラーは出ない。
    """
    now = datetime(2026, 8, 21, hour, minute, tzinfo=JST)

    assert notify_schedule.resolve_target_date(now) == date(2026, 8, 21)


def test_target_date_can_be_given_explicitly():
    """明示指定が実行時刻より優先される。撮り直しと再現のために要る。"""
    now = datetime(2026, 8, 21, 0, 1, tzinfo=JST)

    assert notify_schedule.resolve_target_date(now, date(2026, 12, 31)) == date(
        2026, 12, 31
    )


# ======================================================== 問い合わせ窓を作る


def test_time_window_covers_exactly_the_target_day():
    lo, hi = notify_schedule.build_time_window(date(2026, 8, 21), JST)

    assert lo == "2026-08-21T00:00:00+09:00"
    assert hi == "2026-08-22T00:00:00+09:00"


def test_time_window_always_carries_a_timezone_offset():
    """RFC3339 はオフセット必須。付け忘れると API 側の解釈でズレる。"""
    lo, hi = notify_schedule.build_time_window(date(2026, 8, 21), JST)

    for value in (lo, hi):
        assert value.endswith("+09:00")


def test_time_window_crosses_month_boundary():
    """月末は翌月1日が上限になる。手で組むとここを間違える。"""
    lo, hi = notify_schedule.build_time_window(date(2026, 8, 31), JST)

    assert lo == "2026-08-31T00:00:00+09:00"
    assert hi == "2026-09-01T00:00:00+09:00"


# ================================================ 問い合わせパラメータを作る


def test_params_expand_recurring_events():
    """``singleEvents`` を省くと繰り返し予定が展開されない。

    エラーは出ず、**毎週の予定が明日の分として出てこない**だけなので、
    和文の単発予定1件で試すと永久に気づけない。
    """
    params = notify_schedule.build_list_params(date(2026, 8, 21), JST)

    assert params["singleEvents"] is True


def test_params_order_by_start_time():
    """``orderBy=startTime`` は ``singleEvents`` が真のときだけ使える。"""
    params = notify_schedule.build_list_params(date(2026, 8, 21), JST)

    assert params["orderBy"] == "startTime"


def test_params_carry_the_time_window():
    params = notify_schedule.build_list_params(date(2026, 8, 21), JST)

    assert params["timeMin"] == "2026-08-21T00:00:00+09:00"
    assert params["timeMax"] == "2026-08-22T00:00:00+09:00"


# ============================================================ 予定を取り出す


def timed_event(summary, start):
    return {"summary": summary, "start": {"dateTime": start}}


def all_day_event(summary, day):
    return {"summary": summary, "start": {"date": day}}


def test_timed_event_is_extracted():
    events = notify_schedule.extract_events(
        {"items": [timed_event("歯医者", "2026-08-21T10:00:00+09:00")]}
    )

    assert [e.summary for e in events] == ["歯医者"]
    assert events[0].all_day is False


def test_all_day_event_is_extracted():
    """終日予定は ``start.date`` に入る。``dateTime`` だけ見ると 0 件に見える。"""
    events = notify_schedule.extract_events({"items": [all_day_event("健康診断", "2026-08-21")]})

    assert [e.summary for e in events] == ["健康診断"]
    assert events[0].all_day is True


def test_both_kinds_are_extracted_together():
    """混在しても両方残る。片方しか無い日でテストすると取りこぼしに気づけない。"""
    payload = {
        "items": [
            all_day_event("健康診断", "2026-08-21"),
            timed_event("歯医者", "2026-08-21T10:00:00+09:00"),
        ]
    }

    events = notify_schedule.extract_events(payload)

    assert len(events) == 2
    assert {e.summary for e in events} == {"健康診断", "歯医者"}


def test_event_without_summary_is_kept():
    """``summary`` は省略されうる。落とすと件数が静かに減る。"""
    events = notify_schedule.extract_events({"items": [{"start": {"date": "2026-08-21"}}]})

    assert len(events) == 1
    assert events[0].summary != ""


def test_event_with_unknown_start_is_kept():
    """``start`` が無い／未知の形でも落とさない。

    捨てても**件数が減るだけ**でエラーは出ない。0 件が正常値でありうる以上、
    減ったことに気づく手段が無い。時刻が分からないなら、分からないと書いて残す。
    """
    events = notify_schedule.extract_events({"items": [{"summary": "形の違う予定"}]})

    assert len(events) == 1
    assert events[0].summary == "形の違う予定"
    assert events[0].start_label == notify_schedule.UNKNOWN_TIME_LABEL


def test_time_label_is_formatted_not_raw():
    """生の RFC3339 をそのまま出していないことを、**完全一致**で確かめる。

    ``"10:00" in text`` では確かめられない。生の値 ``2026-08-21T10:00:00+09:00``
    にも ``10:00`` は含まれるので、整形を全部やめても部分一致は満たされる。
    課題9で「2つの assert が両方とも無関係な場所の文字列で満たされていた」のと
    同じ形（2026-08-20 のミューテーションで実際に素通りした）。
    """
    events = notify_schedule.extract_events(
        {"items": [timed_event("歯医者", "2026-08-21T10:00:00+09:00")]}
    )

    assert events[0].start_label == "10:00"


def test_no_items_key_is_zero_events_not_an_error():
    """予定が無い日は ``items`` ごと来ないことがある。0 件は正常値。"""
    assert notify_schedule.extract_events({}) == []


# ============================================================ 本文を組み立てる


def test_message_for_zero_events_says_there_are_none():
    """0 件でも送る。

    送らないと、通知が来ない日が「予定なし」なのか「動いていない」のか
    **受け取る側から区別できない**。
    """
    text = notify_schedule.build_message([], date(2026, 8, 21))

    assert "予定はありません" in text
    assert "2026-08-21" in text


def test_message_lists_every_event():
    events = notify_schedule.extract_events(
        {
            "items": [
                all_day_event("健康診断", "2026-08-21"),
                timed_event("歯医者", "2026-08-21T10:00:00+09:00"),
            ]
        }
    )

    text = notify_schedule.build_message(events, date(2026, 8, 21))

    assert "健康診断" in text
    assert "歯医者" in text


def test_message_shows_the_target_date():
    """いつの予定かを本文に書く。届いた時刻と対象日はズレうる。"""
    events = notify_schedule.extract_events({"items": [all_day_event("健康診断", "2026-08-21")]})

    text = notify_schedule.build_message(events, date(2026, 8, 21))

    assert "2026-08-21" in text


def test_message_marks_all_day_events():
    """終日と時刻付きが本文で区別できる。時刻の有無は受け取る側の関心事。"""
    events = notify_schedule.extract_events(
        {
            "items": [
                all_day_event("健康診断", "2026-08-21"),
                timed_event("歯医者", "2026-08-21T10:00:00+09:00"),
            ]
        }
    )

    text = notify_schedule.build_message(events, date(2026, 8, 21))

    assert "終日" in text
    assert "10:00" in text


def test_message_does_not_leak_raw_timestamp():
    """本文に生の RFC3339 が混ざっていないこと。

    整形を忘れると ``2026-08-21T10:00:00+09:00`` がそのまま出る。読みにくい
    だけでなく、``"10:00" in text`` のような**部分一致では検出できない**
    ——生の値にも ``10:00`` が含まれているため。含まれてはいけないものを
    名指しで否定する。
    """
    events = notify_schedule.extract_events(
        {"items": [timed_event("歯医者", "2026-08-21T10:00:00+09:00")]}
    )

    text = notify_schedule.build_message(events, date(2026, 8, 21))

    assert "T10:00:00" not in text
    assert "+09:00" not in text
