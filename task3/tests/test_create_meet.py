"""create_meet のテスト。

課題1（ドライブ）・課題2（ドキュメント）と同じ3層に分けた。本物の Google には繋がない。
認証が本人の手作業でしか通せないので、偽の service で「呼び方」までを固定する。

1. 送る前に決めること … アクセス種別の解釈・リクエストの組み立て・リンクの組み立て
2. API の呼び方       … spaces().create に何を渡したかを記録して照合する
3. 画面と終了コード   … main が結果を「印字する」ことと、失敗時に 1 を返すことを別々に見る

期待値は課題文（Meet の API で会議の作成または参加リンクの生成）と
Meet API v2 の定義から書いた。実装を読んで数字を合わせにいかない。

定義から取った事実:
  - spaces.create は POST v2/spaces、必要なスコープは meetings.space.created だけ
  - meetingUri は "https://meet.google.com/" に meetingCode を続けたもの
  - meetingCode の形式は [a-z]+-[a-z]+-[a-z]+
  - config.accessType は OPEN / TRUSTED / RESTRICTED
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import create_meet  # noqa: E402


# ---------------------------------------------------------------- テスト用の偽物


class FakeResponse:
    """HttpError が読む最小限のレスポンス。status と reason しか見られない。"""

    def __init__(self, status: int, reason: str = "") -> None:
        self.status = status
        self.reason = reason


def make_http_error(status: int, message: str):
    from googleapiclient.errors import HttpError

    content = json.dumps({"error": {"code": status, "message": message}}).encode("utf-8")
    return HttpError(FakeResponse(status, "Error"), content, uri="https://example.invalid")


SPACE_NAME = "spaces/jQCFfuBOdN5z"
MEETING_CODE = "abc-mnop-xyz"
MEETING_URI = "https://meet.google.com/abc-mnop-xyz"


def a_space(**overrides) -> dict:
    space = {
        "name": SPACE_NAME,
        "meetingUri": MEETING_URI,
        "meetingCode": MEETING_CODE,
        "config": {"accessType": "TRUSTED", "entryPointAccess": "ALL"},
    }
    space.update(overrides)
    return space


class FakeRequest:
    def __init__(self, result, raises) -> None:
        self._result = result
        self._raises = raises

    def execute(self):
        if self._raises is not None:
            raise self._raises
        return self._result


class FakeSpaces:
    """spaces().create / get の呼ばれ方を記録するだけの偽物。"""

    def __init__(self, create_result=None, get_result=None, create_raises=None, get_raises=None):
        self.create_result = create_result if create_result is not None else a_space()
        self.get_result = get_result if get_result is not None else a_space()
        self.create_raises = create_raises
        self.get_raises = get_raises
        self.create_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return FakeRequest(self.create_result, self.create_raises)

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return FakeRequest(self.get_result, self.get_raises)


class FakeService:
    def __init__(self, spaces: FakeSpaces) -> None:
        self._spaces = spaces

    def spaces(self):
        return self._spaces


@pytest.fixture
def spaces() -> FakeSpaces:
    return FakeSpaces()


@pytest.fixture
def service(spaces: FakeSpaces) -> FakeService:
    return FakeService(spaces)


# ================================================================ 1. 送る前


class TestResolveAccessType:
    def test_未指定ならNoneになる(self):
        assert create_meet.resolve_access_type(None) is None

    def test_小文字を大文字に直す(self):
        assert create_meet.resolve_access_type("open") == "OPEN"

    def test_大文字はそのまま(self):
        assert create_meet.resolve_access_type("RESTRICTED") == "RESTRICTED"

    def test_前後の空白を落とす(self):
        assert create_meet.resolve_access_type("  trusted  ") == "TRUSTED"

    def test_定義に無い値は拒否する(self):
        with pytest.raises(create_meet.MeetError):
            create_meet.resolve_access_type("PUBLIC")

    def test_空文字は拒否する(self):
        with pytest.raises(create_meet.MeetError):
            create_meet.resolve_access_type("")

    def test_未指定を意味する値も拒否する(self):
        # ACCESS_TYPE_UNSPECIFIED を送るのは「指定しない」とは別物。
        # 通すと、指定したのに既定値になったのか区別できなくなる。
        with pytest.raises(create_meet.MeetError):
            create_meet.resolve_access_type("ACCESS_TYPE_UNSPECIFIED")


class TestBuildSpaceBody:
    def test_未指定なら空のbodyになる(self):
        # config を空で送らない。課題1で parents=[] が「親を消す」意味に
        # 取られたのと同じで、空を送ることは未指定と同じではない。
        assert create_meet.build_space_body(None) == {}

    def test_指定したらconfigに入れる(self):
        assert create_meet.build_space_body("OPEN") == {"config": {"accessType": "OPEN"}}

    def test_configの中はaccessTypeだけ(self):
        body = create_meet.build_space_body("TRUSTED")
        assert list(body["config"].keys()) == ["accessType"]


class TestMeetingUri:
    def test_会議コードからリンクを組む(self):
        assert create_meet.meeting_uri_for(MEETING_CODE) == MEETING_URI

    def test_基底URLは定数(self):
        assert create_meet.MEET_BASE_URL == "https://meet.google.com/"

    def test_コードが空なら組み立てない(self):
        with pytest.raises(create_meet.MeetError):
            create_meet.meeting_uri_for("")


class TestScopes:
    def test_作成に必要な最小スコープを要求する(self):
        assert create_meet.SCOPES == ("https://www.googleapis.com/auth/meetings.space.created",)

    def test_読み取り専用スコープでは作成できないので使わない(self):
        assert "meetings.space.readonly" not in " ".join(create_meet.SCOPES)

    def test_既定では設定スコープを要求しない(self):
        # 設定を変えないなら要らない。要らない権限は取らない。
        assert "meetings.space.settings" not in " ".join(create_meet.SCOPES)

    def test_アクセス種別を指定しないなら作成スコープだけ(self):
        assert create_meet.scopes_for(None) == create_meet.SCOPES

    def test_アクセス種別を指定するときだけ設定スコープを足す(self):
        # config を設定するには meetings.space.settings が要る（公式ガイド）。
        # 指定したときだけ要求することで、普段の実行では権限を広げない。
        assert create_meet.scopes_for("OPEN") == (
            "https://www.googleapis.com/auth/meetings.space.created",
            "https://www.googleapis.com/auth/meetings.space.settings",
        )

    def test_設定スコープは作成スコープの後ろに足す(self):
        # 作成スコープが先頭にある前提のメッセージがあるので順番を固定する。
        assert create_meet.scopes_for("TRUSTED")[0] == create_meet.SCOPES[0]


class TestDefaultServiceFactory:
    def _run(self, monkeypatch, access_type):
        captured: list[list[str]] = []

        def fake_load(credentials_path, token_path, scopes, **kwargs):
            captured.append(list(scopes))
            return object()

        monkeypatch.setattr(create_meet.google_auth, "load_credentials", fake_load)
        monkeypatch.setattr(create_meet, "build_service", lambda credentials: "SERVICE")
        args = create_meet.parse_args(
            [] if access_type is None else ["--access-type", access_type]
        )
        create_meet._default_service_factory(args)
        return captured[0]

    def test_指定なしなら作成スコープだけで同意を取る(self, monkeypatch):
        assert self._run(monkeypatch, None) == list(create_meet.SCOPES)

    def test_指定ありなら設定スコープも含めて同意を取る(self, monkeypatch):
        assert "https://www.googleapis.com/auth/meetings.space.settings" in self._run(
            monkeypatch, "RESTRICTED"
        )

    def test_不正な値ならAPIへ繋ぐ前に落とす(self, monkeypatch):
        monkeypatch.setattr(
            create_meet.google_auth, "load_credentials", lambda *a, **k: pytest.fail("認証してはいけない")
        )
        args = create_meet.parse_args(["--access-type", "PUBLIC"])
        with pytest.raises(create_meet.MeetError):
            create_meet._default_service_factory(args)


# ================================================================ 2. API の呼び方


class TestCreateSpace:
    def test_spacesのcreateを呼ぶ(self, service, spaces):
        create_meet.create_space(service, {})
        assert len(spaces.create_calls) == 1

    def test_bodyをそのまま渡す(self, service, spaces):
        body = {"config": {"accessType": "OPEN"}}
        create_meet.create_space(service, body)
        assert spaces.create_calls[0]["body"] == body

    def test_未指定なら空のbodyを渡す(self, service, spaces):
        create_meet.create_space(service, {})
        assert spaces.create_calls[0]["body"] == {}

    def test_作成結果を返す(self, service):
        assert create_meet.create_space(service, {}) == a_space()

    def test_nameが返らなければ失敗にする(self, service, spaces):
        spaces.create_result = {"meetingUri": MEETING_URI, "meetingCode": MEETING_CODE}
        with pytest.raises(create_meet.MeetError):
            create_meet.create_space(service, {})

    def test_meetingUriが返らなければ失敗にする(self, service, spaces):
        spaces.create_result = {"name": SPACE_NAME, "meetingCode": MEETING_CODE}
        with pytest.raises(create_meet.MeetError):
            create_meet.create_space(service, {})

    def test_meetingCodeが返らなければ失敗にする(self, service, spaces):
        spaces.create_result = {"name": SPACE_NAME, "meetingUri": MEETING_URI}
        with pytest.raises(create_meet.MeetError):
            create_meet.create_space(service, {})

    def test_空文字のnameも失敗にする(self, service, spaces):
        spaces.create_result = a_space(name="")
        with pytest.raises(create_meet.MeetError):
            create_meet.create_space(service, {})


class TestErrors:
    def test_API未有効化なら有効化の手順を案内する(self, service, spaces):
        # 「有効」の字があるだけでは足りない。原因を並べただけの汎用メッセージにも
        # 「API が有効になっていない」は入っている。どこで有効にするかまで見る。
        spaces.create_raises = make_http_error(
            403, "Google Meet API has not been used in project 123 before or it is disabled"
        )
        with pytest.raises(create_meet.MeetError) as excinfo:
            create_meet.create_space(service, {})
        message = str(excinfo.value)
        assert "有効" in message
        assert "ライブラリ" in message

    def test_権限不足なら要求しているスコープを名指しする(self, service, spaces):
        # 「権限」の字があるだけでは足りない。汎用メッセージにも「権限が足りない」は
        # 入っている。この分岐でしか出せない情報（実際のスコープ）で見分ける。
        spaces.create_raises = make_http_error(403, "Request had insufficient authentication scopes")
        with pytest.raises(create_meet.MeetError) as excinfo:
            create_meet.create_space(service, {})
        message = str(excinfo.value)
        assert create_meet.SCOPES[0] in message
        assert "原因の候補" not in message
        assert "ライブラリ" not in message

    def test_使えない項目を指定した403はその項目を名指しする(self, service, spaces):
        # 実機で出た応答（2026-08-14）。個人アカウントでは spaces.create は通るのに、
        # config.accessType を指定すると 403 になる。Google は項目名まで教えてくれる。
        # 汎用の「原因の候補が3つ」に埋めると、この情報が読み手に届かない。
        spaces.create_raises = make_http_error(
            403, "updateAccessType is not available to the user."
        )
        with pytest.raises(create_meet.MeetError) as excinfo:
            create_meet.create_space(service, {"config": {"accessType": "RESTRICTED"}})
        message = str(excinfo.value)
        assert "アクセス種別" in message
        assert "--access-type" in message
        assert "原因の候補" not in message

    def test_使えない項目の403に応答の原文を残す(self, service, spaces):
        spaces.create_raises = make_http_error(
            403, "updateAccessType is not available to the user."
        )
        with pytest.raises(create_meet.MeetError) as excinfo:
            create_meet.create_space(service, {})
        assert "updateAccessType" in str(excinfo.value)

    def test_403はアカウントの種類にも触れる(self, service, spaces):
        # 個人アカウントで通るか未確認。403 の原因候補として残す。
        # 「アカウント」だけで探すと「別のアカウントで試す」に当たってしまうので、
        # 原因として挙げている側にしか無い言い回しで見る。
        spaces.create_raises = make_http_error(403, "Permission denied")
        with pytest.raises(create_meet.MeetError) as excinfo:
            create_meet.create_space(service, {})
        assert "アカウントの種類" in str(excinfo.value)

    def test_404はスペースが見つからないと伝える(self, service, spaces):
        spaces.create_raises = make_http_error(404, "Not found")
        with pytest.raises(create_meet.MeetError) as excinfo:
            create_meet.create_space(service, {})
        assert "見つかりません" in str(excinfo.value)

    def test_ステータスコードを残す(self, service, spaces):
        spaces.create_raises = make_http_error(429, "Too many requests")
        with pytest.raises(create_meet.MeetError) as excinfo:
            create_meet.create_space(service, {})
        assert "429" in str(excinfo.value)

    def test_知らないステータスでも説明文を残す(self, service, spaces):
        spaces.create_raises = make_http_error(503, "Backend unavailable")
        with pytest.raises(create_meet.MeetError) as excinfo:
            create_meet.create_space(service, {})
        assert "Backend unavailable" in str(excinfo.value)


# ================================================================ 3. 画面と終了コード


class TestFormatResult:
    def test_スペース名を出す(self):
        assert SPACE_NAME in create_meet.format_result(a_space())

    def test_参加リンクを出す(self):
        assert MEETING_URI in create_meet.format_result(a_space())

    def test_会議コードを出す(self):
        assert MEETING_CODE in create_meet.format_result(a_space())

    def test_アクセス種別を出す(self):
        assert "TRUSTED" in create_meet.format_result(a_space())


class TestParseArgs:
    def test_既定の資格情報パスは相対パス(self):
        args = create_meet.parse_args([])
        assert not Path(args.credentials).is_absolute()

    def test_既定のトークンパスは相対パス(self):
        args = create_meet.parse_args([])
        assert not Path(args.token).is_absolute()

    def test_アクセス種別の既定はNone(self):
        assert create_meet.parse_args([]).access_type is None

    def test_アクセス種別を受け取る(self):
        assert create_meet.parse_args(["--access-type", "OPEN"]).access_type == "OPEN"


class TestMain:
    def test_成功したら0を返す(self, service, capsys):
        assert create_meet.main([], service_factory=lambda args: service) == 0

    def test_結果を印字する(self, service, capsys):
        create_meet.main([], service_factory=lambda args: service)
        assert MEETING_URI in capsys.readouterr().out

    def test_失敗したら1を返す(self, service, spaces, capsys):
        spaces.create_raises = make_http_error(403, "Permission denied")
        assert create_meet.main([], service_factory=lambda args: service) == 1

    def test_失敗はstderrに出す(self, service, spaces, capsys):
        spaces.create_raises = make_http_error(403, "Permission denied")
        create_meet.main([], service_factory=lambda args: service)
        assert capsys.readouterr().err.strip() != ""

    def test_アクセス種別が不正なら1を返す(self, service):
        assert create_meet.main(["--access-type", "PUBLIC"], service_factory=lambda args: service) == 1

    def test_アクセス種別が不正ならAPIを呼ばない(self, service, spaces):
        create_meet.main(["--access-type", "PUBLIC"], service_factory=lambda args: service)
        assert spaces.create_calls == []

    def test_アクセス種別が不正なら認証もしない(self, spaces):
        # service を作る＝認証で本人のブラウザが開く。落ちると分かっている実行で
        # 同意画面を出さない。課題2でここが穴になった。
        called: list[int] = []

        def factory(args):
            called.append(1)
            return FakeService(spaces)

        create_meet.main(["--access-type", "PUBLIC"], service_factory=factory)
        assert called == []

    def test_指定したアクセス種別が送られる(self, service, spaces):
        create_meet.main(["--access-type", "open"], service_factory=lambda args: service)
        assert spaces.create_calls[0]["body"] == {"config": {"accessType": "OPEN"}}
