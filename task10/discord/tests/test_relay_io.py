"""relay_uploads.py の、外と話す部分のテスト。

**ネットワークには出ない。** service も session も偽物を渡す。本物を叩くと
クォータを食ううえ、落ちた理由が「実装のバグ」なのか「相手が変わった」のか
分からなくなる。実機での確認は README の「実行結果」で別に行う。

ここで固定するのは、**繰り返し動くプログラムだけが持つ失敗**である。

============================== ================================================
固定すること                     なぜ
============================== ================================================
壊れた状態ファイルで**止まる**    空として扱うと再生リスト全件が流れ直す
状態は**別名で書いてから置換**    書き込み中に落ちても壊れたファイルを残さない
遡りを打ち切ったら**報告する**    黙って切ると「全部見た」と読めてしまう
並びが崩れていても**早切りしない** 公式は再生リストの順序を保証していない
1本ごとに**記録してから次へ**     落ちたときの重複を最大1本に抑える
失敗したら**そこで止まる**        後続を送ると、失敗した1本を後から入れ直せない
``--dry-run`` は**送らない・進めない** 確認のための実行が状態を汚さない
API キーを**印字しない**          キーは URL のクエリに載る。写れば公開事故
============================== ================================================
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from googleapiclient.errors import HttpError

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import relay_uploads  # noqa: E402


# ------------------------------------------------------------------ 偽物


def at(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def upload(video_id, published_at, *, title="タイトル"):
    return relay_uploads.Upload(
        video_id=video_id,
        title=title,
        published_at=at(published_at),
        channel_title="テストチャンネル",
    )


def raw(video_id, video_published_at):
    return {
        "snippet": {
            "title": f"{video_id} のタイトル",
            "publishedAt": "2000-01-01T00:00:00Z",
            "channelTitle": "テストチャンネル",
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        },
        "contentDetails": {
            "videoId": video_id,
            "videoPublishedAt": video_published_at,
        },
    }


class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class FakePlaylistItems:
    """``playlistItems.list`` の代わり。ページを順に返す。"""

    def __init__(self, pages):
        self._pages = pages
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return FakeRequest(self._pages[len(self.calls) - 1])


class FakeChannels:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return FakeRequest(self._payload)


class FakeService:
    def __init__(self, *, channels=None, playlist_items=None):
        self._channels = channels
        self._playlist_items = playlist_items

    def channels(self):
        return self._channels

    def playlistItems(self):  # noqa: N802 — API 側の綴りに合わせる
        return self._playlist_items


class FakeHttpResponse:
    status = 403
    reason = "Forbidden"


# ============================================================ 状態の保存


def test_a_saved_state_reads_back_the_same(tmp_path):
    path = tmp_path / "state.json"
    state = relay_uploads.State(
        watermark=at("2026-08-20T06:33:54Z"), sent_ids=("a", "b")
    )

    relay_uploads.save_state(path, state)

    assert relay_uploads.load_state(path) == state


def test_a_missing_state_file_reads_as_empty(tmp_path):
    """**無いのは正常。** 初回の実行がこれ。"""
    assert relay_uploads.load_state(tmp_path / "nope.json") == relay_uploads.State.empty()


def test_a_broken_state_file_stops_instead_of_resetting(tmp_path):
    """**ここが空に化けると、再生リストの全件がもう一度流れる。**

    「読めなかった」と「まだ何も送っていない」を同じ値で表さない。
    """
    path = tmp_path / "state.json"
    path.write_text("{ これは JSON ではない", encoding="utf-8")

    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.load_state(path)


def test_a_state_file_with_the_wrong_shape_stops(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"sent_ids": "abc"}), encoding="utf-8")

    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.load_state(path)


def test_saving_leaves_no_temporary_file_behind(tmp_path):
    """別名で書いてから置き換える。**置き換え忘れると .tmp が残る。**"""
    path = tmp_path / "state.json"

    relay_uploads.save_state(path, relay_uploads.State.empty())

    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


# ============================================================ 再生リストを引く


def test_the_uploads_playlist_is_resolved():
    channels = FakeChannels(
        {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUxyz"}}}]}
    )

    got = relay_uploads.resolve_uploads_playlist(
        FakeService(channels=channels), channel_id="UCabc"
    )

    assert got == "UUxyz"


def test_resolving_asks_only_for_content_details():
    """**part を増やさない。** 要らない部分を頼むとコストが上がる。"""
    channels = FakeChannels(
        {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUxyz"}}}]}
    )

    relay_uploads.resolve_uploads_playlist(
        FakeService(channels=channels), channel_id="UCabc"
    )

    assert channels.calls[0]["part"] == "contentDetails"


def test_an_unknown_channel_fails_clearly():
    channels = FakeChannels({"items": []})

    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.resolve_uploads_playlist(
            FakeService(channels=channels), channel_id="UCabc"
        )


def test_a_channel_without_an_uploads_playlist_fails():
    channels = FakeChannels({"items": [{"contentDetails": {"relatedPlaylists": {}}}]})

    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.resolve_uploads_playlist(
            FakeService(channels=channels), channel_id="UCabc"
        )


# ============================================================ ページを遡る


def test_a_single_page_is_read():
    items = FakePlaylistItems([{"items": [raw("a", "2026-08-20T00:00:00Z")]}])

    got = relay_uploads.fetch_uploads(
        FakeService(playlist_items=items),
        playlist_id="UUxyz",
        state=relay_uploads.State.empty(),
    )

    assert [u.video_id for u in got] == ["a"]


def test_paging_follows_the_next_token():
    items = FakePlaylistItems(
        [
            {"items": [raw("a", "2026-08-20T00:00:00Z")], "nextPageToken": "p2"},
            {"items": [raw("b", "2026-08-19T00:00:00Z")]},
        ]
    )

    got = relay_uploads.fetch_uploads(
        FakeService(playlist_items=items),
        playlist_id="UUxyz",
        state=relay_uploads.State.empty(),
    )

    assert [u.video_id for u in got] == ["a", "b"]
    assert items.calls[1]["pageToken"] == "p2"


def test_paging_stops_once_a_whole_page_is_older_than_the_margin():
    """水位より**余白ぶん**古いページまで来たら、それ以上は遡らない。"""
    items = FakePlaylistItems(
        [
            {"items": [raw("old", "2026-01-01T00:00:00Z")], "nextPageToken": "p2"},
            {"items": [raw("older", "2025-01-01T00:00:00Z")]},
        ]
    )

    got = relay_uploads.fetch_uploads(
        FakeService(playlist_items=items),
        playlist_id="UUxyz",
        state=relay_uploads.State(watermark=at("2026-08-20T00:00:00Z"), sent_ids=()),
    )

    assert [u.video_id for u in got] == ["old"]
    assert len(items.calls) == 1


def test_a_scrambled_page_does_not_stop_the_paging_early():
    """**先頭1件だけを見て切らない。**

    公式は再生リストの順序を保証していない。古いものが先頭に来ていても、
    同じページに新しいものが混ざっていれば、まだ遡る意味がある。
    """
    items = FakePlaylistItems(
        [
            {
                "items": [
                    raw("old", "2026-01-01T00:00:00Z"),
                    raw("new", "2026-08-25T00:00:00Z"),
                ],
                "nextPageToken": "p2",
            },
            {"items": [raw("b", "2026-08-24T00:00:00Z")]},
        ]
    )

    got = relay_uploads.fetch_uploads(
        FakeService(playlist_items=items),
        playlist_id="UUxyz",
        state=relay_uploads.State(watermark=at("2026-08-20T00:00:00Z"), sent_ids=()),
    )

    assert [u.video_id for u in got] == ["old", "new", "b"]


def test_hitting_the_page_limit_is_reported():
    """**黙って打ち切らない。** 切ったことが伝わらないと「全部見た」と読める。"""
    pages = [
        {"items": [raw(f"v{i}", "2026-08-20T00:00:00Z")], "nextPageToken": f"p{i}"}
        for i in range(3)
    ]
    notes = []

    relay_uploads.fetch_uploads(
        FakeService(playlist_items=FakePlaylistItems(pages)),
        playlist_id="UUxyz",
        state=relay_uploads.State.empty(),
        max_pages=3,
        on_note=notes.append,
    )

    assert len(notes) == 1


def test_finishing_naturally_reports_nothing():
    notes = []

    relay_uploads.fetch_uploads(
        FakeService(playlist_items=FakePlaylistItems([{"items": []}])),
        playlist_id="UUxyz",
        state=relay_uploads.State.empty(),
        max_pages=3,
        on_note=notes.append,
    )

    assert notes == []


# ============================================================ API キーを伏せる


def test_the_api_key_is_hidden_when_the_channel_lookup_fails():
    """**キーは URL のクエリに載る。** 例外をそのまま印字すると画面に出る。"""

    class Exploding:
        def list(self, **kwargs):
            raise AssertionError("execute で落とす")

    class ExplodingRequest:
        def execute(self):
            raise HttpError(
                FakeHttpResponse(),
                b'{"error": {"message": "boom"}}',
                uri="https://www.googleapis.com/youtube/v3/channels?key=SECRET_KEY",
            )

    channels = Exploding()
    channels.list = lambda **kwargs: ExplodingRequest()

    with pytest.raises(relay_uploads.RelayError) as caught:
        relay_uploads.resolve_uploads_playlist(
            FakeService(channels=channels), channel_id="UCabc", api_key="SECRET_KEY"
        )

    assert "SECRET_KEY" not in str(caught.value)
    assert youtube_redacted_marker() in str(caught.value)


def youtube_redacted_marker():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from common import youtube_auth

    return youtube_auth.REDACTED


# ============================================================ 1本ずつ流す


def test_each_send_is_recorded_before_the_next_one():
    """**送る → 記録する → 次を送る**、の順であること。

    まとめて最後に記録すると、落ちたときの重複が最大で全件になる。
    """
    order = []

    def send(item):
        order.append(f"send:{item.video_id}")
        return relay_uploads.Relayed(item.video_id, item.title, "m", "link")

    def persist(state):
        order.append(f"save:{state.sent_ids[-1]}")

    relay_uploads.relay(
        [upload("a", "2026-08-20T00:00:00Z"), upload("b", "2026-08-21T00:00:00Z")],
        state=relay_uploads.State.empty(),
        send=send,
        persist=persist,
    )

    assert order == ["send:a", "save:a", "send:b", "save:b"]


def test_a_failed_send_stops_the_rest():
    """後続を送ると、失敗した1本だけを後から入れ直す手段が無くなる。"""
    sent = []

    def send(item):
        if item.video_id == "b":
            raise relay_uploads.RelayError("送信に失敗")
        sent.append(item.video_id)
        return relay_uploads.Relayed(item.video_id, item.title, "m", "link")

    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.relay(
            [
                upload("a", "2026-08-20T00:00:00Z"),
                upload("b", "2026-08-21T00:00:00Z"),
                upload("c", "2026-08-22T00:00:00Z"),
            ],
            state=relay_uploads.State.empty(),
            send=send,
            persist=lambda state: None,
        )

    assert sent == ["a"]


def test_the_successful_ones_stay_recorded_after_a_failure():
    """途中で落ちても、送れた分は記録に残る。次回に再送しない。"""
    saved = []

    def send(item):
        if item.video_id == "b":
            raise relay_uploads.RelayError("送信に失敗")
        return relay_uploads.Relayed(item.video_id, item.title, "m", "link")

    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.relay(
            [upload("a", "2026-08-20T00:00:00Z"), upload("b", "2026-08-21T00:00:00Z")],
            state=relay_uploads.State.empty(),
            send=send,
            persist=saved.append,
        )

    assert saved[-1].sent_ids == ("a",)


# ============================================================ CLI


def test_dry_run_does_not_build_a_sender(tmp_path):
    """**確認のための実行が Discord の資格情報を要求しない。**

    課題5の ``--dry-run`` は「認証もしない」だった。ここは YouTube だけ読む。
    何が届くのかを先に見られないと、確認の意味がないため。
    """
    built = []

    code = relay_uploads.main(
        [
            "--channel-id", "UCabc",
            "--guild", "1", "--channel", "2",
            "--state", str(tmp_path / "state.json"),
            "--dry-run",
        ],
        env={"YOUTUBE_API_KEY": "k"},
        service_factory=lambda api_key: FakeService(
            channels=FakeChannels(
                {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU"}}}]}
            ),
            playlist_items=FakePlaylistItems(
                [{"items": [raw("a", "2026-08-20T00:00:00Z")]}]
            ),
        ),
        sender_factory=lambda **kwargs: built.append(kwargs),
        out=lambda text: None,
    )

    assert code == 0
    assert built == []


def test_dry_run_does_not_write_the_state(tmp_path):
    path = tmp_path / "state.json"

    relay_uploads.main(
        [
            "--channel-id", "UCabc",
            "--guild", "1", "--channel", "2",
            "--state", str(path),
            "--dry-run",
        ],
        env={"YOUTUBE_API_KEY": "k"},
        service_factory=lambda api_key: FakeService(
            channels=FakeChannels(
                {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU"}}}]}
            ),
            playlist_items=FakePlaylistItems(
                [{"items": [raw("a", "2026-08-20T00:00:00Z")]}]
            ),
        ),
        sender_factory=lambda **kwargs: None,
        out=lambda text: None,
    )

    assert not path.exists()


def test_nothing_new_sends_nothing_and_succeeds(tmp_path):
    """**0件は正常。** 記事A（予定通知）は0件でも送るが、ここは送らない。

    予定が来ないのは異常、新着が来ないのは普通。同じ人が逆の判断をしている。
    """
    path = tmp_path / "state.json"
    relay_uploads.save_state(
        path,
        relay_uploads.State(watermark=at("2026-08-20T00:00:00Z"), sent_ids=("a",)),
    )
    sent = []

    code = relay_uploads.main(
        [
            "--channel-id", "UCabc",
            "--guild", "1", "--channel", "2",
            "--state", str(path),
        ],
        env={"YOUTUBE_API_KEY": "k"},
        service_factory=lambda api_key: FakeService(
            channels=FakeChannels(
                {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU"}}}]}
            ),
            playlist_items=FakePlaylistItems(
                [{"items": [raw("a", "2026-08-20T00:00:00Z")]}]
            ),
        ),
        sender_factory=lambda **kwargs: sent.append,
        out=lambda text: None,
    )

    assert code == 0
    assert sent == []


def test_new_uploads_are_sent_oldest_first(tmp_path):
    path = tmp_path / "state.json"
    sent = []

    def sender_factory(**kwargs):
        def send(item):
            sent.append(item.video_id)
            return relay_uploads.Relayed(item.video_id, item.title, "m", "link")

        return send

    code = relay_uploads.main(
        [
            "--channel-id", "UCabc",
            "--guild", "1", "--channel", "2",
            "--state", str(path),
        ],
        env={"YOUTUBE_API_KEY": "k"},
        service_factory=lambda api_key: FakeService(
            channels=FakeChannels(
                {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU"}}}]}
            ),
            playlist_items=FakePlaylistItems(
                [
                    {
                        "items": [
                            raw("new", "2026-08-22T00:00:00Z"),
                            raw("old", "2026-08-21T00:00:00Z"),
                        ]
                    }
                ]
            ),
        ),
        sender_factory=sender_factory,
        out=lambda text: None,
    )

    assert code == 0
    assert sent == ["old", "new"]


def test_the_state_is_written_after_a_real_run(tmp_path):
    path = tmp_path / "state.json"

    relay_uploads.main(
        [
            "--channel-id", "UCabc",
            "--guild", "1", "--channel", "2",
            "--state", str(path),
        ],
        env={"YOUTUBE_API_KEY": "k"},
        service_factory=lambda api_key: FakeService(
            channels=FakeChannels(
                {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU"}}}]}
            ),
            playlist_items=FakePlaylistItems(
                [{"items": [raw("a", "2026-08-20T00:00:00Z")]}]
            ),
        ),
        sender_factory=lambda **kwargs: (
            lambda item: relay_uploads.Relayed(item.video_id, item.title, "m", "link")
        ),
        out=lambda text: None,
    )

    assert relay_uploads.load_state(path).sent_ids == ("a",)


def test_a_missing_api_key_fails_before_touching_discord(tmp_path):
    """落ちると分かっている実行で、相手に接続しない。"""
    built = []

    code = relay_uploads.main(
        [
            "--channel-id", "UCabc",
            "--guild", "1", "--channel", "2",
            "--state", str(tmp_path / "state.json"),
        ],
        env={},
        service_factory=lambda api_key: FakeService(),
        sender_factory=lambda **kwargs: built.append(kwargs),
        out=lambda text: None,
    )

    assert code != 0
    assert built == []


# ============================================================ 初回の氾濫を防ぐ


def fake_service_with(*video_ids, published="2026-08-20T00:00:00Z"):
    return FakeService(
        channels=FakeChannels(
            {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU"}}}]}
        ),
        playlist_items=FakePlaylistItems(
            [{"items": [raw(v, published) for v in video_ids]}]
        ),
    )


def collecting_sender(sent):
    def sender_factory(**kwargs):
        def send(item):
            sent.append(item.video_id)
            return relay_uploads.Relayed(item.video_id, item.title, "m", "link")

        return send

    return sender_factory


def run(tmp_path, *extra, sent=None, service=None, out=None, env=None):
    return relay_uploads.main(
        [
            "--channel-id", "UCabc",
            "--guild", "1", "--channel", "2",
            "--state", str(tmp_path / "state.json"),
            *extra,
        ],
        env={"YOUTUBE_API_KEY": "k"} if env is None else env,
        service_factory=lambda api_key: service or fake_service_with("a"),
        sender_factory=collecting_sender(sent if sent is not None else []),
        out=out or (lambda text: None),
    )


def test_the_first_run_would_otherwise_flood_the_channel(tmp_path):
    """**状態が空だと、取れた全件が「新着」になる。**

    再生リストに100本入っていれば100通が流れる。既定の上限で止める。
    """
    sent = []

    run(tmp_path, sent=sent, service=fake_service_with(*[f"v{i}" for i in range(30)]))

    assert len(sent) == relay_uploads.DEFAULT_LIMIT


def test_hitting_the_limit_is_reported(tmp_path):
    """**黙って切らない。** 残りがあることが伝わらないと「全部流した」と読める。"""
    notes = []

    run(
        tmp_path,
        service=fake_service_with(*[f"v{i}" for i in range(30)]),
        out=notes.append,
    )

    assert any("残り" in note for note in notes)


def test_the_unsent_ones_are_not_recorded(tmp_path):
    """上限で送らなかったぶんは覚えない。次の実行で届く。"""
    run(tmp_path, service=fake_service_with(*[f"v{i}" for i in range(30)]))

    recorded = relay_uploads.load_state(tmp_path / "state.json").sent_ids

    assert len(recorded) == relay_uploads.DEFAULT_LIMIT


def test_init_records_everything_without_sending(tmp_path):
    """**初回だけは「送らずに覚える」。**

    既にある動画は新着ではない。流したいのは、これから増えるぶんである。
    """
    sent = []

    code = run(
        tmp_path, "--init", sent=sent, service=fake_service_with("a", "b", "c")
    )

    assert code == 0
    assert sent == []
    assert set(relay_uploads.load_state(tmp_path / "state.json").sent_ids) == {
        "a", "b", "c"
    }


def test_init_refuses_when_a_state_file_already_exists(tmp_path):
    """**2回目に誤って叩くと、未送信ぶんを黙って捨てることになる。**"""
    relay_uploads.save_state(tmp_path / "state.json", relay_uploads.State.empty())
    sent = []

    code = run(tmp_path, "--init", sent=sent, service=fake_service_with("a"))

    assert code != 0
    assert sent == []


# ============================================================ 余白と重複


def test_paging_continues_while_inside_the_margin(tmp_path):
    """**水位ちょうどで切らない。**

    公開時刻が後から変わる動画がある。水位で切ると、その隣にいたものを
    二度と見なくなる。余白（既定1日）のぶんは遡り続ける。
    """
    items = FakePlaylistItems(
        [
            {"items": [raw("just_below", "2026-08-19T12:00:00Z")], "nextPageToken": "p2"},
            {"items": [raw("b", "2026-08-19T06:00:00Z")]},
        ]
    )

    got = relay_uploads.fetch_uploads(
        FakeService(playlist_items=items),
        playlist_id="UUxyz",
        state=relay_uploads.State(watermark=at("2026-08-20T00:00:00Z"), sent_ids=()),
    )

    assert [u.video_id for u in got] == ["just_below", "b"]


def test_a_video_appearing_on_two_pages_is_sent_once():
    """同じ動画が複数ページに跨って現れることがある。**畳んでから送る。**"""
    picked = relay_uploads.select_new(
        [
            upload("a", "2026-08-20T00:00:00Z"),
            upload("a", "2026-08-20T00:00:00Z"),
            upload("b", "2026-08-21T00:00:00Z"),
        ],
        relay_uploads.State.empty(),
    )

    assert [u.video_id for u in picked] == ["a", "b"]


def test_the_page_size_is_passed_through():
    """**上限50を勝手に小さくしない。** 1ページ 1 unit なので、細かく刻むほど高い。"""
    items = FakePlaylistItems([{"items": []}])

    relay_uploads.fetch_uploads(
        FakeService(playlist_items=items),
        playlist_id="UUxyz",
        state=relay_uploads.State.empty(),
        page_size=50,
    )

    assert items.calls[0]["maxResults"] == 50


def test_both_parts_are_requested():
    """``contentDetails`` を落とすと videoPublishedAt が取れなくなる。"""
    items = FakePlaylistItems([{"items": []}])

    relay_uploads.fetch_uploads(
        FakeService(playlist_items=items),
        playlist_id="UUxyz",
        state=relay_uploads.State.empty(),
    )

    assert items.calls[0]["part"] == "snippet,contentDetails"


def test_relay_returns_what_it_sent():
    """呼んだ側が「何が届いたか」を報告できるように、結果を返す。"""
    done, _state = relay_uploads.relay(
        [upload("a", "2026-08-20T00:00:00Z", title="第1話")],
        state=relay_uploads.State.empty(),
        send=lambda item: relay_uploads.Relayed(
            item.video_id, item.title, "msg1", "https://discord.com/x"
        ),
        persist=lambda state: None,
    )

    assert [r.video_id for r in done] == ["a"]
    assert done[0].message_id == "msg1"


# ============================================================ 実際に送る部分


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append({"url": url, **kwargs})
        return FakeResponse({"id": "msg1", "channel_id": "2"})


def sender_env():
    return {"DISCORD_BOT_TOKEN": "TOKEN_VALUE"}


def test_the_sender_posts_the_built_message():
    session = FakeSession()

    send = relay_uploads.build_sender(
        guild="1", channel="2", env=sender_env(), session_factory=lambda: session
    )
    send(upload("abc123", "2026-08-20T00:00:00Z", title="第3話"))

    body = session.posts[0]["json"]["content"]
    assert "第3話" in body
    assert "https://www.youtube.com/watch?v=abc123" in body


def test_the_sender_suppresses_mentions():
    """**動画のタイトルに ``@everyone`` が入っていても鳴らさない。**

    タイトルは他人が付けた文字列である。そのまま流すと、投稿者が
    サーバー全員に通知を飛ばせてしまう。
    """
    session = FakeSession()

    send = relay_uploads.build_sender(
        guild="1", channel="2", env=sender_env(), session_factory=lambda: session
    )
    send(upload("abc123", "2026-08-20T00:00:00Z", title="@everyone 見て"))

    assert session.posts[0]["json"]["allowed_mentions"] == {"parse": []}


def test_the_sender_carries_the_bot_token_in_a_header():
    """**トークンは URL に載せない。** 課題6の API キーとはここが違う。"""
    session = FakeSession()

    relay_uploads.build_sender(
        guild="1", channel="2", env=sender_env(), session_factory=lambda: session
    )

    assert session.headers["Authorization"] == "Bot TOKEN_VALUE"


def test_the_sender_returns_a_link_to_the_message():
    session = FakeSession()

    send = relay_uploads.build_sender(
        guild="1", channel="2", env=sender_env(), session_factory=lambda: session
    )
    result = send(upload("abc123", "2026-08-20T00:00:00Z"))

    assert result.message_id == "msg1"
    assert result.link == "https://discord.com/channels/1/2/msg1"


def test_the_sender_needs_a_token():
    with pytest.raises(relay_uploads.discord_auth.DiscordError):
        relay_uploads.build_sender(
            guild="1", channel="2", env={}, session_factory=FakeSession
        )


def test_the_default_page_size_is_the_api_maximum():
    """**1ページ 1 unit。** 細かく刻むほど呼ぶ回数が増えて高くつく。"""
    items = FakePlaylistItems([{"items": []}])

    relay_uploads.fetch_uploads(
        FakeService(playlist_items=items),
        playlist_id="UUxyz",
        state=relay_uploads.State.empty(),
    )

    assert items.calls[0]["maxResults"] == 50


def test_the_api_key_never_reaches_the_final_error_message(tmp_path):
    """**main のエラー経路を1件も通していなかった。**

    ここは最後の網である。個々の呼び出しで伏せていても、想定していない
    経路の例外がそのまま画面に出れば、実行画面のスクリーンショットに
    キーが写る——public リポジトリに置くので、写った時点で公開事故になる。
    """
    said = []

    def exploding(api_key):
        raise relay_uploads.RelayError(f"接続に失敗しました key={api_key}")

    code = relay_uploads.main(
        [
            "--channel-id", "UCabc",
            "--guild", "1", "--channel", "2",
            "--state", str(tmp_path / "state.json"),
        ],
        env={"YOUTUBE_API_KEY": "SECRET_KEY_VALUE"},
        service_factory=exploding,
        sender_factory=lambda **kwargs: None,
        out=said.append,
    )

    assert code != 0
    assert "SECRET_KEY_VALUE" not in "\n".join(said)


# ============================================================ 再生リストを直に指す


def test_a_playlist_id_skips_the_channel_lookup(tmp_path):
    """**再生リストを直に渡せる。**

    ななが実際に見ているのはチャンネルのアップロードではなく、
    キュレーションされた再生リストである。``channels.list`` を1回節約もできる
    （``channels`` を渡していないので、引きに行けば落ちる）。
    """
    sent = []

    code = relay_uploads.main(
        [
            "--playlist-id", "PLxyz",
            "--guild", "1", "--channel", "2",
            "--state", str(tmp_path / "state.json"),
        ],
        env={"YOUTUBE_API_KEY": "k"},
        service_factory=lambda api_key: FakeService(
            playlist_items=FakePlaylistItems(
                [{"items": [raw("a", "2026-08-20T00:00:00Z")]}]
            )
        ),
        sender_factory=collecting_sender(sent),
        out=lambda text: None,
    )

    assert code == 0
    assert sent == ["a"]


def test_one_of_channel_or_playlist_is_required(tmp_path):
    """どちらも無いと、何を見ればよいか決まらない。"""
    code = relay_uploads.main(
        [
            "--guild", "1", "--channel", "2",
            "--state", str(tmp_path / "state.json"),
        ],
        env={"YOUTUBE_API_KEY": "k"},
        service_factory=lambda api_key: FakeService(),
        sender_factory=lambda **kwargs: None,
        out=lambda text: None,
    )

    assert code != 0


def test_new_by_added_reaches_the_selection(tmp_path):
    """``--new-by added`` で、公開が古くても**今日追加されたもの**が届く。

    水位（2026-08-20）より公開が古い動画。公開時刻で見ていると落ちる。
    """
    path = tmp_path / "state.json"
    relay_uploads.save_state(
        path,
        relay_uploads.State(watermark=at("2026-08-20T00:00:00Z"), sent_ids=()),
    )
    old_but_added_today = raw("v", "2020-01-01T00:00:00Z")
    old_but_added_today["snippet"]["publishedAt"] = "2026-08-22T00:00:00Z"
    sent = []

    code = relay_uploads.main(
        [
            "--playlist-id", "PLxyz",
            "--guild", "1", "--channel", "2",
            "--state", str(path),
            "--new-by", "added",
        ],
        env={"YOUTUBE_API_KEY": "k"},
        service_factory=lambda api_key: FakeService(
            playlist_items=FakePlaylistItems([{"items": [old_but_added_today]}])
        ),
        sender_factory=collecting_sender(sent),
        out=lambda text: None,
    )

    assert code == 0
    assert sent == ["v"]


def test_the_same_video_is_skipped_by_publish_time(tmp_path):
    """**同じ入力で、基準を変えると結果が変わることを見せる。**

    片方だけ試すと「動いた」で終わってしまい、選択に意味があるか分からない。

    既定（``published``）では、この動画の公開は 2020 年＝水位のはるか下なので
    送らない。**「古い動画が今日リストに入った」を新着と呼びたいなら
    ``--new-by added`` を選ぶ**、というのがこの2件の対比である。
    """
    path = tmp_path / "state.json"
    relay_uploads.save_state(
        path,
        relay_uploads.State(watermark=at("2026-08-20T00:00:00Z"), sent_ids=()),
    )
    old_but_added_today = raw("v", "2020-01-01T00:00:00Z")
    old_but_added_today["snippet"]["publishedAt"] = "2026-08-22T00:00:00Z"
    sent = []

    relay_uploads.main(
        [
            "--playlist-id", "PLxyz",
            "--guild", "1", "--channel", "2",
            "--state", str(path),
        ],
        env={"YOUTUBE_API_KEY": "k"},
        service_factory=lambda api_key: FakeService(
            playlist_items=FakePlaylistItems([{"items": [old_but_added_today]}])
        ),
        sender_factory=collecting_sender(sent),
        out=lambda text: None,
    )

    assert sent == []


def test_paging_uses_the_chosen_field_for_the_floor():
    """遡る範囲も、選んだ基準で測る。

    公開時刻は 2020 年だが、追加は水位より新しい。``added`` で見ているなら
    このページで打ち切ってはいけない。
    """
    old_but_added_today = raw("v", "2020-01-01T00:00:00Z")
    old_but_added_today["snippet"]["publishedAt"] = "2026-08-22T00:00:00Z"
    items = FakePlaylistItems(
        [
            {"items": [old_but_added_today], "nextPageToken": "p2"},
            {"items": [raw("b", "2026-08-21T00:00:00Z")]},
        ]
    )

    got = relay_uploads.fetch_uploads(
        FakeService(playlist_items=items),
        playlist_id="PLxyz",
        state=relay_uploads.State(watermark=at("2026-08-20T00:00:00Z"), sent_ids=()),
        key=relay_uploads.NEW_BY_ADDED,
    )

    assert [u.video_id for u in got] == ["v", "b"]


def test_giving_both_a_channel_and_a_playlist_is_refused(tmp_path):
    """**どちらか一方。** 両方渡されたら、どちらを見ればよいか決まらない。

    「片方も渡さない」だけを試していたので、この分岐は素通りしていた——
    渡さない場合は後段のチャンネル照会が空で落ちて、同じ終了コードになる。
    """
    code = relay_uploads.main(
        [
            "--channel-id", "UCabc",
            "--playlist-id", "PLxyz",
            "--guild", "1", "--channel", "2",
            "--state", str(tmp_path / "state.json"),
        ],
        env={"YOUTUBE_API_KEY": "k"},
        service_factory=lambda api_key: fake_service_with("a"),
        sender_factory=lambda **kwargs: None,
        out=lambda text: None,
    )

    assert code != 0


# ============================================================ ハンドルで引く
#
# **人が持っているのは `@ハンドル`、API が要るのは `UC` で始まるID。**
# 変換を利用者に押し付けると、そこで詰まる。公式には forHandle がある::
#
#     forHandle — specifies a YouTube handle, thereby requesting the channel
#                 associated with that handle
#
# ただし**フィルタは「ちょうど1つ」**と明記されている::
#
#     Filters (specify exactly one of the following parameters)


def test_a_handle_resolves_the_uploads_playlist():
    channels = FakeChannels(
        {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUxyz"}}}]}
    )

    got = relay_uploads.resolve_uploads_playlist(
        FakeService(channels=channels), handle="@GoogleDevelopers"
    )

    assert got == "UUxyz"
    assert channels.calls[0]["forHandle"] == "@GoogleDevelopers"


def test_a_handle_without_the_at_sign_is_normalised():
    """**人は `@` を付けたり付けなかったりする。** 呼ぶ前に揃える。"""
    channels = FakeChannels(
        {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUxyz"}}}]}
    )

    relay_uploads.resolve_uploads_playlist(
        FakeService(channels=channels), handle="GoogleDevelopers"
    )

    assert channels.calls[0]["forHandle"] == "@GoogleDevelopers"


def test_a_handle_lookup_does_not_also_send_an_id():
    """**フィルタは「ちょうど1つ」。** 両方載せると API が弾く。"""
    channels = FakeChannels(
        {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUxyz"}}}]}
    )

    relay_uploads.resolve_uploads_playlist(
        FakeService(channels=channels), handle="@GoogleDevelopers"
    )

    assert "id" not in channels.calls[0]


def test_an_id_lookup_does_not_also_send_a_handle():
    channels = FakeChannels(
        {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUxyz"}}}]}
    )

    relay_uploads.resolve_uploads_playlist(
        FakeService(channels=channels), channel_id="UCabc"
    )

    assert "forHandle" not in channels.calls[0]


def test_giving_both_an_id_and_a_handle_is_refused():
    """**引ける相手を渡す。**

    偽物が空を返すようにしていると、排他の検査を消しても
    「チャンネルが見つかりません」で落ちて**別の理由でテストが通る**
    （わざと壊す検査で素通りして発覚した）。ここで落ちる理由は
    排他の検査ひとつだけにする。
    """
    channels = FakeChannels(
        {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUxyz"}}}]}
    )

    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.resolve_uploads_playlist(
            FakeService(channels=channels),
            channel_id="UCabc",
            handle="@GoogleDevelopers",
        )

    # 止めたのなら、API は1回も叩いていないはず。
    assert channels.calls == []


def test_giving_neither_is_refused_before_calling_the_api():
    channels = FakeChannels(
        {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UUxyz"}}}]}
    )

    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.resolve_uploads_playlist(FakeService(channels=channels))

    assert channels.calls == []


def test_giving_neither_an_id_nor_a_handle_is_refused():
    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.resolve_uploads_playlist(
            FakeService(channels=FakeChannels({"items": []}))
        )


def test_an_unknown_handle_fails_clearly():
    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.resolve_uploads_playlist(
            FakeService(channels=FakeChannels({"items": []})), handle="@nobody"
        )


def test_the_cli_accepts_a_handle(tmp_path):
    sent = []

    code = relay_uploads.main(
        [
            "--handle", "@GoogleDevelopers",
            "--guild", "1", "--channel", "2",
            "--state", str(tmp_path / "state.json"),
        ],
        env={"YOUTUBE_API_KEY": "k"},
        service_factory=lambda api_key: fake_service_with("a"),
        sender_factory=collecting_sender(sent),
        out=lambda text: None,
    )

    assert code == 0
    assert sent == ["a"]


def test_the_cli_refuses_two_targets(tmp_path):
    """`--handle` と `--playlist-id` はどちらも「見る先」なので排他。"""
    code = relay_uploads.main(
        [
            "--handle", "@GoogleDevelopers",
            "--playlist-id", "PLxyz",
            "--guild", "1", "--channel", "2",
            "--state", str(tmp_path / "state.json"),
        ],
        env={"YOUTUBE_API_KEY": "k"},
        service_factory=lambda api_key: fake_service_with("a"),
        sender_factory=lambda **kwargs: None,
        out=lambda text: None,
    )

    assert code != 0


# ============================================================ 送る前に見せる


def titled(video_id, published_at, title):
    """**タイトルに videoId を含まない検体。**

    ``raw()`` の既定タイトルは ``"<videoId> のタイトル"`` なので、
    「本文に videoId が出るか」を見ると**実装が何もしなくても部分一致で通る**。
    課題10（LINE）で踏んだ「部分一致が意図しない場所で満たされていた」と同じ形。
    """
    item = raw(video_id, published_at)
    item["snippet"]["title"] = title
    return item


def service_listing(*items):
    """`run()` は `--channel-id` を渡すので、チャンネル照会も答えられる偽物にする。"""
    return FakeService(
        channels=FakeChannels(
            {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU"}}}]}
        ),
        playlist_items=FakePlaylistItems([{"items": list(items)}]),
    )


def test_the_listing_shows_the_video_id(tmp_path):
    """**タイトルは同一性ではない。**

    実機の1回目で、同じタイトルの動画が1分違いで2本並んだ::

        2026-01-15 00:00 UTC  Aniket's Story: AI & I
        2026-01-15 00:01 UTC  Aniket's Story: AI & I

    タイトルと時刻だけでは、送る前の確認で**どれがどれか区別できない**。
    videoId を正本にしている判断が、画面にも出ていないと意味がない。
    """
    said = []

    run(
        tmp_path,
        "--dry-run",
        service=service_listing(titled("abc123", "2026-01-15T00:00:00Z", "ぜんぜん違う題")),
        out=said.append,
    )

    assert any("abc123" in line for line in said)


def test_two_videos_with_the_same_title_and_time_are_told_apart(tmp_path):
    """**同時刻・同タイトルにする。**

    実機で並んだのは1分違いだったので、時刻だけでも見分けられてしまう。
    それだと videoId を出さなくても通る。**ID だけが頼りの状態**で確かめる。
    """
    said = []

    run(
        tmp_path,
        "--dry-run",
        service=service_listing(
            titled("first", "2026-01-15T00:00:00Z", "Aniket's Story"),
            titled("second", "2026-01-15T00:00:00Z", "Aniket's Story"),
        ),
        out=said.append,
    )

    listed = [line for line in said if "Aniket" in line]
    assert len(listed) == 2
    assert listed[0] != listed[1]


def test_the_listing_time_follows_the_chosen_basis(tmp_path):
    """**画面に出す時刻も、選んだ基準に従う。**

    公開時刻で選んでいないのに公開時刻を表示すると、
    「なぜこれが新着なのか」が読む人に分からなくなる——
    2020年の日付が並んだ画面を見て「今日の新着」と読める人はいない。
    """
    path = tmp_path / "state.json"
    old_but_added_today = raw("v", "2020-01-01T00:00:00Z")
    old_but_added_today["snippet"]["publishedAt"] = "2026-08-22T09:00:00Z"
    said = []

    relay_uploads.main(
        [
            "--playlist-id", "PLxyz",
            "--guild", "1", "--channel", "2",
            "--state", str(path),
            "--new-by", "added",
            "--dry-run",
        ],
        env={"YOUTUBE_API_KEY": "k"},
        service_factory=lambda api_key: FakeService(
            playlist_items=FakePlaylistItems([{"items": [old_but_added_today]}])
        ),
        sender_factory=lambda **kwargs: None,
        out=said.append,
    )

    listed = [line for line in said if "[v]" in line]
    assert len(listed) == 1
    assert "2026-08-22" in listed[0]
    assert "2020-01-01" not in listed[0]


# ============================================================ 画面に出すパス
#
# **実行画面も提出物である。**
#
# check_docs は README とソースの自宅パスを検査していたが、**実行時の出力は
# 検査していなかった**。実機で `--init` の拒否メッセージに
# `C:\Users\<名前>\...` がそのまま出た——この画面はスクリーンショットになって
# public リポジトリに載る。


def test_a_path_inside_the_repository_is_shown_relative():
    inside = Path(relay_uploads._REPO_ROOT) / "task10" / "discord" / "state.json"

    shown = relay_uploads.shown_path(inside)

    assert shown == "task10/discord/state.json"


def test_a_path_outside_the_repository_shows_only_its_name(tmp_path):
    """**外にあるものは名前だけ。**

    リポジトリの外を指されたときに「相対にできないので絶対パス」に倒すと、
    いちばん出したくない場合にいちばん長いものが出る。
    """
    shown = relay_uploads.shown_path(tmp_path / "somewhere" / "state.json")

    assert shown == "state.json"


def test_no_home_path_reaches_the_screen_when_init_is_refused(tmp_path):
    """`--init` の拒否は、実機で実際に自宅パスを出していた経路。"""
    relay_uploads.save_state(tmp_path / "state.json", relay_uploads.State.empty())
    said = []

    code = run(tmp_path, "--init", out=said.append)
    screen = "\n".join(said)

    assert code != 0
    # **絶対パスそのものを物差しにする。** 特定の綴り（"C:" など）を探すと、
    # 別の場所に置いた瞬間に検査が素通りする。
    assert str(tmp_path) not in screen
    assert "state.json" in screen


def test_no_home_path_reaches_the_screen_when_the_state_is_broken(tmp_path):
    said = []
    (tmp_path / "state.json").write_text("{ 壊れている", encoding="utf-8")

    code = run(tmp_path, out=said.append)

    assert code != 0
    assert str(tmp_path) not in "\n".join(said)
