"""common/zoom_auth のテスト。

Google（common/google_auth.py）とは認証の形がまるごと違う。同意画面もリフレッシュ
トークンも無く、client_id / client_secret から毎回トークンを取り直す。だから共通化
せず別モジュールにした。共有モジュールにテストが無いと壊しても誰も気づかないので、
google_auth と同じくモジュールと同時に書く。

本物の Zoom には繋がない。HTTP の POST は差し替えられるようにしてある。
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common import zoom_auth  # noqa: E402


ACCOUNT_ID = "AID"
CLIENT_ID = "CID"
CLIENT_SECRET = "S3CRET-do-not-leak"

WRITE = "meeting:write:meeting:admin"
READ = "meeting:read:meeting:admin"

# 既定のベース URL とは違う値を使う。応答と同じ値をテストデータにすると、
# 「応答を読まずに既定値を返す」実装でも通ってしまい、たまたま一致していることに
# 気づけない。Zoom は地域ごとに別のホストを返すので、その形を使う。
REGIONAL_API_URL = "https://eu01api-www4local.zoom.us"

ENV = {
    "ZOOM_ACCOUNT_ID": ACCOUNT_ID,
    "ZOOM_CLIENT_ID": CLIENT_ID,
    "ZOOM_CLIENT_SECRET": CLIENT_SECRET,
}


def credentials() -> "zoom_auth.ZoomCredentials":
    return zoom_auth.ZoomCredentials(ACCOUNT_ID, CLIENT_ID, CLIENT_SECRET)


class FakeResponse:
    """requests.Response のうち、こちらが読む属性だけを持つ偽物。"""

    def __init__(self, *, ok=True, status_code=200, payload=None, text="", raw_json=None):
        self.ok = ok
        self.status_code = status_code
        self.text = text
        self._payload = payload
        # dict 以外が返る状況を作るための抜け道。payload=None は
        # 「JSON として読めない」を意味するので、それとは区別する。
        self._raw_json = raw_json

    def json(self):
        if self._raw_json is not None:
            return self._raw_json
        if self._payload is None:
            # Zoom は落ちたときに JSON ではなく HTML を返すことがある。
            raise ValueError("No JSON object could be decoded")
        return self._payload


class FakePoster:
    """呼ばれ方を記録する POST。実際に外へ出ないことの証拠にもなる。"""

    def __init__(self, response: FakeResponse | None = None):
        self.response = response or FakeResponse(
            payload={
                "access_token": "TOKEN",
                "token_type": "bearer",
                "expires_in": 3600,
                "scope": f"{WRITE} {READ}",
                "api_url": REGIONAL_API_URL,
            }
        )
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response

    @property
    def only(self) -> tuple[tuple, dict]:
        assert len(self.calls) == 1, f"POST は1回のはずが {len(self.calls)} 回"
        return self.calls[0]


# ================================================================ 環境変数の読み取り


class TestReadCredentials:
    def test_三つ揃っていれば読める(self):
        got = zoom_auth.read_credentials(ENV)
        assert (got.account_id, got.client_id, got.client_secret) == (
            ACCOUNT_ID,
            CLIENT_ID,
            CLIENT_SECRET,
        )

    @pytest.mark.parametrize(
        "missing", ["ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET"]
    )
    def test_欠けている変数名を名指しする(self, missing: str):
        # 「認証に失敗しました」だけだと、3つのうちどれを直せばいいか分からない。
        env = {k: v for k, v in ENV.items() if k != missing}
        with pytest.raises(zoom_auth.AuthError) as caught:
            zoom_auth.read_credentials(env)
        assert missing in str(caught.value)

    def test_複数欠けていたら全部名指しする(self):
        # 1つ直しては再実行、を3回繰り返させない。
        with pytest.raises(zoom_auth.AuthError) as caught:
            zoom_auth.read_credentials({"ZOOM_ACCOUNT_ID": ACCOUNT_ID})
        message = str(caught.value)
        assert "ZOOM_CLIENT_ID" in message
        assert "ZOOM_CLIENT_SECRET" in message

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_空文字や空白だけは未設定と同じ扱い(self, blank: str):
        # .env に `ZOOM_CLIENT_SECRET=` とだけ書いた状態。変数は「ある」ので、
        # 存在チェックだけだと素通りして、Zoom から 400 が返ってくるまで気づけない。
        env = dict(ENV, ZOOM_CLIENT_SECRET=blank)
        with pytest.raises(zoom_auth.AuthError) as caught:
            zoom_auth.read_credentials(env)
        assert "ZOOM_CLIENT_SECRET" in str(caught.value)

    def test_前後の空白を落とす(self):
        # .env や Marketplace の画面からコピペすると末尾に空白や改行が付く。
        # 付いたまま base64 に入れると 401 になり、値は合っているのに原因が見えない。
        env = {k: f"  {v}\n" for k, v in ENV.items()}
        got = zoom_auth.read_credentials(env)
        assert got.client_secret == CLIENT_SECRET

    def test_シークレットの値をエラーに出さない(self):
        # public リポジトリなので、実行画面のスクリーンショットに載ると事故になる。
        env = {k: v for k, v in ENV.items() if k != "ZOOM_ACCOUNT_ID"}
        with pytest.raises(zoom_auth.AuthError) as caught:
            zoom_auth.read_credentials(env)
        assert CLIENT_SECRET not in str(caught.value)


# ================================================================ Basic 認証ヘッダ


class TestBasicAuthHeader:
    def test_既知の値と一致する(self):
        # 期待値は手計算で直書きする。実装と同じ式で作ると、式ごと間違えていても通る。
        # base64("abc:def") == "YWJjOmRlZg=="
        assert zoom_auth.basic_auth_header("abc", "def") == "Basic YWJjOmRlZg=="

    def test_コロンで連結している(self):
        header = zoom_auth.basic_auth_header(CLIENT_ID, CLIENT_SECRET)
        decoded = base64.b64decode(header.removeprefix("Basic ")).decode("utf-8")
        assert decoded == f"{CLIENT_ID}:{CLIENT_SECRET}"

    def test_長い値でも改行が入らない(self):
        # base64 のエンコーダによっては76文字で折り返す。ヘッダに改行が入ると
        # リクエストそのものが壊れる。
        header = zoom_auth.basic_auth_header("x" * 100, "y" * 100)
        assert "\n" not in header


# ================================================================ トークン取得


class TestFetchAccessToken:
    def test_公式のトークンURLへ送る(self):
        poster = FakePoster()
        zoom_auth.fetch_access_token(credentials(), poster=poster)
        args, kwargs = poster.only
        url = args[0] if args else kwargs["url"]
        assert url == "https://zoom.us/oauth/token"

    def test_grant_typeはaccount_credentials(self):
        poster = FakePoster()
        zoom_auth.fetch_access_token(credentials(), poster=poster)
        assert poster.only[1]["params"]["grant_type"] == "account_credentials"

    def test_account_idを送る(self):
        poster = FakePoster()
        zoom_auth.fetch_access_token(credentials(), poster=poster)
        assert poster.only[1]["params"]["account_id"] == ACCOUNT_ID

    def test_Basic認証ヘッダを付ける(self):
        poster = FakePoster()
        zoom_auth.fetch_access_token(credentials(), poster=poster)
        expected = zoom_auth.basic_auth_header(CLIENT_ID, CLIENT_SECRET)
        assert poster.only[1]["headers"]["Authorization"] == expected

    def test_タイムアウトを必ず渡す(self):
        # 渡し忘れると requests は無限に待つ。CI でも手元でも「固まった」に見える。
        poster = FakePoster()
        zoom_auth.fetch_access_token(credentials(), poster=poster)
        assert poster.only[1]["timeout"] > 0

    def test_アクセストークンを取り出す(self):
        token = zoom_auth.fetch_access_token(credentials(), poster=FakePoster())
        assert token.value == "TOKEN"

    def test_有効期限を取り出す(self):
        token = zoom_auth.fetch_access_token(credentials(), poster=FakePoster())
        assert token.expires_in == 3600

    def test_スコープを空白で割る(self):
        # Zoom は "a b c" の1文字列で返す。割らないと後段の権限チェックが
        # 「1個の長いスコープ」を相手にして必ず不一致になる。
        token = zoom_auth.fetch_access_token(credentials(), poster=FakePoster())
        assert token.scopes == (WRITE, READ)

    def test_api_urlを取り出す(self):
        # 応答に入っている地域別のホストを、そのまま返すこと。
        token = zoom_auth.fetch_access_token(credentials(), poster=FakePoster())
        assert token.api_url == REGIONAL_API_URL

    def test_api_urlが無ければ既定のベースURLになる(self):
        poster = FakePoster(FakeResponse(payload={"access_token": "T", "expires_in": 3600}))
        token = zoom_auth.fetch_access_token(credentials(), poster=poster)
        assert token.api_url == zoom_auth.DEFAULT_API_URL

    def test_scopeが無くても落ちない(self):
        poster = FakePoster(FakeResponse(payload={"access_token": "T", "expires_in": 3600}))
        token = zoom_auth.fetch_access_token(credentials(), poster=poster)
        assert token.scopes == ()

    def test_アクセストークンが無ければ失敗する(self):
        # 「返ってこなかった」を「成功した」にしない。既定値を入れると
        # 空文字のトークンで API を叩き、401 の原因がここだと分からなくなる。
        poster = FakePoster(FakeResponse(payload={"expires_in": 3600}))
        with pytest.raises(zoom_auth.AuthError):
            zoom_auth.fetch_access_token(credentials(), poster=poster)

    def test_アクセストークンが空文字でも失敗する(self):
        poster = FakePoster(FakeResponse(payload={"access_token": "", "expires_in": 3600}))
        with pytest.raises(zoom_auth.AuthError):
            zoom_auth.fetch_access_token(credentials(), poster=poster)

    def test_HTTPエラーはステータスコードを載せる(self):
        poster = FakePoster(
            FakeResponse(ok=False, status_code=401, payload={"reason": "Invalid client"})
        )
        with pytest.raises(zoom_auth.AuthError) as caught:
            zoom_auth.fetch_access_token(credentials(), poster=poster)
        assert "401" in str(caught.value)

    def test_HTTPエラーは本文の理由を載せる(self):
        # Zoom は reason に具体的な理由を書いてくる。こちらで候補を並べ直さず、
        # 相手の言い分をそのまま見せる。
        poster = FakePoster(
            FakeResponse(ok=False, status_code=400, payload={"reason": "Invalid account_id"})
        )
        with pytest.raises(zoom_auth.AuthError) as caught:
            zoom_auth.fetch_access_token(credentials(), poster=poster)
        assert "Invalid account_id" in str(caught.value)

    def test_アプリが未有効化なら有効化を案内する(self):
        # 2026-08-14 に実際に踏んだ応答。Zoom は英語で
        # "The app has been disabled by the developer" とだけ返す。
        # スコープを追加すると有効化が外れるので、初回以外でも起きる。
        poster = FakePoster(
            FakeResponse(
                ok=False,
                status_code=400,
                payload={"reason": "The app has been disabled by the developer"},
            )
        )
        with pytest.raises(zoom_auth.AuthError) as caught:
            zoom_auth.fetch_access_token(credentials(), poster=poster)
        assert "Activate" in str(caught.value)

    def test_別の400には有効化の案内を出さない(self):
        # 原因が違うのに同じ案内を出すと、合っている設定を疑わせて遠回りさせる。
        poster = FakePoster(
            FakeResponse(ok=False, status_code=400, payload={"reason": "Invalid account_id"})
        )
        with pytest.raises(zoom_auth.AuthError) as caught:
            zoom_auth.fetch_access_token(credentials(), poster=poster)
        assert "Activate" not in str(caught.value)

    def test_エラーにシークレットを混ぜない(self):
        poster = FakePoster(FakeResponse(ok=False, status_code=401, payload={"reason": "nope"}))
        with pytest.raises(zoom_auth.AuthError) as caught:
            zoom_auth.fetch_access_token(credentials(), poster=poster)
        assert CLIENT_SECRET not in str(caught.value)

    def test_JSONでない応答でも案内つきで失敗する(self):
        # 502 のときなどに HTML が返る。json() が ValueError を投げて
        # そのまま外に出ると、何が起きたのか読めない例外になる。
        poster = FakePoster(
            FakeResponse(ok=False, status_code=502, payload=None, text="<html>Bad Gateway</html>")
        )
        with pytest.raises(zoom_auth.AuthError) as caught:
            zoom_auth.fetch_access_token(credentials(), poster=poster)
        assert "502" in str(caught.value)

    def test_成功応答がJSONでなければ失敗する(self):
        poster = FakePoster(FakeResponse(ok=True, status_code=200, payload=None, text="not json"))
        with pytest.raises(zoom_auth.AuthError):
            zoom_auth.fetch_access_token(credentials(), poster=poster)

    def test_JSONがdictでなければ失敗する(self):
        # 障害時に配列や文字列が返ることがある。dict だと決めつけて .get を呼ぶと
        # AttributeError になり、利用者には読めない例外がそのまま出る。
        poster = FakePoster(FakeResponse(raw_json=["unexpected"]))
        with pytest.raises(zoom_auth.AuthError):
            zoom_auth.fetch_access_token(credentials(), poster=poster)


# ================================================================ 権限の確認


class TestRequireScopes:
    def _token(self, scopes) -> "zoom_auth.AccessToken":
        return zoom_auth.AccessToken(
            value="T", expires_in=3600, scopes=tuple(scopes), api_url="https://api.zoom.us"
        )

    def test_揃っていれば通る(self):
        zoom_auth.require_scopes(self._token([WRITE, READ]), [WRITE, READ])

    def test_余分な権限があっても通る(self):
        zoom_auth.require_scopes(self._token([WRITE, READ, "user:read:user:admin"]), [WRITE])

    def test_足りない権限を名指しする(self):
        # Marketplace でスコープを足し忘れたときに、どれを足せばいいかが出る。
        with pytest.raises(zoom_auth.AuthError) as caught:
            zoom_auth.require_scopes(self._token([WRITE]), [WRITE, READ])
        assert READ in str(caught.value)

    def test_足りている権限は名指ししない(self):
        with pytest.raises(zoom_auth.AuthError) as caught:
            zoom_auth.require_scopes(self._token([WRITE]), [WRITE, READ])
        assert WRITE not in str(caught.value)

    def test_前方一致では通さない(self):
        # 持っているのが meeting:read:meeting:admin でも、要求が
        # meeting:read:meeting なら別のスコープ。部分一致で判定すると
        # 「足りている」と誤って報告し、実際に叩いた時点で 401 になる。
        with pytest.raises(zoom_auth.AuthError) as caught:
            zoom_auth.require_scopes(self._token([READ]), ["meeting:read:meeting"])
        assert "meeting:read:meeting" in str(caught.value)

    def test_要求が空なら失敗する(self):
        # google_auth と同じ思想。既定値を持たせていないので、呼ぶ側が渡し忘れると
        # 空で来る。空のまま通すと「権限を確認したつもり」だけが残る。
        with pytest.raises(zoom_auth.AuthError):
            zoom_auth.require_scopes(self._token([WRITE]), [])
