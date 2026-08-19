"""task9/send_push.py のテスト。

**この課題は、これまでの8課題と締め方が違う。**

課題1〜8は「送信 → 別経路で読み返して照合」で閉じていた。LINE には
bot が送ったテキストを読み返す API が無い（2026-08-18 に公式 OpenAPI 定義で確認）。
そこで送信側は、**あとから照合できる材料を残すこと**まで仕事に含める。

残す材料は3つ。

============================ ==============================================
材料                          何を言えるか
============================ ==============================================
``sentMessages[].id``         LINE がこの送信に ID を振った
``totalUsage`` の送信前後      **別のエンドポイント**が通数の増加を認めた
``/v2/bot/info`` の ``basicId`` 意図したチャネルを叩いた
============================ ==============================================

**3つとも「何を送ったか」は言わない。** 文面の一致は最後まで確認できない。
そのことを検査結果に明示するのが verify_push.py の役目で、
ここでは「材料を欠けたまま成功にしない」ことだけを守る。

**results.json は public リポジトリに入る。** ``quoteToken`` は書かない
（引用返信に使える値で、照合には要らない）。宛先IDは伏せた形だけを残す。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import send_push  # noqa: E402
from common import line_auth  # noqa: E402


TOKEN = "FAKEtoken0000000000000000000000000000000000/FAKE+aaaa="
USER_ID = "U" + "8" * 32
BOT_USER_ID = "U" + "1" * 32
BASIC_ID = "@fake0000"
MESSAGE_ID = "627984934547751122"
QUOTE_TOKEN = "QUOTEtoken" + "z" * 40


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = ""
        self.headers = headers if headers is not None else {}

    def json(self):
        if self._payload is None:
            raise ValueError("応答が JSON ではありません")
        return self._payload


def push_payload(**overrides):
    sent = {"id": MESSAGE_ID, "quoteToken": QUOTE_TOKEN}
    sent.update(overrides)
    return {"sentMessages": [sent]}


def bot_info():
    return line_auth.BotInfo(
        user_id=BOT_USER_ID,
        basic_id=BASIC_ID,
        display_name="開発テスト",
        chat_mode="bot",
        mark_as_read_mode="auto",
    )


# ================================================================== 送る中身


def test_payload_has_the_documented_shape():
    payload = send_push.build_payload(to=USER_ID, text="やっほー")

    assert payload == {"to": USER_ID, "messages": [{"type": "text", "text": "やっほー"}]}


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_blank_text_is_rejected_before_the_request(text):
    """空文字・空白だけは手元で止める。

    LINE も 400 を返すが、**手元で止めれば1通も消費しない**。
    無料プランは月200通で、失敗した送信も試行のたびに時間を食う。
    """
    with pytest.raises(send_push.SendError):
        send_push.build_payload(to=USER_ID, text=text)


def test_text_is_sent_as_typed_including_surrounding_spaces():
    """**本文は strip しない。**

    空白だけを弾くのと、書いた空白を落とすのは別の話。落とすと
    「送った文字列」と「届いた文字列」が最初からずれ、照合の意味が消える。
    """
    payload = send_push.build_payload(to=USER_ID, text="  端に空白  ")

    assert payload["messages"][0]["text"] == "  端に空白  "


def test_no_length_limit_is_enforced_locally():
    """**長さ検査を自前で持たない。**

    テキストの上限は公式に数字があるが、このセッションでは実物で確かめていない。
    確かめていない数字を定数に置くと、LINE 側が変えた日に**正しい送信を拒む**側で
    壊れる（課題8で ``content`` の最大長を持たないと決めたのと同じ）。
    長すぎるときは API のエラーを訳して見せる。
    """
    payload = send_push.build_payload(to=USER_ID, text="あ" * 6000)

    assert len(payload["messages"][0]["text"]) == 6000


def test_exactly_one_message_object_is_sent():
    """1リクエストに5件まで載るが、**1件に固定する**。

    複数件にすると ``totalUsage`` の増分が「送信対象になった人数」であって
    メッセージ件数ではないことと噛み合わず、照合の解釈が難しくなる。
    """
    payload = send_push.build_payload(to=USER_ID, text="x")

    assert len(payload["messages"]) == 1


# ================================================================== 応答を読む


def test_reads_message_id_from_response():
    sent = send_push.read_send_result(FakeResponse(payload=push_payload()))

    assert sent.message_id == MESSAGE_ID


def test_reads_request_id_from_headers():
    """``x-line-request-id`` を残す。問い合わせるときの唯一の手掛かり。"""
    response = FakeResponse(
        payload=push_payload(), headers={"x-line-request-id": "req-1"}
    )

    assert send_push.read_send_result(response).request_id == "req-1"


def test_missing_request_id_is_empty_not_an_error():
    """ヘッダが無くても送信自体は成功している。ここで落とさない。"""
    assert send_push.read_send_result(FakeResponse(payload=push_payload())).request_id == ""


def test_empty_sent_messages_is_a_failure():
    """``sentMessages`` が空なら失敗にする。

    **HTTP 200 で空**という形は「エラーにならない失敗」そのもの。
    ID が無ければ記録に残す材料が無く、あとから何も言えない。
    課題8の「0 件は不一致」と同じ扱いにする。
    """
    with pytest.raises(send_push.SendError):
        send_push.read_send_result(FakeResponse(payload={"sentMessages": []}))


def test_missing_sent_messages_key_is_a_failure():
    with pytest.raises(send_push.SendError):
        send_push.read_send_result(FakeResponse(payload={}))


def test_blank_message_id_is_a_failure():
    with pytest.raises(send_push.SendError):
        send_push.read_send_result(FakeResponse(payload=push_payload(id="")))


def test_non_json_response_is_a_failure_with_a_readable_message():
    """**「JSON として読めなかった」と名指しできていることまで見る。**

    ここを ``SendError が出た`` だけにすると、空の辞書に倒す実装に変えても
    「sentMessages がありません」で落ちるので素通りする
    （2026-08-19・ミューテーションで検出）。原因が1つ手前にあると分からなくなる。
    """
    with pytest.raises(send_push.SendError) as error:
        send_push.read_send_result(FakeResponse(payload=None))

    assert "JSON" in str(error.value)


def test_list_body_is_a_failure():
    with pytest.raises(send_push.SendError):
        send_push.read_send_result(FakeResponse(payload=[1, 2]))


# ================================================================== 送る


class RecordingSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(payload=push_payload())


def test_push_posts_to_the_documented_path():
    session = RecordingSession()

    send_push.push(session, {"to": USER_ID, "messages": []})

    assert session.calls[0][0].endswith("/v2/bot/message/push")


def test_push_sends_the_payload_as_json():
    session = RecordingSession()
    payload = {"to": USER_ID, "messages": [{"type": "text", "text": "x"}]}

    send_push.push(session, payload)

    assert session.calls[0][1]["json"] == payload


def test_push_sets_a_retry_key():
    """``X-Line-Retry-Key`` を必ず付ける。

    通信が切れて再実行したとき、同じキーなら**二重送信にならない**。
    無料プランは月200通なので、事故った再実行で通数を溶かさない意味もある。
    **付け忘れても手元のテストは通ってしまう**ので、ヘッダの有無を固定する
    （課題8の User-Agent と同じ形）。
    """
    session = RecordingSession()

    send_push.push(session, {"to": USER_ID, "messages": []})

    assert session.calls[0][1]["headers"]["X-Line-Retry-Key"]


def test_push_uses_the_given_retry_key():
    session = RecordingSession()

    send_push.push(session, {"to": USER_ID, "messages": []}, retry_key="fixed-key")

    assert session.calls[0][1]["headers"]["X-Line-Retry-Key"] == "fixed-key"


# ================================================================== 宛先を伏せる


def test_destination_is_masked_for_the_record():
    """記録に残す宛先は伏せた形にする。

    ``results.json`` は public リポジトリに入る。宛先IDはチャネルに紐づく
    識別子で、単体では他人が使えないが、**残す必要が無いものは残さない**。
    先頭と末尾だけ残すのは、記録どうしを見比べたときに
    「同じ宛先か」を人が判断できるようにするため。
    """
    masked = send_push.mask_destination(USER_ID)

    assert masked.startswith("U8")
    assert masked.endswith("88")
    assert USER_ID not in masked
    assert "…" in masked or "..." in masked


def test_mask_keeps_short_values_unreadable_too():
    """短い値でも中身を丸ごと出さない。**例外を作ると、そこだけ漏れる。**

    最初は ``!= "U123"`` とだけ書いていたが、これは弱すぎた。短い値の分岐を
    消すと ``U1…23`` が返り、**元の文字は全部読めるのに ``!= "U123"`` は真**になる
    （2026-08-19・ミューテーションで検出）。「違う文字列になった」ことと
    「読めなくなった」ことは別。**後者を書く。**
    """
    masked = send_push.mask_destination("U123")

    assert not any(char in masked for char in "123")


# ================================================================== 記録


def test_record_contains_everything_verify_needs():
    record = send_push.build_record(
        info=bot_info(),
        to=USER_ID,
        text="やっほー",
        message_id=MESSAGE_ID,
        request_id="req-1",
        usage_before=0,
        usage_after=1,
    )

    assert record["bot"]["basic_id"] == BASIC_ID
    assert record["bot"]["user_id"] == BOT_USER_ID
    assert record["bot"]["chat_mode"] == "bot"
    assert record["text"] == "やっほー"
    assert record["message_id"] == MESSAGE_ID
    assert record["request_id"] == "req-1"
    assert record["usage_before"] == 0
    assert record["usage_after"] == 1


def test_record_never_contains_the_raw_destination():
    record = send_push.build_record(
        info=bot_info(), to=USER_ID, text="x", message_id=MESSAGE_ID,
        request_id="", usage_before=0, usage_after=1,
    )

    assert USER_ID not in json.dumps(record, ensure_ascii=False)
    assert record["to_masked"] == send_push.mask_destination(USER_ID)


def test_record_never_contains_the_quote_token():
    """``quoteToken`` を書かない。照合に要らないうえ、引用返信に使える値。

    read_send_result は受け取るが、**記録には渡さない**。
    「受け取ったものは全部書く」を既定にすると、増えた項目が黙って漏れる。
    """
    record = send_push.build_record(
        info=bot_info(), to=USER_ID, text="x", message_id=MESSAGE_ID,
        request_id="", usage_before=0, usage_after=1,
    )

    assert "quote" not in json.dumps(record).lower()


def test_record_is_written_as_utf8_json(tmp_path):
    """日本語をエスケープせずに書く。**記録は人が読んで確かめるもの。**"""
    path = tmp_path / "results.json"
    record = send_push.build_record(
        info=bot_info(), to=USER_ID, text="やっほー", message_id=MESSAGE_ID,
        request_id="", usage_before=0, usage_after=1,
    )

    send_push.write_record(path, record)

    raw = path.read_text(encoding="utf-8")
    assert "やっほー" in raw
    assert json.loads(raw)["message_id"] == MESSAGE_ID


# ================================================================== 画面に出すパス


def test_display_path_is_relative_inside_the_repository():
    """**実行画面に絶対パスを出さない。**

    絶対パスにはホームディレクトリ名が入り、それが提出用のスクリーンショットに
    そのまま写る。課題3で「mutate.py から実ユーザー名を除く」と決めたのに、
    **画面出力だけ素通りしていた**（2026-08-19 に撮ったスクショで発見）。
    """
    inside = send_push.ROOT / "task9" / "results.json"

    shown = send_push._display_path(inside)

    assert shown == str(Path("task9") / "results.json")
    assert str(send_push.ROOT) not in shown


def test_display_path_keeps_paths_outside_the_repository():
    """外を指すときは隠さない。**どこに書いたか分からなくなるほうが困る。**"""
    outside = Path(send_push.ROOT).anchor + "elsewhere/results.json"

    assert send_push._display_path(outside) == outside


# ================================================================== 通数を読む


def test_fetch_usage_reads_total_usage():
    class Session:
        headers = {}

        def get(self, url, **kwargs):
            assert url.endswith("/v2/bot/message/quota/consumption")
            return FakeResponse(payload={"totalUsage": 7})

    assert send_push.fetch_usage(Session()) == 7


def test_fetch_usage_rejects_missing_field():
    """``totalUsage`` が無いのに 0 として続行しない。

    **0 を返すと「送信前は0通だった」と区別が付かず、増分の照合が
    偽の成功に化ける。** 取れなかったことを取れなかったと言う。
    """
    class Session:
        headers = {}

        def get(self, url, **kwargs):
            return FakeResponse(payload={})

    with pytest.raises(send_push.SendError):
        send_push.fetch_usage(Session())


def test_fetch_usage_rejects_non_integer():
    class Session:
        headers = {}

        def get(self, url, **kwargs):
            return FakeResponse(payload={"totalUsage": "たくさん"})

    with pytest.raises(send_push.SendError):
        send_push.fetch_usage(Session())


def test_fetch_usage_rejects_boolean():
    """``True`` は Python では int の仲間。**通数として通してはいけない。**

    ``isinstance(True, int)`` は真なので、素朴な型検査を素通りする。
    """
    class Session:
        headers = {}

        def get(self, url, **kwargs):
            return FakeResponse(payload={"totalUsage": True})

    with pytest.raises(send_push.SendError):
        send_push.fetch_usage(Session())


# ================================================================== 引数


def test_text_is_required():
    with pytest.raises(SystemExit):
        send_push.parse_args([])


def test_results_path_has_a_default():
    args = send_push.parse_args(["--text", "x"])

    assert args.results.endswith("results.json")


def test_results_path_can_be_overridden():
    args = send_push.parse_args(["--text", "x", "--results", "other.json"])

    assert args.results == "other.json"
