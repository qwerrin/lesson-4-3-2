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

from common import line_auth  # noqa: E402

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


# ============================================================ 送信前ガード
#
# **ここが課題10の主題。** push は未友だち・ブロックの相手にも 200 を返すので、
# 送った後では区別が付かない。送る前に止められるかどうかが全部である。


def reachable():
    return line_auth.Reachability(
        reachable=True,
        reason="",
        profile=line_auth.Profile(user_id="Uxxxx", display_name="なな"),
    )


def unreachable(reason="友だち追加されていないか、ブロックされています。"):
    return line_auth.Reachability(reachable=False, reason=reason, profile=None)


def test_gate_blocks_when_the_destination_is_unreachable():
    """届かないと分かったら送らない。**200 が返るので送った後では気づけない。**"""
    gate = notify_schedule.judge_send(unreachable(), 100)

    assert gate.ok is False
    assert gate.blocks


def test_gate_keeps_the_reason_it_blocked():
    """止めた理由を落とさない。空にすると利用者は何を直せばよいか分からない。"""
    gate = notify_schedule.judge_send(unreachable("ブロックされています。"), 100)

    assert any("ブロック" in reason for reason in gate.blocks)


def test_gate_passes_when_reachable_and_quota_remains():
    gate = notify_schedule.judge_send(reachable(), 100)

    assert gate.ok is True
    assert gate.blocks == ()


def test_gate_passes_when_the_quota_is_unlimited():
    """**無制限（None）で止めない。** ここを 0 と混ぜると真逆の判断になる。

    ``quota.value`` は ``type`` が ``limited`` のときだけ返る。無いものを 0 と
    読むと「無制限」が「使い切った」に化け、ガードが**無制限のアカウントで
    送信を止める**。
    """
    gate = notify_schedule.judge_send(reachable(), None)

    assert gate.ok is True
    assert gate.blocks == ()


def test_gate_says_the_quota_is_unlimited_instead_of_staying_silent():
    """無制限は黙って通さず、そう言う。読み手が枠を誤解しないため。"""
    gate = notify_schedule.judge_send(reachable(), None)

    assert any("無制限" in note for note in gate.notes)


def test_gate_blocks_when_the_quota_is_used_up():
    """残り 0 は止める。無制限（None）と**同じ扱いにしない**。"""
    gate = notify_schedule.judge_send(reachable(), 0)

    assert gate.ok is False
    assert gate.blocks


def test_gate_blocks_when_the_quota_is_over_used():
    """負の残数（超過）も止める。丸めると使い切りと区別が付かなくなる。"""
    gate = notify_schedule.judge_send(reachable(), -12)

    assert gate.ok is False


def test_gate_reports_the_over_used_amount_not_zero():
    """超過した数をそのまま出す。0 に丸めると「あと 0 通」と同じ文面になる。"""
    gate = notify_schedule.judge_send(reachable(), -12)

    assert any("-12" in reason for reason in gate.blocks)


def test_gate_passes_on_the_last_message():
    """あと1通なら送れる。境界の内側で止めない。"""
    gate = notify_schedule.judge_send(reachable(), 1)

    assert gate.ok is True


def test_gate_warns_when_the_quota_is_nearly_gone_but_does_not_block():
    gate = notify_schedule.judge_send(reachable(), 1)

    assert gate.notes
    assert gate.ok is True


def test_gate_collects_every_reason_not_just_the_first():
    """届かない かつ 枠切れ なら、両方言う。

    1つ直して再実行したらもう1つで止まる、を繰り返させない。
    """
    gate = notify_schedule.judge_send(unreachable(), 0)

    assert len(gate.blocks) == 2


# ============================================================ 予定を取りに行く


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class FakeEvents:
    """``events.list`` の偽物。**渡された引数を全部覚える。**"""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        index = min(len(self.calls) - 1, len(self.pages) - 1)
        return FakeRequest(self.pages[index])


class FakeService:
    def __init__(self, pages):
        self._events = FakeEvents(pages)

    def events(self):
        return self._events

    @property
    def calls(self):
        return self._events.calls


def test_fetch_passes_the_params_through():
    service = FakeService([{"items": []}])
    params = notify_schedule.build_list_params(date(2026, 8, 21), JST)

    notify_schedule.fetch_all_events(service, params)

    assert service.calls[0]["singleEvents"] is True
    assert service.calls[0]["timeMin"] == "2026-08-21T00:00:00+09:00"


