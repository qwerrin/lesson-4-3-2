"""common/youtube_auth.py のテスト。

YouTube Data API の検索は**公開データ**なので、公式が案内するのは API キーによる
認証である（OAuth は「ユーザーの認可が必要なメソッド」のためのもの）。
common/google_auth.py（同意画面 → token.json → リフレッシュ）とも
common/zoom_auth.py（Server-to-Server OAuth・毎回取り直し）とも手順が違うので分けた。

**この課題に固有の危険が1つある。API キーは URL のクエリに載る。**

google-api-python-client は失敗したリクエストの URI を HttpError に持たせる。
文字列にすると ``...&key=<APIキー>`` がそのまま出るため、**エラーを画面に出した
時点で鍵が漏れる**。実行画面は public リポジトリに置くスクリーンショットになるので、
「例外をそのまま print する」だけで公開事故になる。

そこで redact() を通してからでないと表に出さない、という形にする。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common import youtube_auth  # noqa: E402


# **本物の形（AIza で始まる39文字）を真似ない。** リポジトリ全体を
# 「APIキーらしき文字列」で検査するので、偽物を置くと検査が鳴り続けるか、
# 除外リストに入れる羽目になって検査が弱くなる。読んだ人が本物と誤解する危険もある。
API_KEY = "DUMMY-KEY-FOR-TESTS-not-a-real-credential"


class TestReadApiKey:
    def test_環境変数から読む(self):
        assert youtube_auth.read_api_key({youtube_auth.API_KEY_ENV: API_KEY}) == API_KEY

    def test_前後の空白を落とす(self):
        env = {youtube_auth.API_KEY_ENV: f"  {API_KEY}\n"}
        assert youtube_auth.read_api_key(env) == API_KEY

    def test_未設定なら落ちる(self):
        with pytest.raises(youtube_auth.AuthError):
            youtube_auth.read_api_key({})

    def test_空文字は未設定と同じ扱い(self):
        # `$env:YOUTUBE_API_KEY = ""` は変数としては存在する。
        # 有無だけ見ると素通りして、後段が 400 を返し原因が遠くなる。
        with pytest.raises(youtube_auth.AuthError):
            youtube_auth.read_api_key({youtube_auth.API_KEY_ENV: ""})

    def test_空白だけも未設定と同じ扱い(self):
        with pytest.raises(youtube_auth.AuthError):
            youtube_auth.read_api_key({youtube_auth.API_KEY_ENV: "   "})

    def test_エラーに環境変数名が出る(self):
        with pytest.raises(youtube_auth.AuthError) as caught:
            youtube_auth.read_api_key({})
        assert youtube_auth.API_KEY_ENV in str(caught.value)

    def test_エラーに鍵の値を載せない(self):
        # 値が壊れている場合でも、メッセージに値そのものを出さない。
        # 実行画面のスクリーンショットが公開されるため。
        with pytest.raises(youtube_auth.AuthError) as caught:
            youtube_auth.read_api_key({youtube_auth.API_KEY_ENV: "   "})
        assert "   " not in str(caught.value).replace(youtube_auth.API_KEY_ENV, "")


class TestRedact:
    def test_鍵を伏せる(self):
        text = f"https://youtube.googleapis.com/youtube/v3/search?q=x&key={API_KEY}"
        hidden = youtube_auth.redact(text, API_KEY)
        assert API_KEY not in hidden

    def test_伏せ字に置き換わる(self):
        hidden = youtube_auth.redact(f"key={API_KEY}", API_KEY)
        assert youtube_auth.REDACTED in hidden

    def test_伏せ字は空でない(self):
        # 空文字にすると「伏せた」と「元から無かった」の区別がつかない。
        # また `REDACTED in hidden` は空文字なら常に真になり、上の検査が死ぬ。
        assert youtube_auth.REDACTED != ""

    def test_複数回出てきても全部伏せる(self):
        text = f"{API_KEY} と {API_KEY}"
        assert API_KEY not in youtube_auth.redact(text, API_KEY)

    def test_鍵以外はそのまま残す(self):
        text = f"HTTP 403: quotaExceeded (key={API_KEY})"
        hidden = youtube_auth.redact(text, API_KEY)
        assert "quotaExceeded" in hidden and "403" in hidden

    def test_鍵が空なら何もしない(self):
        # 鍵が無いまま redact を呼んでも、空文字を全箇所に挿し込んで
        # 文章を壊さないこと。str.replace("", x) は全文字の間に x を入れる。
        assert youtube_auth.redact("そのまま", "") == "そのまま"

    def test_鍵がNoneでも落ちない(self):
        assert youtube_auth.redact("そのまま", None) == "そのまま"

    def test_本文に鍵が無ければそのまま(self):
        assert youtube_auth.redact("ふつうの文", API_KEY) == "ふつうの文"


class TestBuildService:
    def test_サービス名とバージョンを渡す(self):
        calls = []

        def fake_builder(name, version, **kwargs):
            calls.append((name, version, kwargs))
            return "service"

        youtube_auth.build_service(API_KEY, builder=fake_builder)
        # **定数どうしで比べない。** API_SERVICE_NAME と比べると、定数を書き換えても
        # 両辺が一緒に変わって必ず通る（トートロジー）。実際 2026-08-16 に
        # mutate.py で "youtubeAnalytics" に変えたとき素通りした。
        assert calls[0][0] == "youtube"
        assert calls[0][1] == "v3"

    def test_developerKeyとして鍵を渡す(self):
        calls = []

        def fake_builder(name, version, **kwargs):
            calls.append(kwargs)
            return "service"

        youtube_auth.build_service(API_KEY, builder=fake_builder)
        assert calls[0]["developerKey"] == API_KEY

    def test_組み立てたサービスを返す(self):
        assert youtube_auth.build_service(API_KEY, builder=lambda *a, **k: "service") == "service"

    def test_空の鍵では組み立てない(self):
        # 鍵が空のまま build すると、実行時に 400 が返って原因が遠くなる。
        with pytest.raises(youtube_auth.AuthError):
            youtube_auth.build_service("", builder=lambda *a, **k: "service")

    def test_キャッシュ探索を止める(self):
        # discovery のファイルキャッシュは環境によって警告を出す。
        # 実行画面に無関係な警告を写さないため明示的に切る。
        calls = []

        def fake_builder(name, version, **kwargs):
            calls.append(kwargs)
            return "service"

        youtube_auth.build_service(API_KEY, builder=fake_builder)
        assert calls[0]["cache_discovery"] is False
