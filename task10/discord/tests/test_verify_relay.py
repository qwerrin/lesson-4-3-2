"""task10/discord/verify_relay.py のテスト。

**照合の物差しを、照合対象の中から取らない。**

投稿した本文は ``playlistItems.list`` から組み立てた。だから同じ応答を
もう一度読んで比べても、何も確かめたことにならない（課題6で「トートロジー」
として踏んだ形）。ここでは **``videos.list``（別エンドポイント）** から
取り直した値と突き合わせる。

============================== ================================================
突き合わせる相手                 なぜそれか
============================== ================================================
Discord の ``messages/{id}``     **実際に載ったもの**。送信の応答ではない
YouTube の ``videos.list``       本文を作った ``playlistItems.list`` と**別**
``state.json``                   次回に再送しない状態になっているか
============================== ================================================
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import relay_uploads  # noqa: E402
import verify_relay  # noqa: E402


# ------------------------------------------------------------------ 材料

VIDEO_ID = "abc123"
MESSAGE_ID = "msg1"
CHANNEL = "2"
BOT_ID = "999"


def video(*, title="第3話 タイトル", channel_title="テストチャンネル"):
    """``videos.list`` が返す1件（``part=snippet``）。"""
    return {
        "id": VIDEO_ID,
        "snippet": {
            "title": title,
            "channelTitle": channel_title,
            "publishedAt": "2026-08-20T06:33:54Z",
        },
    }


def posted_body(*, title="第3話 タイトル", channel_title="テストチャンネル"):
    """実際に投稿した本文と同じ組み立て方をする。"""
    return relay_uploads.build_message(
        relay_uploads.Upload(
            video_id=VIDEO_ID,
            title=title,
            published_at=datetime.fromisoformat("2026-08-20T06:33:54+00:00"),
            channel_title=channel_title,
        )
    )


def message(*, content=None, channel_id=CHANNEL, author_id=BOT_ID, message_id=MESSAGE_ID):
    return {
        "id": message_id,
        "channel_id": channel_id,
        "content": posted_body() if content is None else content,
        "author": {"id": author_id},
    }


def record():
    return {"video_id": VIDEO_ID, "title": "第3話 タイトル", "message_id": MESSAGE_ID}


def state():
    return relay_uploads.State(
        watermark=datetime.fromisoformat("2026-08-20T06:33:54+00:00"),
        sent_ids=(VIDEO_ID,),
    )


def compare(**overrides):
    kwargs = {
        "record": record(),
        "message": message(),
        "video": video(),
        "channel": CHANNEL,
        "author_id": BOT_ID,
        "state": state(),
    }
    kwargs.update(overrides)
    return verify_relay.compare(**kwargs)


def failing_labels(checks):
    return [c.label for c in checks if not c.ok]


# ============================================================ 全部合う場合


def test_a_clean_relay_has_no_failures():
    assert failing_labels(compare()) == []


def test_every_check_is_reported_even_when_it_passes():
    """**合格したものも数える。** 何項目を見たかが分からないと、
    検査を1つ落としたことに気づけない。"""
    assert len(compare()) == verify_relay.CHECKS_PER_VIDEO


# ============================================================ 食い違いを捕まえる


def test_a_message_on_another_channel_is_caught():
    assert "チャンネル" in failing_labels(compare(message=message(channel_id="999")))


def test_a_message_from_another_author_is_caught():
    """**bot 以外が投稿したものを『送れた』と読まない。**

    同じチャンネルには人間も投稿する。ID を見ないと取り違える。
    """
    assert "投稿者" in failing_labels(compare(message=message(author_id="111")))


def test_a_different_message_id_is_caught():
    assert "メッセージID" in failing_labels(compare(message=message(message_id="other")))


def test_a_missing_video_url_is_caught():
    assert "動画URL" in failing_labels(compare(message=message(content="本文だけ")))


def test_a_title_that_does_not_match_the_other_endpoint_is_caught():
    """**本文は playlistItems、照合は videos.list。**

    ここが食い違うということは、投稿してから動画側が変わったか、
    別の動画のことを喋っているかのどちらかである。
    """
    checks = compare(video=video(title="ぜんぜん違うタイトル"))

    assert "タイトル" in failing_labels(checks)


def test_a_channel_title_mismatch_is_caught():
    assert "チャンネル名" in failing_labels(compare(video=video(channel_title="別の人")))


def test_a_publish_time_mismatch_is_caught():
    other = video()
    other["snippet"]["publishedAt"] = "2020-01-01T00:00:00Z"

    assert "公開時刻" in failing_labels(compare(video=other))


def test_a_video_missing_from_the_state_is_caught():
    """**送ったのに記録されていない＝次回もう一度送る。**"""
    empty = relay_uploads.State.empty()

    assert "状態に記録" in failing_labels(compare(state=empty))


# ============================================================ エスケープ


def test_an_escaped_title_from_the_other_endpoint_still_matches():
    """**``videos.list`` も ``&amp;`` の形で返す。**

    投稿側では unescape してから流している。照合側で戻し忘れると、
    **正しく動いているのに毎回 NG が出る**——そして「照合が厳しすぎる」
    と判断して検査を緩める方向に倒れやすい。
    """
    checks = compare(
        video=video(title="Q&amp;A 回"),
        message=message(content=posted_body(title="Q&A 回")),
    )

    assert failing_labels(checks) == []


# ============================================================ 応答の欠け


def test_a_video_that_the_other_endpoint_does_not_know_fails_loudly():
    """``videos.list`` が空を返したら、照合できないので失敗にする。

    **「確かめられなかった」を「合格」にしない。**
    """
    with pytest.raises(verify_relay.VerifyError):
        verify_relay.read_video({"items": []}, video_id=VIDEO_ID)


def test_the_other_endpoint_returning_a_different_video_fails():
    """要求した ID と違うものが返ったら止める。"""
    with pytest.raises(verify_relay.VerifyError):
        verify_relay.read_video({"items": [{"id": "someone_else"}]}, video_id=VIDEO_ID)


def test_the_video_lookup_asks_for_the_snippet_only():
    payload = {"items": [video()]}

    assert verify_relay.read_video(payload, video_id=VIDEO_ID)["id"] == VIDEO_ID


def test_an_escaped_channel_title_also_matches():
    """タイトルだけでなく**チャンネル名も**エスケープされて返る。

    片方だけ戻していると、名前に ``&`` を含むチャンネルの日にだけ NG が出る。
    """
    checks = compare(
        video=video(channel_title="A&amp;B チャンネル"),
        message=message(content=posted_body(channel_title="A&B チャンネル")),
    )

    assert failing_labels(checks) == []


def test_an_empty_expected_value_is_not_a_pass():
    """**空文字はどんな本文にも含まれる。**

    ``"" in content`` は常に True なので、相手が空を返した瞬間に
    「照合できた」ことにしてしまう。空は照合していないのと同じである。
    """
    assert "タイトル" in failing_labels(compare(video=video(title="")))


# ============================================================ 結合（main）
#
# **単体だけでは、この課題で実際にバグが出た。**
#
# compare() と read_video() は偽物を渡して固めてあったが、main() の経路は
# 1度も通していなかった。実機で初めて落ちた::
#
#     AttributeError: 'Identity' object has no attribute 'id'
#
# 属性名が違うだけの間違いで、**単体テストでは絶対に出ない**——偽物を作る側が
# 実装に合わせて `.id` を生やしてしまうため。だからここでは
# **本物の discord_auth.Identity を通す**。


class FakeVideos:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeRequest(self._payload)


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeService:
    def __init__(self, videos):
        self._videos = videos

    def videos(self):
        return self._videos


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class FakeDiscordSession:
    """``/users/@me`` と ``messages/{id}`` に答える。**本物の経路を通す。**"""

    def __init__(self):
        self.headers = {}
        self.gets = []

    def get(self, url, **kwargs):
        self.gets.append(url)
        if url.endswith("/users/@me"):
            return _FakeResponse({"id": BOT_ID, "username": "test-bot", "bot": True})
        return _FakeResponse(message())


def test_the_whole_verification_runs_end_to_end(tmp_path):
    """**本物の Identity を通す。** 属性名の取り違えはここでしか出ない。"""
    results = tmp_path / "results.json"
    results.write_text(
        '{"playlist_id": "UU", "sent": [{"video_id": "abc123", '
        '"title": "第3話 タイトル", "message_id": "msg1"}]}',
        encoding="utf-8",
    )
    state_path = tmp_path / "state.json"
    relay_uploads.save_state(state_path, state())
    said = []

    code = verify_relay.main(
        [
            "--results", str(results),
            "--state", str(state_path),
            "--channel", CHANNEL,
        ],
        env={"YOUTUBE_API_KEY": "k", "DISCORD_BOT_TOKEN": "TOKEN_VALUE"},
        service_factory=lambda api_key: _FakeService(FakeVideos({"items": [video()]})),
        session_factory=FakeDiscordSession,
        out=said.append,
    )

    assert code == 0
    assert f"照合 {verify_relay.CHECKS_PER_VIDEO} 項目 / NG 0 件" in "\n".join(said)


def test_a_mismatch_makes_the_whole_run_fail(tmp_path):
    """**NG が1件でもあれば終了コードは 0 ではない。**

    「照合した」と「合格した」を同じ扱いにしない。
    """
    results = tmp_path / "results.json"
    results.write_text(
        '{"sent": [{"video_id": "abc123", "title": "x", "message_id": "msg1"}]}',
        encoding="utf-8",
    )
    state_path = tmp_path / "state.json"
    relay_uploads.save_state(state_path, relay_uploads.State.empty())  # 記録なし
    said = []

    code = verify_relay.main(
        [
            "--results", str(results),
            "--state", str(state_path),
            "--channel", CHANNEL,
        ],
        env={"YOUTUBE_API_KEY": "k", "DISCORD_BOT_TOKEN": "TOKEN_VALUE"},
        service_factory=lambda api_key: _FakeService(FakeVideos({"items": [video()]})),
        session_factory=FakeDiscordSession,
        out=said.append,
    )

    assert code != 0
    assert "状態に記録" in "\n".join(said)


def test_an_empty_results_file_fails_instead_of_reporting_success(tmp_path):
    """**照合するものが無いのを「NG 0 件」にしない。**"""
    results = tmp_path / "results.json"
    results.write_text('{"sent": []}', encoding="utf-8")
    said = []

    code = verify_relay.main(
        [
            "--results", str(results),
            "--state", str(tmp_path / "state.json"),
            "--channel", CHANNEL,
        ],
        env={"YOUTUBE_API_KEY": "k", "DISCORD_BOT_TOKEN": "TOKEN_VALUE"},
        service_factory=lambda api_key: _FakeService(FakeVideos({"items": []})),
        session_factory=FakeDiscordSession,
        out=said.append,
    )

    assert code != 0


def test_a_message_posted_by_someone_else_is_caught_end_to_end(tmp_path):
    """**bot 以外の投稿者を、bot の投稿と取り違えない。**

    偽のメッセージの投稿者を bot と同じ ID にしていると、
    「物差しを照合対象そのものから取る」実装に変えても一致してしまう
    ——トートロジーなのに通る。**別人が投稿した形**で確かめる。
    """
    results = tmp_path / "results.json"
    results.write_text(
        '{"sent": [{"video_id": "abc123", "title": "x", "message_id": "msg1"}]}',
        encoding="utf-8",
    )
    state_path = tmp_path / "state.json"
    relay_uploads.save_state(state_path, state())
    said = []

    class SomeoneElsePosted(FakeDiscordSession):
        def get(self, url, **kwargs):
            self.gets.append(url)
            if url.endswith("/users/@me"):
                return _FakeResponse({"id": BOT_ID, "username": "test-bot", "bot": True})
            return _FakeResponse(message(author_id="111"))

    code = verify_relay.main(
        [
            "--results", str(results),
            "--state", str(state_path),
            "--channel", CHANNEL,
        ],
        env={"YOUTUBE_API_KEY": "k", "DISCORD_BOT_TOKEN": "TOKEN_VALUE"},
        service_factory=lambda api_key: _FakeService(FakeVideos({"items": [video()]})),
        session_factory=SomeoneElsePosted,
        out=said.append,
    )

    assert code != 0
    assert "投稿者" in "\n".join(said)


def test_no_absolute_path_reaches_the_screen_when_results_are_empty(tmp_path):
    """**実行画面も提出物である。** 終了コードだけでなく文面も見る。"""
    results = tmp_path / "results.json"
    results.write_text('{"sent": []}', encoding="utf-8")
    said = []

    verify_relay.main(
        [
            "--results", str(results),
            "--state", str(tmp_path / "state.json"),
            "--channel", CHANNEL,
        ],
        env={"YOUTUBE_API_KEY": "k", "DISCORD_BOT_TOKEN": "TOKEN_VALUE"},
        service_factory=lambda api_key: _FakeService(FakeVideos({"items": []})),
        session_factory=FakeDiscordSession,
        out=said.append,
    )

    screen = "\n".join(said)
    assert str(tmp_path) not in screen
    assert "results.json" in screen