def test_fetch_does_not_send_a_page_token_on_the_first_call():
    """初回に空の ``pageToken`` を載せない。載せると API 側の解釈になる。"""
    service = FakeService([{"items": []}])

    notify_schedule.fetch_all_events(service, {"calendarId": "primary"})

    assert "pageToken" not in service.calls[0]


def test_fetch_follows_the_next_page():
    """**ページを追わないと件数が静かに減る。** エラーは出ない。"""
    service = FakeService(
        [
            {"items": [{"summary": "1件目", "start": {"date": "2026-08-21"}}],
             "nextPageToken": "next"},
            {"items": [{"summary": "2件目", "start": {"date": "2026-08-21"}}]},
        ]
    )

    payload = notify_schedule.fetch_all_events(service, {"calendarId": "primary"})

    assert len(payload["items"]) == 2


def test_fetch_carries_the_page_token_on_the_second_call():
    service = FakeService(
        [{"items": [], "nextPageToken": "next"}, {"items": []}]
    )

    notify_schedule.fetch_all_events(service, {"calendarId": "primary"})

    assert service.calls[1]["pageToken"] == "next"


def test_fetch_stops_when_there_is_no_next_page():
    service = FakeService([{"items": []}])

    notify_schedule.fetch_all_events(service, {"calendarId": "primary"})

    assert len(service.calls) == 1


def test_fetch_raises_instead_of_truncating_at_the_page_limit():
    """上限に当たったら**黙って切らずに落とす**。

    切ると件数が減るだけで、0 件が正常値のこの課題では気づけない。
    """
    endless = [{"items": [], "nextPageToken": "next"}]
    service = FakeService(endless)

    with pytest.raises(notify_schedule.NotifyError):
        notify_schedule.fetch_all_events(service, {"calendarId": "primary"})


def test_fetch_rejects_a_response_that_is_not_a_dict():
    service = FakeService(["これは辞書ではない"])

    with pytest.raises(notify_schedule.NotifyError):
        notify_schedule.fetch_all_events(service, {"calendarId": "primary"})


# ============================================================ 対象日の指定


def test_explicit_date_is_parsed():
    assert notify_schedule.parse_target_date("2026-12-31") == date(2026, 12, 31)


def test_broken_date_is_rejected_instead_of_falling_back_to_today():
    """壊れた日付を今日に倒さない。**倒すと黙って別の日の予定を送る。**"""
    with pytest.raises(notify_schedule.NotifyError):
        notify_schedule.parse_target_date("2026/12/31")


# ============================================================ 送信の記録


def bot_info():
    return line_auth.BotInfo(
        user_id="Ubot",
        basic_id="@bot",
        display_name="テスト用チャネル",
        chat_mode="bot",
        mark_as_read_mode="auto",
    )


DESTINATION = "Udestination0123456789"


def make_record(**over):
    events = notify_schedule.extract_events(
        {"items": [all_day_event("健康診断", "2026-08-21")]}
    )
    kwargs = dict(
        info=bot_info(),
        to=DESTINATION,
        target_date=date(2026, 8, 21),
        events=events,
        text=notify_schedule.build_message(events, date(2026, 8, 21)),
        message_id="1234567890",
        request_id="req-1",
        usage_before=10,
        usage_after=11,
        remaining=100,
    )
    kwargs.update(over)
    return notify_schedule.build_record(**kwargs)


def test_record_masks_the_destination():
    """記録は public リポジトリに入る。宛先をそのまま残さない。"""
    record = make_record()

    assert DESTINATION not in str(record)


def test_record_keeps_the_target_date():
    """**いつの予定を送ったか**を記録する。届いた時刻とはズレうる。"""
    assert make_record()["target_date"] == "2026-08-21"


def test_record_keeps_the_event_count():
    assert make_record()["event_count"] == 1


def test_record_writes_unlimited_quota_as_null_not_zero():
    """無制限を 0 と書かない。**0 は「使い切った」で、真逆の意味になる。**"""
    record = make_record(remaining=None)

    assert record["remaining"] is None


def test_record_keeps_a_negative_remaining_as_is():
    """超過を 0 に丸めない。丸めると使い切りと区別が付かなくなる。"""
    assert make_record(remaining=-12)["remaining"] == -12


