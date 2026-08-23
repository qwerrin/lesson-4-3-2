"""task10/discord/relay_uploads.py のテスト。

課題10（連携した API に機能を追加）の Discord 側。課題6の YouTube と
課題8の Discord をつないで、**新着アップロードをチャンネルへ流す**。

このファイルが守っているのは、ほとんどが「**エラーを出さずに間違える**」形の
失敗である。どれも例外にならないので、テストで固定しないと気づけない。

============================== ================================================
静かに間違える点                 固定するテスト
============================== ================================================
時刻の取り違え                   ``snippet.publishedAt`` は「**再生リストに
                                 追加された**時刻」。``contentDetails.
                                 videoPublishedAt`` が「YouTube に公開された
                                 時刻」（公式の文言）。取り違えても API は
                                 200 を返す
並び順への依存                   公式は uploads 再生リストの**順序を保証して
                                 いない**。「N番目＝N番目に新しい」は成り立たない
HTML エンティティ                 タイトルは ``&amp;`` の形で返る。そのまま
                                 流すと人間が読む画面に ``&amp;`` が出る
同時刻の複数動画                 水位（時刻）だけで判定すると ``>`` は取りこぼし・
                                 ``>=`` は重複。**ID 集合を正本にする**
送信と記録のあいだの窓            プロセスが死ぬ位置で重複か取りこぼしが決まる。
                                 **重複側に倒す**と決めたことを固定する
============================== ================================================
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import relay_uploads  # noqa: E402


# ------------------------------------------------------------------ 材料


def playlist_item(
    *,
    video_id="vid_0000001",
    title="第3話 タイトル",
    video_published_at="2026-08-20T06:33:54Z",
    snippet_published_at="2026-08-22T01:00:00Z",
    channel_title="テストチャンネル",
    with_content_details=True,
    with_video_published_at=True,
):
    """``playlistItems.list`` が返す1件ぶんの形。

    **``snippet.publishedAt`` の既定値を、わざと videoPublishedAt と違う値に
    してある。** 同じ値にすると、取り違えたままでもテストが通ってしまう。

    ``with_video_published_at=False`` は ``contentDetails`` を残したまま
    公開時刻だけを落とす。**``contentDetails`` ごと消すと videoId の検査が
    先に拾ってしまい、狙った分岐に当たらない**（わざと壊す検査で発覚した）。
    """
    item = {
        "snippet": {
            "title": title,
            "publishedAt": snippet_published_at,
            "channelTitle": channel_title,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
    }
    if with_content_details:
        item["contentDetails"] = {"videoId": video_id}
        if with_video_published_at:
            item["contentDetails"]["videoPublishedAt"] = video_published_at
    return item


def at(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def upload(video_id, published_at, *, title="タイトル"):
    return relay_uploads.Upload(
        video_id=video_id,
        title=title,
        published_at=at(published_at),
        channel_title="テストチャンネル",
    )


# ============================================================ 1件を読む


def test_the_video_publish_time_is_used_not_the_playlist_add_time():
    """**この課題でいちばん静かな間違い。**

    公式の文言はこう分かれている（2026-08-23 に確認）::

        snippet.publishedAt          … the item was **added to the playlist**
        contentDetails.videoPublishedAt … the video was **published to YouTube**

    取り違えても API はエラーを返さない。順番だけが変わる。
    """
    item = playlist_item(
        video_published_at="2026-08-20T06:33:54Z",
        snippet_published_at="2026-08-22T01:00:00Z",
    )

    assert relay_uploads.parse_upload(item).published_at == at("2026-08-20T06:33:54Z")


def test_a_missing_video_publish_time_fails_instead_of_falling_back():
    """**代わりに snippet.publishedAt を使わない。**

    「取れなかったので別の値で埋めた」は、ここでは順序の破壊になる。
    埋めれば動いてしまうぶん、落とすほうが安全である。
    """
    item = playlist_item(with_video_published_at=False)

    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.parse_upload(item)


def test_the_title_is_unescaped():
    """YouTube は ``&`` を ``&amp;`` にして返す。

    そのまま Discord へ流すと、**人間が読む画面に ``&amp;`` が出る**。
    課題7で Slack の同じ変換を踏んでいる（保存時に変換し表示時に戻す）。
    """
    item = playlist_item(title="Q&amp;A 回&lt;答&gt;")

    assert relay_uploads.parse_upload(item).title == "Q&A 回<答>"


def test_the_time_is_timezone_aware_utc():
    """素朴な ``datetime`` にすると、比較のたびに例外か暗黙のローカル解釈になる。"""
    parsed = relay_uploads.parse_upload(playlist_item()).published_at

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(None)


def test_a_non_video_item_is_rejected():
    """再生リストには動画以外も入りうる。``resourceId.kind`` を見る。"""
    item = playlist_item()
    item["snippet"]["resourceId"] = {"kind": "youtube#playlist", "playlistId": "pl_1"}
    del item["contentDetails"]["videoId"]

    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.parse_upload(item)


def test_an_empty_video_id_is_rejected():
    """空文字は「無い」と同じ。URL に組んだ時点で別のページを指してしまう。"""
    item = playlist_item(video_id="")

    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.parse_upload(item)


# ============================================================ 並べ替え


def test_the_input_order_is_not_trusted():
    """**公式は uploads 再生リストの順序を保証していない**（2026-08-23 に確認）。

    「並び順を意味に使わない。意味はデータ自身から取る」——再生リストの
    N番目＝N話がズレた件と同じ形。ここでは自分で並べ直す。
    """
    shuffled = [
        upload("c", "2026-08-22T00:00:00Z"),
        upload("a", "2026-08-20T00:00:00Z"),
        upload("b", "2026-08-21T00:00:00Z"),
    ]

    picked = relay_uploads.select_new(shuffled, relay_uploads.State.empty())

    assert [u.video_id for u in picked] == ["a", "b", "c"]


def test_the_oldest_is_delivered_first():
    """届く順が公開順になっていないと、読む側が話数を追えない。"""
    picked = relay_uploads.select_new(
        [upload("new", "2026-08-22T00:00:00Z"), upload("old", "2026-08-01T00:00:00Z")],
        relay_uploads.State.empty(),
    )

    assert picked[0].video_id == "old"


# ============================================================ 重複を防ぐ


def test_an_already_sent_video_is_not_picked_again():
    state = relay_uploads.State(watermark=at("2026-08-20T00:00:00Z"), sent_ids=("a",))

    picked = relay_uploads.select_new([upload("a", "2026-08-20T00:00:00Z")], state)

    assert picked == []


def test_two_videos_sharing_a_timestamp_are_both_delivered():
    """**水位（時刻）だけで判定すると、ここで必ず壊れる。**

    ``>`` なら片方を取りこぼし、``>=`` なら送った側をもう一度送る。
    ID 集合を正本にすれば、どちらも起きない。
    """
    same = "2026-08-20T06:33:54Z"

    picked = relay_uploads.select_new(
        [upload("a", same), upload("b", same)], relay_uploads.State.empty()
    )

    assert {u.video_id for u in picked} == {"a", "b"}


def test_the_other_video_at_the_watermark_survives_a_restart():
    """1件目を送った直後に落ちても、2件目は次回に届く。

    水位は両方 ``same`` に進んでいるが、正本は ID 集合なので b は残る。
    """
    same = "2026-08-20T06:33:54Z"
    after_first = relay_uploads.remember(relay_uploads.State.empty(), upload("a", same))

    picked = relay_uploads.select_new([upload("a", same), upload("b", same)], after_first)

    assert [u.video_id for u in picked] == ["b"]


def test_remembering_returns_a_new_state():
    """既存の状態を書き換えない。呼んだ側が古い状態を持ち続けられる。"""
    before = relay_uploads.State.empty()

    after = relay_uploads.remember(before, upload("a", "2026-08-20T00:00:00Z"))

    assert before.sent_ids == ()
    assert after.sent_ids == ("a",)


def test_the_id_list_is_bounded():
    """**無限に伸ばさない。** 状態ファイルが際限なく育つと、いつか読めなくなる。"""
    state = relay_uploads.State.empty()
    for index in range(10):
        state = relay_uploads.remember(
            state, upload(f"v{index}", "2026-08-20T00:00:00Z"), keep=3
        )

    assert state.sent_ids == ("v7", "v8", "v9")


def test_the_watermark_never_goes_backwards():
    """古い動画を送っても水位は下がらない。下がるとページングが延びるだけ。"""
    state = relay_uploads.remember(
        relay_uploads.State.empty(), upload("new", "2026-08-22T00:00:00Z")
    )

    state = relay_uploads.remember(state, upload("old", "2026-08-01T00:00:00Z"))

    assert state.watermark == at("2026-08-22T00:00:00Z")


# ============================================================ 送る本文


def test_the_message_carries_the_watchable_url():
    text = relay_uploads.build_message(upload("abc123", "2026-08-20T00:00:00Z"))

    assert "https://www.youtube.com/watch?v=abc123" in text


def test_the_message_carries_the_title():
    text = relay_uploads.build_message(
        upload("abc123", "2026-08-20T00:00:00Z", title="第3話 タイトル")
    )

    assert "第3話 タイトル" in text


# ============================================================ 物差しを細くする
#
# 下の5件は、わざと壊す検査で**素通りした**箇所を塞ぐために足した。
# どれも「別の検査が先に拾うので、その行が無くても落ちない」形だった。
# **通っていることと、守っていることは別**である。


def test_the_missing_time_error_names_the_field_that_must_not_be_used():
    """メッセージまで見る。

    ``videoPublishedAt`` が無いとき、``parse_time`` の空チェックが先に拾うので
    「落ちるか」だけ見ていると**この分岐を消しても通ってしまう**。
    ここが伝えたいのは「``snippet.publishedAt`` で代用してはいけない」なので、
    その一文が出ることを固定する。
    """
    with pytest.raises(relay_uploads.RelayError) as caught:
        relay_uploads.parse_upload(playlist_item(with_video_published_at=False))

    assert "snippet.publishedAt" in str(caught.value)


def test_a_time_without_a_zone_is_rejected():
    """**naive な時刻を1件も渡していなかった。**

    通すと ``astimezone`` が暗黙にローカル時刻として解釈する。
    日本で動かすと9時間ずれるが、例外は出ない。
    """
    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.parse_time("2026-08-20T06:33:54", label="テスト")


def test_a_non_utc_offset_is_converted_to_utc():
    """**検体が全部 ``Z`` だった**ので、揃える処理を消しても落ちなかった。

    ここは ``==`` で比べても意味がない——aware な datetime どうしの比較は
    同じ瞬間なら True になる。**持っている時差そのもの**を見る。
    """
    got = relay_uploads.parse_time("2026-08-20T15:33:54+09:00", label="テスト")

    assert got.utcoffset() == timezone.utc.utcoffset(None)
    assert got.hour == 6


def test_a_non_video_item_is_rejected_even_when_a_video_id_is_present():
    """``kind`` の検査だけが拾える形にする。

    前の検体は ``videoId`` も消していたので、空チェックが先に拾っていた。
    """
    item = playlist_item()
    item["snippet"]["resourceId"] = {"kind": "youtube#playlist", "playlistId": "pl_1"}

    with pytest.raises(relay_uploads.RelayError):
        relay_uploads.parse_upload(item)


def test_the_first_occurrence_wins_when_a_video_repeats():
    """**中身の違う重複**を渡す。

    同じ videoId でも、ページを跨ぐと内容が違って返ることがある。
    検体の中身が同じだと、辞書が勝手に畳むぶんと区別が付かない。
    """
    picked = relay_uploads.select_new(
        [
            upload("a", "2026-08-20T00:00:00Z", title="最初に見た方"),
            upload("a", "2026-08-20T00:00:00Z", title="あとから来た方"),
        ],
        relay_uploads.State.empty(),
    )

    assert [u.title for u in picked] == ["最初に見た方"]


# ============================================================ 何を「新着」と呼ぶか
#
# **どちらの時刻が正しいか、ではなかった。**
#
# チャンネルのアップロード再生リストなら2つはほぼ一致するので、取り違えても
# たいてい動く。**キュレーションされた再生リスト**では話が変わる——
# 古い動画が今日追加されうるからで、そのとき2つは何年もずれる。
#
# ============================== ================================================
# 何を流したいか                   見るべき時刻
# ============================== ================================================
# 新しく公開された動画             contentDetails.videoPublishedAt
# リストに新しく入った動画          snippet.publishedAt
# ============================== ================================================
#
# 選ばせる。**片方に決め打つと、もう片方の使い方で静かに取りこぼす。**


def test_both_timestamps_are_kept():
    """読んだ時点で両方持つ。**あとから片方を取りに戻れない。**"""
    parsed = relay_uploads.parse_upload(
        playlist_item(
            video_published_at="2020-01-01T00:00:00Z",
            snippet_published_at="2026-08-22T01:00:00Z",
        )
    )

    assert parsed.published_at == at("2020-01-01T00:00:00Z")
    assert parsed.added_at == at("2026-08-22T01:00:00Z")


def test_selecting_by_added_orders_by_when_it_entered_the_playlist():
    old_video_added_today = relay_uploads.Upload(
        video_id="old_but_new_here",
        title="古い動画",
        published_at=at("2020-01-01T00:00:00Z"),
        added_at=at("2026-08-22T00:00:00Z"),
        channel_title="c",
    )
    recent_video_added_long_ago = relay_uploads.Upload(
        video_id="new_but_old_here",
        title="新しい動画",
        published_at=at("2026-08-21T00:00:00Z"),
        added_at=at("2026-01-01T00:00:00Z"),
        channel_title="c",
    )

    picked = relay_uploads.select_new(
        [old_video_added_today, recent_video_added_long_ago],
        relay_uploads.State.empty(),
        key=relay_uploads.NEW_BY_ADDED,
    )

    assert [u.video_id for u in picked] == ["new_but_old_here", "old_but_new_here"]


def test_a_video_added_late_is_not_lost_when_selecting_by_added():
    """**この課題で実際に踏みかけた取りこぼし。**

    水位（2026-08-20）より公開が古い動画が、今日リストに追加された。
    公開時刻で見ていると水位の下なので、二度と届かない。
    """
    old_video_added_today = relay_uploads.Upload(
        video_id="v",
        title="2020年の動画",
        published_at=at("2020-01-01T00:00:00Z"),
        added_at=at("2026-08-22T00:00:00Z"),
        channel_title="c",
    )
    state = relay_uploads.State(watermark=at("2026-08-20T00:00:00Z"), sent_ids=())

    picked = relay_uploads.select_new(
        [old_video_added_today], state, key=relay_uploads.NEW_BY_ADDED
    )

    assert [u.video_id for u in picked] == ["v"]


def test_the_watermark_follows_the_chosen_field():
    """水位を公開時刻で進めながら追加時刻で選ぶと、遡る範囲が合わなくなる。"""
    item = relay_uploads.Upload(
        video_id="v",
        title="t",
        published_at=at("2020-01-01T00:00:00Z"),
        added_at=at("2026-08-22T00:00:00Z"),
        channel_title="c",
    )

    state = relay_uploads.remember(
        relay_uploads.State.empty(), item, key=relay_uploads.NEW_BY_ADDED
    )

    assert state.watermark == at("2026-08-22T00:00:00Z")


def test_asking_for_a_missing_timestamp_fails_loudly():
    """**無いものを既定値で埋めない。** 埋めると順序が静かに壊れる。"""
    item = relay_uploads.Upload(
        video_id="v",
        title="t",
        published_at=at("2026-08-20T00:00:00Z"),
        added_at=None,
        channel_title="c",
    )

    with pytest.raises(relay_uploads.RelayError):
        item.when(relay_uploads.NEW_BY_ADDED)


def test_the_default_is_the_video_publish_time():
    """既定は「公開された動画を流す」。チャンネルを見る使い方が素直なため。"""
    assert relay_uploads.DEFAULT_NEW_BY == relay_uploads.NEW_BY_PUBLISHED


# ============================================================ 窓と床
#
# 下の5件も、わざと壊す検査で**素通りした**箇所を塞ぐために足した。
# 3件は「その計算を単体で呼ぶ経路がテストに無かった」ことが原因で、
# **実装を関数に出すまで確かめようがなかった**。


def test_the_memory_is_never_narrower_than_the_window():
    """遡る窓が 250 件なら、覚えるのも 250 件。

    窓のほうが広いと、こぼれたぶんが「知らない動画」に見えて再送される。
    """
    assert relay_uploads.keep_for(5, 50) == 250


def test_a_small_window_still_keeps_the_baseline():
    """窓が狭いからといって記憶まで狭めない。"""
    assert relay_uploads.keep_for(1, 10) == relay_uploads.DEFAULT_KEEP_IDS


def test_the_floor_sits_below_the_watermark_by_the_margin():
    """**水位ちょうどでは切らない。**

    ここが素通りしたのは、床の式が2か所にベタ書きされていて、
    片方だけ壊しても**もう片方が同じ判定をしていた**ため。1つにまとめた。
    """
    state = relay_uploads.State(watermark=at("2026-08-20T00:00:00Z"), sent_ids=())

    assert relay_uploads.floor_of(state) == at("2026-08-19T00:00:00Z")


def test_there_is_no_floor_before_the_first_run():
    assert relay_uploads.floor_of(relay_uploads.State.empty()) is None


def test_an_item_just_inside_the_margin_is_still_delivered():
    """床は ``select_new`` にも効く。**効きすぎると取りこぼす**ので境界を固定する。"""
    state = relay_uploads.State(watermark=at("2026-08-20T00:00:00Z"), sent_ids=())

    picked = relay_uploads.select_new([upload("v", "2026-08-19T12:00:00Z")], state)

    assert [u.video_id for u in picked] == ["v"]


def test_an_unknown_basis_is_rejected():
    """**知らない基準を黙って公開時刻で代用しない。**

    代用すると、綴りを間違えた ``--new-by`` が「動いているのに違うものを見る」
    状態になる。
    """
    item = upload("v", "2026-08-20T00:00:00Z")

    with pytest.raises(relay_uploads.RelayError):
        item.when("だいたい新しいやつ")
