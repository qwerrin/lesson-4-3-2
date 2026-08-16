"""task7/verify_message.py のテスト。

投稿が成功しても、それは「API が ok を返した」までしか意味しない。本当にその
チャンネルに、狙った本文が、こちらの Bot の名前で載ったかは、別のところから
読み直さないと閉じない。

**物差しを応答の外から取る。**

1. **チャンネルと本文** — 人間がコマンドラインで渡す（``--channel`` / ``--expect-text``）
2. **タイムスタンプ** — 投稿時に記録した値と、読み返した値を突き合わせる
3. **投稿者** — ``auth.test``（**conversations.history とは別のエンドポイント**）が
   答えた Bot の user_id と突き合わせる

3 が効くのは、投稿の応答と読み返しの応答という**同じ系統の中だけで比べていない**から。
同じ応答の中で値どうしを比べるのはトートロジーで、何も確かめていない。

**conversations.history に固有の罠がある。**
``oldest=<ts>`` + ``inclusive=true`` + ``limit=1`` は「その ts **以降の最初の1件**」で
あって「その ts のメッセージ」ではない。狙ったメッセージが消えていれば
**次のメッセージが 1 件返る**。件数だけ見て「取れた」と判断すると、別の
メッセージを相手に照合して、本文が違うことしか分からない。
課題6の「videos.list は存在しない ID を黙って落として 200 を返す」と同じ形である。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "task7"))

import verify_message  # noqa: E402
from common import slack_auth  # noqa: E402


CHANNEL = "C0DUMMYCHAN"
TS = "1503435956.000247"
BOT_USER = "U0DUMMYBOT"
PERMALINK = f"https://example.slack.com/archives/{CHANNEL}/p1503435956000247"


def make_record(**overrides):
    record = {
        "team": "TeamName",
        "channel": CHANNEL,
        "text": "課題7の動作確認です",
        "ts": TS,
        "permalink": PERMALINK,
        "posted_by": BOT_USER,
    }
    record.update(overrides)
    return record


def write_record(tmp_path, record=None, raw=None):
    path = tmp_path / "results.json"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(record if record is not None else make_record(), ensure_ascii=False),
            encoding="utf-8",
        )
    return path


def identity(user_id=BOT_USER):
    return slack_auth.Identity(
        team="TeamName",
        team_id="T111",
        user_id=user_id,
        bot_id="B333",
        scopes=("chat:write", "channels:history"),
    )


class FakeResponse:
    def __init__(self, data):
        self.data = dict(data)
        self.headers = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __getitem__(self, key):
        return self.data[key]


class FakeClient:
    def __init__(self, history=None, error=None):
        self.calls = []
        self._history = history
        self._error = error

    def conversations_history(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        if self._history is not None:
            return FakeResponse(self._history)
        return FakeResponse(
            {
                "ok": True,
                "messages": [
                    {"type": "message", "user": BOT_USER, "text": "課題7の動作確認です", "ts": TS}
                ],
            }
        )


class TestLoadResults:
    def test_読み込める(self, tmp_path):
        payload = verify_message.load_results(write_record(tmp_path))
        assert payload["ts"] == TS

    def test_ファイルが無ければ落ちる(self, tmp_path):
        with pytest.raises(verify_message.VerifyError):
            verify_message.load_results(tmp_path / "missing.json")

    def test_JSONでなければ落ちる(self, tmp_path):
        with pytest.raises(verify_message.VerifyError):
            verify_message.load_results(write_record(tmp_path, raw="{壊れた"))

    def test_辞書でなければ落ちる(self, tmp_path):
        with pytest.raises(verify_message.VerifyError):
            verify_message.load_results(write_record(tmp_path, raw="[1, 2]"))

    @pytest.mark.parametrize("key", ["channel", "text", "ts", "permalink", "posted_by"])
    def test_必要な項目が無ければ落ちる(self, tmp_path, key):
        record = make_record()
        del record[key]
        with pytest.raises(verify_message.VerifyError):
            verify_message.load_results(write_record(tmp_path, record))

    @pytest.mark.parametrize("key", ["channel", "text", "ts", "permalink", "posted_by"])
    def test_空文字も落とす(self, tmp_path, key):
        with pytest.raises(verify_message.VerifyError):
            verify_message.load_results(write_record(tmp_path, make_record(**{key: ""})))

    def test_tsが数値なら落ちる(self, tmp_path):
        # float で書かれていたら、その時点で精度が落ちている。
        # 「読めたから大丈夫」にせず、書いた側の誤りとして落とす。
        with pytest.raises(verify_message.VerifyError):
            verify_message.load_results(write_record(tmp_path, make_record(ts=1503435956.000247)))


class TestBuildLocalChecks:
    def _checks(self, payload=None, *, channel=CHANNEL, text="課題7の動作確認です"):
        return verify_message.build_local_checks(
            payload if payload is not None else make_record(),
            expected_channel=channel,
            expected_text=text,
        )

    def test_全部一致すれば通る(self):
        assert verify_message.all_ok(self._checks())

    def test_チャンネルが違えば落ちる(self):
        assert not verify_message.all_ok(self._checks(channel="C0OTHER"))

    def test_本文が違えば落ちる(self):
        assert not verify_message.all_ok(self._checks(text="ちがう本文"))

    def test_リンクにチャンネルIDが無ければ落ちる(self):
        payload = make_record(permalink="https://example.slack.com/archives/C0OTHER/p1")
        assert not verify_message.all_ok(self._checks(payload))

    def test_tsの形式が違えば落ちる(self):
        assert not verify_message.all_ok(self._checks(make_record(ts="not-a-timestamp")))

    def test_確かめた項目がゼロにならない(self):
        # 空のリストに all() を掛けると True になる。「何も確かめていない」が
        # 「全部一致」に化けるので、項目が必ず立つことを固定する。
        assert len(self._checks()) >= 4


class TestFetchMessage:
    def test_チャンネルとtsを渡す(self):
        client = FakeClient()
        verify_message.fetch_message(client, channel=CHANNEL, ts=TS)
        assert client.calls[0]["channel"] == CHANNEL
        assert client.calls[0]["oldest"] == TS

    def test_inclusiveを渡す(self):
        # oldest は「その時刻より後」なので、inclusive を落とすと
        # **自分自身が結果に入らない**。落としても 0 件になるだけで
        # エラーにならないため、指定を消したことに気づけない。
        client = FakeClient()
        verify_message.fetch_message(client, channel=CHANNEL, ts=TS)
        assert client.calls[0]["inclusive"] is True

    def test_1件だけ要求する(self):
        client = FakeClient()
        verify_message.fetch_message(client, channel=CHANNEL, ts=TS)
        assert client.calls[0]["limit"] == 1

    def test_メッセージを返す(self):
        client = FakeClient()
        message = verify_message.fetch_message(client, channel=CHANNEL, ts=TS)
        assert message["ts"] == TS

    def test_0件ならNoneを返す(self):
        client = FakeClient(history={"ok": True, "messages": []})
        assert verify_message.fetch_message(client, channel=CHANNEL, ts=TS) is None

    def test_okがfalseなら落ちる(self):
        client = FakeClient(history={"ok": False, "error": "not_in_channel"})
        with pytest.raises(verify_message.VerifyError):
            verify_message.fetch_message(client, channel=CHANNEL, ts=TS)

    def test_okがfalseならmessagesが揃っていても落ちる(self):
        # ok=False の応答から messages を抜いた入力で試すと、messages の検査で
        # 落ちてしまい「ok を見ているか」を確かめられない。
        client = FakeClient(
            history={
                "ok": False,
                "error": "not_in_channel",
                "messages": [{"type": "message", "user": BOT_USER, "text": "課題7の動作確認です", "ts": TS}],
            }
        )
        with pytest.raises(verify_message.VerifyError):
            verify_message.fetch_message(client, channel=CHANNEL, ts=TS)

    def test_messagesが無ければ落ちる(self):
        client = FakeClient(history={"ok": True})
        with pytest.raises(verify_message.VerifyError):
            verify_message.fetch_message(client, channel=CHANNEL, ts=TS)


class TestBuildRemoteChecks:
    # **「省略した」と「None を渡した」を区別する。** 既定値を None にすると
    # message=None（＝読み返せなかった）を渡しても既定のメッセージに差し替わり、
    # 0 件のケースを一度も通さないまま緑になる。
    _OMITTED = object()

    def _message(self, **overrides):
        message = {"type": "message", "user": BOT_USER, "text": "課題7の動作確認です", "ts": TS}
        message.update(overrides)
        return message

    def _checks(self, message=_OMITTED, payload=None, who=None):
        if message is self._OMITTED:
            message = self._message()
        return verify_message.build_remote_checks(
            payload if payload is not None else make_record(),
            message,
            who if who is not None else identity(),
        )

    def test_全部一致すれば通る(self):
        assert verify_message.all_ok(self._checks())

    def test_読み返せなければ落ちる(self):
        assert not verify_message.all_ok(self._checks(message=None))

    def test_読み返せないときも項目を立てる(self):
        # 0 件を「照合する対象が無い＝全部一致」にしない。
        checks = self._checks(message=None)
        assert len(checks) >= 1
        assert not verify_message.all_ok(checks)

    def test_別のメッセージが返ってきたら落ちる(self):
        # oldest+inclusive は「その ts 以降の最初の1件」なので、狙った
        # メッセージが消えていれば次のメッセージが返る。**ここを見ないと
        # 「返ってこなかった」が「一致した」に化ける。**
        assert not verify_message.all_ok(self._checks(self._message(ts="1503435999.000111")))

    def test_投稿者が違えば落ちる(self):
        assert not verify_message.all_ok(self._checks(self._message(user="U0SOMEONE")))

    def test_投稿者が空でも落ちる(self):
        # 空文字を「一致した」にしない。
        assert not verify_message.all_ok(self._checks(self._message(user="")))

    def test_本文が違えば落ちる(self):
        assert not verify_message.all_ok(self._checks(self._message(text="ちがう本文")))

    def test_本文が空でも落ちる(self):
        assert not verify_message.all_ok(self._checks(self._message(text="")))

    def test_エスケープされて返っても一致とみなす(self):
        # Slack は & < > を HTML エンティティに変換して保存する。
        # **送った文字列そのものと比べると永久に不一致になる。**
        payload = make_record(text="A & B <tag>")
        message = self._message(text="A &amp; B &lt;tag&gt;")
        assert verify_message.all_ok(self._checks(message, payload))

    def test_エスケープしない生の値が返ったら落ちる(self):
        # 変換を「どちらでも通す」形にすると、照合が何も確かめなくなる。
        # Slack の仕様どおりに変換された形だけを一致とする。
        payload = make_record(text="A & B")
        assert not verify_message.all_ok(self._checks(self._message(text="A & B"), payload))

    def test_記録した投稿者と実行中のBotが違えば落ちる(self):
        # 別のアプリのトークンで確認しようとしている状態。
        # 「一致した」と出ても、それは別の Bot の投稿を見ているだけ。
        assert not verify_message.all_ok(self._checks(who=identity(user_id="U0OTHERBOT")))

    def test_記録した投稿者だけが違っても落ちる(self):
        # 上のケースは「投稿者」の項目でも落ちるので、**「実行中のBot」の項目を
        # 丸ごと消しても緑のまま**になる（2026-08-16 のミューテーションで素通りした）。
        # 読み返した投稿者と実行中の Bot は一致させ、記録だけを別の Bot にする。
        who = identity(user_id="U0OTHERBOT")
        assert not verify_message.all_ok(self._checks(self._message(user="U0OTHERBOT"), who=who))


class TestAllOk:
    def test_空なら偽(self):
        assert verify_message.all_ok([]) is False

    def test_全部OKなら真(self):
        assert verify_message.all_ok([verify_message.Check("a", True)]) is True

    def test_1つでもNGなら偽(self):
        checks = [verify_message.Check("a", True), verify_message.Check("b", False)]
        assert verify_message.all_ok(checks) is False


class TestMain:
    def test_正常系は0を返す(self, tmp_path):
        client = FakeClient()
        path = write_record(tmp_path)
        code = verify_message.main(
            [
                "--results", str(path),
                "--channel", CHANNEL,
                "--expect-text", "課題7の動作確認です",
            ],
            client_factory=lambda: (client, identity(), "xoxb-DUMMY"),
        )
        assert code == 0

    def test_手元の照合が落ちたらAPIを呼ばない(self, tmp_path):
        # ここで落ちる実行はネットワークに出す価値がない。
        client = FakeClient()
        path = write_record(tmp_path)
        code = verify_message.main(
            ["--results", str(path), "--channel", "C0OTHER", "--expect-text", "課題7の動作確認です"],
            client_factory=lambda: (client, identity(), "xoxb-DUMMY"),
        )
        assert code == 1
        assert client.calls == []

    def test_履歴のスコープが無ければ読み直さない(self, tmp_path):
        # **投稿の chat:write と読み返しの channels:history は別物。**
        # テストの Bot に両方持たせていると、必要なスコープの定数を
        # 取り違えても気づけない（2026-08-16 のミューテーションで素通りした）。
        client = FakeClient()
        path = write_record(tmp_path)
        who = slack_auth.Identity(
            team="TeamName",
            team_id="T111",
            user_id=BOT_USER,
            bot_id="B333",
            scopes=("chat:write",),
        )
        code = verify_message.main(
            ["--results", str(path), "--channel", CHANNEL, "--expect-text", "課題7の動作確認です"],
            client_factory=lambda: (client, who, "xoxb-DUMMY"),
        )
        assert code == 1
        assert client.calls == []

    def test_読み返しが食い違えば1を返す(self, tmp_path):
        client = FakeClient(
            history={"ok": True, "messages": [{"type": "message", "user": BOT_USER, "text": "ちがう", "ts": TS}]}
        )
        path = write_record(tmp_path)
        code = verify_message.main(
            ["--results", str(path), "--channel", CHANNEL, "--expect-text", "課題7の動作確認です"],
            client_factory=lambda: (client, identity(), "xoxb-DUMMY"),
        )
        assert code == 1

    def test_期待する本文は必須(self, tmp_path):
        # 結果ファイルの値で埋める逃げ道を作らない。人間が渡すから物差しになる。
        path = write_record(tmp_path)
        with pytest.raises(SystemExit):
            verify_message.main(["--results", str(path), "--channel", CHANNEL])

    def test_何を確かめていないかを書く(self, tmp_path, capsys):
        # 「すべて一致しました」だけを出す道具は、検査していない場所まで
        # 保証しているように読める（課題5の教訓）。
        client = FakeClient()
        path = write_record(tmp_path)
        verify_message.main(
            ["--results", str(path), "--channel", CHANNEL, "--expect-text", "課題7の動作確認です"],
            client_factory=lambda: (client, identity(), "xoxb-DUMMY"),
        )
        assert "確かめていない" in capsys.readouterr().out
