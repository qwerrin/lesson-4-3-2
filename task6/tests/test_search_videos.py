"""task6/search_videos.py のテスト。

課題6: YouTube Data API を利用して、特定のキーワードに基づく動画検索を行い、
検索結果から動画のタイトルと URL を抽出してコンソールに表示する。

**前の5課題と前提が違う。この課題は「作る」ではなく「読む」。**
Drive のファイルも Docs も Meet も Zoom の会議も Gmail の送信も、こちらが作った物が
相手側に残った。検索は何も作らないので、「実物を読み返して閉じる」が使えない。

代わりに罠になるのが**トートロジー**である。読み取り API は「取ってきた値どうしを
比べる」形になりやすく、それだと何も確かめていないのに全部一致してしまう。
そこでこのスクリプトの責務は「表示するところまで」に閉じ、突合は verify_search.py が
**別のエンドポイント（videos.list）**を使って行う。

この課題に固有の危険が2つある。

1. **API キーは URL のクエリに載る**。エラーをそのまま印字すると鍵が公開される
2. **タイトルは HTML 実体参照で返る**（``&amp;`` や ``&#39;``）。解かずに表示すると
   画面に出る文字列が実際のタイトルと違う
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import search_videos  # noqa: E402
from common import youtube_auth  # noqa: E402


KEYWORD = "Python 入門"
# 本物の形（AIza で始まる39文字）を真似ない。理由は common/tests/test_youtube_auth.py。
API_KEY = "DUMMY-KEY-FOR-TESTS-not-a-real-credential"

# YouTube の動画 ID は 11 文字（英数字と - _）。実在の動画を指さない値にしてある。
VIDEO_ID_1 = "aB3dE5gH7jK"
VIDEO_ID_2 = "zY9xW7vU5tS"
VIDEO_ID_3 = "ab-de_ghijk"

TITLE_1 = "Python 入門 第1回"
TITLE_2 = "はじめての Python"


def item(video_id: str = VIDEO_ID_1, title: str = TITLE_1) -> dict:
    return {
        "kind": "youtube#searchResult",
        "id": {"kind": "youtube#video", "videoId": video_id},
        "snippet": {"title": title, "channelTitle": "テストチャンネル"},
    }


def search_response(*items: dict) -> dict:
    records = list(items) if items else [item(), item(VIDEO_ID_2, TITLE_2)]
    return _response(records)


def empty_response() -> dict:
    """該当0件の応答。

    search_response(*[]) では作れない（引数ゼロ＝既定の2件になる）。
    「0件」を表せないヘルパーのまま 0 件の扱いをテストすると、
    2件の応答を相手に「0件のとき」を確かめたつもりになる。
    """
    return _response([])


def _response(records: list[dict]) -> dict:
    return {
        "kind": "youtube#searchListResponse",
        "pageInfo": {"totalResults": 1234, "resultsPerPage": len(records)},
        "items": records,
    }


class FakeSearch:
    """service.search().list(...).execute() の形を真似る。"""

    def __init__(self, response: dict | None = None, error: Exception | None = None):
        self.response = search_response() if response is None else response
        self.error = error
        self.calls: list[dict] = []

    def search(self):
        return self

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        if self.error is not None:
            raise self.error
        return self.response

    @property
    def called_once(self) -> bool:
        return len(self.calls) == 1


class FakeResp:
    def __init__(self, status: int):
        self.status = status
        self.reason = ""


def http_error(status: int, message: str, reason: str = "forbidden", uri: str = ""):
    from googleapiclient.errors import HttpError

    content = json.dumps(
        {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}},
        ensure_ascii=False,
    ).encode("utf-8")
    return HttpError(FakeResp(status), content, uri=uri)


# ---------------------------------------------------------------- 入力の確定


class TestNormalizeKeyword:
    def test_そのまま返す(self):
        assert search_videos.normalize_keyword(KEYWORD) == KEYWORD

    def test_前後の空白を落とす(self):
        assert search_videos.normalize_keyword(f"  {KEYWORD} ") == KEYWORD

    def test_語の間の空白は残す(self):
        # "Python 入門" の空白は検索語の一部。潰すと別の検索になる。
        assert " " in search_videos.normalize_keyword(KEYWORD)

    def test_空は落ちる(self):
        with pytest.raises(search_videos.SearchError):
            search_videos.normalize_keyword("")

    def test_空白だけも落ちる(self):
        with pytest.raises(search_videos.SearchError):
            search_videos.normalize_keyword("   ")

    def test_Noneも落ちる(self):
        with pytest.raises(search_videos.SearchError):
            search_videos.normalize_keyword(None)


class TestNormalizeMaxResults:
    def test_既定値は5(self):
        assert search_videos.DEFAULT_MAX_RESULTS == 5

    def test_範囲内はそのまま(self):
        assert search_videos.normalize_max_results(10) == 10

    def test_上限50は通る(self):
        assert search_videos.normalize_max_results(50) == 50

    def test_下限1は通る(self):
        assert search_videos.normalize_max_results(1) == 1

    def test_0は落ちる(self):
        # 公式は 0〜50 を受け付けるが、0 件の結果に「全部一致」を出すと
        # 何も確かめていない実行が成功に化ける。こちらの都合で 1 以上に狭める。
        with pytest.raises(search_videos.SearchError):
            search_videos.normalize_max_results(0)

    def test_51は落ちる(self):
        with pytest.raises(search_videos.SearchError):
            search_videos.normalize_max_results(51)

    def test_負の数は落ちる(self):
        with pytest.raises(search_videos.SearchError):
            search_videos.normalize_max_results(-1)

    def test_上限は公式の50(self):
        assert search_videos.MAX_RESULTS_LIMIT == 50

    def test_エラーに範囲が出る(self):
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.normalize_max_results(99)
        assert "50" in str(caught.value)


class TestNormalizeOrder:
    def test_既定はrelevance(self):
        assert search_videos.DEFAULT_ORDER == "relevance"

    def test_公式が認める値は通る(self):
        for value in ("date", "rating", "relevance", "title", "viewCount"):
            assert search_videos.normalize_order(value) == value

    def test_知らない値は落ちる(self):
        with pytest.raises(search_videos.SearchError):
            search_videos.normalize_order("popular")

    def test_エラーに使える値が出る(self):
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.normalize_order("popular")
        assert "relevance" in str(caught.value)


# ---------------------------------------------------------------- URL の組み立て


class TestWatchUrl:
    def test_視聴URLを組む(self):
        assert search_videos.watch_url(VIDEO_ID_1) == f"https://www.youtube.com/watch?v={VIDEO_ID_1}"

    def test_記号を含むIDも通る(self):
        # 動画 ID には - と _ が入る。弾くと正当な動画を落とす。
        assert VIDEO_ID_3 in search_videos.watch_url(VIDEO_ID_3)

    def test_空のIDは落ちる(self):
        with pytest.raises(search_videos.SearchError):
            search_videos.watch_url("")

    def test_空のIDは空だと言う(self):
        # 形の検査だけでも空は弾けるが、それだと "形式が不正: ''" と出る。
        # 「空だった」と「形が違った」は直す場所が違う。
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.watch_url("")
        assert "空" in str(caught.value)

    def test_短すぎるIDは落ちる(self):
        # 形の検査をしないと、壊れた ID でも「それらしい URL」ができてしまい、
        # 開くまで気づけない。表示する前に落とす。
        with pytest.raises(search_videos.SearchError):
            search_videos.watch_url("abc")

    def test_長すぎるIDは落ちる(self):
        with pytest.raises(search_videos.SearchError):
            search_videos.watch_url("a" * 12)

    def test_使えない文字は落ちる(self):
        with pytest.raises(search_videos.SearchError):
            search_videos.watch_url("abcdefghij!")

    def test_クエリ記号の混入は落ちる(self):
        # & が入ると URL のパラメータが増える形になる。
        with pytest.raises(search_videos.SearchError):
            search_videos.watch_url("abcde&fghij")


class TestVideoIdFromUrl:
    def test_組んだURLから取り出せる(self):
        url = search_videos.watch_url(VIDEO_ID_1)
        assert search_videos.video_id_from_url(url) == VIDEO_ID_1

    def test_記号入りのIDも往復する(self):
        url = search_videos.watch_url(VIDEO_ID_3)
        assert search_videos.video_id_from_url(url) == VIDEO_ID_3

    def test_httpは通さない(self):
        assert search_videos.video_id_from_url(f"http://www.youtube.com/watch?v={VIDEO_ID_1}") is None

    def test_別ホストは通さない(self):
        assert search_videos.video_id_from_url(f"https://youtu.be/{VIDEO_ID_1}") is None

    def test_wwwが無ければ通さない(self):
        # 上の youtu.be はパスが /watch でないので、ホストを見なくても弾ける。
        # ホストの検査そのものが効いているかは、パスまで同じ URL でしか確かめられない。
        assert search_videos.video_id_from_url(f"https://youtube.com/watch?v={VIDEO_ID_1}") is None

    def test_別パスは通さない(self):
        assert search_videos.video_id_from_url(f"https://www.youtube.com/embed/{VIDEO_ID_1}") is None

    def test_パスが違えばvがあっても通さない(self):
        # 上の /embed/ID はクエリが無いので、パスを見なくても弾けてしまう。
        # パスの検査そのものが効いているかは、v を持つ別パスでしか確かめられない。
        assert search_videos.video_id_from_url(f"https://www.youtube.com/other?v={VIDEO_ID_1}") is None

    def test_vが無ければ通さない(self):
        assert search_videos.video_id_from_url("https://www.youtube.com/watch?t=5") is None

    def test_vが2つあれば通さない(self):
        url = f"https://www.youtube.com/watch?v={VIDEO_ID_1}&v={VIDEO_ID_2}"
        assert search_videos.video_id_from_url(url) is None

    def test_余分なパラメータは通さない(self):
        # このスクリプトが組む URL は v ひとつだけ。増えていたら誰かが書き換えている。
        url = f"https://www.youtube.com/watch?v={VIDEO_ID_1}&t=5"
        assert search_videos.video_id_from_url(url) is None

    def test_IDの形が違えば通さない(self):
        assert search_videos.video_id_from_url("https://www.youtube.com/watch?v=abc") is None

    def test_空文字は通さない(self):
        assert search_videos.video_id_from_url("") is None

    def test_Noneは通さない(self):
        assert search_videos.video_id_from_url(None) is None

    def test_文字列でない値は通さない(self):
        # None と "" は urlparse に渡しても空の結果になるだけで例外にならないため、
        # 型の検査を外しても素通りする。**真値の非文字列**でしか効きめを確かめられない
        # （urlparse(12345) は TypeError を投げる）。
        assert search_videos.video_id_from_url(12345) is None


# ---------------------------------------------------------------- タイトルの整形


class TestCleanTitle:
    def test_ふつうのタイトルはそのまま(self):
        assert search_videos.clean_title(TITLE_1) == TITLE_1

    def test_アンパサンドを戻す(self):
        # YouTube Data API はタイトルを HTML 実体参照で返す。
        # 解かずに表示すると、画面の文字列が実際のタイトルと違う。
        assert search_videos.clean_title("A &amp; B") == "A & B"

    def test_アポストロフィを戻す(self):
        assert search_videos.clean_title("It&#39;s Python") == "It's Python"

    def test_山括弧を戻す(self):
        assert search_videos.clean_title("&lt;tag&gt;") == "<tag>"

    def test_引用符を戻す(self):
        assert search_videos.clean_title("&quot;quoted&quot;") == '"quoted"'

    def test_前後の空白を落とす(self):
        assert search_videos.clean_title("  タイトル  ") == "タイトル"

    def test_Noneは空文字(self):
        assert search_videos.clean_title(None) == ""


# ---------------------------------------------------------------- 応答の読み方


class TestExtractVideos:
    def test_件数どおり取り出す(self):
        assert len(search_videos.extract_videos(search_response())) == 2

    def test_タイトルを取り出す(self):
        videos = search_videos.extract_videos(search_response())
        assert videos[0].title == TITLE_1

    def test_動画IDを取り出す(self):
        videos = search_videos.extract_videos(search_response())
        assert videos[0].video_id == VIDEO_ID_1

    def test_URLを組む(self):
        videos = search_videos.extract_videos(search_response())
        assert videos[0].url == f"https://www.youtube.com/watch?v={VIDEO_ID_1}"

    def test_順序を保つ(self):
        response = search_response(item(VIDEO_ID_2, TITLE_2), item(VIDEO_ID_1, TITLE_1))
        videos = search_videos.extract_videos(response)
        assert [v.video_id for v in videos] == [VIDEO_ID_2, VIDEO_ID_1]

    def test_タイトルの実体参照を解く(self):
        response = search_response(item(VIDEO_ID_1, "A &amp; B"))
        assert search_videos.extract_videos(response)[0].title == "A & B"

    def test_該当なしは空リスト(self):
        # 0 件は API としては正常。ここでは落とさず、main が扱いを決める。
        assert search_videos.extract_videos(empty_response()) == []

    def test_itemsが無ければ落ちる(self):
        # 「返ってこなかった」を「0 件だった」にしない。
        with pytest.raises(search_videos.SearchError):
            search_videos.extract_videos({"kind": "youtube#searchListResponse"})

    def test_itemsが無いエラーに受け取ったキーを出す(self):
        # 何が返ってきたのかを書かないと、直しようがない。
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.extract_videos({"kind": "youtube#searchListResponse", "etag": "x"})
        assert "etag" in str(caught.value)

    def test_itemsがリストでなければ落ちる(self):
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.extract_videos({"items": {"id": "x"}})
        # 「1件目の形式が不正」ではなく、items 自体の型がおかしいと言うこと。
        assert "リスト" in str(caught.value)

    def test_videoIdが無ければ落ちる(self):
        broken = {"id": {"kind": "youtube#channel", "channelId": "UC123"}, "snippet": {"title": TITLE_1}}
        with pytest.raises(search_videos.SearchError):
            search_videos.extract_videos(search_response(broken))

    def test_動画以外が混ざったと伝える(self):
        # videoId の欠落は type の絞り込みが効いていないときに起きる。
        # 「IDが空です」とだけ言うと、どこを直すのか分からない。
        broken = {"id": {"kind": "youtube#channel", "channelId": "UC123"}, "snippet": {"title": TITLE_1}}
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.extract_videos(search_response(broken))
        assert search_videos.SEARCH_TYPE in str(caught.value)

    def test_idそのものが無ければ落ちる(self):
        with pytest.raises(search_videos.SearchError):
            search_videos.extract_videos(search_response({"snippet": {"title": TITLE_1}}))

    def test_snippetが無ければ落ちる(self):
        broken = {"id": {"videoId": VIDEO_ID_1}}
        with pytest.raises(search_videos.SearchError):
            search_videos.extract_videos(search_response(broken))

    def test_タイトルが空なら落ちる(self):
        # 空文字を「タイトルが取れた」にしない。画面には URL だけが並び、
        # 抽出できていないことが成功に見える。
        with pytest.raises(search_videos.SearchError):
            search_videos.extract_videos(search_response(item(VIDEO_ID_1, "")))

    def test_タイトルが空白だけでも落ちる(self):
        with pytest.raises(search_videos.SearchError):
            search_videos.extract_videos(search_response(item(VIDEO_ID_1, "   ")))

    def test_壊れたIDは落ちる(self):
        with pytest.raises(search_videos.SearchError):
            search_videos.extract_videos(search_response(item("short", TITLE_1)))

    def test_エラーに何件目かが出る(self):
        response = search_response(item(), {"snippet": {"title": TITLE_2}})
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.extract_videos(response)
        assert "2" in str(caught.value)


# ---------------------------------------------------------------- 検索する


class TestSearch:
    def test_一度だけ呼ぶ(self):
        service = FakeSearch()
        search_videos.search(service, KEYWORD, 5, "relevance")
        assert service.called_once

    def test_partにsnippetを渡す(self):
        service = FakeSearch()
        search_videos.search(service, KEYWORD, 5, "relevance")
        assert service.calls[0]["part"] == "snippet"

    def test_キーワードをqに渡す(self):
        service = FakeSearch()
        search_videos.search(service, KEYWORD, 5, "relevance")
        assert service.calls[0]["q"] == KEYWORD

    def test_typeをvideoに絞る(self):
        # 既定は video,channel,playlist。絞らないとチャンネルが混ざり、
        # videoId を持たない項目が返って抽出が落ちる。
        service = FakeSearch()
        search_videos.search(service, KEYWORD, 5, "relevance")
        assert service.calls[0]["type"] == "video"

    def test_maxResultsを渡す(self):
        service = FakeSearch()
        search_videos.search(service, KEYWORD, 7, "relevance")
        assert service.calls[0]["maxResults"] == 7

    def test_orderを渡す(self):
        service = FakeSearch()
        search_videos.search(service, KEYWORD, 5, "date")
        assert service.calls[0]["order"] == "date"

    def test_応答をそのまま返す(self):
        response = search_response()
        assert search_videos.search(FakeSearch(response), KEYWORD, 5, "relevance") is response


class TestSearchErrors:
    def test_クォータ超過を見分ける(self):
        error = http_error(403, "The request cannot be completed because you have exceeded your quota.", "quotaExceeded")
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.search(FakeSearch(error=error), KEYWORD, 5, "relevance")
        assert "クォータ" in str(caught.value) or "上限" in str(caught.value)

    def test_1日の上限は公式の100回(self):
        # 公式のクォータ表：「100 search.list calls ... and 10,000 units per day
        # combined for all other endpoints」。search.list だけ別枠になっている。
        assert search_videos.DAILY_SEARCH_CALL_LIMIT == 100

    def test_クォータ超過に1日100回を書く(self):
        # search.list は 10,000 ユニットの枠とは別に「1日 100 回」の枠を持つ。
        # 数字を出さないと「明日まで待つ」以外の判断ができない。
        #
        # 定数を使って比べない。定数を書き換えると両辺が一緒に変わって必ず通る。
        error = http_error(403, "quota exceeded", "quotaExceeded")
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.search(FakeSearch(error=error), KEYWORD, 5, "relevance")
        assert "100" in str(caught.value)

    def test_API未有効化を見分ける(self):
        error = http_error(
            403,
            "YouTube Data API v3 has not been used in project 123 before or it is disabled.",
            "accessNotConfigured",
        )
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.search(FakeSearch(error=error), KEYWORD, 5, "relevance")
        assert "有効" in str(caught.value)

    def test_鍵が不正なら見分ける(self):
        error = http_error(400, "API key not valid. Please pass a valid API key.", "badRequest")
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.search(FakeSearch(error=error), KEYWORD, 5, "relevance")
        assert youtube_auth.API_KEY_ENV in str(caught.value)

    def test_未有効化とクォータ超過を混ぜない(self):
        # どちらも 403 だが直す場所が違う。混ぜると読み手がどちらを直すか決められない。
        error = http_error(403, "has not been used in project", "accessNotConfigured")
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.search(FakeSearch(error=error), KEYWORD, 5, "relevance")
        assert "クォータ" not in str(caught.value)

    def test_内部表現を見せない(self):
        error = http_error(500, "Backend Error", "backendError")
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.search(FakeSearch(error=error), KEYWORD, 5, "relevance")
        assert "when requesting" not in str(caught.value)

    def test_JSONでない応答でも落ちない(self):
        from googleapiclient.errors import HttpError

        error = HttpError(FakeResp(502), b"<html>bad gateway</html>", uri="")
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.search(FakeSearch(error=error), KEYWORD, 5, "relevance")
        # 本文が読めないときに str(error) へ逃げない。そこには URI が入っていて、
        # URI には API キーが載る。
        assert "when requesting" not in str(caught.value)


class TestApiKeyIsNeverPrinted:
    """API キーは URL のクエリに載る。エラー経路すべてで伏せる。"""

    def test_URIに載った鍵を伏せる(self):
        uri = f"https://youtube.googleapis.com/youtube/v3/search?q=x&key={API_KEY}"
        error = http_error(403, "quota exceeded", "quotaExceeded", uri=uri)
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.search(FakeSearch(error=error), KEYWORD, 5, "relevance", api_key=API_KEY)
        assert API_KEY not in str(caught.value)

    def test_応答本文に載った鍵も伏せる(self):
        error = http_error(400, f"API key not valid: {API_KEY}", "badRequest")
        with pytest.raises(search_videos.SearchError) as caught:
            search_videos.search(FakeSearch(error=error), KEYWORD, 5, "relevance", api_key=API_KEY)
        assert API_KEY not in str(caught.value)

    def test_鍵を渡さなくても落ちない(self):
        error = http_error(500, "Backend Error", "backendError")
        with pytest.raises(search_videos.SearchError):
            search_videos.search(FakeSearch(error=error), KEYWORD, 5, "relevance")


# ---------------------------------------------------------------- 画面と保存


class TestFormatVideos:
    def test_タイトルを出す(self):
        videos = search_videos.extract_videos(search_response())
        assert TITLE_1 in search_videos.format_videos(videos)

    def test_URLを出す(self):
        videos = search_videos.extract_videos(search_response())
        assert f"https://www.youtube.com/watch?v={VIDEO_ID_1}" in search_videos.format_videos(videos)

    def test_全件出す(self):
        videos = search_videos.extract_videos(search_response())
        text = search_videos.format_videos(videos)
        assert TITLE_1 in text and TITLE_2 in text

    def test_番号を振る(self):
        videos = search_videos.extract_videos(search_response())
        text = search_videos.format_videos(videos)
        assert "1." in text and "2." in text

    def test_0件でも落ちない(self):
        assert isinstance(search_videos.format_videos([]), str)


class TestBuildResults:
    def test_キーワードを記録する(self):
        videos = search_videos.extract_videos(search_response())
        payload = search_videos.build_results(KEYWORD, "relevance", videos)
        assert payload["keyword"] == KEYWORD

    def test_並び順を記録する(self):
        videos = search_videos.extract_videos(search_response())
        assert search_videos.build_results(KEYWORD, "date", videos)["order"] == "date"

    def test_件数を記録する(self):
        videos = search_videos.extract_videos(search_response())
        assert search_videos.build_results(KEYWORD, "relevance", videos)["count"] == 2

    def test_タイトルとURLを記録する(self):
        videos = search_videos.extract_videos(search_response())
        record = search_videos.build_results(KEYWORD, "relevance", videos)["videos"][0]
        assert record["title"] == TITLE_1
        assert record["url"] == f"https://www.youtube.com/watch?v={VIDEO_ID_1}"

    def test_動画IDは記録しない(self):
        # **意図的に持たせない。** ID を書いておくと verify 側がそれを読んで
        # 済ませてしまい、URL が正しく組めているかを一度も確かめないまま通る。
        # URL から取り出させることで、組み立てがここで初めて閉じる。
        videos = search_videos.extract_videos(search_response())
        record = search_videos.build_results(KEYWORD, "relevance", videos)["videos"][0]
        assert "video_id" not in record and "videoId" not in record


class TestWriteResults:
    def test_ファイルに書く(self, tmp_path):
        videos = search_videos.extract_videos(search_response())
        path = tmp_path / "results.json"
        search_videos.write_results(path, search_videos.build_results(KEYWORD, "relevance", videos))
        assert path.exists()

    def test_UTF8で読み返せる(self, tmp_path):
        videos = search_videos.extract_videos(search_response())
        path = tmp_path / "results.json"
        search_videos.write_results(path, search_videos.build_results(KEYWORD, "relevance", videos))
        assert json.loads(path.read_text(encoding="utf-8"))["keyword"] == KEYWORD

    def test_日本語をエスケープしない(self, tmp_path):
        videos = search_videos.extract_videos(search_response())
        path = tmp_path / "results.json"
        search_videos.write_results(path, search_videos.build_results(KEYWORD, "relevance", videos))
        assert "入門" in path.read_text(encoding="utf-8")

    def test_親フォルダが無ければ作る(self, tmp_path):
        videos = search_videos.extract_videos(search_response())
        path = tmp_path / "nested" / "results.json"
        search_videos.write_results(path, search_videos.build_results(KEYWORD, "relevance", videos))
        assert path.exists()


# ---------------------------------------------------------------- 入口


class TestParseArgs:
    def test_キーワードは必須(self):
        with pytest.raises(SystemExit):
            search_videos.parse_args([])

    def test_件数の既定値(self):
        assert search_videos.parse_args(["--keyword", KEYWORD]).max_results == 5

    def test_APIキーはコマンドラインから渡せない(self):
        # 鍵をコマンドラインに書くと、シェルの履歴に残り、実行画面の
        # スクリーンショットにも写る。環境変数からしか受け取らない。
        with pytest.raises(SystemExit):
            search_videos.parse_args(["--keyword", KEYWORD, "--api-key", API_KEY])


class TestMain:
    def test_成功なら0(self, capsys):
        code = search_videos.main(["--keyword", KEYWORD], service_factory=lambda args: FakeSearch())
        assert code == 0

    def test_タイトルとURLを表示する(self, capsys):
        search_videos.main(["--keyword", KEYWORD], service_factory=lambda args: FakeSearch())
        out = capsys.readouterr().out
        assert TITLE_1 in out and VIDEO_ID_1 in out

    def test_該当なしは1(self, capsys):
        # 「対象が尽きた」を成功で終わらせない。0 件で 0 を返すと、
        # 毎回成功しているのに何も表示しない状態に気づけない。
        service = FakeSearch(empty_response())
        assert search_videos.main(["--keyword", KEYWORD], service_factory=lambda args: service) == 1

    def test_キーワードが空なら1(self):
        assert search_videos.main(["--keyword", "  "], service_factory=lambda args: FakeSearch()) == 1

    def test_引数が不正ならAPIを呼ばない(self):
        # 落ちると分かっている実行でネットワークに出ない。
        # search.list は 1 日 100 回しか使えない。
        called = []

        def factory(args):
            called.append(args)
            return FakeSearch()

        search_videos.main(["--keyword", "  "], service_factory=factory)
        assert called == []

    def test_件数が範囲外ならAPIを呼ばない(self):
        called = []

        def factory(args):
            called.append(args)
            return FakeSearch()

        search_videos.main(["--keyword", KEYWORD, "--max-results", "99"], service_factory=factory)
        assert called == []

    def test_APIが落ちたら1(self, capsys):
        error = http_error(403, "quota exceeded", "quotaExceeded")
        service = FakeSearch(error=error)
        assert search_videos.main(["--keyword", KEYWORD], service_factory=lambda args: service) == 1

    def test_結果をファイルに書ける(self, tmp_path):
        path = tmp_path / "results.json"
        search_videos.main(
            ["--keyword", KEYWORD, "--json-out", str(path)],
            service_factory=lambda args: FakeSearch(),
        )
        assert json.loads(path.read_text(encoding="utf-8"))["count"] == 2

    def test_json_outを指定しなければ書かない(self, tmp_path):
        search_videos.main(["--keyword", KEYWORD], service_factory=lambda args: FakeSearch())
        assert list(tmp_path.iterdir()) == []

    def test_該当なしならファイルを書かない(self, tmp_path):
        # 0 件の結果ファイルを残すと、verify がそれを読んで
        # 「0 件すべて一致」を出しうる。書かない。
        path = tmp_path / "results.json"
        service = FakeSearch(empty_response())
        search_videos.main(
            ["--keyword", KEYWORD, "--json-out", str(path)],
            service_factory=lambda args: service,
        )
        assert not path.exists()

    def test_次にやることを案内する(self, capsys):
        search_videos.main(["--keyword", KEYWORD], service_factory=lambda args: FakeSearch())
        assert "verify_search" in capsys.readouterr().out
