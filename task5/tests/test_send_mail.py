"""task5/send_mail.py のテスト。

本物の Gmail には繋がない。service は差し替えられるようにしてある。
偽物で確かめられるのは「呼び方」までなので、実物を1回読み返して閉じるのは
verify_mail.py の仕事。

この課題だけ、これまでの4課題と前提が違う。**送信は取り消せない**。
Drive のファイルも Docs も Zoom の会議も、間違えたら消してやり直せた。
メールは受け取った相手の受信箱に残る。

そこで関心事を2つに分ける。

- **送る前に確かめられること** — 宛先・件名・本文・MIME の組み立て・base64url。
  ネットワークに出ないので何度でもやり直せる。ここはこのファイルで全部見る
- **送った後にしか確かめられないこと** — サーバに載った実際の値。verify_mail.py の仕事

いちばんの関心事は「送るつもりが無い実行で、絶対に送らないこと」。
"""

from __future__ import annotations

import base64
import sys
from email import policy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import send_mail  # noqa: E402


TO = "you@example.com"
SUBJECT = "Gmail API からの送信テスト"
BODY = "こんにちは。\nこれは Gmail API から送ったメールです。"

# RFC 2822 は行末を CRLF と定める。LF で書いた本文は、ワイヤに載る時点で
# CRLF に正規化され、末尾に改行が1つ足される。
# **送った本文と読み返した本文は、文字列としては一致しない。**
# 照合側でここを正規化しないと、複数行の本文は必ず NG になる。
BODY_ON_WIRE = BODY.replace("\n", "\r\n") + "\r\n"

MESSAGE_ID = "19f2c0a1b2c3d4e5"
THREAD_ID = "19f2c0a1b2c3d4e5"


def sent_message(**overrides) -> dict:
    base = {
        "id": MESSAGE_ID,
        "threadId": THREAD_ID,
        "labelIds": ["SENT"],
    }
    base.update(overrides)
    return base


