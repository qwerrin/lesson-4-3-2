#!/usr/bin/env python3
"""YouTube の新着アップロードを拾って、Discord のチャンネルへ流す。

課題10（連携した API に機能を追加）の Discord 側。課題8で作った「送る」に、
**課題6の YouTube というデータ源**と、**どこまで送ったかの記憶**を足す。

課題8との違い
------------------------------------------------------------------

課題8の主題は「送れたことを、どう確かめるか」だった。1回送って1回読み返せば
閉じる。今回は**繰り返し動く**ので、閉じない問題が出てくる。

    1回だけ動くプログラムには「前回」が無い。
    繰り返し動くプログラムは、前回の自分と話をしないといけない。

そこで足すものは2つある。**新着かどうかを決める規則**と、**それを覚える場所**である。

なぜ search.list を使わないのか
------------------------------------------------------------------

課題6は ``search.list`` を使った。新着を取るのにも使えるが、**繰り返し呼ぶ
用途には向かない**。公式のクォータ表にこう書いてある（2026-08-23 に確認）。

    The search.list and videos.insert methods have their own quota buckets.
    Each of these methods has a default daily limit of 100 per day.

============================== ========== ======================================
メソッド                        コスト     別枠の上限
============================== ========== ======================================
``search.list``                100 units  **1日100回**（10,000 ユニットとは別）
``playlistItems.list``         1 unit     無し
``channels.list``              1 unit     無し
============================== ========== ======================================

毎時ポーリングすると1日24回。``search`` だと**1チャンネルだけで別枠の24%**を
使う。3チャンネル見たら 72%、4つで溢れる。**「ユニットを節約する」では
回避できない**（課題6の時点で分かっていた制約）。

そこで ``channels.list`` でそのチャンネルの「アップロード動画」再生リストを
引き、``playlistItems.list`` で中身を読む。1回あたり 2 units で済む。

エラーを出さずに間違える3か所
------------------------------------------------------------------

この課題の失敗は、ほとんど例外にならない。**動き続けたまま、間違ったものが
届く**か、**何も届かなくなる**かのどちらかになる。

============================== ================================================
間違いやすい点                   どうしたか
============================== ================================================
並び順への依存                   公式は再生リストの**順序を保証していない**。
                                 取得後に自分で並べ直す
HTML エンティティ                 タイトルは ``&amp;`` の形で返る。``html.
                                 unescape`` を通す。課題7で Slack の同じ変換を
                                 踏んでいる（保存時に変換し、表示時に戻す）
時刻の取り違え                   下記（**これは「間違い」ではなかった**）
============================== ================================================

「新着」は2つある
------------------------------------------------------------------

最初は ``snippet.publishedAt`` を使うのを**バグ**だと思っていた。公式の
文言がはっきり分かれているためである（2026-08-23 に確認）。

    snippet.publishedAt              … the item was **added to the playlist**
    contentDetails.videoPublishedAt  … the video was **published to YouTube**

チャンネルのアップロード再生リストなら、2つはほぼ一致する。だから取り違えても
たいてい動く。ところが**キュレーションされた再生リスト**では、古い動画が
今日追加されうるので、2つは何年もずれる。

そこで**「どちらが正しいか」ではなく「何を新着と呼ぶか」の問題**だと分かった。

============================== ================================================
何を流したいか                   ``--new-by``
============================== ================================================
新しく公開された動画             ``published``（既定）
リストに新しく入った動画          ``added``
============================== ================================================

片方に決め打つと、もう片方の使い方で**静かに取りこぼす**——公開が水位より
古い動画が今日追加された場合、``published`` で見ていると二度と届かない。

どこまで送ったかの覚え方
------------------------------------------------------------------

素直に考えると「最後に送った動画の公開時刻」を覚えて、次はそれより新しいものを
送ればよさそうに見える。**これは同時刻の動画が2本あると必ず壊れる。**

============================== ================================================
比較                            起きること
============================== ================================================
``時刻 > 水位``                 同時刻のもう1本を**永久に取りこぼす**
``時刻 >= 水位``                送った1本を**毎回もう一度送る**
============================== ================================================

だから**正本は videoId の集合**にした。水位は「どこまで遡れば十分か」を
決めるためだけに使う。集合が正本なら、比較を緩い側（``>=``）に倒しても
重複しない。**取りこぼす側に倒すと、取りこぼしたことに気づけない。**

**記憶より広い窓を覗かない。** 集合は無限には伸ばさない（既定 200 件）——
状態ファイルが際限なく育つほうが困る。ところが遡る窓は最大
``max_pages × page_size`` ＝ 250 件で、**記憶より広い**。こぼれた 50 件は
「知らない動画」に見えるので、並びが崩れた日に**古い動画が新着として流れる**。

塞ぎ方は2つ重ねてある。

1. 覚える件数を窓に合わせる（``keep = max(200, max_pages × page_size)``）
2. ``select_new`` でも水位の床を効かせ、床より下は ID 集合に無くても送らない

2 が要るのは、1 だけだと**打ち切りが並び順に頼ったまま**だからである。
その並び順は公式が保証していない。

送信と記録のあいだの窓
------------------------------------------------------------------

「Discord へ送る」と「送ったと記録する」は別の操作なので、**あいだで
プロセスが死ぬ可能性は消せない**。決められるのはどちらに倒すかだけである。

============================== ================================================
倒し方                          代償
============================== ================================================
記録してから送る                 送信に失敗した1本を**永久に落とす**
**送ってから記録する**（採用）    死んだ位置によっては**1本だけ重複する**
============================== ================================================

通知が2回来たら人間が気づく。来なかったら気づけない。だから重複側に倒し、
**1本送るごとに記録する**（まとめて最後に書くと、落ちたときの重複が最大件数になる）。

使い方（リポジトリのルートで実行する）::

    # チャンネルのアップロードを見る
    .venv\\Scripts\\python.exe task10\\discord\\relay_uploads.py \\
        --channel-id UCxxxxxxxxxxxxxxxxxxxxxx \\
        --guild <サーバーID> --channel <チャンネルID> --dry-run

    # 再生リストを直に見て、「リストに入った順」で新着を判定する
    .venv\\Scripts\\python.exe task10\\discord\\relay_uploads.py \\
        --playlist-id PLxxxxxxxxxxxxxxxxxxxxxx --new-by added \\
        --guild <サーバーID> --channel <チャンネルID> --dry-run

**初回は ``--init`` を1度だけ通す。** 状態が空のままだと、いま再生リストに
入っているもの全部が「新着」になる。``--init`` は送らずに記録だけする。

``--dry-run`` は **YouTube は読むが Discord へは送らず、状態も進めない**。
課題5の ``--dry-run`` は「認証もしない」だったが、ここは違う。何が届くのかを
先に見られないと、確認の意味がないため。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from googleapiclient.errors import HttpError

# common/ と task8/ を import する前に、リポジトリのルートを sys.path へ通す。
# **module 直下の import 文より先に**通す必要がある（関数の中では遅い）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASK8 = _REPO_ROOT / "task8"
for _extra_path in (_REPO_ROOT, _TASK8):
    if str(_extra_path) not in sys.path:
        sys.path.insert(0, str(_extra_path))

from common import discord_auth, env_file, youtube_auth  # noqa: E402

# **課題8の送信をそのまま使う。同じ処理を2度書かない。**
# 宛先の検証・伏せ字・メンション抑止・応答の読み取りは、あちらで
# 163件のテストと実機で確かめてある。ここで書き直すと、確かめ直しになる。
import send_notification  # noqa: E402


WATCH_URL = "https://www.youtube.com/watch?v="

# 覚えておく videoId の上限。状態ファイルが際限なく育つのを防ぐ。
DEFAULT_KEEP_IDS = 200

# playlistItems.list の1ページあたり件数（API の上限が 50）。
DEFAULT_PAGE_SIZE = 50

# 遡るページ数の上限。**打ち切ったことは必ず報告する**（下の on_note）。
DEFAULT_MAX_PAGES = 5

# 水位より「少し古い」ところまで遡る余白。公開時刻が後から変わる動画があるため、
# 水位ちょうどで切ると、その隣にいたものを二度と見なくなる。
WATERMARK_MARGIN = timedelta(days=1)


class RelayError(Exception):
    """利用者にそのまま見せられる失敗。"""


def shown_path(path: str | Path) -> str:
    """画面に出すためのパス。**絶対パスを出さない。**

    **実行画面も提出物である。** この課題の実行結果は public リポジトリに置く
    スクリーンショットになるので、``C:\\Users\\<名前>\\...`` が写った時点で
    利用者の名前が公開される。実機で ``--init`` の拒否メッセージが実際に
    これを出していた（README とソースは検査していたのに、**実行時の出力だけ
    検査していなかった**）。

    リポジトリの外を指されたら**名前だけ**にする。「相対にできないので絶対パス」
    に倒すと、いちばん出したくない場合にいちばん長いものが出る。
    """
    target = Path(path)
    try:
        return target.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return target.name


def keep_for(max_pages: int, page_size: int) -> int:
    """覚えておく件数。**遡る窓より狭くしない。**

    窓のほうが広いと、こぼれたぶんが「知らない動画」に見えて再送される。
    式を ``main`` の中に埋めていたときは、**わざと壊しても素通りした**
    （250 件を用意する経路がテストに無かった）。外に出すと1行で確かめられる。
    """
    return max(DEFAULT_KEEP_IDS, max_pages * page_size)


def floor_of(state: "State") -> "datetime | None":
    """遡る／受け付ける下限。**水位ちょうどでは切らない。**

    公開時刻が後から変わる動画があるので、水位ちょうどで切ると、その隣に
    いたものを二度と見なくなる。``fetch_uploads`` と ``select_new`` の
    両方がこれを使う——**片方だけ余白を持つと、取ってきたのに捨てる**、
    あるいは**捨てるつもりのものを送る**というねじれが起きる。
    """
    if state.watermark is None:
        return None
    return state.watermark - WATERMARK_MARGIN


# ------------------------------------------------------------------ 値


# 「新着」の意味。**どちらが正しいか、ではない。**
#
# チャンネルのアップロード再生リストなら2つはほぼ一致するので、取り違えても
# たいてい動く。**キュレーションされた再生リスト**では話が変わる——
# 古い動画が今日追加されうるので、そのとき2つは何年もずれる。
#
# ============================== ================================================
# 何を流したいか                   見る時刻
# ============================== ================================================
# 新しく公開された動画             contentDetails.videoPublishedAt
# リストに新しく入った動画          snippet.publishedAt
# ============================== ================================================
#
# 片方に決め打つと、もう片方の使い方で**静かに取りこぼす**。
# 公開が水位より古い動画を今日追加された場合、公開時刻で見ていると二度と届かない。
NEW_BY_PUBLISHED = "published"
NEW_BY_ADDED = "added"

# 既定は「公開された動画を流す」。チャンネルを見る使い方が素直なため。
DEFAULT_NEW_BY = NEW_BY_PUBLISHED


@dataclass(frozen=True)
class Upload:
    """流す対象の1本。**API の応答そのものは持ち回さない。**

    生の dict を下流へ流すと、意図しないフィールドを掴む経路がいつでも
    復活しうる。読んだ時点で必要な形に落とし切る。

    **2つの時刻を両方持つ。** あとから片方を取りに戻ることはできない
    （応答はもう手元に無い）ので、読むときに両方拾っておく。
    """

    video_id: str
    title: str
    published_at: datetime
    channel_title: str
    added_at: datetime | None = None

    def when(self, key: str = DEFAULT_NEW_BY) -> datetime:
        """「新着」の判定に使う時刻を返す。

        **無いものを既定値で埋めない。** 埋めると順序が静かに壊れる。
        """
        if key == NEW_BY_PUBLISHED:
            return self.published_at
        if key == NEW_BY_ADDED:
            if self.added_at is None:
                raise RelayError(
                    f"snippet.publishedAt が読めなかったので --new-by {NEW_BY_ADDED} "
                    f"は使えません: {self.video_id}"
                )
            return self.added_at
        raise RelayError(f"知らない新着の基準です: {key}")


@dataclass(frozen=True)
class State:
    """前回までに何を送ったか。

    ``sent_ids`` が正本で、``watermark`` は遡る範囲を決める目安でしかない。
    """

    watermark: datetime | None
    sent_ids: tuple[str, ...]

    @classmethod
    def empty(cls) -> "State":
        return cls(watermark=None, sent_ids=())


# ------------------------------------------------------------------ 読む


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def parse_time(value, *, label: str) -> datetime:
    """ISO 8601 の時刻を、UTC の aware な datetime にする。

    naive のまま持つと、比較のたびに例外になるか、暗黙にローカル時刻として
    解釈される。**後者は例外が出ないぶん厄介**なので、ここで必ず tz を付ける。
    """
    text = _text(value).strip()
    if not text:
        raise RelayError(f"{label} が空です")

    try:
        # Python の fromisoformat は 3.11 から "Z" を解釈するが、
        # 明示的に置き換えておく（読む人にとっても、どの形を想定しているか分かる）。
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise RelayError(f"{label} を時刻として読めませんでした: {text}") from error

    if parsed.tzinfo is None:
        raise RelayError(f"{label} にタイムゾーンがありません: {text}")

    return parsed.astimezone(timezone.utc)


def parse_upload(item: dict) -> Upload:
    """``playlistItems.list`` の1件を Upload にする。

    **``contentDetails.videoPublishedAt`` が無ければ落とす。**
    ``snippet.publishedAt`` で埋めると動いてしまうが、それは
    「再生リストに追加された時刻」なので、並び順が静かに壊れる。
    """
    if not isinstance(item, dict):
        raise RelayError("再生リストの項目が辞書ではありません")

    snippet = item.get("snippet") or {}
    content = item.get("contentDetails") or {}

    resource = snippet.get("resourceId") or {}
    kind = _text(resource.get("kind"))
    if kind != "youtube#video":
        # 再生リストには動画以外も入りうる。URL に組んだ時点で別のものを指す。
        raise RelayError(f"動画ではない項目が入っています: kind={kind or '(無し)'}")

    video_id = _text(content.get("videoId")).strip()
    if not video_id:
        raise RelayError("videoId がありません")

    if "videoPublishedAt" not in content:
        raise RelayError(
            "contentDetails.videoPublishedAt がありません。"
            "snippet.publishedAt は『再生リストに追加された時刻』なので代わりに使えません"
        )

    published_at = parse_time(
        content.get("videoPublishedAt"), label="contentDetails.videoPublishedAt"
    )

    # **こちらは無くても落とさない。** ``--new-by published`` だけを使うなら
    # 要らない値なので、ここで止めると使えるはずの実行が止まる。
    # 実際に必要になった時点で Upload.when() が名指しで落ちる。
    try:
        added_at = parse_time(snippet.get("publishedAt"), label="snippet.publishedAt")
    except RelayError:
        added_at = None

    return Upload(
        video_id=video_id,
        # **必ず unescape する。** API は "&" を "&amp;" にして返す。
        title=html.unescape(_text(snippet.get("title"))),
        published_at=published_at,
        channel_title=html.unescape(_text(snippet.get("channelTitle"))),
        added_at=added_at,
    )


# ------------------------------------------------------------------ 選ぶ


def select_new(
    uploads: Iterable[Upload], state: State, *, key: str = DEFAULT_NEW_BY
) -> list[Upload]:
    """まだ送っていないものを、**古い順**に返す。

    並べ直すのは、**公式が再生リストの順序を保証していない**ため。
    届く順が公開順になっていないと、読む側が話数を追えない。

    同じ videoId が複数ページに跨って現れることがあるので、ここでも畳む。

    **水位より下は、ID 集合に無くても送らない。**
    ここが抜けていると、次の形で古い動画が「新着」として流れる:

    1. 覚えている ID は 200 件（``DEFAULT_KEEP_IDS``）
    2. 遡る窓は最大 250 件（``max_pages`` × ``page_size``）
    3. **窓のほうが記憶より広い**ので、こぼれた 50 件は「知らない動画」に見える

    遡りの打ち切りは並び順に頼っているが、**その並び順は公式が保証していない**。
    崩れた日に取れてしまった古いものを、ここで止める。
    """
    already = frozenset(state.sent_ids)
    floor = floor_of(state)

    picked: dict[str, Upload] = {}
    for item in uploads:
        if item.video_id in already or item.video_id in picked:
            continue
        if floor is not None and item.when(key) < floor:
            continue
        picked[item.video_id] = item

    # video_id を第2キーにするのは、同時刻のときの順番を実行ごとに変えないため。
    return sorted(picked.values(), key=lambda u: (u.when(key), u.video_id))


def remember(
    state: State,
    upload: Upload,
    *,
    keep: int = DEFAULT_KEEP_IDS,
    key: str = DEFAULT_NEW_BY,
) -> State:
    """1本ぶんを記録した**新しい**状態を返す。

    元の状態は変えない。呼んだ側が「保存に失敗したので前の状態に戻す」を
    選べるようにするため。
    """
    ids = tuple(item for item in state.sent_ids if item != upload.video_id)
    ids = (ids + (upload.video_id,))[-keep:] if keep > 0 else ()

    # **水位は「新着」の判定に使ったのと同じ時刻で進める。**
    # 公開時刻で水位を進めながら追加時刻で選ぶと、遡る範囲が噛み合わなくなる。
    stamp = upload.when(key)

    # **水位は下げない。** 古い動画を後から送っても、遡る範囲が広がるだけ。
    watermark = state.watermark
    if watermark is None or stamp > watermark:
        watermark = stamp

    return replace(state, watermark=watermark, sent_ids=ids)


# ------------------------------------------------------------------ 状態の保存


def state_to_json(state: State) -> dict:
    return {
        "watermark": state.watermark.isoformat() if state.watermark else None,
        "sent_ids": list(state.sent_ids),
    }


def state_from_json(payload) -> State:
    """保存した形から戻す。**壊れていたら空にせず落とす。**

    ここで黙って ``State.empty()` を返すと、**再生リストの全件がもう一度
    流れる**。「読めなかった」と「まだ何も送っていない」は別のことなので、
    同じ値で表してはいけない。
    """
    if not isinstance(payload, dict):
        raise RelayError("状態ファイルの形式が不正です（辞書ではありません）")

    raw_ids = payload.get("sent_ids", [])
    if not isinstance(raw_ids, list) or any(not isinstance(x, str) for x in raw_ids):
        raise RelayError("状態ファイルの sent_ids が文字列の配列ではありません")

    raw_watermark = payload.get("watermark")
    watermark = (
        None if raw_watermark is None else parse_time(raw_watermark, label="watermark")
    )

    return State(watermark=watermark, sent_ids=tuple(raw_ids))


def load_state(path: str | Path) -> State:
    """状態を読む。**ファイルが無いときだけ空を返す。**"""
    target = Path(path)
    if not target.exists():
        return State.empty()

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RelayError(
            f"状態ファイルを読めませんでした: {shown_path(target)}\n"
            "空として扱うと再生リストの全件が流れるので、ここで止めます"
        ) from error

    return state_from_json(payload)


def save_state(path: str | Path, state: State) -> None:
    """状態を書く。**途中で落ちても壊れた状態を残さない。**

    同じ場所へ直接書くと、書き込み中に落ちたとき中途半端な JSON が残る。
    それは load_state から見て「壊れている」なので、次の実行が止まる。
    別名で書いてから置き換える。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state_to_json(state), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)


# ------------------------------------------------------------------ 本文


def video_url(video_id: str) -> str:
    return f"{WATCH_URL}{video_id}"


def build_message(upload: Upload) -> str:
    """Discord へ送る本文。

    リンクは**行の先頭に置く**。Discord は本文中の URL から埋め込みを作るが、
    行頭にあるほうが人間にも押しやすい。
    """
    heading = f"【新着】{upload.channel_title}" if upload.channel_title else "【新着】"
    when = upload.published_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"{heading}\n{upload.title}\n{video_url(upload.video_id)}\n(公開 {when})"


# ------------------------------------------------------------------ YouTube


def _redacted(error: HttpError, api_key: str | None) -> RelayError:
    """**API キーは URL のクエリに載る。**

    HttpError は失敗したリクエストの URI を持っているので、そのまま印字すると
    ``...&key=<APIキー>`` が画面に出る。実行画面は public リポジトリに置く
    スクリーンショットになるため、印字した時点で公開事故になる。
    """
    return RelayError(youtube_auth.redact(str(error), api_key))


def normalize_handle(handle: str) -> str:
    """``@`` を必ず付けた形に揃える。

    **人は付けたり付けなかったりする。** 公式のパラメータ説明の例は ``@`` 付き
    （``forHandle=@handle``）なので、そちらに寄せる。
    """
    text = (handle or "").strip()
    return text if text.startswith("@") else f"@{text}"


def resolve_uploads_playlist(
    service,
    *,
    channel_id: str | None = None,
    handle: str | None = None,
    api_key: str | None = None,
) -> str:
    """チャンネルの「アップロード動画」再生リストの ID を引く（1 unit）。

    ``search.list`` を避けるための入口。ここで得た再生リストを
    ``playlistItems.list`` で読めば、1回 1 unit で新着が取れる。

    **人が持っているのは ``@ハンドル``、API が要るのは ``UC`` で始まるID。**
    変換を利用者に押し付けると、そこで詰まる。公式に ``forHandle`` があるので
    どちらでも引けるようにした。

    ただし**フィルタは「ちょうど1つ」**と明記されている::

        Filters (specify exactly one of the following parameters)

    だから両方渡さないし、両方渡されたら**こちらで止める**。API に投げてから
    400 で返されると、原因がここだと分からなくなる。
    """
    wanted = (channel_id or "").strip()
    wanted_handle = normalize_handle(handle) if (handle or "").strip() else ""

    if bool(wanted) == bool(wanted_handle):
        raise RelayError(
            "チャンネルIDかハンドルを、どちらか一方だけ指定してください"
            f"（ID={wanted or '(無し)'} / ハンドル={wanted_handle or '(無し)'}）"
        )

    # **フィルタは1つだけ載せる。** 空文字でも載せると「指定した」と数えられる。
    query = {"id": wanted} if wanted else {"forHandle": wanted_handle}
    shown = wanted or wanted_handle

    try:
        response = service.channels().list(part="contentDetails", **query).execute()
    except HttpError as error:
        raise _redacted(error, api_key) from error

    items = response.get("items") or []
    if not items:
        raise RelayError(
            f"チャンネルが見つかりません: {shown}\n"
            "ハンドルは @ から始まる表示名、チャンネルIDは UC から始まる文字列です"
        )

    related = (items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}
    uploads = _text(related.get("uploads")).strip()
    if not uploads:
        raise RelayError(f"アップロード再生リストがありません: {shown}")

    return uploads


def fetch_uploads(
    service,
    *,
    playlist_id: str,
    state: State,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    key: str = DEFAULT_NEW_BY,
    api_key: str | None = None,
    on_note: Callable[[str], None] | None = None,
) -> list[Upload]:
    """再生リストを遡って Upload を集める（1ページ 1 unit）。

    **打ち切ったことは黙らない。** 上限まで読んでも水位に届かなかったら
    ``on_note`` で報告する。黙って切ると「全部見た」と読めてしまう。
    """
    floor = floor_of(state)

    collected: list[Upload] = []
    page_token = None
    pages_read = 0

    while pages_read < max_pages:
        try:
            response = (
                service.playlistItems()
                .list(
                    part="snippet,contentDetails",
                    playlistId=playlist_id,
                    maxResults=page_size,
                    pageToken=page_token,
                )
                .execute()
            )
        except HttpError as error:
            raise _redacted(error, api_key) from error

        pages_read += 1
        items = response.get("items") or []
        page = [parse_upload(item) for item in items]
        collected.extend(page)

        page_token = response.get("nextPageToken")
        if not page_token:
            return collected

        # 順序は保証されていないので「1件でも余白より新しければ続ける」で判定する。
        # 先頭だけを見ると、並びが崩れていたときに早く切り上げてしまう。
        if floor is not None and page and all(u.when(key) < floor for u in page):
            return collected

    if on_note is not None and page_token:
        on_note(
            f"{max_pages} ページで打ち切りました（まだ続きがあります）。"
            "初回や長い空白のあとは --max-pages を増やしてください"
        )
    return collected


# ------------------------------------------------------------------ 流す


@dataclass(frozen=True)
class Relayed:
    """1本を送った結果。"""

    video_id: str
    title: str
    message_id: str
    link: str


def relay(
    uploads: Sequence[Upload],
    *,
    state: State,
    send: Callable[[Upload], Relayed],
    persist: Callable[[State], None],
    keep: int = DEFAULT_KEEP_IDS,
    key: str = DEFAULT_NEW_BY,
) -> tuple[list[Relayed], State]:
    """1本ずつ送り、**1本ごとに記録する**。

    まとめて最後に記録すると、落ちたときの重複が最大で全件になる。
    1本ずつなら最大1本で済む。

    送信が失敗したらそこで止める。**先へ進めない**——後続を送ってしまうと、
    失敗した1本だけを後から入れ直す手段が無くなる（順番が崩れる）。
    """
    done: list[Relayed] = []
    current = state

    for item in uploads:
        result = send(item)
        current = remember(current, item, keep=keep, key=key)
        persist(current)
        done.append(result)

    return done, current


# ------------------------------------------------------------------ Discord へ


def build_sender(
    *,
    guild: str,
    channel: str,
    env,
    session_factory: Callable | None = None,
) -> Callable[[Upload], Relayed]:
    """課題8の送信をそのまま使う ``send`` を組む。

    **トークンはここで読む。呼ぶ側に読ませない。**
    呼ぶ側が先に読むと、送り先を差し替えたテストでも本物の資格情報を
    要求してしまう。実際にそう書いて4件落とした。資格情報は、それを
    使う関数が自分で取りに行く（``fetch_webhook`` が伏せ字を呼び出し側に
    任せないのと同じ理由）。

    **セッションは1本を使い回す。** 1本ごとに張り直すと、送る数だけ
    TCP と TLS の握手が増える。
    """
    token = discord_auth.read_bot_token(env)
    session = (
        discord_auth.build_session(token)
        if session_factory is None
        else discord_auth.build_session(token, factory=session_factory)
    )

    def send(item: Upload) -> Relayed:
        payload = send_notification.build_payload(
            build_message(item), allow_mentions=False
        )
        message = send_notification.post_via_bot(
            session, channel=channel, payload=payload, secrets=(token,)
        )
        message_id = _text(message.get("id"))
        return Relayed(
            video_id=item.video_id,
            title=item.title,
            message_id=message_id,
            link=send_notification.message_link(
                guild=guild, channel=channel, message_id=message_id
            ),
        )

    return send


# ------------------------------------------------------------------ CLI


# 1回の実行で送る上限。**初回は状態が空なので、取れた全件が「新着」になる。**
# 再生リストに100本あれば100通が流れる。既定で止めて、残りは次の実行へ回す。
DEFAULT_LIMIT = 5


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="YouTube の新着アップロードを Discord のチャンネルへ流す"
    )
    parser.add_argument(
        "--channel-id", help="YouTube のチャンネルID（UC で始まる）。アップロード再生リストを引く"
    )
    parser.add_argument(
        "--handle",
        help="YouTube のハンドル（@GoogleDevelopers）。forHandle で引く",
    )
    parser.add_argument(
        "--playlist-id",
        help="再生リストID（PL で始まる）を直に指す",
    )
    parser.add_argument(
        "--new-by",
        choices=(NEW_BY_PUBLISHED, NEW_BY_ADDED),
        default=DEFAULT_NEW_BY,
        help=(
            f"何を新着と呼ぶか。{NEW_BY_PUBLISHED}=動画が公開された時刻 / "
            f"{NEW_BY_ADDED}=再生リストに追加された時刻"
        ),
    )
    parser.add_argument("--guild", required=True, help="Discord のサーバーID（数字）")
    parser.add_argument("--channel", required=True, help="Discord のチャンネルID（数字）")
    parser.add_argument(
        "--state",
        default=str(Path(__file__).resolve().parent / "state.json"),
        help="どこまで送ったかを覚えておくファイル",
    )
    parser.add_argument(
        "--env",
        default=str(_REPO_ROOT / env_file.ENV_FILENAME),
        help=f"資格情報が入った {env_file.ENV_FILENAME}",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"1回の実行で送る上限（既定 {DEFAULT_LIMIT}）",
    )
    parser.add_argument(
        "--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="遡るページ数の上限"
    )
    parser.add_argument(
        "--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="1ページの件数（上限50）"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="YouTube は読むが Discord へは送らず、状態も進めない",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="いま再生リストにあるものを『送信済み』として覚える（送らない）",
    )
    parser.add_argument("--json-out", help="実行の記録を書き出す先")
    return parser.parse_args(argv)


def _resolve_env(args, env):
    """テストは env をそのまま渡す。**本番だけ .env を読む。**

    テストで既定の .env を読ませると、手元の本物の資格情報が混ざる。
    「テストの宛先も隔離する」——state を仮置きにしても、宛先設定が
    本番なら実際に配達される。
    """
    if env is not None:
        return env

    # **ファイルを優先する。** 環境変数は他のプロセスと名前空間を共有していて、
    # いつ誰が置いたか分からない値が残りうる（課題7で誤値を踏んでいる）。
    return {**os.environ, **env_file.load(args.env)}


def main(
    argv: Sequence[str] | None = None,
    *,
    env=None,
    service_factory: Callable | None = None,
    sender_factory: Callable | None = None,
    out: Callable[[str], None] | None = None,
) -> int:
    args = parse_args(argv)
    say = out if out is not None else (lambda text: print(text))

    try:
        resolved_env = _resolve_env(args, env)

        # **落ちると分かっている実行で、相手に接続しない。**
        api_key = youtube_auth.read_api_key(resolved_env)

        state_path = Path(args.state)
        if args.init and state_path.exists():
            raise RelayError(
                f"状態ファイルが既にあります: {shown_path(state_path)}\n"
                "--init は初回だけです。2回目に使うと、まだ送っていない動画を"
                "『送信済み』にしてしまいます"
            )

        state = load_state(state_path)

        build_service = (
            service_factory
            if service_factory is not None
            else (lambda key: youtube_auth.build_service(key))
        )
        # **「見る先」はちょうど1つ。** 3つとも「どこを見るか」を指すので、
        # 2つ以上あるとどれに従うかが決まらない。0 個なら何も見られない。
        targets = [args.channel_id, args.handle, args.playlist_id]
        if sum(1 for t in targets if t) != 1:
            raise RelayError(
                "--channel-id / --handle / --playlist-id を、"
                "どれか一つだけ指定してください"
            )

        service = build_service(api_key)

        if args.playlist_id:
            playlist_id = args.playlist_id
            say(f"再生リスト: {playlist_id}")
        else:
            playlist_id = resolve_uploads_playlist(
                service,
                channel_id=args.channel_id,
                handle=args.handle,
                api_key=api_key,
            )
            say(f"アップロード再生リスト: {playlist_id}")

        # **覚える件数を、遡る窓より狭くしない。** 窓のほうが広いと、
        # こぼれたぶんが「知らない動画」に見えて再送される。
        keep = keep_for(args.max_pages, args.page_size)

        found = fetch_uploads(
            service,
            playlist_id=playlist_id,
            state=state,
            page_size=args.page_size,
            max_pages=args.max_pages,
            key=args.new_by,
            api_key=api_key,
            on_note=say,
        )
        fresh = select_new(found, state, key=args.new_by)
        say(f"取得 {len(found)} 件 / うち新着 {len(fresh)} 件（基準: {args.new_by}）")

        if args.init:
            current = state
            for item in fresh:
                current = remember(current, item, keep=keep, key=args.new_by)
            save_state(state_path, current)
            say(f"{len(fresh)} 件を『送信済み』として記録しました（送っていません）")
            return 0

        if args.limit > 0 and len(fresh) > args.limit:
            # **黙って切らない。** 残りが伝わらないと「全部流した」と読める。
            say(
                f"上限 {args.limit} 件で切りました。"
                f"残り {len(fresh) - args.limit} 件は次の実行で送ります"
            )
            fresh = fresh[: args.limit]

        if not fresh:
            # **0 件は正常。** 記事A（予定の通知）は0件でも送るが、ここは送らない。
            # 予定が来ないのは異常、新着が来ないのは普通、という違いによる。
            say("新着はありません")
            return 0

        for item in fresh:
            # **videoId を必ず出す。** 実機の1回目で、同じタイトルの動画が
            # 1分違いで2本並んだ。タイトルと時刻だけでは、送る前の確認で
            # どれがどれか区別できない——**タイトルは同一性ではない**。
            say(
                f"  - {item.when(args.new_by):%Y-%m-%d %H:%M UTC}  "
                f"[{item.video_id}]  {item.title}"
            )

        if args.dry_run:
            # **状態を進めない。** 確認のための実行が、本番の記憶を汚さない。
            say("--dry-run のため送信しません（状態も更新しません）")
            return 0

        make_sender = sender_factory if sender_factory is not None else build_sender
        send = make_sender(guild=args.guild, channel=args.channel, env=resolved_env)

        done, _final = relay(
            fresh,
            state=state,
            send=send,
            persist=lambda updated: save_state(state_path, updated),
            keep=keep,
            key=args.new_by,
        )

        for result in done:
            say(f"送信: {result.title} -> {result.link}")

        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps(
                    {
                        "playlist_id": playlist_id,
                        "sent": [
                            {
                                "video_id": r.video_id,
                                "title": r.title,
                                "message_id": r.message_id,
                                "link": r.link,
                                "video_url": video_url(r.video_id),
                            }
                            for r in done
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        return 0

    except (RelayError, youtube_auth.AuthError, discord_auth.DiscordError, env_file.EnvFileError) as error:
        say(f"エラー: {youtube_auth.redact(str(error), locals().get('api_key'))}")
        return 1


if __name__ == "__main__":
    # **動画のタイトルには絵文字が入る。** Windows の既定のコンソールは cp932 で、
    # 変換できない文字に当たると UnicodeEncodeError で落ちる——送信そのものは
    # 成功しているのに、報告の途中で落ちる。しかも「絵文字の入った動画が来た日」
    # にしか起きないので、手元では再現しない。
    #
    # ライブラリとして呼ばれたときに標準出力を書き換えるのは行儀が悪いので、
    # スクリプトとして走らせたときだけ直す。
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

    raise SystemExit(main())