# ============================================================ 通しで動かす


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    """LINE の各エンドポイントに**パスで**答える偽物。

    呼ばれた順ではなく URL で答える。ここで確かめたいのは
    「何を確かめてから送るか」であって、呼ぶ順番ではない。
    """

    def __init__(self, *, profile_status=200, quota=None, consumption=0):
        self.profile_status = profile_status
        self.quota = quota if quota is not None else {"type": "limited", "value": 200}
        self.consumption = consumption
        self.gets = []
        self.pushes = []

    def get(self, url):
        self.gets.append(url)
        if url.endswith("/v2/bot/info"):
            return FakeResponse(
                200,
                {
                    "userId": "Ubot",
                    "basicId": "@bot",
                    "displayName": "テスト用チャネル",
                    "chatMode": "bot",
                    "markAsReadMode": "auto",
                },
            )
        if "/v2/bot/profile/" in url:
            if self.profile_status != 200:
                return FakeResponse(self.profile_status, {})
            return FakeResponse(200, {"userId": DESTINATION, "displayName": "なな"})
        if url.endswith("/quota/consumption"):
            return FakeResponse(200, {"totalUsage": self.consumption})
        if url.endswith("/v2/bot/message/quota"):
            return FakeResponse(200, self.quota)
        raise AssertionError(f"想定していない GET: {url}")

    def post(self, url, json=None, headers=None):
        self.pushes.append({"url": url, "body": json, "headers": headers})
        self.consumption += 1
        return FakeResponse(
            200,
            {"sentMessages": [{"id": "1234567890"}]},
            {"x-line-request-id": "req-1"},
        )


def write_env(tmp_path):
    """テスト用の .env。**本物の形は真似しない。**

    それらしい値を置くと、漏れの検査や目視で本物と見分けが付かなくなる。
    """
    path = tmp_path / ".env"
    path.write_text(
        "LINE_CHANNEL_ACCESS_TOKEN=not-a-real-token-only-for-tests\n"
        f"LINE_USER_ID={DESTINATION}\n",
        encoding="utf-8",
    )
    return str(path)


def run_main(tmp_path, *extra, pages=None, target="2026-08-21", **session_kwargs):
    session = FakeSession(**session_kwargs)
    service = FakeService(
        pages
        if pages is not None
        else [{"items": [all_day_event("健康診断", "2026-08-21")]}]
    )
    results = tmp_path / "results.json"

    code = notify_schedule.main(
        [
            "--date",
            target,
            "--env",
            write_env(tmp_path),
            "--results",
            str(results),
            *extra,
        ],
        session_factory=lambda token: session,
        service_factory=lambda: service,
    )
    return code, session, service, results


def test_it_sends_and_records(tmp_path):
    code, session, _service, results = run_main(tmp_path)

    assert code == 0
    assert len(session.pushes) == 1
    assert results.is_file()


def test_the_message_carries_the_target_date(tmp_path):
    _code, session, _service, _results = run_main(tmp_path)

    assert "2026-08-21" in session.pushes[0]["body"]["messages"][0]["text"]


def test_the_target_date_reaches_the_calendar_query(tmp_path):
    """**今日と同じ日付でテストしない。**

    ここを今日で書くと、対象日を捨てて ``date.today()`` で引く実装でも通る。
    2026-08-20 のミューテーションで実際に素通りした——テストの日付が
    たまたま実行日と同じで、**壊れた実装と正しい実装が同じ答えを返した**。
    """
    _code, _session, service, _results = run_main(
        tmp_path, target="2026-12-31", pages=[{"items": []}]
    )

    assert service.calls[0]["timeMin"] == "2026-12-31T00:00:00+09:00"
    assert service.calls[0]["timeMax"] == "2027-01-01T00:00:00+09:00"


def test_an_unreachable_destination_stops_the_send(tmp_path):
    """**404 なら送らない。** push は届かない相手にも 200 を返す。"""
    code, session, _service, _results = run_main(tmp_path, profile_status=404)

    assert code != 0
    assert session.pushes == []


def test_an_unreachable_destination_does_not_even_read_the_calendar(tmp_path):
    """届かないと分かった時点で止める。**同意画面すら開かせない。**

    ガードを後ろに置くと、送らないと決まっている実行でも Google の
    認可を通すことになる。確かめる順番そのものを固定する。
    """
    _code, _session, service, _results = run_main(tmp_path, profile_status=404)

    assert service.calls == []


