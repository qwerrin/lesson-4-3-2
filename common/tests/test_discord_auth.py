"""common/discord_auth.py のテスト。

Discord の Bot Token は、これまでに書いた4つの認証のどれとも違う。

===================== ================================================
何を使うか             どういう相手か
===================== ================================================
google_auth（OAuth）  **本人のデータ**を触る。同意画面 → token.json → リフレッシュ
zoom_auth（S2S）      アカウントの権限で動く。同意画面なし・毎回取り直し
youtube_auth（キー）  **公開データ**を読むだけ。認可する相手がいない
slack_auth（Bot）     **アプリ自身**が動く。インストール時に1回発行、期限なし
discord_auth（Bot）   アプリ自身が動く。**加えて「認証しない経路」が併存する**
===================== ================================================

Discord で固有なのは次の3つである。

1. **資格情報が2種類あって、片方は URL そのもの。**
   Webhook URL は ``Authorization`` ヘッダを付けずに投稿できる。つまり
   **URL を知っている人は誰でもそのチャンネルに投稿できる**。Bot Token と
   危険度は同じなのに「URL」という見た目のせいで軽く扱われる。
   だから redact() は**両方**を伏せられる形にする。

2. **User-Agent が必須。**
   公式リファレンスは ``DiscordBot ($url, $versionNumber)`` の形を要求し、
   妥当な User-Agent が無いリクエストは「may be blocked and return a
   Cloudflare error」と書いている。requests の既定 UA では要件を満たさない。
   **付け忘れても手元のテストは通る**ので、ヘッダの検査をテストに置く。

3. **付与された権限を問い合わせる安い方法が無い。**
   Slack は ``x-oauth-scopes`` ヘッダ（記載は無いが実測では返る）があった。
   Discord のチャンネル権限はロールと上書きの計算結果で、1回の API では出ない。
   → **「権限を確認した」という顔をしない。** 代わりにエラーコードを正確に
   訳し分ける。特に ``READ_MESSAGE_HISTORY`` 不足は**エラーにならず 0 件**に
   なるので、そこは呼び出し側の照合で捕まえる（このモジュールの責務外）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common import discord_auth  # noqa: E402


# **本物の形を真似ない。** 本物の Bot Token はドット区切りの3節で、
# リポジトリ全体を「資格情報らしき文字列」で検査する以上、そっくりな偽物を
# 置くと検査が鳴り続け、除外リストで通す羽目になる。除外で通す癖をつけると
# **本物を置いたときも同じ言い訳で通る**（課題6で実際に踏んだ）。
TOKEN = "DUMMY-BOT-TOKEN-FOR-TESTS-not-a-real-credential"

# Webhook URL も同じ方針。id の桁だけ本物に合わせてあるのは、
# 「id は数字」という検査そのものを確かめるため。
WEBHOOK_ID = "1234567890123456789"
WEBHOOK_TOKEN = "DUMMY-WEBHOOK-TOKEN-not-a-real-credential"
WEBHOOK_URL = f"https://discord.com/api/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}"


class FakeResponse:
    """requests.Response のうち、このモジュールが触る部分だけを持つ器。

    ``json()`` が例外を投げる場合を作れるようにしてある。**本文が JSON でない
    応答は実際に来る**（Cloudflare が挟まると HTML が返る）ので、そこで
    素の例外が出ると利用者に読めないものが表示される。
    """

    def __init__(self, *, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers if headers is not None else {}

    def json(self):
        if self._payload is None:
            raise ValueError("応答が JSON ではありません")
        return self._payload


class FakeSession:
    """requests.Session の代わり。呼ばれた URL を記録する。"""

    def __init__(self, response=None):
        self.headers = {}
        self.calls = []
        self._response = response or FakeResponse(
            payload={"id": "999", "username": "notify-bot", "bot": True}
        )

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._response


# ------------------------------------------------------------------ トークン読み取り


def test_read_bot_token_returns_value():
    env = {discord_auth.BOT_TOKEN_ENV: TOKEN}
    assert discord_auth.read_bot_token(env) == TOKEN


def test_read_bot_token_strips_surrounding_whitespace():
    env = {discord_auth.BOT_TOKEN_ENV: f"  {TOKEN}\n"}
    assert discord_auth.read_bot_token(env) == TOKEN


def test_read_bot_token_missing_raises():
    with pytest.raises(discord_auth.AuthError):
        discord_auth.read_bot_token({})


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_read_bot_token_blank_is_treated_as_missing(value):
    """空文字を「設定済み」と扱わない。

    ``$env:DISCORD_BOT_TOKEN = ""`` と書いただけでも変数としては存在する。
    有無だけ見ると素通りして、後段の API が 401 を返し、原因がここだと
    分からなくなる。
    """
    with pytest.raises(discord_auth.AuthError):
        discord_auth.read_bot_token({discord_auth.BOT_TOKEN_ENV: value})


def test_read_bot_token_rejects_authorization_header_prefix():
    """``Bot xxx`` ごと貼られたものを弾く。

    ヘッダに組むときに ``Bot `` を付けるので、値に含まれていると
    ``Bot Bot xxx`` になって 401 になる。原因が遠い。
    """
    env = {discord_auth.BOT_TOKEN_ENV: f"Bot {TOKEN}"}
    with pytest.raises(discord_auth.AuthError):
        discord_auth.read_bot_token(env)


def test_read_bot_token_rejects_webhook_url():
    """Webhook URL を Bot Token の欄に入れた取り違えを検出する。

    資格情報が2種類あるので、この取り違えは実際に起きる。素通りさせると
    ``Authorization: Bot https://...`` を送って 401 になり、
    「トークンが違う」としか分からない。
    """
    env = {discord_auth.BOT_TOKEN_ENV: WEBHOOK_URL}
    with pytest.raises(discord_auth.AuthError):
        discord_auth.read_bot_token(env)


@pytest.mark.parametrize("value", [f"Bot {TOKEN}", WEBHOOK_URL])
def test_read_bot_token_error_never_echoes_the_value(value):
    """壊れた値でも画面に出さない。このメッセージは公開スクショに写る。

    空白だけの値をここに混ぜてはいけない。``"" not in s`` は**必ず False** で、
    何を実装しても落ちるテストになる（空文字は全ての文字列に含まれる）。
    空のケースは上の blank のテストが見ている。
    """
    env = {discord_auth.BOT_TOKEN_ENV: value}
    with pytest.raises(discord_auth.AuthError) as error:
        discord_auth.read_bot_token(env)
    assert value.strip() not in str(error.value)


def test_read_webhook_url_returns_value():
    env = {discord_auth.WEBHOOK_URL_ENV: WEBHOOK_URL}
    assert discord_auth.read_webhook_url(env) == WEBHOOK_URL


def test_read_webhook_url_missing_says_it_is_unset():
    """「渡していない」と「形が違う」を同じ文言で返さない。

    型だけ見るテストだと、未設定の分岐を消しても parse 側が
    AuthError を出すので素通りする（実際にミューテーションで生き残った）。
    """
    with pytest.raises(discord_auth.AuthError) as error:
        discord_auth.read_webhook_url({})
    assert "設定されていません" in str(error.value)


def test_read_webhook_url_error_never_echoes_the_value():
    env = {discord_auth.WEBHOOK_URL_ENV: "https://example.com/not-a-webhook"}
    with pytest.raises(discord_auth.AuthError) as error:
        discord_auth.read_webhook_url(env)
    assert "https://example.com/not-a-webhook" not in str(error.value)


# ------------------------------------------------------------------ Webhook URL の解析


def test_parse_webhook_url_extracts_id_and_token():
    webhook = discord_auth.parse_webhook_url(WEBHOOK_URL)
    assert webhook.id == WEBHOOK_ID
    assert webhook.token == WEBHOOK_TOKEN


def test_parse_webhook_url_keeps_the_host_and_path():
    """ホストとパスは受け取った形のまま。組み直すと取り違える余地ができる。"""
    webhook = discord_auth.parse_webhook_url(WEBHOOK_URL)
    assert webhook.url == WEBHOOK_URL


def test_parse_webhook_url_drops_a_trailing_slash():
    """読み返しは ``{url}/messages/{id}`` を継ぎ足して組む。

    末尾スラッシュが残ると ``//messages/`` になって別の URL になる。
    """
    webhook = discord_auth.parse_webhook_url(WEBHOOK_URL + "/")
    assert webhook.url == WEBHOOK_URL


def test_parse_webhook_url_drops_a_query_string():
    """``?thread_id=...`` が残っていると継ぎ足した先が壊れる。"""
    webhook = discord_auth.parse_webhook_url(WEBHOOK_URL + "?thread_id=123")
    assert webhook.url == WEBHOOK_URL


def test_parse_webhook_url_rejects_userinfo():
    """認証情報部つきの URL は、ホスト検査を素通りする形で紛れ込む。

    ここに ``利用者:合言葉@ホスト`` の形を**そのまま書かない**。
    公開物をメールアドレスで検査しているので、説明のつもりで書いた文字列が
    実アドレスとして検出される（実際に踏んだ）。
    """
    url = f"https://someone:secret@discord.com/api/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}"
    with pytest.raises(discord_auth.AuthError):
        discord_auth.parse_webhook_url(url)


def test_parse_webhook_url_rejects_a_port():
    url = f"https://discord.com:8443/api/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}"
    with pytest.raises(discord_auth.AuthError):
        discord_auth.parse_webhook_url(url)


@pytest.mark.parametrize(
    "url",
    [
        f"https://discord.com/api/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}",
        f"https://discord.com/api/v10/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}",
        f"https://discordapp.com/api/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}",
        f"https://canary.discord.com/api/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}",
        f"https://discord.com/api/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}/",
    ],
)
def test_parse_webhook_url_accepts_the_documented_shapes(url):
    webhook = discord_auth.parse_webhook_url(url)
    assert webhook.id == WEBHOOK_ID
    assert webhook.token == WEBHOOK_TOKEN


def test_parse_webhook_url_rejects_other_hosts():
    """**他の条件は全部同じで、ホストだけ違う入力**で確かめる。

    パスや形まで一緒に崩した入力を使うと、ホストの検査を消しても
    別の検査に吸われて通ってしまう（課題6で3件この形の素通りが出た）。
    """
    url = f"https://discord.com.evil.example/api/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}"
    with pytest.raises(discord_auth.AuthError):
        discord_auth.parse_webhook_url(url)


def test_parse_webhook_url_rejects_plain_http():
    """http だと URL ＝資格情報が平文で流れる。ホスト以外は正しい入力で見る。"""
    url = f"http://discord.com/api/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}"
    with pytest.raises(discord_auth.AuthError):
        discord_auth.parse_webhook_url(url)


def test_parse_webhook_url_rejects_wrong_path():
    url = f"https://discord.com/api/channels/{WEBHOOK_ID}/{WEBHOOK_TOKEN}"
    with pytest.raises(discord_auth.AuthError):
        discord_auth.parse_webhook_url(url)


def test_parse_webhook_url_rejects_non_numeric_id():
    """Webhook ID は snowflake（数字）。他は正しい入力で見る。"""
    url = f"https://discord.com/api/webhooks/not-a-snowflake/{WEBHOOK_TOKEN}"
    with pytest.raises(discord_auth.AuthError):
        discord_auth.parse_webhook_url(url)


def test_parse_webhook_url_rejects_missing_token():
    url = f"https://discord.com/api/webhooks/{WEBHOOK_ID}"
    with pytest.raises(discord_auth.AuthError):
        discord_auth.parse_webhook_url(url)


def test_parse_webhook_url_error_never_echoes_the_token():
    """**壊れた URL でもトークン部分は出さない。**

    「壊れているから安全」ではない。打ち間違いなら本物の token が
    そのまま入っている。
    """
    url = f"https://discord.com/api/webhooks/not-a-snowflake/{WEBHOOK_TOKEN}"
    with pytest.raises(discord_auth.AuthError) as error:
        discord_auth.parse_webhook_url(url)
    assert WEBHOOK_TOKEN not in str(error.value)


# ------------------------------------------------------------------ 伏せ字


def test_redact_hides_the_bot_token():
    text = f"失敗しました: Authorization: Bot {TOKEN}"
    assert TOKEN not in discord_auth.redact(text, TOKEN)


def test_redact_leaves_a_visible_marker():
    """**伏せ字はリテラルで期待する。**

    ``discord_auth.REDACTED in ...`` と書くと、REDACTED を空文字に変えたとき
    両辺が一緒に動いて必ず通る（空文字は全ての文字列に含まれる）。
    「伏せた」と「元から無かった」を区別できない実装が素通りする。
    """
    assert "***" in discord_auth.redact(f"Bot {TOKEN}", TOKEN)


def test_redact_hides_several_secrets_at_once():
    text = f"{TOKEN} と {WEBHOOK_TOKEN} が出た"
    hidden = discord_auth.redact(text, TOKEN, WEBHOOK_TOKEN)
    assert TOKEN not in hidden
    assert WEBHOOK_TOKEN not in hidden


@pytest.mark.parametrize("secret", ["", None])
def test_redact_ignores_empty_secrets(secret):
    """``str.replace("", x)`` は**全部の文字の間に x を挿し込む**。

    素通りさせると文章が壊れる。空を渡す経路は実在する（Webhook を
    使わない実行では webhook token が None）。
    """
    assert discord_auth.redact("そのままの文章", secret) == "そのままの文章"


def test_redact_leaves_text_without_secrets_alone():
    assert discord_auth.redact("何も無い", TOKEN) == "何も無い"


# ------------------------------------------------------------------ セッション


def test_build_session_sets_bot_authorization_header():
    session = discord_auth.build_session(TOKEN, factory=FakeSession)
    # 期待値はリテラルで書く。定数どうしを比べると、定数を書き換えても
    # 両辺が一緒に動いて必ず通る（課題6で3件素通りした形）。
    assert session.headers["Authorization"] == f"Bot {TOKEN}"


def test_build_session_sets_the_required_user_agent():
    """User-Agent は必須。無いと Cloudflare に弾かれうる。"""
    session = discord_auth.build_session(TOKEN, factory=FakeSession)
    assert session.headers["User-Agent"].startswith("DiscordBot (")


def test_build_session_rejects_empty_token():
    with pytest.raises(discord_auth.AuthError):
        discord_auth.build_session("   ", factory=FakeSession)


def test_build_anonymous_session_has_no_authorization_header():
    """Webhook に Authorization を付けない。

    付けても動くが、**付ける必要が無いことがこの経路の性質**で、
    そこを曖昧にすると「webhook も認証している」と誤解した記事になる。
    """
    session = discord_auth.build_anonymous_session(factory=FakeSession)
    assert "Authorization" not in session.headers


def test_build_anonymous_session_still_sets_user_agent():
    session = discord_auth.build_anonymous_session(factory=FakeSession)
    assert session.headers["User-Agent"].startswith("DiscordBot (")


# ------------------------------------------------------------------ 自分が誰か


def test_fetch_identity_reads_the_bot_user():
    session = FakeSession(
        FakeResponse(payload={"id": "42", "username": "notify-bot", "bot": True})
    )
    identity = discord_auth.fetch_identity(session)
    assert identity.user_id == "42"
    assert identity.username == "notify-bot"


def test_fetch_identity_calls_users_me():
    session = FakeSession()
    discord_auth.fetch_identity(session)
    method, url, _ = session.calls[0]
    assert method == "GET"
    assert url == "https://discord.com/api/v10/users/@me"


def test_fetch_identity_rejects_a_non_bot_token():
    """**プレフィックス検査は形式しか見ていない。** ここが性質の検査になる。

    ユーザーのトークンでも ``/users/@me`` は成功する。``bot`` が真でない
    ものを通すと、「アプリが投稿した」つもりで本人名義の投稿ができあがる。
    """
    session = FakeSession(
        FakeResponse(payload={"id": "42", "username": "nana", "bot": False})
    )
    with pytest.raises(discord_auth.AuthError):
        discord_auth.fetch_identity(session)


def test_fetch_identity_rejects_a_response_without_the_bot_field():
    """``bot`` が無いのを「たぶん bot」に倒さない。"""
    session = FakeSession(FakeResponse(payload={"id": "42", "username": "nana"}))
    with pytest.raises(discord_auth.AuthError):
        discord_auth.fetch_identity(session)


def test_fetch_identity_requires_an_id():
    """id が空のまま進むと、投稿者の照合が素通りする。"""
    session = FakeSession(
        FakeResponse(payload={"id": "", "username": "notify-bot", "bot": True})
    )
    with pytest.raises(discord_auth.AuthError):
        discord_auth.fetch_identity(session)


def test_fetch_identity_translates_unauthorized():
    session = FakeSession(
        FakeResponse(status_code=401, payload={"code": 0, "message": "401: Unauthorized"})
    )
    with pytest.raises(discord_auth.AuthError) as error:
        discord_auth.fetch_identity(session)
    assert "401" in str(error.value)


def test_fetch_identity_handles_a_non_json_body():
    """Cloudflare が挟まると HTML が返る。素の ValueError を出さない。"""
    session = FakeSession(FakeResponse(status_code=403, payload=None, text="<html>"))
    with pytest.raises(discord_auth.AuthError):
        discord_auth.fetch_identity(session)


def test_fetch_identity_handles_a_200_that_is_not_json():
    """**成功したのに JSON でない**、を別に見る。

    403 の場合は raise_for_discord_error が先に止めるので、
    ``isinstance(payload, dict)`` の分岐まで到達しない。そこを守るには
    2xx で壊れた本文が返る場合を入れないといけない。
    """
    session = FakeSession(FakeResponse(status_code=200, payload=None, text="<html>"))
    with pytest.raises(discord_auth.AuthError):
        discord_auth.fetch_identity(session)


def test_fetch_identity_handles_a_200_json_array():
    """JSON ではあるが辞書ではない、も同じ扱い。"""
    session = FakeSession(FakeResponse(status_code=200, payload=[1, 2]))
    with pytest.raises(discord_auth.AuthError):
        discord_auth.fetch_identity(session)


# ------------------------------------------------------------------ エラーの訳し分け


def test_raise_for_discord_error_passes_through_success():
    """2xx では何も起こさない。"""
    discord_auth.raise_for_discord_error(FakeResponse(status_code=200, payload={}))


def test_raise_for_discord_error_includes_the_numeric_code():
    """**エラーコードは必ず出す。** 日本語だけだと公式ドキュメントを引けない。"""
    response = FakeResponse(
        status_code=403, payload={"code": 50013, "message": "Missing Permissions"}
    )
    with pytest.raises(discord_auth.ApiError) as error:
        discord_auth.raise_for_discord_error(response)
    assert "50013" in str(error.value)


@pytest.mark.parametrize(
    "code, needle",
    [
        (50001, "View Channels"),
        (50013, "Send Messages"),
        (10003, "チャンネル"),
    ],
)
def test_raise_for_discord_error_explains_the_known_codes(code, needle):
    """こちらが直しかたを知っているコードにだけ一行足す。

    **相手が名指しで答えているときに、こちらで候補を並べ直さない**
    （課題4・Meet の教訓）。
    """
    response = FakeResponse(status_code=403, payload={"code": code, "message": "x"})
    with pytest.raises(discord_auth.ApiError) as error:
        discord_auth.raise_for_discord_error(response)
    assert needle in str(error.value)


def test_raise_for_discord_error_reports_rate_limit_seconds():
    """429 の ``retry_after`` は**秒**（小数あり）。ミリ秒と取り違えない。"""
    response = FakeResponse(
        status_code=429,
        payload={"message": "You are being rate limited.", "retry_after": 1.5, "global": False},
    )
    with pytest.raises(discord_auth.ApiError) as error:
        discord_auth.raise_for_discord_error(response)
    assert "1.5" in str(error.value)


def test_raise_for_discord_error_hides_secrets():
    """例外の本文に資格情報が混ざっても伏せる。"""
    response = FakeResponse(
        status_code=400, payload={"code": 50035, "message": f"bad {TOKEN}"}
    )
    with pytest.raises(discord_auth.ApiError) as error:
        discord_auth.raise_for_discord_error(response, TOKEN)
    assert TOKEN not in str(error.value)


def test_raise_for_discord_error_handles_a_non_json_body():
    response = FakeResponse(status_code=500, payload=None, text="<html>Internal Error</html>")
    with pytest.raises(discord_auth.ApiError) as error:
        discord_auth.raise_for_discord_error(response)
    assert "500" in str(error.value)
