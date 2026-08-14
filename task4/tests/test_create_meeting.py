"""task4/create_meeting.py のテスト。

本物の Zoom には繋がない。HTTP の POST は差し替えられるようにしてある。
偽物で確かめられるのは「呼び方」までなので、実物を1回読み返して閉じるのは
verify_meeting.py の仕事。

課題の要件は「会議を作成し、ID・パスワード・会議リンクを作成する」。
この3つが返ってこなかったときに成功にしないことが、ここでの一番の関心事。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import create_meeting  # noqa: E402


MEETING_ID = 81234567890
PASSWORD = "aB3xY9"
JOIN_URL = f"https://us05web.zoom.us/j/{MEETING_ID}?pwd=Zm9vYmFy"
START_URL = f"https://us05web.zoom.us/s/{MEETING_ID}?zak=SECRET-HOST-TOKEN"
API_BASE = "https://api.zoom.us"
TOKEN = "ACCESS-TOKEN"


def meeting(**overrides) -> dict:
    base = {
        "uuid": "abc123==",
        "id": MEETING_ID,
        "host_email": "nana@example.com",
        "topic": "打ち合わせ",
        "type": 2,
        "status": "waiting",
        "duration": 30,
        "timezone": "Asia/Tokyo",
        "password": PASSWORD,
        "join_url": JOIN_URL,
        "start_url": START_URL,
    }
    base.update(overrides)
    return base


class FakeResponse:
    def __init__(self, *, ok=True, status_code=201, payload=None, text="", raw_json=None):
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


class FakePoster:
    def __init__(self, response: FakeResponse | None = None):
        self.response = response or FakeResponse(payload=meeting())
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response

    @property
    def only(self) -> tuple[tuple, dict]:
        assert len(self.calls) == 1, f"POST は1回のはずが {len(self.calls)} 回"
        return self.calls[0]


def send(poster=None, body=None):
    return create_meeting.create_meeting(
        body or {"topic": "打ち合わせ"},
        api_base=API_BASE,
        access_token=TOKEN,
        poster=poster or FakePoster(),
    )


# ================================================================ スコープ


class TestScopes:
    def test_書き込みスコープを要求する(self):
        # 読み取りだけのスコープでは会議を作れない。値を固定しておかないと、
        # 取り違えても「テストは通るが実行すると 401」になる。
        assert create_meeting.SCOPES == ("meeting:write:meeting:admin",)


# ================================================================ 送る内容の組み立て


class TestBuildMeetingBody:
    def test_議題を入れる(self):
        assert create_meeting.build_meeting_body("打ち合わせ")["topic"] == "打ち合わせ"

    def test_予定された会議として作る(self):
        # type=2（scheduled）。1（instant）だと作った瞬間に始まってしまい、
        # 読み返して照合する余地が無くなる。
        body = create_meeting.build_meeting_body("打ち合わせ")
        assert body["type"] == create_meeting.MEETING_TYPE_SCHEDULED
        assert body["type"] == 2

    def test_議題の前後の空白を落とす(self):
        assert create_meeting.build_meeting_body("  打ち合わせ  ")["topic"] == "打ち合わせ"

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_議題が空なら失敗する(self, blank: str):
        with pytest.raises(create_meeting.MeetingError):
            create_meeting.build_meeting_body(blank)

    def test_既定の所要時間を入れる(self):
        body = create_meeting.build_meeting_body("打ち合わせ")
        assert body["duration"] == create_meeting.DEFAULT_DURATION_MINUTES

    def test_所要時間を指定できる(self):
        assert create_meeting.build_meeting_body("打ち合わせ", duration=45)["duration"] == 45

    @pytest.mark.parametrize("bad", [0, -1])
    def test_所要時間が正の数でなければ失敗する(self, bad: int):
        with pytest.raises(create_meeting.MeetingError):
            create_meeting.build_meeting_body("打ち合わせ", duration=bad)

    def test_開始時刻を指定しなければ送らない(self):
        # 空文字を送ると 400 になる。「指定しない」は「空を送る」ではない。
        assert "start_time" not in create_meeting.build_meeting_body("打ち合わせ")

    def test_開始時刻を指定できる(self):
        body = create_meeting.build_meeting_body("打ち合わせ", start_time="2026-08-20T10:00:00Z")
        assert body["start_time"] == "2026-08-20T10:00:00Z"

    def test_タイムゾーン付きの開始時刻も通す(self):
        body = create_meeting.build_meeting_body("打ち合わせ", start_time="2026-08-20T10:00:00")
        assert body["start_time"] == "2026-08-20T10:00:00"

    @pytest.mark.parametrize(
        "bad",
        [
            "2026-08-20",
            "20/08/2026 10:00",
            "明日の10時",
            # 正しい書式が「混ざっている」だけの値。部分一致で見ていると通る。
            "来週 2026-08-20T10:00:00Z に開始",
        ],
    )
    def test_開始時刻の形が違えば送る前に失敗する(self, bad: str):
        # Zoom に投げれば 400 で返ってくるが、その頃には
        # 「何が悪いのか」がエラー本文からしか分からない。手前で落とす。
        with pytest.raises(create_meeting.MeetingError) as caught:
            create_meeting.build_meeting_body("打ち合わせ", start_time=bad)
        assert "2026-" in str(caught.value)  # 期待する書式が案内に出る

    def test_タイムゾーンを指定しなければ送らない(self):
        assert "timezone" not in create_meeting.build_meeting_body("打ち合わせ")

    def test_タイムゾーンを指定できる(self):
        body = create_meeting.build_meeting_body("打ち合わせ", timezone="Asia/Tokyo")
        assert body["timezone"] == "Asia/Tokyo"

    def test_パスワードを指定しなければ送らない(self):
        # 送らなければ Zoom 側が生成する（アカウント設定が要求している場合）。
        # 空文字を送ると「パスワード無し」を指定したことになってしまう。
        assert "password" not in create_meeting.build_meeting_body("打ち合わせ")

    def test_パスワードを指定できる(self):
        body = create_meeting.build_meeting_body("打ち合わせ", password="aB3xY9")
        assert body["password"] == "aB3xY9"

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_空のパスワードを指定したら失敗する(self, blank: str):
        # --password "" は打ち間違い。黙って「未指定」に倒すと、
        # 指定したつもりの値と違うパスワードで会議が立つ。
        with pytest.raises(create_meeting.MeetingError):
            create_meeting.build_meeting_body("打ち合わせ", password=blank)

    def test_議題を指定しなければ既定の議題になる(self):
        body = create_meeting.build_meeting_body(None)
        assert body["topic"] == create_meeting.DEFAULT_TOPIC


# ================================================================ 呼び出し


class TestMeetingsUrl:
    def test_自分のユーザーの会議を作る先(self):
        assert create_meeting.meetings_url(API_BASE) == "https://api.zoom.us/v2/users/me/meetings"

    def test_末尾のスラッシュが重複しない(self):
        assert create_meeting.meetings_url("https://api.zoom.us/") == (
            "https://api.zoom.us/v2/users/me/meetings"
        )

    def test_地域別のホストにも付いていく(self):
        # トークン応答の api_url は地域ごとに変わる。決め打ちにすると
        # 別地域のアカウントで動かなくなる。
        assert create_meeting.meetings_url("https://eu01api-www4local.zoom.us") == (
            "https://eu01api-www4local.zoom.us/v2/users/me/meetings"
        )


class TestCreateMeeting:
    def test_組み立てた内容をそのまま送る(self):
        poster = FakePoster()
        body = {"topic": "打ち合わせ", "type": 2}
        send(poster, body)
        assert poster.only[1]["json"] == body

    def test_ベアラートークンを付ける(self):
        poster = FakePoster()
        send(poster)
        assert poster.only[1]["headers"]["Authorization"] == f"Bearer {TOKEN}"

    def test_タイムアウトを必ず渡す(self):
        poster = FakePoster()
        send(poster)
        assert poster.only[1]["timeout"] > 0

    def test_作成した会議を返す(self):
        assert send()["id"] == MEETING_ID

    def test_HTTPエラーはステータスコードを載せる(self):
        poster = FakePoster(
            FakeResponse(ok=False, status_code=400, payload={"code": 300, "message": "Bad request"})
        )
        with pytest.raises(create_meeting.MeetingError) as caught:
            send(poster)
        assert "400" in str(caught.value)

    def test_HTTPエラーは相手の言い分を載せる(self):
        poster = FakePoster(
            FakeResponse(
                ok=False, status_code=400, payload={"code": 300, "message": "Invalid start_time"}
            )
        )
        with pytest.raises(create_meeting.MeetingError) as caught:
            send(poster)
        assert "Invalid start_time" in str(caught.value)

    def test_権限不足は足すスコープを案内する(self):
        poster = FakePoster(
            FakeResponse(ok=False, status_code=401, payload={"code": 124, "message": "Invalid token"})
        )
        with pytest.raises(create_meeting.MeetingError) as caught:
            send(poster)
        assert create_meeting.SCOPES[0] in str(caught.value)

    def test_回数制限は1日の上限を案内する(self):
        # Zoom は「1ユーザーあたり1日100回」の作成上限を別に持つ。
        # 429 を汎用のエラーにすると、待てば直るのか設定が悪いのか分からない。
        poster = FakePoster(
            FakeResponse(ok=False, status_code=429, payload={"code": 429, "message": "Too many"})
        )
        with pytest.raises(create_meeting.MeetingError) as caught:
            send(poster)
        assert "100" in str(caught.value)

    def test_JSONでない応答でも案内つきで失敗する(self):
        poster = FakePoster(
            FakeResponse(ok=False, status_code=502, payload=None, text="<html>Bad Gateway</html>")
        )
        with pytest.raises(create_meeting.MeetingError) as caught:
            send(poster)
        assert "502" in str(caught.value)

    def test_成功応答がJSONでなければ失敗する(self):
        poster = FakePoster(FakeResponse(ok=True, status_code=201, payload=None, text="not json"))
        with pytest.raises(create_meeting.MeetingError):
            send(poster)

    def test_JSONがdictでなければ失敗する(self):
        poster = FakePoster(FakeResponse(raw_json=["unexpected"]))
        with pytest.raises(create_meeting.MeetingError):
            send(poster)


# ================================================================ 返ってきた内容の確認


class TestRequireFields:
    def test_三つ揃っていれば通る(self):
        create_meeting.require_fields(meeting())

    @pytest.mark.parametrize("key,label", [("id", "会議ID"), ("password", "パスワード"), ("join_url", "参加リンク")])
    def test_欠けていたら名前を出して失敗する(self, key: str, label: str):
        # 課題の要件がこの3つ。返ってこなかったものを「作成できた」にしない。
        broken = meeting()
        del broken[key]
        with pytest.raises(create_meeting.MeetingError) as caught:
            create_meeting.require_fields(broken)
        assert label in str(caught.value)

    @pytest.mark.parametrize("key", ["id", "password", "join_url"])
    def test_空文字は返ってきていない扱い(self, key: str):
        with pytest.raises(create_meeting.MeetingError):
            create_meeting.require_fields(meeting(**{key: ""}))

    def test_パスワードが無いときはアカウント設定を案内する(self):
        # パスコードが生成されるかはアカウント設定次第。「なぜ空なのか」が
        # 分からないと、コードを疑って時間を溶かす。
        broken = meeting()
        del broken["password"]
        with pytest.raises(create_meeting.MeetingError) as caught:
            create_meeting.require_fields(broken)
        assert "--password" in str(caught.value)


class TestCheckJoinUrl:
    def test_会議IDを含んでいれば通る(self):
        create_meeting.check_join_url(meeting())

    def test_会議IDを含まなければ失敗する(self):
        # 応答の中だけで閉じる照合。別の会議のリンクが返っていたり、
        # 組み立てを間違えていたりを、外部に問い合わせずに検出できる。
        create_meeting.check_join_url(meeting())
        with pytest.raises(create_meeting.MeetingError):
            create_meeting.check_join_url(meeting(join_url="https://us05web.zoom.us/j/99999999999"))

    def test_参加リンクの形が違えば失敗する(self):
        with pytest.raises(create_meeting.MeetingError):
            create_meeting.check_join_url(meeting(join_url=f"https://example.com/{MEETING_ID}"))


# ================================================================ 印字


class TestFormatResult:
    @staticmethod
    def _line(text: str, label: str) -> str:
        """label を含む行だけを返す。

        出力全体に対して `in` で見ると、参加リンクの中に会議IDが入っている
        ぶん、会議IDの行を消しても通ってしまう。行を特定して見る。
        """
        matched = [line for line in text.splitlines() if label in line]
        assert len(matched) == 1, f"「{label}」を含む行が {len(matched)} 行"
        return matched[0]

    def test_会議IDを出す(self):
        line = self._line(create_meeting.format_result(meeting()), "会議ID")
        assert str(MEETING_ID) in line

    def test_パスワードを出す(self):
        line = self._line(create_meeting.format_result(meeting()), "パスワード")
        assert PASSWORD in line

    def test_参加リンクを出す(self):
        line = self._line(create_meeting.format_result(meeting()), "参加リンク")
        assert JOIN_URL in line

    def test_開始用リンクは出さない(self):
        # start_url にはホスト権限のトークン（zak）が入っている。
        # 実行画面のスクリーンショットを public リポジトリに置くので、
        # 写ると他人がホストとして会議を乗っ取れる。
        assert START_URL not in create_meeting.format_result(meeting())
        assert "zak=" not in create_meeting.format_result(meeting())

    def test_議題を出す(self):
        assert "打ち合わせ" in create_meeting.format_result(meeting())


# ================================================================ 入口


class TestMain:
    @pytest.fixture(autouse=True)
    def _patch(self, monkeypatch):
        """認証と HTTP を差し替える。ここが本物だとテストが外へ出る。"""
        self.connected: list[str] = []
        self.bodies: list[dict] = []
        self.response = meeting()

        def fake_token(credentials):
            # 認証は最初のネットワーク接続。ここが呼ばれた＝外に出た。
            self.connected.append("token")
            return create_meeting.zoom_auth.AccessToken(
                value=TOKEN, expires_in=3600, scopes=create_meeting.SCOPES, api_url=API_BASE
            )

        def fake_create(body, **kwargs):
            self.connected.append("create")
            self.bodies.append(body)
            return self.response

        monkeypatch.setattr(create_meeting.zoom_auth, "read_credentials", lambda env: "C")
        monkeypatch.setattr(create_meeting.zoom_auth, "fetch_access_token", fake_token)
        monkeypatch.setattr(create_meeting.zoom_auth, "require_scopes", lambda t, s: None)
        monkeypatch.setattr(create_meeting, "create_meeting", fake_create)

    def test_成功したら0を返す(self):
        assert create_meeting.main([]) == 0

    def test_結果を印字する(self, capsys):
        create_meeting.main([])
        assert str(MEETING_ID) in capsys.readouterr().out

    def test_要件が欠けていたら1を返す(self):
        broken = meeting()
        del broken["password"]
        self.response = broken
        assert create_meeting.main([]) == 1

    def test_失敗した理由を標準エラーに出す(self, capsys):
        broken = meeting()
        del broken["password"]
        self.response = broken
        create_meeting.main([])
        assert "パスワード" in capsys.readouterr().err

    def test_引数が不正ならAPIへ繋がない(self):
        # 落ちると分かっている実行で、トークンを取りに行かない。
        assert create_meeting.main(["--duration", "0"]) == 1
        assert self.connected == []

    def test_コマンドラインの議題を送る内容に載せる(self):
        create_meeting.main(["--topic", "定例"])
        assert self.bodies[0]["topic"] == "定例"

    def test_コマンドラインのパスワードを送る内容に載せる(self):
        create_meeting.main(["--password", "aB3xY9"])
        assert self.bodies[0]["password"] == "aB3xY9"