class FakeSend:
    """service.users().messages().send(...).execute() の形を真似る。"""

    def __init__(self, response: dict | None = None, error: Exception | None = None):
        self.response = sent_message() if response is None else response
        self.error = error
        self.calls: list[dict] = []

    # --- 呼ばれ方をそのまま記録する ---
    def users(self):
        return self

    def messages(self):
        return self

    def send(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        if self.error is not None:
            raise self.error
        return self.response

    @property
    def only(self) -> dict:
        assert len(self.calls) == 1, f"send は1回のはずが {len(self.calls)} 回"
        return self.calls[0]


def raw_of(body: dict) -> bytes:
    """送信 body の raw を MIME のバイト列に戻す。"""
    return send_mail.decode_raw(body["raw"])


def text_of(body: dict) -> str:
    return raw_of(body).decode("utf-8")


# ================================================================ スコープ


class TestScopes:
    def test_送信スコープを要求する(self):
        # gmail.send は送信専用。読み返しはできない（公式のスコープ一覧で確認済み）。
        # 値を固定しておかないと、取り違えても「テストは通るが実行すると 403」になる。
        assert send_mail.SCOPES == ("https://www.googleapis.com/auth/gmail.send",)

    def test_読み取りスコープを要求しない(self):
        # 送るだけのスクリプトが受信箱を読める権限を持たない。
        # 同意画面に出る権限は、スクショに写って公開される。
        assert not any("readonly" in scope for scope in send_mail.SCOPES)
        assert not any(scope == "https://mail.google.com/" for scope in send_mail.SCOPES)

    def test_トークンの既定値が送信専用になっている(self):
        # 同意フローは token.json を丸ごと置き換える（課題3で実測）。
        # 課題ごとに分けないと、課題を行き来するたび同意画面が出る。
        # さらに課題5では、送信用と読み取り用でスコープが違うため、
        # 同じ課題の中でもファイルを分ける（共有すると毎回同意画面が出る）。
        assert send_mail.DEFAULT_TOKEN == "task5/token-send.json"

    def test_トークンが課題5の下に置かれる(self):
        assert send_mail.DEFAULT_TOKEN.startswith("task5/")


# ================================================================ 宛先の検証


class TestNormalizeAddress:
    def test_前後の空白を落とす(self):
        assert send_mail.normalize_address(f"  {TO}  ") == TO

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_空なら失敗する(self, blank: str):
        with pytest.raises(send_mail.MailError) as caught:
            send_mail.normalize_address(blank)
        # 「例外が出ること」と「正しい理由で落ちること」は別物（課題2の教訓）。
        # 空の宛先は、形式チェックにも引っ掛かって落ちる。理由まで見ないと
        # 空の判定を外しても素通りする（2026-08-15 に mutate で実際に素通りした）。
        assert "空" in str(caught.value)

    def test_アットマークが無ければ失敗する(self):
        # 送信は取り消せない。宛先の形だけでも送る前に弾く。
        with pytest.raises(send_mail.MailError):
            send_mail.normalize_address("you")

    @pytest.mark.parametrize("bad", ["a@", "@example.com"])
    def test_ローカル部かドメイン部が空なら失敗する(self, bad: str):
        with pytest.raises(send_mail.MailError):
            send_mail.normalize_address(bad)

    def test_アットマークが2つあれば失敗する(self):
        with pytest.raises(send_mail.MailError):
            send_mail.normalize_address("a@b@example.com")

    @pytest.mark.parametrize("injected", ["a@example.com\nBcc: x@example.com", "a@example.com\rBcc: x@example.com"])
    def test_改行を含むアドレスを弾く(self, injected: str):
        # ヘッダインジェクション。改行から先が別のヘッダとして解釈されると、
        # 見えない宛先が足される。**送信は取り消せない**ので、ここは送る前に落とす。
        with pytest.raises(send_mail.MailError) as caught:
            send_mail.normalize_address(injected)
        # 改行は空白でもあるので、空白チェックにも引っ掛かって落ちる。
        # 理由を見ないと、改行の検査を丸ごと外しても素通りする。
        assert "改行" in str(caught.value)

    def test_内部の空白を弾く(self):
        with pytest.raises(send_mail.MailError):
            send_mail.normalize_address("a b@example.com")


# ================================================================ 件名の検証


class TestNormalizeSubject:
    def test_前後の空白を落とす(self):
        assert send_mail.normalize_subject(f"  {SUBJECT}  ") == SUBJECT

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_空なら失敗する(self, blank: str):
        # 空の件名を既定値で埋めない。埋めると「指定し忘れ」が成功として通る。
        with pytest.raises(send_mail.MailError):
            send_mail.normalize_subject(blank)

    @pytest.mark.parametrize("injected", ["件名\nBcc: x@example.com", "件名\rBcc: x@example.com"])
    def test_改行を含む件名を弾く(self, injected: str):
        with pytest.raises(send_mail.MailError):
            send_mail.normalize_subject(injected)


# ================================================================ 本文の決定


class TestResolveBody:
    def test_文字列をそのまま使う(self):
        assert send_mail.resolve_body(BODY, None) == BODY

    def test_ファイルから読む(self, tmp_path: Path):
        path = tmp_path / "body.txt"
        path.write_text(BODY, encoding="utf-8")
        assert send_mail.resolve_body(None, str(path)) == BODY

    def test_ファイルはUTF8として読む(self, tmp_path: Path):
        path = tmp_path / "body.txt"
        path.write_bytes(BODY.encode("utf-8"))
        assert send_mail.resolve_body(None, str(path)) == BODY

    def test_両方指定したら失敗する(self, tmp_path: Path):
        path = tmp_path / "body.txt"
        path.write_text(BODY, encoding="utf-8")
        with pytest.raises(send_mail.MailError):
            send_mail.resolve_body(BODY, str(path))

    def test_ファイルが無ければ失敗する(self, tmp_path: Path):
        with pytest.raises(send_mail.MailError) as caught:
            send_mail.resolve_body(None, str(tmp_path / "missing.txt"))
        # どのファイルを探したのか分からないと直しようがない。
        assert "missing.txt" in str(caught.value)

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_本文が空なら失敗する(self, blank: str):
        with pytest.raises(send_mail.MailError):
            send_mail.resolve_body(blank, None)

    def test_空のファイルでも失敗する(self, tmp_path: Path):
        path = tmp_path / "empty.txt"
        path.write_text("", encoding="utf-8")
        with pytest.raises(send_mail.MailError):
            send_mail.resolve_body(None, str(path))

    def test_どちらも指定しなければ既定の本文を使う(self):
        assert send_mail.resolve_body(None, None) == send_mail.DEFAULT_BODY

    def test_本文の前後の改行は落とさない(self):
        # 件名と違って本文の改行は意味を持つ。strip すると末尾の空行が消える。
        assert send_mail.resolve_body("本文\n\n", None) == "本文\n\n"


# ================================================================ MIME の組み立て


class TestBuildMessage:
    def test_宛先を入れる(self):
        assert send_mail.build_message(TO, SUBJECT, BODY)["To"] == TO

    def test_件名を入れる(self):
        # EmailMessage は取り出すとき RFC 2047 を解いて返す。
        assert send_mail.build_message(TO, SUBJECT, BODY)["Subject"] == SUBJECT

    def test_本文を入れる(self):
        message = send_mail.build_message(TO, SUBJECT, BODY)
        assert message.get_content() == BODY_ON_WIRE

    def test_本文の改行はCRLFに正規化される(self):
        # policy.SMTP はヘッダだけでなく本文の行末も CRLF に直す。
        # 送った文字列と読み返した文字列が一致しないのはこれが理由。
        content = send_mail.build_message(TO, SUBJECT, "1行目\n2行目").get_content()
        assert content == "1行目\r\n2行目\r\n"

    def test_末尾の改行を増やさない(self):
        # 既に改行で終わっている本文に、さらに改行を足さない。
        content = send_mail.build_message(TO, SUBJECT, "末尾改行あり\n\n").get_content()
        assert content == "末尾改行あり\r\n\r\n"

    def test_テキストとして送る(self):
        assert send_mail.build_message(TO, SUBJECT, BODY).get_content_type() == "text/plain"

    def test_文字コードはUTF8(self):
        assert send_mail.build_message(TO, SUBJECT, BODY).get_content_charset() == "utf-8"

    def test_SMTPポリシーで組む(self):
        # 既定のポリシーは行末が LF になる。RFC 2822 は CRLF を要求する。
        assert send_mail.build_message(TO, SUBJECT, BODY).policy is policy.SMTP

    def test_行末がCRLFになる(self):
        raw = send_mail.build_message(TO, SUBJECT, BODY).as_bytes()
        assert b"\r\n" in raw
        # LF 単独が残っていないこと（CRLF の LF は必ず CR を伴う）。
        assert raw.replace(b"\r\n", b"").count(b"\n") == 0

    def test_7ビットに収まる(self):
        # 既定の cte は 8bit で、生の UTF-8 バイトがそのまま本文に乗る。
        # base64 を指定して初めて 7bit クリーンになる（2026-08-15 実測）。
        raw = send_mail.build_message(TO, SUBJECT, BODY).as_bytes()
        assert all(byte < 128 for byte in raw)

    def test_本文の転送エンコードはbase64(self):
        assert send_mail.build_message(TO, SUBJECT, BODY)["Content-Transfer-Encoding"] == "base64"

    def test_日本語の件名はRFC2047で符号化される(self):
        # 生のヘッダ行を見る。照合側はこれを解いてから比べないと永久に一致しない。
        raw = send_mail.build_message(TO, SUBJECT, BODY).as_bytes()
        assert b"=?utf-8?b?" in raw

    def test_送信元を指定できる(self):
        message = send_mail.build_message(TO, SUBJECT, BODY, sender="me@example.com")
        assert message["From"] == "me@example.com"

    @pytest.mark.parametrize("bad", ["こわれた", "a@example.com\nBcc: x@example.com", "  "])
    def test_送信元が不正なら失敗する(self, bad: str):
        # From も宛先と同じヘッダ。検証を通さずに入れると、
        # ここからヘッダインジェクションが通る。
        with pytest.raises(send_mail.MailError):
            send_mail.build_message(TO, SUBJECT, BODY, sender=bad)

    def test_送信元を指定しなければFromを付けない(self):
        # 付けないと Gmail が認証済みのアカウントで埋める。偽の From を書かない。
        assert send_mail.build_message(TO, SUBJECT, BODY)["From"] is None

    def test_宛先を検証してから組む(self):
        with pytest.raises(send_mail.MailError):
            send_mail.build_message("こわれた", SUBJECT, BODY)

    def test_件名を検証してから組む(self):
        with pytest.raises(send_mail.MailError):
            send_mail.build_message(TO, "  ", BODY)


# ================================================================ base64url


class TestEncodeRaw:
    def test_元のバイト列に戻せる(self):
        message = send_mail.build_message(TO, SUBJECT, BODY)
        assert send_mail.decode_raw(send_mail.encode_raw(message)) == message.as_bytes()

    def test_base64urlを使う(self):
        # 標準 base64 の + と / は URL/JSON 上で別の意味を持つ。
        # Gmail の raw は base64url（- と _）と定められている。
        encoded = send_mail.encode_raw(send_mail.build_message(TO, SUBJECT, BODY))
        assert "+" not in encoded
        assert "/" not in encoded

    def test_標準base64とは別物であることを固定する(self):
        # + / を含むバイト列を必ず作って、両者が食い違うことを見る。
        # たまたま + / が出ないデータだと、この違いを検出できない。
        data = bytes(range(256))
        standard = base64.b64encode(data).decode("ascii")
        urlsafe = base64.urlsafe_b64encode(data).decode("ascii")
        assert standard != urlsafe
        assert send_mail.decode_raw(urlsafe) == data

    def test_パディングが欠けていても復号できる(self):
        # Gmail はパディングを落とした形で返すことがある。
        data = b"abcde"
        encoded = base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")
        assert send_mail.decode_raw(encoded) == data


class TestBuildSendBody:
    def test_rawだけを入れる(self):
        body = send_mail.build_send_body(TO, SUBJECT, BODY)
        assert set(body) == {"raw"}

    def test_rawから宛先を読み戻せる(self):
        assert f"To: {TO}" in text_of(send_mail.build_send_body(TO, SUBJECT, BODY))

    def test_rawから本文を読み戻せる(self):
        body = send_mail.build_send_body(TO, SUBJECT, BODY)
        assert send_mail.body_text_of(raw_of(body)) == BODY_ON_WIRE

    def test_rawから件名を読み戻せる(self):
        body = send_mail.build_send_body(TO, SUBJECT, BODY)
        assert send_mail.subject_of(raw_of(body)) == SUBJECT


# ================================================================ 送信


class TestSendMessage:
    def test_認証済みユーザーとして送る(self):
        service = FakeSend()
        send_mail.send_message(service, {"raw": "..."})
        assert service.only["userId"] == "me"

    def test_bodyをそのまま渡す(self):
        service = FakeSend()
        body = send_mail.build_send_body(TO, SUBJECT, BODY)
        send_mail.send_message(service, body)
        assert service.only["body"] == body

    def test_応答をそのまま返す(self):
        assert send_mail.send_message(FakeSend(), {"raw": "..."}) == sent_message()

    def test_メッセージIDが無ければ失敗する(self):
        service = FakeSend(response={"threadId": THREAD_ID})
        with pytest.raises(send_mail.MailError):
            send_mail.send_message(service, {"raw": "..."})

    def test_スレッドIDが無ければ失敗する(self):
        service = FakeSend(response={"id": MESSAGE_ID})
        with pytest.raises(send_mail.MailError):
            send_mail.send_message(service, {"raw": "..."})

    @pytest.mark.parametrize("empty", ["", "   "])
    def test_空文字を返ってきたとみなさない(self, empty: str):
        # 「返ってこなかった」を「成功した」にしない。既定値で埋めると全部 OK になる。
        service = FakeSend(response=sent_message(id=empty))
        with pytest.raises(send_mail.MailError):
            send_mail.send_message(service, {"raw": "..."})

    def test_送信できていないのに成功にしない(self):
        service = FakeSend(response={})
        with pytest.raises(send_mail.MailError) as caught:
            send_mail.send_message(service, {"raw": "..."})
        assert "メッセージID" in str(caught.value)


# ================================================================ エラーの翻訳


class FakeResp:
    def __init__(self, status: int):
        self.status = status
        self.reason = ""


def http_error(status: int, message: str):
    from googleapiclient.errors import HttpError

    content = (
        '{"error": {"code": %d, "message": %s, "status": "PERMISSION_DENIED"}}'
        % (status, _json_str(message))
    ).encode("utf-8")
    return HttpError(FakeResp(status), content)


def _json_str(value: str) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


class TestTranslateHttpError:
    def test_未有効化を見分ける(self):
        error = http_error(403, "Gmail API has not been used in project 123 before or it is disabled.")
        service = FakeSend(error=error)
        with pytest.raises(send_mail.MailError) as caught:
            send_mail.send_message(service, {"raw": "..."})
        text = str(caught.value)
        assert "Gmail API" in text and "有効" in text

    def test_権限不足を見分ける(self):
        error = http_error(403, "Request had insufficient authentication scopes.")
        service = FakeSend(error=error)
        with pytest.raises(send_mail.MailError) as caught:
            send_mail.send_message(service, {"raw": "..."})
        text = str(caught.value)
        assert send_mail.SCOPES[0] in text

    def test_未有効化と権限不足を混ぜない(self):
        # どちらも 403 で返る。混ぜると、どっちを直せばいいのか読み取れない。
        disabled = http_error(403, "Gmail API has not been used in project 123 before or it is disabled.")
        with pytest.raises(send_mail.MailError) as caught:
            send_mail.send_message(FakeSend(error=disabled), {"raw": "..."})
        assert "スコープ" not in str(caught.value)

        scopes = http_error(403, "Request had insufficient authentication scopes.")
        with pytest.raises(send_mail.MailError) as caught:
            send_mail.send_message(FakeSend(error=scopes), {"raw": "..."})
        assert "有効" not in str(caught.value)

    def test_ステータスコードを載せる(self):
        error = http_error(429, "User-rate limit exceeded.")
        with pytest.raises(send_mail.MailError) as caught:
            send_mail.send_message(FakeSend(error=error), {"raw": "..."})
        assert "429" in str(caught.value)

    def test_相手が言っている理由を載せる(self):
        error = http_error(400, "Invalid to header")
        with pytest.raises(send_mail.MailError) as caught:
            send_mail.send_message(FakeSend(error=error), {"raw": "..."})
        assert "Invalid to header" in str(caught.value)

    def test_その他の失敗でもステータスコードを載せる(self):
        # 429 は専用の分岐に吸われる。そこだけ見ていると、
        # それ以外の失敗からステータスが落ちても気づけない。
        error = http_error(400, "Invalid to header")
        with pytest.raises(send_mail.MailError) as caught:
            send_mail.send_message(FakeSend(error=error), {"raw": "..."})
        assert "400" in str(caught.value)

    def test_応答から読んだ理由を載せる(self):
        # HttpError をそのまま文字列にすると
        # 「<HttpError 400 when requesting ... returned "...">」という
        # 内部表現が利用者の画面に出る。応答の JSON から読んだ理由だけを見せる。
        error = http_error(400, "Invalid to header")
        with pytest.raises(send_mail.MailError) as caught:
            send_mail.send_message(FakeSend(error=error), {"raw": "..."})
        assert "when requesting" not in str(caught.value)

    def test_JSONでない応答でも落ちない(self):
        from googleapiclient.errors import HttpError

        # 500 番台では HTML が返ることがある。解析の失敗をそのまま外に出すと、
        # 利用者には ValueError だけが見えて原因が分からなくなる。
        error = HttpError(FakeResp(500), b"<html>Internal Server Error</html>")
        with pytest.raises(send_mail.MailError) as caught:
            send_mail.send_message(FakeSend(error=error), {"raw": "..."})
        assert "500" in str(caught.value)


# ================================================================ 画面まわり


class TestFormatPreview:
    def test_宛先と件名を出す(self):
        text = send_mail.format_preview(TO, SUBJECT, BODY)
        assert TO in text
        assert SUBJECT in text

    def test_送っていないことを明示する(self):
        # dry-run の出力が送信成功の画面と見分けられないと、
        # 「送ったつもりで送っていない」「送っていないつもりで送った」が起きる。
        assert "送信していません" in send_mail.format_preview(TO, SUBJECT, BODY)

    def test_本文を出す(self):
        assert "こんにちは。" in send_mail.format_preview(TO, SUBJECT, BODY)


class TestFormatResult:
    def test_メッセージIDを出す(self):
        # 「どこかに ID が出ている」では足りない。読み返しコマンドの中にも
        # 同じ ID が入っているので、専用の行を消しても素通りしてしまう。
        text = send_mail.format_result(sent_message(), TO, SUBJECT)
        labeled = [line for line in text.splitlines() if line.strip().startswith("メッセージID")]
        assert labeled, "メッセージID の行が無い"
        assert MESSAGE_ID in labeled[0]

    def test_宛先を出す(self):
        assert TO in send_mail.format_result(sent_message(), TO, SUBJECT)

    def test_読み返しの手順を出す(self):
        # 送っただけでは「相手に届く形で載ったか」は分からない。
        # 次にやることを画面に出しておかないと、照合が省かれる。
        text = send_mail.format_result(sent_message(), TO, SUBJECT)
        assert "verify_mail.py" in text
        assert MESSAGE_ID in text


# ================================================================ 引数


class TestParseArgs:
    def test_宛先を受け取る(self):
        assert send_mail.parse_args(["--to", TO]).to == TO

    def test_宛先は必須(self):
        with pytest.raises(SystemExit):
            send_mail.parse_args([])

    def test_件名の既定値がある(self):
        assert send_mail.parse_args(["--to", TO]).subject == send_mail.DEFAULT_SUBJECT

    def test_トークンの既定は課題5専用(self):
        assert send_mail.parse_args(["--to", TO]).token == send_mail.DEFAULT_TOKEN

    def test_資格情報の既定は相対パス(self):
        # 公開する実行画面に自宅の絶対パスを写さない。
        assert send_mail.parse_args(["--to", TO]).credentials == "credentials.json"

    def test_dry_runを受け取る(self):
        assert send_mail.parse_args(["--to", TO, "--dry-run"]).dry_run is True

    def test_dry_runの既定はFalse(self):
        assert send_mail.parse_args(["--to", TO]).dry_run is False


# ================================================================ main


class RecordingFactory:
    """service を作った回数を数える。作る＝同意画面が開きうる。"""

    def __init__(self, service=None):
        self.service = service or FakeSend()
        self.calls = 0

    def __call__(self, args):
        self.calls += 1
        return self.service


class TestMain:
    def test_送信して0を返す(self, capsys):
        factory = RecordingFactory()
        assert send_mail.main(["--to", TO], service_factory=factory) == 0
        assert MESSAGE_ID in capsys.readouterr().out

    def test_送る内容がrawに載る(self):
        factory = RecordingFactory()
        send_mail.main(["--to", TO, "--subject", SUBJECT, "--body", BODY], service_factory=factory)
        body = factory.service.only["body"]
        assert send_mail.subject_of(raw_of(body)) == SUBJECT
        assert send_mail.body_text_of(raw_of(body)) == BODY_ON_WIRE

    def test_dry_runでは送らない(self):
        # ここがこの課題でいちばん大事なテスト。
        factory = RecordingFactory()
        assert send_mail.main(["--to", TO, "--dry-run"], service_factory=factory) == 0
        assert factory.service.calls == []

    def test_dry_runでは認証もしない(self):
        # service を作る＝ブラウザの同意画面が開きうる。
        # 送らないと分かっている実行で、本人の画面を触らない。
        factory = RecordingFactory()
        send_mail.main(["--to", TO, "--dry-run"], service_factory=factory)
        assert factory.calls == 0

    def test_dry_runの出力に送信していないと書く(self, capsys):
        send_mail.main(["--to", TO, "--dry-run"], service_factory=RecordingFactory())
        assert "送信していません" in capsys.readouterr().out

    def test_宛先が不正なら認証より前に落ちる(self):
        # 落ちると分かっている実行で同意画面を出さない（課題3と同じ形）。
        factory = RecordingFactory()
        assert send_mail.main(["--to", "こわれた"], service_factory=factory) == 1
        assert factory.calls == 0

    def test_本文ファイルが無ければ認証より前に落ちる(self, tmp_path: Path):
        factory = RecordingFactory()
        code = send_mail.main(
            ["--to", TO, "--body-file", str(tmp_path / "missing.txt")],
            service_factory=factory,
        )
        assert code == 1
        assert factory.calls == 0

    def test_送信に失敗したら1を返す(self, capsys):
        factory = RecordingFactory(FakeSend(response={}))
        assert send_mail.main(["--to", TO], service_factory=factory) == 1
        assert capsys.readouterr().err.strip()

    def test_失敗の理由は標準エラーに出す(self, capsys):
        factory = RecordingFactory(FakeSend(response={}))
        send_mail.main(["--to", TO], service_factory=factory)
        captured = capsys.readouterr()
        assert "メッセージID" in captured.err
        assert captured.out == ""
