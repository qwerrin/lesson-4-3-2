"""task8/verify_notification.py のテスト。

この課題の読み返しには、**落ちない失敗**が2つある。どちらもエラーを返さないので、
「取れなかった」が「一致した」に化けやすい。

1. ``READ_MESSAGE_HISTORY`` が無いと、公式リファレンス曰く
   「no messages will be returned」。**例外ではなく空**。
2. ``MESSAGE_CONTENT`` 特権インテントが無いと ``content`` が**空文字**で返る。
   自分が送ったメッセージは例外扱いなので普段は取れるが、
   「取れるはず」を根拠に空を素通りさせない。

だから照合は「一致したか」だけでなく「**そもそも中身があったか**」を見る。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import verify_notification  # noqa: E402
from common import discord_auth  # noqa: E402


GUILD = "111111111111111111"
CHANNEL = "222222222222222222"
MESSAGE_ID = "333333333333333333"
BOT_USER_ID = "444444444444444444"
WEBHOOK_ID = "555555555555555555"

WEBHOOK_TOKEN = "DUMMY-WEBHOOK-TOKEN-not-a-real-credential"
WEBHOOK_URL = f"https://discord.com/api/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}"
WEBHOOK = discord_auth.Webhook(id=WEBHOOK_ID, token=WEBHOOK_TOKEN, url=WEBHOOK_URL)

TEXT = "課題8: Discord への自動通知テスト & <embed> 無し"


def record(**overrides):
    payload = {
        "via": "bot",
        "guild": GUILD,
        "channel": CHANNEL,
        "text": TEXT,
        "message_id": MESSAGE_ID,
        "author_id": BOT_USER_ID,
        "link": f"https://discord.com/channels/{GUILD}/{CHANNEL}/{MESSAGE_ID}",
    }
    payload.update(overrides)
    return payload


def stored_message(**overrides):
    payload = {
        "id": MESSAGE_ID,
        "channel_id": CHANNEL,
        "content": TEXT,
        "author": {"id": BOT_USER_ID, "username": "notify-bot", "bot": True},
    }
    payload.update(overrides)
    return payload


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("応答が JSON ではありません")
        return self._payload


class FakeSession:
    def __init__(self, *responses):
        self.headers = {}
        self.calls = []
        self._responses = list(responses) or [FakeResponse(payload=stored_message())]

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def write_results(tmp_path, payload):
    destination = tmp_path / "results.json"
    destination.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return destination


# ------------------------------------------------------------------ 結果ファイル


def test_load_results_reads_the_record(tmp_path):
    path = write_results(tmp_path, record())
    assert verify_notification.load_results(path)["message_id"] == MESSAGE_ID


def test_load_results_rejects_a_missing_file(tmp_path):
    with pytest.raises(verify_notification.VerifyError):
        verify_notification.load_results(tmp_path / "nope.json")


def test_load_results_rejects_broken_json(tmp_path):
    path = tmp_path / "results.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(verify_notification.VerifyError):
        verify_notification.load_results(path)


def test_load_results_rejects_a_non_dict(tmp_path):
    path = tmp_path / "results.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(verify_notification.VerifyError):
        verify_notification.load_results(path)


@pytest.mark.parametrize(
    "key", ["via", "guild", "channel", "text", "message_id", "author_id", "link"]
)
def test_load_results_requires_every_key(tmp_path, key):
    """欠けたまま照合に進むと「確かめた項目ゼロで全部一致」が出る。"""
    payload = record()
    del payload[key]
    path = write_results(tmp_path, payload)
    with pytest.raises(verify_notification.VerifyError):
        verify_notification.load_results(path)


def test_load_results_rejects_a_numeric_message_id(tmp_path):
    """**snowflake を数値で持たない。**

    64bit なので、数値になった時点で末尾が変わっている可能性がある。
    課題7の「ts を float にすると別のメッセージを指す」と同じ形。
    """
    path = write_results(tmp_path, record(message_id=333333333333333333))
    with pytest.raises(verify_notification.VerifyError):
        verify_notification.load_results(path)


# ------------------------------------------------------------------ 手元でできる照合


def test_local_checks_pass_for_a_consistent_record():
    checks = verify_notification.build_local_checks(
        record(), expected_guild=GUILD, expected_channel=CHANNEL, expected_text=TEXT
    )
    assert verify_notification.all_ok(checks)


@pytest.mark.parametrize(
    "field, value",
    [("guild", "999"), ("channel", "999"), ("text", "ちがう本文")],
)
def test_local_checks_catch_a_mismatch(field, value):
    """期待値は**人間がコマンドラインで渡した値**から取る。

    結果ファイルの値で埋めると、ファイルが間違っていても一致する。
    """
    checks = verify_notification.build_local_checks(
        record(**{field: value}),
        expected_guild=GUILD,
        expected_channel=CHANNEL,
        expected_text=TEXT,
    )
    assert not verify_notification.all_ok(checks)


def test_local_checks_catch_a_link_that_points_elsewhere():
    checks = verify_notification.build_local_checks(
        record(link="https://discord.com/channels/1/2/3"),
        expected_guild=GUILD,
        expected_channel=CHANNEL,
        expected_text=TEXT,
    )
    assert not verify_notification.all_ok(checks)


def test_local_checks_catch_an_unknown_route():
    checks = verify_notification.build_local_checks(
        record(via="carrier-pigeon"),
        expected_guild=GUILD,
        expected_channel=CHANNEL,
        expected_text=TEXT,
    )
    assert not verify_notification.all_ok(checks)


def test_all_ok_is_false_for_zero_checks():
    """**「何も確かめていない」を「全部一致」に化けさせない。**"""
    assert verify_notification.all_ok([]) is False


# ------------------------------------------------------------------ 読み直す


def test_fetch_message_via_bot_calls_the_documented_endpoint():
    session = FakeSession()
    verify_notification.fetch_message_via_bot(session, channel=CHANNEL, message_id=MESSAGE_ID)
    method, url, _ = session.calls[0]
    assert method == "GET"
    assert url == f"https://discord.com/api/v10/channels/{CHANNEL}/messages/{MESSAGE_ID}"


def test_fetch_message_via_bot_returns_the_message():
    session = FakeSession()
    message = verify_notification.fetch_message_via_bot(
        session, channel=CHANNEL, message_id=MESSAGE_ID
    )
    assert message["id"] == MESSAGE_ID


def test_fetch_message_via_bot_explains_a_404():
    """404 の原因は1つに決まらない。**候補を出しつつ断定しない。**"""
    session = FakeSession(FakeResponse(status_code=404, payload={"code": 10008, "message": "Unknown Message"}))
    with pytest.raises(discord_auth.ApiError):
        verify_notification.fetch_message_via_bot(session, channel=CHANNEL, message_id=MESSAGE_ID)


def test_fetch_message_via_webhook_uses_get_webhook_message():
    session = FakeSession()
    verify_notification.fetch_message_via_webhook(session, WEBHOOK, message_id=MESSAGE_ID)
    method, url, _ = session.calls[0]
    assert method == "GET"
    assert url == f"{WEBHOOK_URL}/messages/{MESSAGE_ID}"


def test_fetch_message_via_webhook_hides_the_token_on_error():
    session = FakeSession(
        FakeResponse(status_code=404, payload={"code": 10015, "message": f"gone {WEBHOOK_TOKEN}"})
    )
    with pytest.raises(discord_auth.ApiError) as error:
        verify_notification.fetch_message_via_webhook(session, WEBHOOK, message_id=MESSAGE_ID)
    assert WEBHOOK_TOKEN not in str(error.value)


# ------------------------------------------------------------------ 読み直した内容との照合


def remote(message=None, **kwargs):
    options = {
        "payload": record(),
        "message": stored_message() if message is None else message,
        "actor_id": BOT_USER_ID,
        "guild_id": GUILD,
    }
    options.update(kwargs)
    return verify_notification.build_remote_checks(**options)


def test_remote_checks_pass_when_everything_matches():
    assert verify_notification.all_ok(remote())


def test_remote_checks_reject_a_missing_message():
    """0 件を「照合する対象が無い＝全部一致」にしない。"""
    assert not verify_notification.all_ok(remote(message=False))


@pytest.mark.parametrize(
    "field, value",
    [("id", "999"), ("channel_id", "999"), ("content", "ちがう本文")],
)
def test_remote_checks_catch_a_mismatch(field, value):
    assert not verify_notification.all_ok(remote(message=stored_message(**{field: value})))


def test_remote_checks_catch_a_different_author():
    assert not verify_notification.all_ok(
        remote(message=stored_message(author={"id": "999", "username": "someone"}))
    )


def test_remote_checks_compare_the_author_against_the_live_actor():
    """記録した投稿者と、**いま動かしている主体**が同じかを見る。

    別のアプリのトークンで確認すると、一致しても他人の投稿を見ているだけになる。
    """
    assert not verify_notification.all_ok(remote(actor_id="999"))


def test_remote_checks_catch_a_foreign_guild():
    assert not verify_notification.all_ok(remote(guild_id="999"))


def test_remote_checks_flag_empty_content_as_the_intent_problem():
    """``content`` が空なのは「本文が違う」ではなく「読めていない」。

    **原因が違えば直しかたも違う**ので、同じ「不一致」で片付けない。
    """
    checks = remote(message=stored_message(content=""))
    assert not verify_notification.all_ok(checks)
    detail = " ".join(check.detail for check in checks)
    assert "MESSAGE CONTENT" in detail


def test_remote_checks_do_not_mention_the_intent_when_content_merely_differs():
    """空でないのに intent の話を出すと、合っている設定を疑わせる。

    課題7の「not_in_channel でスコープの話をしない」と同じ線。
    """
    checks = remote(message=stored_message(content="ちがう本文"))
    detail = " ".join(check.detail for check in checks)
    assert "MESSAGE CONTENT" not in detail


def test_remote_checks_do_not_treat_empty_content_as_a_pass():
    """空でも「一致」にしない。ここを緩めると照合が何も確かめなくなる。"""
    assert not verify_notification.all_ok(remote(message=stored_message(content="")))


def test_remote_checks_compare_the_content_byte_for_byte():
    """**Discord は本文を変換して保存しない**（Slack との違い）。

    課題7では ``&`` が ``&amp;`` になったので変換してから比べた。
    ここで同じ「どちらでも通す」判定を入れると、照合が何も確かめなくなる。
    実測で確かめるまでは**そのまま一致**を期待する。
    """
    checks = remote(message=stored_message(content=TEXT.replace("&", "&amp;")))
    assert not verify_notification.all_ok(checks)


# ------------------------------------------------------------------ 通しの流れ


def bot_factory(*responses, identity_id=BOT_USER_ID):
    """(session, identity, token) を返す。

    ``_verify_via_bot`` は GET を2回叩く——先に ``/channels/{id}``（所属サーバー）、
    次に ``/channels/{id}/messages/{id}``（本体）。**順番も含めて固定する。**
    """
    session = FakeSession(*(responses or (
        FakeResponse(payload={"id": CHANNEL, "guild_id": GUILD}),
        FakeResponse(payload=stored_message()),
    )))
    identity = discord_auth.Identity(user_id=identity_id, username="notify-bot")

    def factory():
        return session, identity, "DUMMY-BOT-TOKEN-not-a-real-credential"

    return factory


def args_for(path, *, guild=GUILD, channel=CHANNEL, text=TEXT):
    return [
        "--results", str(path),
        "--guild", guild,
        "--channel", channel,
        "--expect-text", text,
    ]


def test_main_succeeds_for_a_matching_record(tmp_path):
    path = write_results(tmp_path, record())
    assert verify_notification.main(args_for(path), bot_factory=bot_factory()) == 0


def test_main_fails_when_the_local_check_disagrees(tmp_path):
    path = write_results(tmp_path, record())
    assert verify_notification.main(
        args_for(path, text="ちがう本文"), bot_factory=bot_factory()
    ) == 1


def test_main_does_not_call_the_api_when_the_local_check_fails(tmp_path):
    """**手元で落ちる実行はネットワークに出す価値がない。**"""
    path = write_results(tmp_path, record())
    factory = bot_factory()
    session, _, _ = factory()
    verify_notification.main(args_for(path, text="ちがう本文"), bot_factory=factory)
    assert session.calls == []


def test_main_fails_when_the_stored_message_differs(tmp_path):
    path = write_results(tmp_path, record())
    factory = bot_factory(
        FakeResponse(payload={"id": CHANNEL, "guild_id": GUILD}),
        FakeResponse(payload=stored_message(content="書き換えられた本文")),
    )
    assert verify_notification.main(args_for(path), bot_factory=factory) == 1


def test_main_fails_when_the_channel_belongs_to_another_guild(tmp_path):
    path = write_results(tmp_path, record())
    factory = bot_factory(
        FakeResponse(payload={"id": CHANNEL, "guild_id": "999"}),
        FakeResponse(payload=stored_message()),
    )
    assert verify_notification.main(args_for(path), bot_factory=factory) == 1


def test_main_reports_what_it_did_not_check(tmp_path, capsys):
    """**「すべて一致しました」だけを出す道具は、検査していない場所まで
    保証しているように読める**（課題5の教訓）。範囲を必ず書く。
    """
    path = write_results(tmp_path, record())
    verify_notification.main(args_for(path), bot_factory=bot_factory())
    assert "確かめていないこと" in capsys.readouterr().out


# ------------------------------------------------------------------ 1点だけ違う入力での照合
#
# 上の parametrize は「食い違いを検出する」ことは見ているが、**どの検査が
# 捕まえたか**は見ていない。実際、guild を変えるとリンク検査のほうが先に
# NG を出すので、サーバーの照合そのものを消しても素通りした。
# 効きめを確かめるには「他の条件は全部満たしたうえで、狙った1点だけ違う」入力が要る。


def test_local_checks_isolate_the_guild_comparison():
    """リンクも辻褄が合っている状態で、サーバーだけ食い違わせる。"""
    checks = verify_notification.build_local_checks(
        record(guild="999", link=f"https://discord.com/channels/999/{CHANNEL}/{MESSAGE_ID}"),
        expected_guild=GUILD,
        expected_channel=CHANNEL,
        expected_text=TEXT,
    )
    assert [check.label for check in checks if not check.ok] == ["サーバー"]


def test_local_checks_isolate_the_channel_comparison():
    checks = verify_notification.build_local_checks(
        record(channel="999", link=f"https://discord.com/channels/{GUILD}/999/{MESSAGE_ID}"),
        expected_guild=GUILD,
        expected_channel=CHANNEL,
        expected_text=TEXT,
    )
    assert [check.label for check in checks if not check.ok] == ["チャンネル"]


def test_local_checks_isolate_the_message_id_format():
    """snowflake でない ID を、リンクとは辻褄が合った状態で入れる。"""
    checks = verify_notification.build_local_checks(
        record(message_id="abc", link=f"https://discord.com/channels/{GUILD}/{CHANNEL}/abc"),
        expected_guild=GUILD,
        expected_channel=CHANNEL,
        expected_text=TEXT,
    )
    assert [check.label for check in checks if not check.ok] == ["メッセージIDの形式"]


def test_remote_checks_isolate_the_running_actor():
    """**記録した主体**と**いま動かしている主体**の食い違いだけを起こす。

    メッセージ側の投稿者は actor と一致させておく。そうしないと「投稿者」の
    検査が先に NG を出して、こちらの検査を消しても素通りする。
    """
    checks = remote(
        actor_id="999", message=stored_message(author={"id": "999", "username": "other"})
    )
    assert [check.label for check in checks if not check.ok] == ["実行中の主体"]
