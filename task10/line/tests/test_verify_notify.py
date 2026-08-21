"""task10/line/verify_notify.py のテスト。

課題9の verify_push.py から**主題を引き継ぐ**：
「確認できないことを、確認できないと言う」。

課題10で変わるのは、照合できる相手が1つ増えたこと。

============================== ================================================
照合の相手                      何を言えるか
============================== ================================================
``/v2/bot/info``                意図したチャネルを叩いた（課題9から継承）
``quota/consumption``           **送信対象として数えられた**（課題9から継承）
``/v2/bot/profile/{userId}``    **いまも届く状態にある**（課題10で追加）
Google カレンダー                 **対象日の件数が記録と合う**（課題10で追加）
============================== ================================================

**それでも「何が届いたか」は言えない。** LINE に読み返す API が無いことは
課題9から変わっていないので、注記は消えない。

カレンダーの照合には**時点のズレ**という新しい限界がある。この検査が読むのは
「いま」のカレンダーで、送信した瞬間のものではない。送信後に予定を足せば、
実装が正しくても不一致になる。**それを検査結果に書く**のが課題9の型の継承である。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import notify_schedule  # noqa: E402
import verify_notify  # noqa: E402

DESTINATION = "Udestination0123456789"


def make_text(count=1):
    events = notify_schedule.extract_events(
        {
            "items": [
                {"summary": f"予定{index}", "start": {"date": "2026-08-21"}}
                for index in range(count)
            ]
        }
    )
    return notify_schedule.build_message(events, date(2026, 8, 21))


def make_record(**over):
    record = {
        "bot": {
            "user_id": "Ubot",
            "basic_id": "@bot",
            "display_name": "テスト用チャネル",
            "chat_mode": "bot",
            "mark_as_read_mode": "auto",
        },
        "to_masked": "Ud…89",
        "target_date": "2026-08-21",
        "event_count": 1,
        "all_day_count": 1,
        "text": make_text(1),
        "message_id": "1234567890",
        "request_id": "req-1",
        "usage_before": 10,
        "usage_after": 11,
        "remaining": 189,
    }
    record.update(over)
    return record


# ============================================================ 記録を読む


def test_a_missing_record_is_an_error_not_a_pass(tmp_path):
    """記録が無いのを「照合できた」にしない。"""
    with pytest.raises(verify_notify.VerifyError):
        verify_notify.load_results(tmp_path / "nope.json")


def test_a_record_missing_a_key_is_an_error(tmp_path):
    """**欠けた項目を「無いので OK」にしない。**

    比べる相手がいない検査は必ず通る。0 件の照合を合格として出すのと同じ形。
    """
    path = tmp_path / "results.json"
    broken = make_record()
    del broken["event_count"]
    path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(verify_notify.VerifyError):
        verify_notify.load_results(path)


def test_a_readable_record_loads(tmp_path):
    path = tmp_path / "results.json"
    path.write_text(json.dumps(make_record(), ensure_ascii=False), encoding="utf-8")

    assert verify_notify.load_results(path)["target_date"] == "2026-08-21"


# ============================================================ 手元の検査


def local(record):
    return {check.label: check for check in verify_notify.build_local_checks(record)}


def test_one_message_counted_passes():
    assert local(make_record())["通数の増分"].ok is True


def test_two_messages_counted_fails():
    """「増えた」ではなく「**1 増えた**」で見る。多ければ良いわけではない。"""
    assert local(make_record(usage_after=12))["通数の増分"].ok is False


def test_the_message_must_carry_the_target_date():
    assert local(make_record())["本文に対象日がある"].ok is True


def test_a_message_without_the_target_date_fails():
    assert local(make_record(text="予定はありません。"))["本文に対象日がある"].ok is False


def test_the_line_count_must_match_the_event_count():
    """本文の行数と記録の件数が合うこと。

    **本文の組み立てが壊れると、件数だけ静かに減る。** 0 件が正常値である
    以上、行が消えても「予定が無い日」に見えてしまう。
    """
    assert local(make_record())["予定の行数と件数"].ok is True


def test_a_dropped_line_fails_the_count_check():
    assert local(make_record(event_count=3))["予定の行数と件数"].ok is False


def test_zero_events_is_consistent_when_the_message_says_so():
    record = make_record(event_count=0, all_day_count=0, text=make_text(0))

    assert local(record)["予定の行数と件数"].ok is True


def test_an_unmasked_destination_fails():
    """記録は public リポジトリに入る。伏せ忘れを検査で捕まえる。"""
    assert local(make_record(to_masked=DESTINATION))["宛先が伏せられている"].ok is False


def test_a_null_remaining_is_accepted_as_unlimited():
    """**無制限（null）を不合格にしない。** 0 と混ぜると真逆の判定になる。"""
    assert local(make_record(remaining=None))["残数が読める形"].ok is True


def test_a_numeric_remaining_is_accepted():
    assert local(make_record(remaining=0))["残数が読める形"].ok is True


def test_a_non_numeric_remaining_fails():
    assert local(make_record(remaining="たくさん"))["残数が読める形"].ok is False


# ============================================================ 遠隔の検査


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, *, basic_id="@bot", consumption=11, profile_status=200):
        self.basic_id = basic_id
        self.consumption = consumption
        self.profile_status = profile_status
        self.gets = []

    def get(self, url):
        self.gets.append(url)
        if url.endswith("/v2/bot/info"):
            return FakeResponse(
                200,
                {
                    "userId": "Ubot",
                    "basicId": self.basic_id,
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
            return FakeResponse(200, {"type": "limited", "value": 200})
        raise AssertionError(f"想定していない GET: {url}")


def remote(record, **session_kwargs):
    session = FakeSession(**session_kwargs)
    checks = verify_notify.build_remote_checks(session, record, DESTINATION)
    return {check.label: check for check in checks}


def test_the_channel_must_be_the_recorded_one():
    """**API を叩き直す。** 記録の中で値を比べるとトートロジーになる。"""
    assert remote(make_record())["basicId（API と記録）"].ok is True


def test_a_different_channel_fails():
    assert remote(make_record(), basic_id="@other")["basicId（API と記録）"].ok is False


def test_the_counted_messages_may_grow_but_not_shrink():
    """月内で単調に増えるので「記録の値以上」で見る。"""
    assert remote(make_record(), consumption=40)["いま数えた通数"].ok is True


def test_a_shrinking_count_fails():
    assert remote(make_record(), consumption=3)["いま数えた通数"].ok is False


def test_the_destination_is_checked_again():
    """**いまも届くか**を確かめ直す。課題9には無かった検査。"""
    assert remote(make_record())["いまも宛先に届く"].ok is True


def test_a_now_unreachable_destination_fails():
    """送ったあとにブロックされていれば、ここで分かる。

    ただし**送った時点で届いたことの証明にはならない**。逆向きも同じで、
    それは「確認できないこと」に書く。
    """
    assert remote(make_record(), profile_status=404)["いまも宛先に届く"].ok is False


# ============================================================ カレンダーの照合


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class FakeEvents:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return FakeRequest(self._payload)


class FakeService:
    def __init__(self, count):
        self._events = FakeEvents(
            {
                "items": [
                    {"summary": f"予定{index}", "start": {"date": "2026-08-21"}}
                    for index in range(count)
                ]
            }
        )

    def events(self):
        return self._events

    @property
    def calls(self):
        return self._events.calls


def calendar(record, count):
    checks = verify_notify.build_calendar_checks(FakeService(count), record)
    return {check.label: check for check in checks}


def test_the_calendar_count_matches_the_record():
    assert calendar(make_record(), 1)["カレンダーの件数（いま）"].ok is True


def test_a_changed_calendar_fails_the_count_check():
    """予定が変われば不一致になる。**それが正しい。**

    実装が壊れたのか、人が予定を足したのかは検査からは区別できない。
    だから「確認できないこと」に時点のズレを明記する。
    """
    assert calendar(make_record(), 3)["カレンダーの件数（いま）"].ok is False


def test_the_calendar_is_queried_for_the_recorded_date():
    """**記録に書いた対象日で問い合わせる。** 今日で引くと毎日ズレる。

    **記録の日付を「今日」にしてはいけない。** 今日で書くと、対象日を捨てて
    ``date.today()`` で引く実装でも通る。2026-08-20 のミューテーションで
    実際に素通りした。
    """
    service = FakeService(1)
    verify_notify.build_calendar_checks(service, make_record(target_date="2026-12-31"))

    assert service.calls[0]["timeMin"] == "2026-12-31T00:00:00+09:00"


# ============================================================ 確認できないこと


def test_the_notes_are_never_empty():
    """**空にできない作りにしてある。** 空になれば「全部確認した」に見える。"""
    assert verify_notify.unverifiable_notes()


def test_the_notes_say_the_message_body_cannot_be_read_back():
    assert any("読み返す API が無い" in note for note in verify_notify.unverifiable_notes())


def test_the_notes_say_the_calendar_is_read_at_a_different_time():
    """カレンダー照合の限界。**新しく足した検査の限界を自分で言う。**"""
    assert any("いま" in note and "カレンダー" in note
               for note in verify_notify.unverifiable_notes())


def test_the_notes_say_the_guard_does_not_prove_delivery():
    """送信前ガードが「届く」と答えても、届いたことにはならない。"""
    assert any("送信前" in note for note in verify_notify.unverifiable_notes())


def test_the_report_shows_the_notes_even_when_everything_passes():
    """**合格でも注記を出す。ここが課題9から継承した主題。**

    ``in`` の部分一致ではなく、注記の全文が1つずつ現れることで確かめる。
    課題9では、この主題を確かめる assert が2つとも**無関係な場所の文字列で
    満たされていて**、行を丸ごと消しても通った。
    """
    report = verify_notify.format_report(verify_notify.build_local_checks(make_record()))

    for note in verify_notify.unverifiable_notes():
        assert note in report


def test_the_report_marks_failures():
    report = verify_notify.format_report(
        verify_notify.build_local_checks(make_record(usage_after=99))
    )

    assert "[NG]" in report


# ============================================================ 通しで動かす


def write_env(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "LINE_CHANNEL_ACCESS_TOKEN=not-a-real-token-only-for-tests\n"
        f"LINE_USER_ID={DESTINATION}\n",
        encoding="utf-8",
    )
    return str(path)


def write_results(tmp_path, **over):
    path = tmp_path / "results.json"
    path.write_text(
        json.dumps(make_record(**over), ensure_ascii=False), encoding="utf-8"
    )
    return str(path)


def test_local_only_does_not_touch_the_network(tmp_path):
    """``--local-only`` で API を1回も叩かない。

    課題9から継承。**叩かずに済ませられる検査があること**自体が、
    どこまでが手元の話かを示している。
    """
    called = []

    code = verify_notify.main(
        ["--results", write_results(tmp_path), "--local-only"],
        session_factory=lambda token: called.append(token),
        service_factory=lambda: called.append("service"),
    )

    assert code == 0
    assert called == []


def test_a_failing_local_check_returns_non_zero(tmp_path):
    code = verify_notify.main(
        ["--results", write_results(tmp_path, usage_after=99), "--local-only"]
    )

    assert code != 0


def test_no_calendar_skips_only_the_calendar(tmp_path):
    """LINE 側だけ照合できる。**カレンダーの同意画面を開かせない。**"""
    session = FakeSession()
    service_calls = []

    code = verify_notify.main(
        [
            "--results",
            write_results(tmp_path),
            "--env",
            write_env(tmp_path),
            "--no-calendar",
        ],
        session_factory=lambda token: session,
        service_factory=lambda: service_calls.append("built"),
    )

    assert code == 0
    assert service_calls == []
    assert session.gets


def test_a_broken_target_date_is_a_verify_error():
    """記録の対象日が壊れていたら、**生の ValueError を外へ出さない**。

    スタックトレースだけが出ると、原因がカレンダー側に見える。
    「記録が壊れている」と言えるのは、記録を読んだこちら側だけである。
    """
    with pytest.raises(verify_notify.VerifyError):
        verify_notify.build_calendar_checks(FakeService(1), make_record(target_date=""))


def test_the_recorded_destination_must_match_the_current_one():
    """記録した宛先と、いま .env にある宛先が同じかを見る。

    **伏せ字どうしで比べる。** 違っていれば、別の宛先の記録を
    別の宛先で照合していることになり、通数も届くかどうかも別の話になる。
    """
    assert remote(make_record())["宛先（.env と記録）"].ok is True


def test_a_different_destination_fails():
    assert remote(make_record(to_masked="Ux…yz"))["宛先（.env と記録）"].ok is False
