"""verify_meet のテスト。

照合するプログラムなので、「照合しているフリ」が通らないことを重点的に見る。
落ちることより、確かめていないのに OK が並ぶことのほうが危ない。

照合する項目は README に先に書いた7つ。実物を見る前に決めた。
  1. スペース名が一致  2. 参加リンクが一致  3. 会議コードが一致
  4. 参加リンクが会議コードと整合  5. 会議コードの形が正しい
  6. アクセス種別が指定どおり  7. 会議はまだ始まっていない

4番が要。2番と3番は「同じ値が返ってきた」しか言っていない。
サーバが両方おかしな値を返したら、2番も3番も一致する。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import verify_meet  # noqa: E402


class FakeResponse:
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
        "config": {"accessType": "TRUSTED"},
    }
    space.update(overrides)
    return space


def expected(**overrides) -> dict:
    values = {
        "expected_name": SPACE_NAME,
        "expected_uri": MEETING_URI,
        "expected_code": MEETING_CODE,
        "expected_access_type": None,
    }
    values.update(overrides)
    return values


class FakeRequest:
    def __init__(self, result, raises) -> None:
        self._result = result
        self._raises = raises

    def execute(self):
        if self._raises is not None:
            raise self._raises
        return self._result


class FakeSpaces:
    def __init__(self, get_result=None, get_raises=None) -> None:
        self.get_result = get_result if get_result is not None else a_space()
        self.get_raises = get_raises
        self.get_calls: list[dict] = []
        self.create_calls: list[dict] = []

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return FakeRequest(self.get_result, self.get_raises)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        raise AssertionError("照合は読むだけ。create を呼んではいけない")


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


def labels(checks) -> list[str]:
    return [c.label for c in checks]


def by_label(checks, needle: str):
    for check in checks:
        if needle in check.label:
            return check
    raise AssertionError(f"{needle} を含む照合項目がない: {labels(checks)}")


# ================================================================ 会議コードの形


class TestMeetingCodeShape:
    def test_正しい形を通す(self):
        assert verify_meet.is_valid_meeting_code("abc-mnop-xyz")

    def test_区切りが2つ必要(self):
        assert not verify_meet.is_valid_meeting_code("abc-mnop")

    def test_大文字は不正(self):
        assert not verify_meet.is_valid_meeting_code("ABC-mnop-xyz")

    def test_数字は不正(self):
        assert not verify_meet.is_valid_meeting_code("abc-mn0p-xyz")

    def test_空文字は不正(self):
        assert not verify_meet.is_valid_meeting_code("")

    def test_前後に余計な文字があれば不正(self):
        assert not verify_meet.is_valid_meeting_code(" abc-mnop-xyz ")

    def test_リンクをまるごと渡したら不正(self):
        assert not verify_meet.is_valid_meeting_code(MEETING_URI)

    def test_末尾に改行があれば不正(self):
        # 正規表現の $ は最後の改行の手前にも一致する。search で見ていると
        # ここだけ通ってしまう。端末からコードを写すと改行が付いてくる。
        assert not verify_meet.is_valid_meeting_code("abc-mnop-xyz\n")


# ================================================================ 照合


class TestCompare:
    def test_一致していれば全部OK(self):
        checks = verify_meet.compare_with_expected(a_space(), **expected())
        assert verify_meet.all_ok(checks)

    def test_照合項目は6つ(self):
        # アクセス種別を指定しない場合。指定すると7つになる。
        checks = verify_meet.compare_with_expected(a_space(), **expected())
        assert len(checks) == 6

    def test_アクセス種別を指定すると7つ(self):
        checks = verify_meet.compare_with_expected(
            a_space(), **expected(expected_access_type="TRUSTED")
        )
        assert len(checks) == 7

    def test_スペース名が違えばNG(self):
        checks = verify_meet.compare_with_expected(a_space(name="spaces/OTHER"), **expected())
        assert not verify_meet.all_ok(checks)
        assert not by_label(checks, "スペース名").ok

    def test_スペース名が返らなければNG(self):
        space = a_space()
        del space["name"]
        assert not by_label(verify_meet.compare_with_expected(space, **expected()), "スペース名").ok

    def test_参加リンクが違えばNG(self):
        space = a_space(meetingUri="https://meet.google.com/zzz-zzzz-zzz")
        assert not by_label(verify_meet.compare_with_expected(space, **expected()), "参加リンク").ok

    def test_参加リンクが返らなければNG(self):
        space = a_space()
        del space["meetingUri"]
        assert not by_label(verify_meet.compare_with_expected(space, **expected()), "参加リンク").ok

    def test_会議コードが違えばNG(self):
        space = a_space(meetingCode="zzz-zzzz-zzz")
        assert not by_label(verify_meet.compare_with_expected(space, **expected()), "会議コードが一致").ok

    def test_会議コードが返らなければNG(self):
        space = a_space()
        del space["meetingCode"]
        checks = verify_meet.compare_with_expected(space, **expected())
        assert not by_label(checks, "会議コードが一致").ok

    def test_リンクとコードが食い違えばNG(self):
        # 2番と3番は期待値と一致させたまま、規則だけ壊す。
        # サーバが両方おかしな値を返した状況にあたる。
        space = a_space(meetingUri="https://meet.google.com/different-code-here")
        checks = verify_meet.compare_with_expected(
            space,
            **expected(expected_uri="https://meet.google.com/different-code-here"),
        )
        assert not by_label(checks, "整合").ok

    def test_リンクとコードの整合は期待値を使わない(self):
        # 期待値が全部間違っていても、この項目だけは応答の中で完結して判定できる。
        space = a_space()
        checks = verify_meet.compare_with_expected(
            space,
            expected_name="spaces/WRONG",
            expected_uri="https://meet.google.com/wrong-wrong-wrng",
            expected_code="wrong-wrong-wrng",
            expected_access_type=None,
        )
        assert by_label(checks, "整合").ok

    def test_会議コードの形が不正ならNG(self):
        space = a_space(meetingCode="ABC123", meetingUri="https://meet.google.com/ABC123")
        checks = verify_meet.compare_with_expected(
            space, **expected(expected_code="ABC123", expected_uri="https://meet.google.com/ABC123")
        )
        assert not by_label(checks, "会議コードの形").ok

    def test_アクセス種別が違えばNG(self):
        checks = verify_meet.compare_with_expected(
            a_space(), **expected(expected_access_type="OPEN")
        )
        assert not by_label(checks, "アクセス種別").ok

    def test_アクセス種別が返らなければNG(self):
        checks = verify_meet.compare_with_expected(
            a_space(config={}), **expected(expected_access_type="TRUSTED")
        )
        assert not by_label(checks, "アクセス種別").ok

    def test_configごと無くてもNG(self):
        space = a_space()
        del space["config"]
        checks = verify_meet.compare_with_expected(
            space, **expected(expected_access_type="TRUSTED")
        )
        assert not by_label(checks, "アクセス種別").ok

    def test_会議が始まっていなければOK(self):
        # 「会議」だけで探すと「会議コードが一致」に先に当たる。別の項目を見て
        # 確かめた気になるので、この項目にしか無い語で探す。
        assert by_label(verify_meet.compare_with_expected(a_space(), **expected()), "始まって").ok

    def test_会議が始まっていればNG(self):
        # 作った直後は会議が無いはず。あるなら「作った」以外のことが起きている。
        space = a_space(activeConference={"conferenceRecord": "conferenceRecords/xyz"})
        assert not by_label(verify_meet.compare_with_expected(space, **expected()), "始まって").ok

    def test_会議の有無は会議コードとは別の項目(self):
        # 上の2つが「会議コードが一致」を見ていないことを固定する。
        checks = verify_meet.compare_with_expected(a_space(), **expected())
        assert by_label(checks, "始まって").label != by_label(checks, "会議コードが一致").label


class TestAllOk:
    def test_全部OKならTrue(self):
        assert verify_meet.all_ok([verify_meet.Check("a", True), verify_meet.Check("b", True)])

    def test_1つでもNGならFalse(self):
        assert not verify_meet.all_ok([verify_meet.Check("a", True), verify_meet.Check("b", False)])

    def test_空なら真にしない(self):
        # 照合が0件なのに「全部一致」と言わせない。
        assert not verify_meet.all_ok([])


class TestFormatChecks:
    def test_OKを印字する(self):
        assert "OK" in verify_meet.format_checks([verify_meet.Check("項目", True)])

    def test_NGをOKと印字しない(self):
        out = verify_meet.format_checks([verify_meet.Check("項目", False)])
        assert "NG" in out
        assert "OK" not in out

    def test_項目名を印字する(self):
        assert "スペース名" in verify_meet.format_checks([verify_meet.Check("スペース名", True)])

    def test_詳細を印字する(self):
        assert "詳しい話" in verify_meet.format_checks(
            [verify_meet.Check("項目", True, "詳しい話")]
        )


# ================================================================ API の呼び方


class TestFetchSpace:
    def test_spacesのgetを呼ぶ(self, service, spaces):
        verify_meet.fetch_space(service, SPACE_NAME)
        assert len(spaces.get_calls) == 1

    def test_スペース名を渡す(self, service, spaces):
        verify_meet.fetch_space(service, SPACE_NAME)
        assert spaces.get_calls[0]["name"] == SPACE_NAME

    def test_読むだけで作らない(self, service, spaces):
        verify_meet.fetch_space(service, SPACE_NAME)
        assert spaces.create_calls == []

    def test_404は見つからないと伝える(self, service, spaces):
        spaces.get_raises = make_http_error(404, "Not found")
        with pytest.raises(verify_meet.VerifyError) as excinfo:
            verify_meet.fetch_space(service, SPACE_NAME)
        assert "見つかりません" in str(excinfo.value)

    def test_エラーにスペース名を残す(self, service, spaces):
        spaces.get_raises = make_http_error(404, "Not found")
        with pytest.raises(verify_meet.VerifyError) as excinfo:
            verify_meet.fetch_space(service, SPACE_NAME)
        assert SPACE_NAME in str(excinfo.value)

    def test_403も失敗として伝える(self, service, spaces):
        spaces.get_raises = make_http_error(403, "Permission denied")
        with pytest.raises(verify_meet.VerifyError):
            verify_meet.fetch_space(service, SPACE_NAME)

    def test_ステータスコードを残す(self, service, spaces):
        spaces.get_raises = make_http_error(429, "Too many requests")
        with pytest.raises(verify_meet.VerifyError) as excinfo:
            verify_meet.fetch_space(service, SPACE_NAME)
        assert "429" in str(excinfo.value)


# ================================================================ 画面と終了コード


BASE_ARGS = [SPACE_NAME, "--meeting-uri", MEETING_URI, "--meeting-code", MEETING_CODE]


class TestParseArgs:
    def test_スペース名を受け取る(self):
        assert verify_meet.parse_args(BASE_ARGS).name == SPACE_NAME

    def test_既定の資格情報パスは相対パス(self):
        assert not Path(verify_meet.parse_args(BASE_ARGS).credentials).is_absolute()

    def test_既定のトークンパスは相対パス(self):
        assert not Path(verify_meet.parse_args(BASE_ARGS).token).is_absolute()


class TestMain:
    def test_一致したら0を返す(self, service):
        assert verify_meet.main(BASE_ARGS, service_factory=lambda args: service) == 0

    def test_照合結果を印字する(self, service, capsys):
        verify_meet.main(BASE_ARGS, service_factory=lambda args: service)
        assert "OK" in capsys.readouterr().out

    def test_食い違ったら1を返す(self, service, spaces):
        spaces.get_result = a_space(name="spaces/OTHER")
        assert verify_meet.main(BASE_ARGS, service_factory=lambda args: service) == 1

    def test_食い違いを印字する(self, service, spaces, capsys):
        spaces.get_result = a_space(name="spaces/OTHER")
        verify_meet.main(BASE_ARGS, service_factory=lambda args: service)
        assert "NG" in capsys.readouterr().out

    def test_アクセス種別を渡すと照合する(self, service, capsys):
        verify_meet.main(
            BASE_ARGS + ["--access-type", "OPEN"], service_factory=lambda args: service
        )
        assert "NG" in capsys.readouterr().out

    def test_アクセス種別が不正なら1を返す(self, service):
        assert (
            verify_meet.main(
                BASE_ARGS + ["--access-type", "PUBLIC"], service_factory=lambda args: service
            )
            == 1
        )

    def test_アクセス種別が不正なら認証もしない(self, spaces):
        called: list[int] = []

        def factory(args):
            called.append(1)
            return FakeService(spaces)

        verify_meet.main(BASE_ARGS + ["--access-type", "PUBLIC"], service_factory=factory)
        assert called == []

    def test_失敗はstderrに出す(self, service, spaces, capsys):
        spaces.get_raises = make_http_error(404, "Not found")
        verify_meet.main(BASE_ARGS, service_factory=lambda args: service)
        assert capsys.readouterr().err.strip() != ""

    def test_参加リンクを印字する(self, service, capsys):
        verify_meet.main(BASE_ARGS, service_factory=lambda args: service)
        assert MEETING_URI in capsys.readouterr().out
