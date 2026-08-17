"""task8/send_notification.py のテスト。

この課題は**送信経路が2つある**のが他の課題と違う。

===============  ==========================  ==============================
                 Bot Token                   Webhook
===============  ==========================  ==============================
認証             Authorization: Bot <token>  **無し**（URL 自体が資格情報）
送信先           トークンが見えるチャンネル  その webhook のチャンネルだけ
既定の応答       作成したメッセージ          **204 No Content**（空）
メンションの既定 全種類を解釈                **ユーザーのみ**を解釈
===============  ==========================  ==============================

**既定が経路によって違う**ので、既定に頼らず allowed_mentions を明示する。
公式リファレンス曰く、省略時は通常のメッセージが
``{"parse": ["users", "roles", "everyone"]}`` 相当、
「In interactions and webhooks, only user mentions are parsed」。
自動通知を作るときに一番怖いのは、本文に紛れた ``@everyone`` が
全員に飛ぶこと。**既定に頼ると経路を変えた日に挙動が変わる。**
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import send_notification  # noqa: E402
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


def created_message(**overrides):
    """chat 作成の応答を模した最小の器。"""
    payload = {
        "id": MESSAGE_ID,
        "channel_id": CHANNEL,
        "content": TEXT,
        "author": {"id": BOT_USER_ID, "username": "notify-bot", "bot": True},
    }
    payload.update(overrides)
    return payload


class FakeSession:
    """呼ばれた URL・パラメータ・本文を記録する。"""

    def __init__(self, *, get=None, post=None):
        self.headers = {}
        self.calls = []
        self._get = get or FakeResponse(payload={})
        self._post = post or FakeResponse(payload=created_message())

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._get

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._post


# ------------------------------------------------------------------ 本文の組み立て


def test_build_payload_carries_the_content():
    assert send_notification.build_payload(TEXT)["content"] == TEXT


def test_build_payload_suppresses_every_mention_by_default():
    """自動通知が ``@everyone`` を飛ばす事故を、既定で起こさない。"""
    payload = send_notification.build_payload(TEXT)
    assert payload["allowed_mentions"] == {"parse": []}


def test_build_payload_omits_allowed_mentions_when_explicitly_allowed():
    """明示的に許したときだけ Discord の既定に任せる。"""
    payload = send_notification.build_payload(TEXT, allow_mentions=True)
    assert "allowed_mentions" not in payload


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_build_payload_rejects_an_empty_body(text):
    """空のまま送ると API 側でも弾かれるが、手前で止めたほうが原因が近い。"""
    with pytest.raises(send_notification.SendError):
        send_notification.build_payload(text)


def test_build_payload_keeps_the_text_byte_for_byte():
    """**前後の空白を落とさない。**

    照合は送った文字列と読み返した文字列を突き合わせる。ここで黙って
    整形すると、記録した値と送った値が食い違う。課題7で「末尾の改行1バイト」
    の差に気づけたのは、途中で整形していなかったから。
    """
    text = "  前後に空白  "
    assert send_notification.build_payload(text)["content"] == text


# ------------------------------------------------------------------ ID の検査


def test_require_snowflake_returns_the_value():
    assert send_notification.require_snowflake(CHANNEL, "チャンネルID") == CHANNEL


def test_require_snowflake_strips_surrounding_whitespace():
    assert send_notification.require_snowflake(f" {CHANNEL} ", "チャンネルID") == CHANNEL


@pytest.mark.parametrize("value", ["", "   ", "not-a-number", "123abc", "12.3"])
def test_require_snowflake_rejects_non_numeric(value):
    """snowflake は数字だけ。チャンネル名を貼った取り違えをここで止める。"""
    with pytest.raises(send_notification.SendError):
        send_notification.require_snowflake(value, "チャンネルID")


def test_require_snowflake_names_the_field_in_the_error():
    with pytest.raises(send_notification.SendError) as error:
        send_notification.require_snowflake("x", "サーバーID")
    assert "サーバーID" in str(error.value)


def test_require_snowflake_distinguishes_missing_from_malformed():
    """「渡していない」と「形が違う」を同じ文言で返さない。

    どちらも SendError なので**例外の型だけ見るテストでは区別できない**。
    型だけ見ていると、空のときの分岐を消しても素通りする。
    """
    with pytest.raises(send_notification.SendError) as missing:
        send_notification.require_snowflake("", "チャンネルID")
    assert "指定されていません" in str(missing.value)

    with pytest.raises(send_notification.SendError) as malformed:
        send_notification.require_snowflake("general", "チャンネルID")
    assert "数字ではありません" in str(malformed.value)


# ------------------------------------------------------------------ Bot での投稿


def test_post_via_bot_calls_the_documented_endpoint():
    session = FakeSession()
    send_notification.post_via_bot(session, channel=CHANNEL, payload={"content": TEXT})
    method, url, _ = session.calls[0]
    assert method == "POST"
    # 期待値はリテラルで書く。定数どうしを比べると必ず通る。
    assert url == f"https://discord.com/api/v10/channels/{CHANNEL}/messages"


def test_post_via_bot_sends_the_payload_as_json():
    session = FakeSession()
    payload = {"content": TEXT, "allowed_mentions": {"parse": []}}
    send_notification.post_via_bot(session, channel=CHANNEL, payload=payload)
    _, _, kwargs = session.calls[0]
    assert kwargs["json"] == payload


def test_post_via_bot_returns_the_created_message():
    session = FakeSession()
    message = send_notification.post_via_bot(
        session, channel=CHANNEL, payload={"content": TEXT}
    )
    assert message["id"] == MESSAGE_ID


def test_post_via_bot_rejects_a_different_channel():
    """**物差しは、こちらが要求した値から取る。**

    応答の中だけで突き合わせるとトートロジーになる（課題4で join_url を
    応答の id と比べかけたのと同じ形）。
    """
    session = FakeSession(post=FakeResponse(payload=created_message(channel_id="999")))
    with pytest.raises(send_notification.SendError):
        send_notification.post_via_bot(session, channel=CHANNEL, payload={"content": TEXT})


def test_post_via_bot_rejects_a_response_without_an_id():
    """id が無いと読み返せない。「返ってこなかった」を「送れた」にしない。"""
    session = FakeSession(post=FakeResponse(payload=created_message(id="")))
    with pytest.raises(send_notification.SendError):
        send_notification.post_via_bot(session, channel=CHANNEL, payload={"content": TEXT})


def test_post_via_bot_translates_an_http_error():
    session = FakeSession(
        post=FakeResponse(status_code=403, payload={"code": 50013, "message": "Missing Permissions"})
    )
    with pytest.raises(discord_auth.ApiError):
        send_notification.post_via_bot(session, channel=CHANNEL, payload={"content": TEXT})


def test_post_via_bot_handles_a_non_json_success_body():
    session = FakeSession(post=FakeResponse(status_code=200, payload=None, text="<html>"))
    with pytest.raises(send_notification.SendError):
        send_notification.post_via_bot(session, channel=CHANNEL, payload={"content": TEXT})


# ------------------------------------------------------------------ Webhook での投稿


def test_post_via_webhook_posts_to_the_webhook_url():
    session = FakeSession()
    send_notification.post_via_webhook(
        session, WEBHOOK, payload={"content": TEXT}, expected_channel=CHANNEL
    )
    method, url, _ = session.calls[0]
    assert method == "POST"
    assert url == WEBHOOK_URL


def test_post_via_webhook_asks_for_the_created_message():
    """``wait`` の既定は false で、**204 で何も返らない**。

    返らなければ message id が無く、読み返しが成立しない。
    クエリで組み立てず params に渡すのは、URL に ``?`` が付いていても壊れないため。
    """
    session = FakeSession()
    send_notification.post_via_webhook(
        session, WEBHOOK, payload={"content": TEXT}, expected_channel=CHANNEL
    )
    _, _, kwargs = session.calls[0]
    assert kwargs["params"] == {"wait": "true"}


def test_post_via_webhook_explains_an_empty_204_response():
    """wait を落とすと 204。**エラーではない**ので、何が起きたか言わないと分からない。"""
    session = FakeSession(post=FakeResponse(status_code=204, payload=None, text=""))
    with pytest.raises(send_notification.SendError) as error:
        send_notification.post_via_webhook(
            session, WEBHOOK, payload={"content": TEXT}, expected_channel=CHANNEL
        )
    assert "wait" in str(error.value)


def test_post_via_webhook_rejects_a_different_channel():
    session = FakeSession(post=FakeResponse(payload=created_message(channel_id="999")))
    with pytest.raises(send_notification.SendError):
        send_notification.post_via_webhook(
            session, WEBHOOK, payload={"content": TEXT}, expected_channel=CHANNEL
        )


def test_post_via_webhook_never_leaks_the_token_on_error():
    session = FakeSession(
        post=FakeResponse(status_code=404, payload={"code": 10015, "message": f"gone {WEBHOOK_TOKEN}"})
    )
    with pytest.raises(discord_auth.ApiError) as error:
        send_notification.post_via_webhook(
            session, WEBHOOK, payload={"content": TEXT}, expected_channel=CHANNEL
        )
    assert WEBHOOK_TOKEN not in str(error.value)


# ------------------------------------------------------------------ 送る前に確かめる


def test_fetch_channel_calls_the_documented_endpoint():
    session = FakeSession(get=FakeResponse(payload={"id": CHANNEL, "guild_id": GUILD}))
    send_notification.fetch_channel(session, channel=CHANNEL)
    method, url, _ = session.calls[0]
    assert method == "GET"
    assert url == f"https://discord.com/api/v10/channels/{CHANNEL}"


def test_fetch_channel_returns_the_guild_id():
    """「**特定のサーバー内の**チャンネル」を確かめる物差し。

    メッセージ本体の応答には guild_id が入らないので、別のエンドポイントから取る。
    """
    session = FakeSession(get=FakeResponse(payload={"id": CHANNEL, "guild_id": GUILD}))
    assert send_notification.fetch_channel(session, channel=CHANNEL)["guild_id"] == GUILD


def test_require_guild_accepts_a_match():
    send_notification.require_guild(GUILD, expected=GUILD)


def test_require_guild_rejects_a_mismatch():
    with pytest.raises(send_notification.SendError):
        send_notification.require_guild("999", expected=GUILD)


def test_require_guild_rejects_a_direct_message_channel():
    """DM チャンネルには guild_id が無い。要件は「サーバー内のチャンネル」。

    「無い」と「違う」は原因が別なので、文言まで見る。型だけ見ると
    どちらの分岐を消しても素通りする。
    """
    with pytest.raises(send_notification.SendError) as error:
        send_notification.require_guild(None, expected=GUILD)
    assert "DM" in str(error.value)


def test_fetch_webhook_uses_get_webhook_with_token():
    session = FakeSession(get=FakeResponse(payload={"id": WEBHOOK_ID, "channel_id": CHANNEL}))
    send_notification.fetch_webhook(session, WEBHOOK)
    method, url, _ = session.calls[0]
    assert method == "GET"
    assert url == WEBHOOK_URL


def test_fetch_webhook_reports_which_channel_it_targets():
    session = FakeSession(
        get=FakeResponse(payload={"id": WEBHOOK_ID, "channel_id": CHANNEL, "guild_id": GUILD})
    )
    webhook_object = send_notification.fetch_webhook(session, WEBHOOK)
    assert webhook_object["channel_id"] == CHANNEL


# ------------------------------------------------------------------ リンクと記録


def test_message_link_has_the_client_url_shape():
    link = send_notification.message_link(guild=GUILD, channel=CHANNEL, message_id=MESSAGE_ID)
    assert link == f"https://discord.com/channels/{GUILD}/{CHANNEL}/{MESSAGE_ID}"


def test_build_record_keeps_every_field_the_verifier_needs():
    record = send_notification.build_record(
        via="bot",
        guild=GUILD,
        channel=CHANNEL,
        text=TEXT,
        message_id=MESSAGE_ID,
        author_id=BOT_USER_ID,
    )
    for key in ("via", "guild", "channel", "text", "message_id", "author_id", "link"):
        assert key in record


def test_build_record_never_stores_a_credential():
    """**results.json は git に入る。** Webhook の token を書かない。

    author_id に入るのは webhook の **id**（URL の前半）で、これは
    それだけでは投稿できない。token（URL の後半）が資格情報である。
    """
    record = send_notification.build_record(
        via="webhook",
        guild=GUILD,
        channel=CHANNEL,
        text=TEXT,
        message_id=MESSAGE_ID,
        author_id=WEBHOOK_ID,
    )
    assert WEBHOOK_TOKEN not in json.dumps(record, ensure_ascii=False)


def test_write_record_writes_utf8_json(tmp_path):
    destination = tmp_path / "nested" / "results.json"
    send_notification.write_record(destination, {"text": "日本語"})
    assert json.loads(destination.read_text(encoding="utf-8"))["text"] == "日本語"


# ------------------------------------------------------------------ 投稿者の照合


def test_read_author_id_returns_the_id():
    assert send_notification.read_author_id(created_message()) == BOT_USER_ID


def test_read_author_id_rejects_a_missing_author():
    message = created_message()
    del message["author"]
    with pytest.raises(send_notification.SendError):
        send_notification.read_author_id(message)


def test_read_author_id_rejects_an_author_without_an_id():
    with pytest.raises(send_notification.SendError):
        send_notification.read_author_id(created_message(author={"username": "x"}))


def test_require_author_accepts_a_match():
    assert send_notification.require_author(created_message(), expected=BOT_USER_ID) == BOT_USER_ID


def test_require_author_rejects_someone_else():
    """同じチャンネルの**別の投稿**を掴んでいないかを見る。"""
    with pytest.raises(send_notification.SendError):
        send_notification.require_author(created_message(), expected="999")


# ------------------------------------------------------------------ 通しの流れ


def bot_factory(session=None, *, identity_id=BOT_USER_ID):
    session = session or FakeSession(
        get=FakeResponse(payload={"id": CHANNEL, "guild_id": GUILD}),
        post=FakeResponse(payload=created_message()),
    )
    identity = discord_auth.Identity(user_id=identity_id, username="notify-bot")

    def factory():
        return session, identity, "DUMMY-BOT-TOKEN-not-a-real-credential"

    return factory


def webhook_factory(session=None):
    session = session or FakeSession(
        get=FakeResponse(payload={"id": WEBHOOK_ID, "channel_id": CHANNEL, "guild_id": GUILD}),
        post=FakeResponse(
            payload=created_message(
                author={"id": WEBHOOK_ID, "username": "notify-hook"}, webhook_id=WEBHOOK_ID
            )
        ),
    )

    def factory():
        return session, WEBHOOK

    return factory


BOT_ARGS = ["--via", "bot", "--guild", GUILD, "--channel", CHANNEL, "--text", TEXT]
HOOK_ARGS = ["--via", "webhook", "--guild", GUILD, "--channel", CHANNEL, "--text", TEXT]


def test_main_bot_path_succeeds():
    assert send_notification.main(BOT_ARGS, bot_factory=bot_factory()) == 0


def test_main_bot_path_writes_the_record(tmp_path):
    out = tmp_path / "results.json"
    send_notification.main(
        BOT_ARGS + ["--json-out", str(out)], bot_factory=bot_factory()
    )
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["via"] == "bot"
    assert record["message_id"] == MESSAGE_ID
    assert record["author_id"] == BOT_USER_ID


def test_main_does_not_write_without_json_out(tmp_path):
    """既定のパスへ勝手に上書きしない。"""
    send_notification.main(BOT_ARGS, bot_factory=bot_factory())
    assert list(tmp_path.iterdir()) == []


def test_main_bot_path_fails_on_a_foreign_guild():
    """チャンネルが別のサーバーのものなら送らない。"""
    session = FakeSession(get=FakeResponse(payload={"id": CHANNEL, "guild_id": "999"}))
    assert send_notification.main(BOT_ARGS, bot_factory=bot_factory(session)) == 1


def test_main_bot_path_does_not_post_when_the_guild_is_wrong():
    """**確かめてから送る。** 落ちる前に POST が飛んでいたら意味が無い。"""
    session = FakeSession(get=FakeResponse(payload={"id": CHANNEL, "guild_id": "999"}))
    send_notification.main(BOT_ARGS, bot_factory=bot_factory(session))
    assert [method for method, _, _ in session.calls] == ["GET"]


def test_main_bot_path_fails_when_someone_else_posted():
    assert send_notification.main(BOT_ARGS, bot_factory=bot_factory(identity_id="999")) == 1


def test_main_reports_an_auth_failure():
    def failing():
        raise discord_auth.AuthError("トークンがありません")

    assert send_notification.main(BOT_ARGS, bot_factory=failing) == 1


def test_main_webhook_path_succeeds():
    assert send_notification.main(HOOK_ARGS, webhook_factory=webhook_factory()) == 0


def test_main_webhook_path_fails_when_the_author_is_not_the_webhook():
    """webhook で送ると author.id は webhook の id になる。違えば別物。"""
    session = FakeSession(
        get=FakeResponse(payload={"id": WEBHOOK_ID, "channel_id": CHANNEL, "guild_id": GUILD}),
        post=FakeResponse(
            payload=created_message(
                author={"id": "999", "username": "someone"}, webhook_id=WEBHOOK_ID
            )
        ),
    )
    assert send_notification.main(HOOK_ARGS, webhook_factory=webhook_factory(session)) == 1


def webhook_message():
    """webhook 経由で正常に載ったメッセージ。author.id は webhook の id になる。

    **送る前の検査を試すテストには、投稿側は正常なものを積む。**
    壊れた応答を積むと、検査を消しても投稿側の別の検査に吸われて落ち、
    「守れているつもり」になる（実際にこの形で2件のミューテーションが生き残った）。
    """
    return created_message(
        author={"id": WEBHOOK_ID, "username": "notify-hook"}, webhook_id=WEBHOOK_ID
    )


def test_main_webhook_path_fails_on_a_foreign_guild():
    """guild_id が返ったのに食い違うなら止める。"""
    session = FakeSession(
        get=FakeResponse(payload={"id": WEBHOOK_ID, "channel_id": CHANNEL, "guild_id": "999"}),
        post=FakeResponse(payload=webhook_message()),
    )
    assert send_notification.main(HOOK_ARGS, webhook_factory=webhook_factory(session)) == 1


def test_main_webhook_path_does_not_post_to_a_foreign_guild():
    """**確かめてから送る。** 落ちる前に POST が飛んでいたら意味が無い。"""
    session = FakeSession(
        get=FakeResponse(payload={"id": WEBHOOK_ID, "channel_id": CHANNEL, "guild_id": "999"}),
        post=FakeResponse(payload=webhook_message()),
    )
    send_notification.main(HOOK_ARGS, webhook_factory=webhook_factory(session))
    assert [method for method, _, _ in session.calls] == ["GET"]


def test_main_webhook_path_fails_when_it_targets_another_channel():
    """**宛先は URL 側で決まる。** 指定と食い違ったまま送らない。"""
    session = FakeSession(
        get=FakeResponse(payload={"id": WEBHOOK_ID, "channel_id": "999", "guild_id": GUILD}),
        post=FakeResponse(payload=webhook_message()),
    )
    assert send_notification.main(HOOK_ARGS, webhook_factory=webhook_factory(session)) == 1


def test_main_webhook_path_does_not_post_to_the_wrong_channel():
    session = FakeSession(
        get=FakeResponse(payload={"id": WEBHOOK_ID, "channel_id": "999", "guild_id": GUILD}),
        post=FakeResponse(payload=webhook_message()),
    )
    send_notification.main(HOOK_ARGS, webhook_factory=webhook_factory(session))
    assert [method for method, _, _ in session.calls] == ["GET"]


def test_main_webhook_path_fails_without_the_webhook_marker():
    """``webhook_id`` が付いていないなら、webhook 経由で載っていない。"""
    session = FakeSession(
        get=FakeResponse(payload={"id": WEBHOOK_ID, "channel_id": CHANNEL, "guild_id": GUILD}),
        post=FakeResponse(
            payload=created_message(author={"id": WEBHOOK_ID, "username": "notify-hook"})
        ),
    )
    assert send_notification.main(HOOK_ARGS, webhook_factory=webhook_factory(session)) == 1


def test_main_webhook_path_survives_a_response_without_guild_id(capsys):
    """guild_id が返らないときは**「確認できません」と言う**。黙って通さない。"""
    session = FakeSession(
        get=FakeResponse(payload={"id": WEBHOOK_ID, "channel_id": CHANNEL}),
        post=FakeResponse(
            payload=created_message(
                author={"id": WEBHOOK_ID, "username": "notify-hook"}, webhook_id=WEBHOOK_ID
            )
        ),
    )
    assert send_notification.main(HOOK_ARGS, webhook_factory=webhook_factory(session)) == 0
    assert "確認できません" in capsys.readouterr().out
