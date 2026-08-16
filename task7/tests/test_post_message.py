"""task7/post_message.py のテスト。

課題7は「指定のチャンネルにメッセージを投稿する」。作る API なので、課題1〜5と
同じく「作った物を読み返す」で閉じられる（課題6の検索とはそこが違う）。

**ただし Slack には固有の罠が2つある。**

1. **送った文字列はそのままの形では保存されない。** ``&`` ``<`` ``>`` は Slack の
   制御文字で、HTML エンティティ（``&amp;`` ``&lt;`` ``&gt;``）に変換される。
   課題5（Gmail）で件名が RFC 2047 で返ってきたのと同じ形の罠で、
   **変換を解かずに比べると永久に不一致**になる。

2. **スコープを付けただけでは投稿できない。** Bot をチャンネルに招待していないと
   ``not_in_channel`` で落ちる。権限（スコープ）と所属（チャンネル参加）は別物で、
   エラーメッセージがそれを言い分けられないと利用者は延々スコープを疑う。

期待値はリテラルで書く。定数と比べると、定数を書き換えても両辺が一緒に動いて
必ず通る（課題6のミューテーションで3件が素通りした形）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "task7"))

import post_message  # noqa: E402
from common import slack_auth  # noqa: E402


CHANNEL = "C0DUMMYCHAN"
TS = "1503435956.000247"
TOKEN = "xoxb-DUMMY-TOKEN-FOR-TESTS-not-a-real-credential"


class FakeResponse:
    def __init__(self, data, headers=None):
        self.data = dict(data)
        self.headers = headers or {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __getitem__(self, key):
        return self.data[key]


class FakeClient:
    """chat.postMessage / chat.getPermalink を記録するだけの器。"""

    def __init__(self, post=None, permalink=None, post_error=None, permalink_error=None):
        self.post_calls = []
        self.permalink_calls = []
        self._post = post
        self._permalink = permalink
        self._post_error = post_error
        self._permalink_error = permalink_error

    def chat_postMessage(self, **kwargs):
        self.post_calls.append(kwargs)
        if self._post_error is not None:
            raise self._post_error
        return FakeResponse(
            self._post
            if self._post is not None
            else {"ok": True, "channel": CHANNEL, "ts": TS}
        )

    def chat_getPermalink(self, **kwargs):
        self.permalink_calls.append(kwargs)
        if self._permalink_error is not None:
            raise self._permalink_error
        return FakeResponse(
            self._permalink
            if self._permalink is not None
            else {
                "ok": True,
                "channel": CHANNEL,
                "permalink": f"https://example.slack.com/archives/{CHANNEL}/p1503435956000247",
            }
        )


class TestEscapeForSlack:
    def test_アンパサンドを変換する(self):
        assert post_message.escape_for_slack("a & b") == "a &amp; b"

    def test_小なりを変換する(self):
        assert post_message.escape_for_slack("a < b") == "a &lt; b"

    def test_大なりを変換する(self):
        assert post_message.escape_for_slack("a > b") == "a &gt; b"

    def test_アンパサンドを最初に変換する(self):
        # **順序を間違えると二重変換になる。** `<` を先に `&lt;` にすると、
        # そのあと `&` を変換する段でこの `&` まで拾って `&amp;lt;` になる。
        # 実際に起きると、照合が「送った文字列と違う」としか言わないので原因が遠い。
        assert post_message.escape_for_slack("<") == "&lt;"

    def test_混在しても二重変換しない(self):
        assert post_message.escape_for_slack("&<>") == "&amp;&lt;&gt;"

    def test_該当しない文字はそのまま(self):
        assert post_message.escape_for_slack("こんにちは 123") == "こんにちは 123"

    def test_空文字はそのまま(self):
        assert post_message.escape_for_slack("") == ""

    def test_既にエンティティでも素通りさせない(self):
        # 利用者が `&amp;` と書いたなら、それは「& a m p ;」という文字列である。
        # Slack はこの `&` も変換するので、こちらも同じように変換する。
        assert post_message.escape_for_slack("&amp;") == "&amp;amp;"


class TestPostMessage:
    def test_チャンネルとテキストを渡す(self):
        client = FakeClient()
        post_message.post_message(client, channel=CHANNEL, text="こんにちは")
        assert client.post_calls[0]["channel"] == CHANNEL
        assert client.post_calls[0]["text"] == "こんにちは"

    def test_タイムスタンプを取り出す(self):
        client = FakeClient()
        result = post_message.post_message(client, channel=CHANNEL, text="こんにちは")
        assert result.ts == "1503435956.000247"

    def test_タイムスタンプを文字列のまま持つ(self):
        # **float にしない。** 1503435956.000247 は倍精度で表せず、
        # 数値化した時点で末尾が変わって別のメッセージを指す。
        client = FakeClient()
        result = post_message.post_message(client, channel=CHANNEL, text="こんにちは")
        assert isinstance(result.ts, str)

    def test_チャンネルを取り出す(self):
        client = FakeClient()
        result = post_message.post_message(client, channel=CHANNEL, text="こんにちは")
        assert result.channel == "C0DUMMYCHAN"

    def test_okがfalseなら落ちる(self):
        client = FakeClient(post={"ok": False, "error": "not_in_channel"})
        with pytest.raises(post_message.PostError):
            post_message.post_message(client, channel=CHANNEL, text="こんにちは")

    def test_okがfalseならtsが揃っていても落ちる(self):
        # ok=False の応答から ts を抜いた入力で試すと、ts の検査のほうで落ちてしまい
        # 「ok を見ているか」を確かめられない。**狙った1点だけ違う入力**にする。
        client = FakeClient(post={"ok": False, "error": "not_in_channel", "channel": CHANNEL, "ts": TS})
        with pytest.raises(post_message.PostError):
            post_message.post_message(client, channel=CHANNEL, text="こんにちは")

    def test_tsが無ければ落ちる(self):
        # 「返ってこなかった」を「投稿できた」にしない。
        client = FakeClient(post={"ok": True, "channel": CHANNEL})
        with pytest.raises(post_message.PostError):
            post_message.post_message(client, channel=CHANNEL, text="こんにちは")

    def test_tsが空文字でも落ちる(self):
        client = FakeClient(post={"ok": True, "channel": CHANNEL, "ts": ""})
        with pytest.raises(post_message.PostError):
            post_message.post_message(client, channel=CHANNEL, text="こんにちは")

    def test_別のチャンネルが返ってきたら落ちる(self):
        # 照合の物差しは**こちらが要求した値**から取る。応答の中だけで
        # 突き合わせるとサーバが何を返しても通ってしまう（課題4の join_url と同じ形）。
        client = FakeClient(post={"ok": True, "channel": "C0OTHER", "ts": TS})
        with pytest.raises(post_message.PostError):
            post_message.post_message(client, channel=CHANNEL, text="こんにちは")

    def test_空のテキストは送らない(self):
        client = FakeClient()
        with pytest.raises(post_message.PostError):
            post_message.post_message(client, channel=CHANNEL, text="")
        assert client.post_calls == []

    def test_空白だけのテキストも送らない(self):
        client = FakeClient()
        with pytest.raises(post_message.PostError):
            post_message.post_message(client, channel=CHANNEL, text="   ")
        assert client.post_calls == []

    def test_チャンネルが空なら送らない(self):
        client = FakeClient()
        with pytest.raises(post_message.PostError):
            post_message.post_message(client, channel="", text="こんにちは")
        assert client.post_calls == []


class TestTranslateSlackError:
    def _error(self, code):
        from slack_sdk.errors import SlackApiError

        return SlackApiError("failed", FakeResponse({"ok": False, "error": code}))

    def test_チャンネル未参加は招待を案内する(self):
        # 相手が not_in_channel と名指ししているのだから、原因候補を並べない。
        message = str(post_message.translate_slack_error(self._error("not_in_channel"), None))
        assert "招待" in message

    def test_チャンネル未参加でスコープの話をしない(self):
        # スコープは足りている。ここでスコープを疑わせると遠回りさせる。
        message = str(post_message.translate_slack_error(self._error("not_in_channel"), None))
        assert "スコープ" not in message

    def test_チャンネルが見つからない場合はIDを案内する(self):
        message = str(post_message.translate_slack_error(self._error("channel_not_found"), None))
        assert "ID" in message

    def test_スコープ不足はスコープを案内する(self):
        message = str(post_message.translate_slack_error(self._error("missing_scope"), None))
        assert "スコープ" in message

    def test_知らないエラーでもコードをそのまま出す(self):
        message = str(post_message.translate_slack_error(self._error("something_new"), None))
        assert "something_new" in message

    def test_エラーコードは常に出す(self):
        # 日本語の説明だけにすると、公式ドキュメントを引けなくなる。
        message = str(post_message.translate_slack_error(self._error("not_in_channel"), None))
        assert "not_in_channel" in message

    def test_トークンを伏せる(self):
        from slack_sdk.errors import SlackApiError

        error = SlackApiError(
            f"failed with {TOKEN}", FakeResponse({"ok": False, "error": "invalid_auth"})
        )
        message = str(post_message.translate_slack_error(error, TOKEN))
        assert TOKEN not in message


class TestFetchPermalink:
    def test_チャンネルとタイムスタンプを渡す(self):
        client = FakeClient()
        post_message.fetch_permalink(client, channel=CHANNEL, ts=TS)
        assert client.permalink_calls[0]["channel"] == CHANNEL
        assert client.permalink_calls[0]["message_ts"] == TS

    def test_リンクを返す(self):
        client = FakeClient()
        link = post_message.fetch_permalink(client, channel=CHANNEL, ts=TS)
        assert link == f"https://example.slack.com/archives/{CHANNEL}/p1503435956000247"

    def test_okがfalseなら落ちる(self):
        client = FakeClient(permalink={"ok": False, "error": "message_not_found"})
        with pytest.raises(post_message.PostError):
            post_message.fetch_permalink(client, channel=CHANNEL, ts=TS)

    def test_okがfalseならリンクが揃っていても落ちる(self):
        client = FakeClient(
            permalink={
                "ok": False,
                "error": "message_not_found",
                "permalink": "https://example.slack.com/archives/C0DUMMYCHAN/p1503435956000247",
            }
        )
        with pytest.raises(post_message.PostError):
            post_message.fetch_permalink(client, channel=CHANNEL, ts=TS)

    def test_リンクが空なら落ちる(self):
        client = FakeClient(permalink={"ok": True, "permalink": ""})
        with pytest.raises(post_message.PostError):
            post_message.fetch_permalink(client, channel=CHANNEL, ts=TS)


class TestBuildRecord:
    def _identity(self):
        return slack_auth.Identity(
            team="TeamName", team_id="T111", user_id="U222", bot_id="B333", scopes=("chat:write",)
        )

    def test_送ったテキストを記録する(self):
        record = post_message.build_record(
            identity=self._identity(),
            channel=CHANNEL,
            text="こんにちは",
            ts=TS,
            permalink="https://example.slack.com/archives/C0DUMMYCHAN/p1503435956000247",
        )
        assert record["text"] == "こんにちは"

    def test_投稿者を記録する(self):
        # auth.test が答えた user_id。読み返したメッセージの user と突き合わせる物差し。
        record = post_message.build_record(
            identity=self._identity(),
            channel=CHANNEL,
            text="こんにちは",
            ts=TS,
            permalink="https://example.slack.com/archives/C0DUMMYCHAN/p1503435956000247",
        )
        assert record["posted_by"] == "U222"

    def test_タイムスタンプを文字列で記録する(self):
        record = post_message.build_record(
            identity=self._identity(),
            channel=CHANNEL,
            text="こんにちは",
            ts=TS,
            permalink="https://example.slack.com/archives/C0DUMMYCHAN/p1503435956000247",
        )
        assert record["ts"] == "1503435956.000247"
        assert isinstance(record["ts"], str)

    def test_JSONにしても精度が落ちない(self):
        # json.dumps が数値に変換しないこと。文字列で持っていれば起きないが、
        # 実装が int/float に触った瞬間に末尾が変わるので、往復で固定しておく。
        record = post_message.build_record(
            identity=self._identity(),
            channel=CHANNEL,
            text="こんにちは",
            ts=TS,
            permalink="https://example.slack.com/archives/C0DUMMYCHAN/p1503435956000247",
        )
        restored = json.loads(json.dumps(record, ensure_ascii=False))
        assert restored["ts"] == "1503435956.000247"


class TestFormatScopeReport:
    def test_足りていれば足りない旨を出さない(self):
        check = slack_auth.ScopeCheck(known=True, missing=(), granted=("chat:write",))
        assert "足りません" not in post_message.format_scope_report(check)

    def test_足りなければ名指しする(self):
        check = slack_auth.ScopeCheck(
            known=True, missing=("channels:history",), granted=("chat:write",)
        )
        assert "channels:history" in post_message.format_scope_report(check)

    def test_確認できなかったことを確認できたことにしない(self):
        # **ここが「検査していない場所まで保証しているように読める」の対策。**
        # 読めなかったときに「足りています」と出すと、実際には足りていない場合に
        # 利用者が原因を探せなくなる。
        check = slack_auth.ScopeCheck(known=False, missing=("chat:write",), granted=None)
        report = post_message.format_scope_report(check)
        assert "確認できません" in report

    def test_確認できなかった場合と足りない場合で文面が違う(self):
        unknown = post_message.format_scope_report(
            slack_auth.ScopeCheck(known=False, missing=("chat:write",), granted=None)
        )
        missing = post_message.format_scope_report(
            slack_auth.ScopeCheck(known=True, missing=("chat:write",), granted=())
        )
        assert unknown != missing


class TestMain:
    def _identity(self, scopes=("chat:write",)):
        return slack_auth.Identity(
            team="TeamName", team_id="T111", user_id="U222", bot_id="B333", scopes=scopes
        )

    def test_正常系は0を返す(self, tmp_path, monkeypatch, capsys):
        client = FakeClient()
        out = tmp_path / "results.json"
        code = post_message.main(
            ["--channel", CHANNEL, "--text", "こんにちは", "--json-out", str(out)],
            client_factory=lambda: (client, self._identity(), TOKEN),
        )
        assert code == 0

    def test_結果ファイルを書く(self, tmp_path):
        client = FakeClient()
        out = tmp_path / "results.json"
        post_message.main(
            ["--channel", CHANNEL, "--text", "こんにちは", "--json-out", str(out)],
            client_factory=lambda: (client, self._identity(), TOKEN),
        )
        saved = json.loads(out.read_text(encoding="utf-8"))
        assert saved["ts"] == "1503435956.000247"

    def test_スコープが確実に足りなければ投稿しない(self, tmp_path):
        # known=True で missing がある＝読めたうえで足りない。送る前に止める。
        client = FakeClient()
        code = post_message.main(
            ["--channel", CHANNEL, "--text", "こんにちは"],
            client_factory=lambda: (client, self._identity(scopes=("channels:history",)), TOKEN),
        )
        assert code == 1
        assert client.post_calls == []

    def test_スコープが不明でも投稿は試す(self, tmp_path, capsys):
        # 読めなかっただけで足りていないとは限らない。ここで止めると、
        # ヘッダが返らない環境で課題そのものが実行できなくなる。
        # ただし「確認できていない」ことは必ず画面に出す。
        client = FakeClient()
        code = post_message.main(
            ["--channel", CHANNEL, "--text", "こんにちは"],
            client_factory=lambda: (client, self._identity(scopes=None), TOKEN),
        )
        assert code == 0
        assert len(client.post_calls) == 1
        assert "確認できません" in capsys.readouterr().out

    def test_投稿に失敗したら1を返す(self):
        from slack_sdk.errors import SlackApiError

        client = FakeClient(
            post_error=SlackApiError("boom", FakeResponse({"ok": False, "error": "not_in_channel"}))
        )
        code = post_message.main(
            ["--channel", CHANNEL, "--text", "こんにちは"],
            client_factory=lambda: (client, self._identity(), TOKEN),
        )
        assert code == 1

    def test_リンクを画面に出す(self, capsys):
        client = FakeClient()
        post_message.main(
            ["--channel", CHANNEL, "--text", "こんにちは"],
            client_factory=lambda: (client, self._identity(), TOKEN),
        )
        assert "https://example.slack.com/archives/" in capsys.readouterr().out

    def test_結果ファイルを指定しなければ書かない(self, tmp_path, monkeypatch):
        # 既定のパスへ勝手に書くと、前回の結果を黙って上書きする。
        monkeypatch.chdir(tmp_path)
        client = FakeClient()
        post_message.main(
            ["--channel", CHANNEL, "--text", "こんにちは"],
            client_factory=lambda: (client, self._identity(), TOKEN),
        )
        assert list(tmp_path.iterdir()) == []