def test_a_used_up_quota_stops_the_send(tmp_path):
    code, session, _service, _results = run_main(
        tmp_path, quota={"type": "limited", "value": 200}, consumption=200
    )

    assert code != 0
    assert session.pushes == []


def test_an_unlimited_quota_does_not_stop_the_send(tmp_path):
    """**無制限で止めない。** value が省略されるのを 0 と読むと真逆になる。"""
    code, session, _service, _results = run_main(
        tmp_path, quota={"type": "none"}, consumption=9999
    )

    assert code == 0
    assert len(session.pushes) == 1


def test_dry_run_does_not_send(tmp_path):
    """本文とガードだけ見る。**通数を消費せずに確かめられる。**"""
    code, session, service, results = run_main(tmp_path, "--dry-run")

    assert code == 0
    assert session.pushes == []
    assert service.calls
    assert not results.is_file()


def test_zero_events_still_sends(tmp_path):
    """予定が無い日も送る。**来ないことが「予定なし」か故障か区別できない。**"""
    code, session, _service, _results = run_main(tmp_path, pages=[{"items": []}])

    assert code == 0
    assert "予定はありません" in session.pushes[0]["body"]["messages"][0]["text"]


def test_the_record_is_readable_json(tmp_path):
    import json

    _code, _session, _service, results = run_main(tmp_path)
    record = json.loads(results.read_text(encoding="utf-8"))

    assert record["target_date"] == "2026-08-21"
    assert record["event_count"] == 1
    assert record["usage_after"] == record["usage_before"] + 1


def test_a_broken_date_stops_before_any_call(tmp_path):
    """壊れた日付で1回も叩かない。**手元で分かることは通信の前に済ませる。**"""
    session = FakeSession()
    service = FakeService([{"items": []}])

    with pytest.raises(notify_schedule.NotifyError):
        notify_schedule.main(
            ["--date", "2026/08/21", "--env", write_env(tmp_path)],
            session_factory=lambda token: session,
            service_factory=lambda: service,
        )

    assert session.gets == []
    assert service.calls == []


# ================================================ Google のエラーを訳し分ける


def http_error(status):
    """本物の ``HttpError`` を組む。

    偽物のオブジェクトで訳し分けだけ試すと、``except HttpError`` を丸ごと
    消しても気づけない。**捕まえる側も一緒に確かめる。**
    """
    import httplib2
    from googleapiclient.errors import HttpError

    return HttpError(httplib2.Response({"status": status}), b"{}")


class RaisingEvents:
    def __init__(self, error):
        self._error = error
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        raise self._error


class RaisingService:
    def __init__(self, error):
        self._events = RaisingEvents(error)

    def events(self):
        return self._events

    @property
    def calls(self):
        return self._events.calls


def test_a_403_becomes_a_readable_error():
    """403 は「API を有効にしていない」がいちばん多い。名指しで案内する。"""
    service = RaisingService(http_error(403))

    with pytest.raises(notify_schedule.NotifyError) as caught:
        notify_schedule.fetch_all_events(service, {"calendarId": "primary"})

    assert "Calendar API" in str(caught.value)


def test_any_google_error_is_translated_not_leaked():
    """訳せない番号でも、生の HttpError のまま外へ出さない。"""
    service = RaisingService(http_error(500))

    with pytest.raises(notify_schedule.NotifyError):
        notify_schedule.fetch_all_events(service, {"calendarId": "primary"})


def test_a_summary_with_a_newline_stays_on_one_line():
    """予定名に改行が入っても本文の「1行=1件」を崩さない。

    崩れると**件数と行数が合わなくなる**が、エラーは出ない。
    照合側（verify_notify）はこの対応関係を物差しにしているので、
    ここが崩れると照合そのものが意味を失う。
    """
    events = notify_schedule.extract_events(
        {"items": [{"summary": "歯医者\n（保険証を持つ）", "start": {"date": "2026-08-21"}}]}
    )
    text = notify_schedule.build_message(events, date(2026, 8, 21))

    # **行数だけを見ても足りない。** 続きの行は "- " で始まらないので、
    # 改行が残ったままでも「予定の行は1本」に見える（2026-08-21 に素通りした）。
    # 畳んだ結果そのものを完全一致で押さえる。
    assert events[0].summary == "歯医者 （保険証を持つ）"
    assert len(text.splitlines()) == 3
