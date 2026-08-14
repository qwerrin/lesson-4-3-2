"""task4/verify_meeting.py のテスト。

作った会議を Zoom から読み返して、要件の3つ（ID・パスワード・参加リンク）が
本当にそこにあるかを照合する側。

一番の関心事は「返ってこなかった」を「一致した」にしないこと。既定値を入れると
何もかも OK になり、確かめた気持ちだけが残る。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import create_meeting  # noqa: E402
import verify_meeting  # noqa: E402


MEETING_ID = 81234567890
PASSWORD = "aB3xY9"
TOPIC = "打ち合わせ"
JOIN_URL = f"https://us05web.zoom.us/j/{MEETING_ID}?pwd=Zm9vYmFy"
API_BASE = "https://api.zoom.us"
TOKEN = "ACCESS-TOKEN"


def meeting(**overrides) -> dict:
    base = {
        "uuid": "abc123==",
        "id": MEETING_ID,
        "topic": TOPIC,
        "type": 2,
        "status": "waiting",
        "duration": 30,
        "password": PASSWORD,
        "join_url": JOIN_URL,
    }
    base.update(overrides)
    for key, value in list(base.items()):
        if value is _ABSENT:
            del base[key]
    return base


class _Absent:
    pass


_ABSENT = _Absent()


class FakeResponse:
    def __init__(self, *, ok=True, status_code=200, payload=None, text="", raw_json=None):
        self.ok = ok
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self._raw_json = raw_json

    def json(self):
        if self._raw_json is not None:
            return self._raw_json
        if self._payload is None:
            raise ValueError("No JSON object could be decoded")
        return self._payload


class FakeGetter:
    def __init__(self, response: FakeResponse | None = None):
        self.response = response or FakeResponse(payload=meeting())
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response

    @property
    def only(self) -> tuple[tuple, dict]:
        assert len(self.calls) == 1, f"GET は1回のはずが {len(self.calls)} 回"
        return self.calls[0]


def fetch(getter=None, meeting_id=MEETING_ID):
    return verify_meeting.fetch_meeting(
        meeting_id, api_base=API_BASE, access_token=TOKEN, getter=getter or FakeGetter()
    )


def checks_for(**overrides):
    expected = {
        "meeting_id": MEETING_ID,
        "expected_topic": None,
        "expected_password": None,
    }
    data = overrides.pop("meeting", None) or meeting()
    expected.update(overrides)
    return verify_meeting.build_checks(data, **expected)


def find(checks, fragment: str):
    matched = [c for c in checks if fragment in c.label]
    assert len(matched) == 1, f"「{fragment}」を含む照合が {len(matched)} 件"
    return matched[0]


# ================================================================ スコープ


class TestScopes:
    def test_読み取りだけのスコープを要求する(self):
        # 確認のための実行が、書き込み権限を持たないこと。最小権限を値で固定する。
        assert verify_meeting.SCOPES == ("meeting:read:meeting:admin",)


# ================================================================ 読み取り


class TestMeetingUrl:
    def test_会議IDを含む(self):
        assert verify_meeting.meeting_url(API_BASE, MEETING_ID) == (
            f"https://api.zoom.us/v2/meetings/{MEETING_ID}"
        )

    def test_末尾のスラッシュが重複しない(self):
        assert verify_meeting.meeting_url("https://api.zoom.us/", MEETING_ID) == (
            f"https://api.zoom.us/v2/meetings/{MEETING_ID}"
        )

    def test_地域別のホストにも付いていく(self):
        assert verify_meeting.meeting_url("https://eu01api-www4local.zoom.us", MEETING_ID) == (
            f"https://eu01api-www4local.zoom.us/v2/meetings/{MEETING_ID}"
        )


class TestFetchMeeting:
    def test_ベアラートークンを付ける(self):
        getter = FakeGetter()
        fetch(getter)
        assert getter.only[1]["headers"]["Authorization"] == f"Bearer {TOKEN}"

    def test_タイムアウトを必ず渡す(self):
        getter = FakeGetter()
        fetch(getter)
        assert getter.only[1]["timeout"] > 0

    def test_書き込みをしない(self):
        # 確認のつもりの実行が状態を変えてしまうと、やり直しがきかなくなる。
        getter = FakeGetter()
        fetch(getter)
        assert "json" not in getter.only[1] and "data" not in getter.only[1]

    def test_会議を返す(self):
        assert fetch()["id"] == MEETING_ID

    def test_見つからなければ会議IDを載せて失敗する(self):
        getter = FakeGetter(
            FakeResponse(ok=False, status_code=404, payload={"code": 3001, "message": "not found"})
        )
        with pytest.raises(create_meeting.MeetingError) as caught:
            fetch(getter)
        assert str(MEETING_ID) in str(caught.value)

    def test_読み取り失敗はステータスコードを載せる(self):
        getter = FakeGetter(
            FakeResponse(ok=False, status_code=401, payload={"code": 124, "message": "Invalid"})
        )
        with pytest.raises(create_meeting.MeetingError) as caught:
            fetch(getter)
        assert "401" in str(caught.value)

    def test_読み取り失敗は相手の言い分を載せる(self):
        getter = FakeGetter(
            FakeResponse(ok=False, status_code=400, payload={"code": 300, "message": "Bad id"})
        )
        with pytest.raises(create_meeting.MeetingError) as caught:
            fetch(getter)
        assert "Bad id" in str(caught.value)

    def test_JSONでなければ失敗する(self):
        getter = FakeGetter(FakeResponse(ok=True, status_code=200, payload=None, text="not json"))
        with pytest.raises(create_meeting.MeetingError):
            fetch(getter)

    def test_JSONがdictでなければ失敗する(self):
        getter = FakeGetter(FakeResponse(raw_json=["unexpected"]))
        with pytest.raises(create_meeting.MeetingError):
            fetch(getter)


# ================================================================ 照合


class TestBuildChecks:
    def test_全部そろっていれば全て一致(self):
        assert verify_meeting.all_ok(checks_for())

    def test_照合が空なら一致にしない(self):
        # 照合ゼロ件を all() に渡すと True になる。「何も確かめていない」が
        # 「全部一致」として出るのを防ぐ。
        assert not verify_meeting.all_ok([])

    # ---------------------------------------------------------- 会議ID

    def test_会議IDが一致する(self):
        assert find(checks_for(), "会議ID").ok

    def test_別の会議IDが返ったら不一致(self):
        assert not find(checks_for(meeting=meeting(id=99999999999)), "会議ID").ok

    def test_会議IDが返らなければ不一致(self):
        # 「返ってこなかった」を「一致した」にしない。
        assert not find(checks_for(meeting=meeting(id=_ABSENT)), "会議ID").ok

    def test_数値と文字列の会議IDを同じものとして扱う(self):
        # コマンドラインからは文字列で来る。応答は数値。
        assert find(checks_for(meeting=meeting(id=str(MEETING_ID))), "会議ID").ok

    # ---------------------------------------------------------- パスワード

    def test_パスワードが入っていれば一致(self):
        assert find(checks_for(), "パスワードが入っている").ok

    def test_パスワードが空なら不一致(self):
        assert not find(checks_for(meeting=meeting(password="")), "パスワードが入っている").ok

    def test_パスワードが返らなければ不一致(self):
        assert not find(checks_for(meeting=meeting(password=_ABSENT)), "パスワードが入っている").ok

    def test_期待するパスワードと一致する(self):
        checks = checks_for(expected_password=PASSWORD)
        assert find(checks, "パスワードが一致").ok

    def test_期待するパスワードと違えば不一致(self):
        checks = checks_for(expected_password="ちがう")
        assert not find(checks, "パスワードが一致").ok

    def test_期待するパスワードを指定しなければ照合しない(self):
        labels = [c.label for c in checks_for()]
        assert not [label for label in labels if "パスワードが一致" in label]

    def test_パスワードが返らなければ期待値と一致扱いにしない(self):
        checks = checks_for(meeting=meeting(password=_ABSENT), expected_password=PASSWORD)
        assert not find(checks, "パスワードが一致").ok

    # ---------------------------------------------------------- 参加リンク

    def test_参加リンクが入っていれば一致(self):
        assert find(checks_for(), "参加リンクが入っている").ok

    def test_参加リンクが返らなければ不一致(self):
        assert not find(checks_for(meeting=meeting(join_url=_ABSENT)), "参加リンクが入っている").ok

    def test_参加リンクが会議IDを指していれば一致(self):
        assert find(checks_for(), "参加リンクが同じ会議").ok

    def test_参加リンクが別の会議を指していたら不一致(self):
        broken = meeting(join_url="https://us05web.zoom.us/j/99999999999")
        assert not find(checks_for(meeting=broken), "参加リンクが同じ会議").ok

    def test_参加リンクの照合を応答のIDでなく要求したIDで行う(self):
        # 応答の id と join_url が「揃って」間違っている場合、応答どうしで
        # 比べるとトートロジーになって通ってしまう。要求した ID を物差しにする。
        both_wrong = meeting(id=99999999999, join_url="https://us05web.zoom.us/j/99999999999")
        assert not find(checks_for(meeting=both_wrong), "参加リンクが同じ会議").ok

    # ---------------------------------------------------------- 議題

    def test_期待する議題と一致する(self):
        assert find(checks_for(expected_topic=TOPIC), "議題").ok

    def test_期待する議題と違えば不一致(self):
        assert not find(checks_for(expected_topic="べつの会議"), "議題").ok

    def test_議題が返らなければ一致扱いにしない(self):
        checks = checks_for(meeting=meeting(topic=_ABSENT), expected_topic=TOPIC)
        assert not find(checks, "議題").ok

    def test_期待する議題を指定しなければ照合しない(self):
        assert not [c for c in checks_for() if "議題" in c.label]

    # ---------------------------------------------------------- 種別と状態

    def test_予定された会議なら一致(self):
        assert find(checks_for(), "予定された会議").ok

    def test_即時会議なら不一致(self):
        assert not find(checks_for(meeting=meeting(type=1)), "予定された会議").ok

    def test_種別が返らなければ一致扱いにしない(self):
        assert not find(checks_for(meeting=meeting(type=_ABSENT)), "予定された会議").ok

    def test_まだ始まっていなければ一致(self):
        assert find(checks_for(), "始まっていない").ok

    def test_始まっていたら不一致(self):
        assert not find(checks_for(meeting=meeting(status="started")), "始まっていない").ok

    def test_状態が返らなければ一致扱いにしない(self):
        assert not find(checks_for(meeting=meeting(status=_ABSENT)), "始まっていない").ok


# ================================================================ 印字と終了コード


class TestFormatChecks:
    def test_一致はOKと出す(self):
        assert "OK" in verify_meeting.format_checks(checks_for())

    def test_不一致はNGと出す(self):
        broken = checks_for(meeting=meeting(id=99999999999))
        assert "NG" in verify_meeting.format_checks(broken)

    def test_詳細を出す(self):
        broken = checks_for(meeting=meeting(id=99999999999))
        assert "99999999999" in verify_meeting.format_checks(broken)

    def test_照合の名前を全部出す(self):
        checks = checks_for()
        text = verify_meeting.format_checks(checks)
        for check in checks:
            assert check.label in text


class TestAllOk:
    def test_ひとつでも不一致なら全体は不一致(self):
        checks = checks_for(meeting=meeting(id=99999999999))
        assert not verify_meeting.all_ok(checks)


# ================================================================ 入口


class TestMain:
    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch):
        self.response = meeting()

        def fake_token(credentials):
            return verify_meeting.zoom_auth.AccessToken(
                value=TOKEN, expires_in=3600, scopes=verify_meeting.SCOPES, api_url=API_BASE
            )

        monkeypatch.setattr(verify_meeting.zoom_auth, "read_credentials", lambda env: "C")
        monkeypatch.setattr(verify_meeting.zoom_auth, "fetch_access_token", fake_token)
        monkeypatch.setattr(verify_meeting.zoom_auth, "require_scopes", lambda t, s: None)
        monkeypatch.setattr(
            verify_meeting, "fetch_meeting", lambda mid, **kwargs: self.response
        )

    def test_すべて一致したら0を返す(self):
        assert verify_meeting.main([str(MEETING_ID)]) == 0

    def test_食い違いがあれば1を返す(self):
        self.response = meeting(password="")
        assert verify_meeting.main([str(MEETING_ID)]) == 1

    def test_照合結果を印字する(self, capsys):
        verify_meeting.main([str(MEETING_ID)])
        assert "会議IDが一致する" in capsys.readouterr().out

    def test_期待する議題を照合に渡す(self, capsys):
        verify_meeting.main([str(MEETING_ID), "--expect-topic", "べつの会議"])
        assert "NG" in capsys.readouterr().out

    def test_読み取りに失敗したら1を返す(self, monkeypatch):
        def boom(mid, **kwargs):
            raise create_meeting.MeetingError("読めない")

        monkeypatch.setattr(verify_meeting, "fetch_meeting", boom)
        assert verify_meeting.main([str(MEETING_ID)]) == 1
