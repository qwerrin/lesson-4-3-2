"""task5/verify_mail.py のテスト。

送ったメールを Gmail から読み返して、こちらが指定した内容と突き合わせる。

**照合の物差しは応答の外から取る。** 応答の値どうしを比べると、サーバが
おかしな値を返したときトートロジーで通ってしまう（課題4の教訓）。
期待値は必ずコマンドラインから渡す。

この課題に固有の罠が2つある。

1. **日本語の件名は RFC 2047 で符号化されて返る**（=?utf-8?b?...?=）。
   解かずに比べると永久に一致しない
2. **本文の改行は CRLF になって返る**。LF で送った文字列とは一致しない
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import send_mail  # noqa: E402
import verify_mail  # noqa: E402


MESSAGE_ID = "19f2c0a1b2c3d4e5"
THREAD_ID = "19f2c0a1b2c3d4e5"
TO = "you@example.com"
FROM = "you@example.com"
SUBJECT = "Gmail API からの送信テスト"
BODY = "こんにちは。\nこれは Gmail API から送ったメールです。"

# サーバに載る形。RFC 2822 は行末を CRLF と定める。
BODY_ON_WIRE = BODY.replace("\n", "\r\n") + "\r\n"

# 日本語を含む件名は、生のヘッダでは RFC 2047 の符号化になる。
SUBJECT_ENCODED = "Gmail API =?utf-8?b?44GL44KJ44Gu6YCB5L+h44OG44K544OI?="


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def headers(**overrides) -> list[dict]:
    base = {"To": TO, "From": FROM, "Subject": SUBJECT_ENCODED, "Message-ID": "<abc@mail.example.com>"}
    base.update(overrides)
    return [{"name": name, "value": value} for name, value in base.items() if value is not None]


def message(**overrides) -> dict:
    base = {
        "id": MESSAGE_ID,
        "threadId": THREAD_ID,
        "labelIds": ["SENT"],
        "payload": {
            "mimeType": "text/plain",
            "headers": headers(),
            "body": {"size": len(BODY_ON_WIRE), "data": b64(BODY_ON_WIRE)},
        },
    }
    base.update(overrides)
    return base


def multipart_message() -> dict:
    return {
        "id": MESSAGE_ID,
        "threadId": THREAD_ID,
        "labelIds": ["SENT"],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": headers(),
            "body": {"size": 0},
            "parts": [
                {"mimeType": "text/plain", "body": {"size": 1, "data": b64(BODY_ON_WIRE)}},
                {"mimeType": "text/html", "body": {"size": 1, "data": b64("<p>ignored</p>")}},
            ],
        },
    }


class FakeGet:
    """service.users().messages().get(...).execute() の形を真似る。"""

    def __init__(self, response: dict | None = None, error: Exception | None = None):
        self.response = message() if response is None else response
        self.error = error
        self.calls: list[dict] = []

    def users(self):
        return self

    def messages(self):
        return self

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        if self.error is not None:
            raise self.error
        return self.response

    @property
    def only(self) -> dict:
        assert len(self.calls) == 1, f"get は1回のはずが {len(self.calls)} 回"
        return self.calls[0]


def checks_for(msg: dict | None = None, **overrides):
    kwargs = {
        "message_id": MESSAGE_ID,
        "expected_to": TO,
        "expected_subject": SUBJECT,
        "expected_body": BODY,
    }
    kwargs.update(overrides)
    return verify_mail.build_checks(message() if msg is None else msg, **kwargs)


def check_named(checks, fragment: str):
    found = [c for c in checks if fragment in c.label]
    assert found, f"{fragment!r} を含む照合項目が無い（実際: {[c.label for c in checks]}）"
    return found[0]


# ================================================================ スコープ


class TestScopes:
    def test_読み取りスコープを要求する(self):
        assert verify_mail.SCOPES == ("https://www.googleapis.com/auth/gmail.readonly",)

    def test_送信スコープを要求しない(self):
        # 確認するだけのスクリプトが送信権限を持たない。
        assert not any("gmail.send" in scope for scope in verify_mail.SCOPES)
        assert "https://mail.google.com/" not in verify_mail.SCOPES

    def test_送信側とはスコープが別(self):
        # gmail.send では messages.get が通らない（公式のスコープ一覧で確認済み）。
        assert verify_mail.SCOPES != send_mail.SCOPES

    def test_トークンの既定値が読み取り専用になっている(self):
        assert verify_mail.DEFAULT_TOKEN == "task5/token-verify.json"

    def test_送信側とトークンのファイルを分ける(self):
        # スコープが違うので共有できない。load_credentials は権限の足りない
        # トークンを捨てて取り直すため、同じファイルを使うと
        # 送る→読む→送る のたびに同意画面が出る。
        assert verify_mail.DEFAULT_TOKEN != send_mail.DEFAULT_TOKEN


# ================================================================ ヘッダの取り出し


class TestHeaderValue:
    def test_名前で引ける(self):
        assert verify_mail.header_value(message()["payload"], "To") == TO

    def test_大文字小文字を区別しない(self):
        # ヘッダ名の大小は保証されない。決め打ちで比べると取りこぼす。
        payload = {"headers": [{"name": "TO", "value": TO}]}
        assert verify_mail.header_value(payload, "To") == TO
        assert verify_mail.header_value(payload, "to") == TO

    def test_無ければNone(self):
        assert verify_mail.header_value(message()["payload"], "Bcc") is None

    def test_ヘッダが無くても落ちない(self):
        assert verify_mail.header_value({}, "To") is None


# ================================================================ 件名の復号


class TestDecodeSubject:
    def test_RFC2047を解く(self):
        assert verify_mail.decode_subject(SUBJECT_ENCODED) == SUBJECT

    def test_ASCIIだけの件名はそのまま(self):
        assert verify_mail.decode_subject("Hello") == "Hello"

    def test_全体が符号化されていても解ける(self):
        encoded = "=?utf-8?b?" + base64.b64encode("日本語だけ".encode()).decode() + "?="
        assert verify_mail.decode_subject(encoded) == "日本語だけ"

    def test_Noneは空文字にする(self):
        assert verify_mail.decode_subject(None) == ""


# ================================================================ 本文の取り出し


class TestExtractBody:
    def test_単一パートから読む(self):
        assert verify_mail.extract_body(message()["payload"]) == BODY_ON_WIRE

    def test_マルチパートのテキストを読む(self):
        # コンテナ型は body.data が空で、parts[] の中に本文がある。
        assert verify_mail.extract_body(multipart_message()["payload"]) == BODY_ON_WIRE

    def test_HTMLではなくプレーンテキストを選ぶ(self):
        assert "<p>" not in verify_mail.extract_body(multipart_message()["payload"])

    def test_HTMLが先にあってもプレーンテキストを選ぶ(self):
        # parts の順序は保証されない。先頭を本文とみなす実装だと、
        # text/plain が2番目にある応答で HTML を本文として照合してしまう。
        # 偽の応答で text/plain を先に置いていると、この間違いを検出できない。
        payload = multipart_message()["payload"]
        payload["parts"] = list(reversed(payload["parts"]))
        assert verify_mail.extract_body(payload) == BODY_ON_WIRE

    def test_本文が無ければNone(self):
        assert verify_mail.extract_body({"mimeType": "text/plain", "body": {"size": 0}}) is None

    def test_パディングが無くても復号できる(self):
        # Gmail はパディング（=）を落とした形で返す。
        payload = {"mimeType": "text/plain", "body": {"data": b64("abcde")}}
        assert verify_mail.extract_body(payload) == "abcde"

    def test_添付として分離された本文は読まない(self):
        # attachmentId があるとき、data は空で中身は別リクエストにある。
        payload = {"mimeType": "text/plain", "body": {"attachmentId": "x", "size": 10}}
        assert verify_mail.extract_body(payload) is None


# ================================================================ 改行の正規化


class TestNormalizeNewlines:
    def test_CRLFをLFにする(self):
        assert verify_mail.normalize_newlines("a\r\nb") == "a\nb"

    def test_CR単独もLFにする(self):
        assert verify_mail.normalize_newlines("a\rb") == "a\nb"

    def test_LFはそのまま(self):
        assert verify_mail.normalize_newlines("a\nb") == "a\nb"

    def test_内容は変えない(self):
        assert verify_mail.normalize_newlines("あいう") == "あいう"


# ================================================================ 照合


class TestBuildChecks:
    def test_全部一致すればOK(self):
        assert verify_mail.all_ok(checks_for())

    def test_照合はゼロ件にならない(self):
        assert checks_for()

    # ---------------- メッセージID

    def test_メッセージIDを照合する(self):
        assert check_named(checks_for(), "メッセージID").ok

    def test_メッセージIDが違えばNG(self):
        assert not verify_mail.all_ok(checks_for(message(id="別のID")))

    def test_メッセージIDの物差しは応答の外から取る(self):
        # 応答の id どうしを比べるとトートロジーになる。
        # こちらが要求した ID を物差しにする（課題4の教訓）。
        checks = checks_for(message(id="サーバが返した別のID"), message_id=MESSAGE_ID)
        assert not check_named(checks, "メッセージID").ok

    def test_メッセージIDが返らなければNG(self):
        checks = checks_for(message(id=None))
        assert not check_named(checks, "メッセージID").ok

    # ---------------- 宛先

    def test_宛先を照合する(self):
        assert check_named(checks_for(), "宛先").ok

    def test_宛先が違えばNG(self):
        assert not verify_mail.all_ok(checks_for(expected_to="someone@example.com"))

    def test_食い違いは期待値と実際の両方を出す(self):
        # どちらか片方だと、何と何が違うのか分からず直せない。
        check = check_named(checks_for(expected_to="someone@example.com"), "宛先")
        assert "someone@example.com" in check.detail
        assert TO in check.detail

    def test_返ってこなかったことを詳細に書く(self):
        # 「一致しなかった」と「そもそも返ってこなかった」は原因が別。
        # str(None) と比べて不一致になるだけだと、この区別が画面に出ない。
        msg = message()
        msg["payload"]["headers"] = headers(To=None)
        assert "返ってきませんでした" in check_named(checks_for(msg), "宛先").detail

    def test_期待値が実際の一部でも一致にしない(self):
        # 部分一致で判定すると、宛先の一部が合っているだけで通ってしまう。
        # you@example.com に対して、末尾が1文字欠けたような取り違え。
        assert not verify_mail.all_ok(checks_for(expected_to=TO[:-1]))

    def test_宛先が返らなければNG(self):
        # 「返ってこなかった」を「一致した」にしない。
        msg = message()
        msg["payload"]["headers"] = headers(To=None)
        assert not check_named(checks_for(msg), "宛先").ok

    # ---------------- 件名

    def test_件名を照合する(self):
        assert check_named(checks_for(), "件名").ok

    def test_符号化された件名を解いてから比べる(self):
        # ここが解けていないと、日本語の件名は永久に一致しない。
        assert check_named(checks_for(), "件名").ok

    def test_件名が違えばNG(self):
        assert not verify_mail.all_ok(checks_for(expected_subject="別の件名"))

    def test_件名が返らなければNG(self):
        msg = message()
        msg["payload"]["headers"] = headers(Subject=None)
        assert not check_named(checks_for(msg), "件名").ok

    # ---------------- 本文

    def test_本文を照合する(self):
        assert check_named(checks_for(), "本文").ok

    def test_改行コードの違いでは落とさない(self):
        # 送るときは LF、載るときは CRLF。正規化しないと複数行は必ず NG になる。
        assert check_named(checks_for(expected_body=BODY), "本文").ok

    def test_期待値にCRLFが混ざっていても一致する(self):
        # 正規化は実際の値だけでなく期待値にも掛ける。
        # 片側だけだと、CRLF で書かれた本文ファイルを渡したときに落ちる。
        assert check_named(checks_for(expected_body=BODY.replace("\n", "\r\n")), "本文").ok

    def test_本文が違えばNG(self):
        assert not verify_mail.all_ok(checks_for(expected_body="別の本文"))

    def test_本文の一部が欠けていればNG(self):
        assert not verify_mail.all_ok(checks_for(expected_body="こんにちは。"))

    def test_本文が返らなければNG(self):
        msg = message()
        msg["payload"] = {"mimeType": "text/plain", "headers": headers(), "body": {"size": 0}}
        assert not check_named(checks_for(msg), "本文").ok

    def test_マルチパートでも照合できる(self):
        assert verify_mail.all_ok(checks_for(multipart_message()))

    # ---------------- ラベル

    def test_SENTラベルを照合する(self):
        assert check_named(checks_for(), "送信済み").ok

    def test_SENTラベルが無ければNG(self):
        # 下書きのまま残っていると DRAFT になる。送信済みかはラベルで見る。
        assert not verify_mail.all_ok(checks_for(message(labelIds=["DRAFT"])))

    def test_ラベルが返らなければNG(self):
        assert not check_named(checks_for(message(labelIds=None)), "送信済み").ok

    # ---------------- 送信元・スレッド

    def test_送信元があることを照合する(self):
        assert check_named(checks_for(), "送信元").ok

    def test_送信元が無ければNG(self):
        msg = message()
        msg["payload"]["headers"] = headers(From=None)
        assert not check_named(checks_for(msg), "送信元").ok

    def test_スレッドIDを照合する(self):
        assert check_named(checks_for(), "スレッドID").ok

    def test_スレッドIDが返らなければNG(self):
        assert not check_named(checks_for(message(threadId=None)), "スレッドID").ok

    @pytest.mark.parametrize("empty", ["", "   "])
    def test_空文字を返ってきたとみなさない(self, empty: str):
        assert not check_named(checks_for(message(threadId=empty)), "スレッドID").ok


class TestAllOk:
    def test_照合ゼロ件を全部一致にしない(self):
        # 空のリストに all() を掛けると True になる。
        # 「何も確かめていない」が「全部一致」に化ける形。
        assert not verify_mail.all_ok([])

    def test_1つでもNGならFalse(self):
        checks = [verify_mail.Check("A", True), verify_mail.Check("B", False)]
        assert not verify_mail.all_ok(checks)


class TestFormatChecks:
    def test_OKとNGを書き分ける(self):
        text = verify_mail.format_checks(
            [verify_mail.Check("A", True), verify_mail.Check("B", False)]
        )
        assert "OK" in text
        assert "NG" in text

    def test_詳細を出す(self):
        text = verify_mail.format_checks([verify_mail.Check("A", False, "期待 x / 実際 y")])
        assert "期待 x / 実際 y" in text

    def test_項目名を出す(self):
        assert "宛先" in verify_mail.format_checks([verify_mail.Check("宛先", True)])


# ================================================================ 読み取り


class TestFetchMessage:
    def test_認証済みユーザーとして読む(self):
        service = FakeGet()
        verify_mail.fetch_message(service, MESSAGE_ID)
        assert service.only["userId"] == "me"

    def test_指定したメッセージを読む(self):
        service = FakeGet()
        verify_mail.fetch_message(service, MESSAGE_ID)
        assert service.only["id"] == MESSAGE_ID

    def test_本文まで取れる形式で読む(self):
        # metadata では本文が返らない。full を指定する。
        service = FakeGet()
        verify_mail.fetch_message(service, MESSAGE_ID)
        assert service.only["format"] == "full"

    def test_書き込みになる引数を渡さない(self):
        # 読み取りのつもりで body を送ると、相手によっては書き換えになる。
        service = FakeGet()
        verify_mail.fetch_message(service, MESSAGE_ID)
        assert "body" not in service.only

    def test_応答をそのまま返す(self):
        assert verify_mail.fetch_message(FakeGet(), MESSAGE_ID) == message()

    def test_読み取りに失敗したら理由を伝える(self):
        from googleapiclient.errors import HttpError

        class Resp:
            status = 404
            reason = ""

        error = HttpError(Resp(), b'{"error": {"message": "Not Found"}}')
        with pytest.raises(verify_mail.VerifyError) as caught:
            verify_mail.fetch_message(FakeGet(error=error), MESSAGE_ID)
        text = str(caught.value)
        assert "404" in text
        # どのメッセージを読もうとしたのか分からないと直しようがない。
        assert MESSAGE_ID in text


# ================================================================ 引数


class TestParseArgs:
    def test_メッセージIDを受け取る(self):
        args = verify_mail.parse_args(["--message-id", MESSAGE_ID, "--to", TO])
        assert args.message_id == MESSAGE_ID

    def test_メッセージIDは必須(self):
        with pytest.raises(SystemExit):
            verify_mail.parse_args(["--to", TO])

    def test_宛先は必須(self):
        # 期待値を応答から取らないための必須引数。
        with pytest.raises(SystemExit):
            verify_mail.parse_args(["--message-id", MESSAGE_ID])

    def test_件名の既定は送信側と同じ(self):
        args = verify_mail.parse_args(["--message-id", MESSAGE_ID, "--to", TO])
        assert args.subject == send_mail.DEFAULT_SUBJECT

    def test_トークンの既定は課題5専用(self):
        args = verify_mail.parse_args(["--message-id", MESSAGE_ID, "--to", TO])
        assert args.token == verify_mail.DEFAULT_TOKEN


# ================================================================ main


class RecordingFactory:
    def __init__(self, service=None):
        self.service = service or FakeGet()
        self.calls = 0

    def __call__(self, args):
        self.calls += 1
        return self.service


def main_args(**overrides) -> list[str]:
    """一致する組み合わせを既定にする。

    本文を渡さないと send_mail の既定本文が期待値になり、
    偽の応答（BODY）と食い違う。**宛先の違いを見たいテストが
    本文の違いで落ちる**ので、変えたい項目以外は必ず揃える。
    """
    args = {"--message-id": MESSAGE_ID, "--to": TO, "--subject": SUBJECT, "--body": BODY}
    args.update(overrides)
    return [item for pair in args.items() for item in pair]


class TestMain:
    def test_一致すれば0を返す(self, capsys):
        code = verify_mail.main(main_args(), service_factory=RecordingFactory())
        assert code == 0
        assert "OK" in capsys.readouterr().out
        assert "NG" not in capsys.readouterr().out

    def test_宛先が違えば1を返す(self, capsys):
        code = verify_mail.main(
            main_args(**{"--to": "someone@example.com"}),
            service_factory=RecordingFactory(),
        )
        assert code == 1
        out = capsys.readouterr().out
        assert "NG" in out
        # 落ちた理由が宛先であることまで見る。
        # 見ないと、別の項目が壊れていても同じように通ってしまう。
        ng = [line for line in out.splitlines() if "[NG]" in line]
        assert len(ng) == 1 and "宛先" in ng[0]

    def test_件名が違えば1を返す(self, capsys):
        code = verify_mail.main(
            main_args(**{"--subject": "別の件名"}), service_factory=RecordingFactory()
        )
        assert code == 1
        ng = [line for line in capsys.readouterr().out.splitlines() if "[NG]" in line]
        assert len(ng) == 1 and "件名" in ng[0]

    def test_本文が違えば1を返す(self, capsys):
        code = verify_mail.main(
            main_args(**{"--body": "別の本文"}), service_factory=RecordingFactory()
        )
        assert code == 1
        ng = [line for line in capsys.readouterr().out.splitlines() if "[NG]" in line]
        assert len(ng) == 1 and "本文" in ng[0]

    def test_照合の一覧を出す(self, capsys):
        verify_mail.main(main_args(), service_factory=RecordingFactory())
        out = capsys.readouterr().out
        for fragment in ("メッセージID", "宛先", "件名", "本文", "送信済み", "送信元", "スレッドID"):
            assert fragment in out

    def test_読み取りに失敗したら1を返す(self, capsys):
        from googleapiclient.errors import HttpError

        class Resp:
            status = 404
            reason = ""

        factory = RecordingFactory(FakeGet(error=HttpError(Resp(), b"{}")))
        code = verify_mail.main(main_args(), service_factory=factory)
        assert code == 1
        assert capsys.readouterr().err.strip()

    def test_本文をファイルから渡せる(self, tmp_path: Path):
        path = tmp_path / "body.txt"
        path.write_text(BODY, encoding="utf-8")
        code = verify_mail.main(
            ["--message-id", MESSAGE_ID, "--to", TO, "--subject", SUBJECT, "--body-file", str(path)],
            service_factory=RecordingFactory(),
        )
        assert code == 0

    def test_宛先が不正なら読み取りより前に落ちる(self):
        # 落ちると分かっている実行で同意画面を出さない。
        factory = RecordingFactory()
        code = verify_mail.main(
            main_args(**{"--to": "こわれた"}), service_factory=factory
        )
        assert code == 1
        assert factory.calls == 0
