"""task6/verify_search.py のテスト。

検索結果を **別のエンドポイント**（videos.list）で突き合わせる。

**この課題の設計上の山場はここにある。** 読み取り中心の API は「取ってきた値どうしを
比べる」形になりやすい。search.list の応答の中で id と title を比べても、それは
同じ1回の応答が自分自身と一致していると言っているだけで、何も確かめていない
（トートロジー）。課題4で `join_url` を応答の `id` と比べかけたのと同じ形である。

そこで物差しを3つとも応答の外から取る。

1. **キーワードと件数** — 人間がコマンドラインで渡す
2. **動画ID** — search_videos.py が**表示した URL から取り出す**
   （結果ファイルに ID を書いていないので、URL の組み立てが正しくないとここで落ちる）
3. **タイトル** — videos.list（search.list とは別のエンドポイント）から取り直して比べる

さらに videos.list には固有の罠がある。**存在しない ID を渡してもエラーにならず、
その分が items から黙って抜けて 200 で返る**。件数を見ないと「返ってこなかった」が
「一致した」に化ける。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import search_videos  # noqa: E402
import verify_search  # noqa: E402


KEYWORD = "Python 入門"
# 本物の形（AIza で始まる39文字）を真似ない。理由は common/tests/test_youtube_auth.py。
API_KEY = "DUMMY-KEY-FOR-TESTS-not-a-real-credential"

VIDEO_ID_1 = "aB3dE5gH7jK"
VIDEO_ID_2 = "zY9xW7vU5tS"

TITLE_1 = "Python 入門 第1回"
TITLE_2 = "はじめての Python"

URL_1 = f"https://www.youtube.com/watch?v={VIDEO_ID_1}"
URL_2 = f"https://www.youtube.com/watch?v={VIDEO_ID_2}"


def results(**overrides) -> dict:
    payload = {
        "keyword": KEYWORD,
        "order": "relevance",
        "count": 2,
        "videos": [
            {"title": TITLE_1, "url": URL_1},
            {"title": TITLE_2, "url": URL_2},
        ],
    }
    payload.update(overrides)
    return payload


def videos_response(*ids_and_titles) -> dict:
    pairs = list(ids_and_titles) or [(VIDEO_ID_1, TITLE_1), (VIDEO_ID_2, TITLE_2)]
    return {
        "kind": "youtube#videoListResponse",
        "items": [
            {"kind": "youtube#video", "id": vid, "snippet": {"title": title}}
            for vid, title in pairs
        ],
    }


def write_results(tmp_path: Path, payload: dict | None = None) -> Path:
    path = tmp_path / "results.json"
    path.write_text(
        json.dumps(results() if payload is None else payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


class FakeVideos:
    """service.videos().list(...).execute() の形を真似る。"""

    def __init__(self, response: dict | None = None, error: Exception | None = None):
        self.response = videos_response() if response is None else response
        self.error = error
        self.calls: list[dict] = []

    def videos(self):
        return self

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        if self.error is not None:
            raise self.error
        return self.response


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


def ok(checks, label: str) -> bool:
    for check in checks:
        if check.label.startswith(label):
            return check.ok
    raise AssertionError(f"{label} という項目がありません: {[c.label for c in checks]}")


# ---------------------------------------------------------------- 結果ファイル


class TestLoadResults:
    def test_読み込める(self, tmp_path):
        assert verify_search.load_results(write_results(tmp_path))["keyword"] == KEYWORD

    def test_無いファイルは落ちる(self, tmp_path):
        with pytest.raises(verify_search.VerifyError):
            verify_search.load_results(tmp_path / "missing.json")

    def test_JSONでなければ落ちる(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("これはJSONではない", encoding="utf-8")
        with pytest.raises(verify_search.VerifyError):
            verify_search.load_results(path)

    def test_辞書でなければ落ちる(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(verify_search.VerifyError):
            verify_search.load_results(path)

    def test_キーワードが無ければ落ちる(self, tmp_path):
        payload = results()
        del payload["keyword"]
        with pytest.raises(verify_search.VerifyError):
            verify_search.load_results(write_results(tmp_path, payload))

    def test_videosが無ければ落ちる(self, tmp_path):
        payload = results()
        del payload["videos"]
        with pytest.raises(verify_search.VerifyError):
            verify_search.load_results(write_results(tmp_path, payload))

    def test_videosがリストでなければ落ちる(self, tmp_path):
        with pytest.raises(verify_search.VerifyError) as caught:
            verify_search.load_results(write_results(tmp_path, results(videos={"a": 1})))
        # 中身を1件ずつ見る前に、videos 自体の型で落ちること。
        assert "リスト" in str(caught.value)

    def test_件数が無ければ落ちる(self, tmp_path):
        # search_videos.py が必ず書く項目。無いなら別物のファイルを渡している。
        payload = results()
        del payload["count"]
        with pytest.raises(verify_search.VerifyError):
            verify_search.load_results(write_results(tmp_path, payload))

    def test_件数が数でなければ落ちる(self, tmp_path):
        with pytest.raises(verify_search.VerifyError):
            verify_search.load_results(write_results(tmp_path, results(count="2")))

    def test_videosが空なら落ちる(self, tmp_path):
        # 0 件のファイルを受け付けると「0 件すべて一致」が出る。
        with pytest.raises(verify_search.VerifyError):
            verify_search.load_results(write_results(tmp_path, results(videos=[], count=0)))

    def test_URLが無い項目があれば落ちる(self, tmp_path):
        payload = results(videos=[{"title": TITLE_1}])
        with pytest.raises(verify_search.VerifyError):
            verify_search.load_results(write_results(tmp_path, payload))

    def test_タイトルが無い項目があれば落ちる(self, tmp_path):
        payload = results(videos=[{"url": URL_1}])
        with pytest.raises(verify_search.VerifyError):
            verify_search.load_results(write_results(tmp_path, payload))

    def test_UTF8で読む(self, tmp_path):
        payload = verify_search.load_results(write_results(tmp_path))
        assert payload["videos"][0]["title"] == TITLE_1


# ---------------------------------------------------------------- URL から ID


class TestParseUrls:
    def test_IDを取り出す(self):
        ids, bad = verify_search.parse_urls(results())
        assert ids == [VIDEO_ID_1, VIDEO_ID_2]

    def test_正常なら壊れたURLは無い(self):
        assert verify_search.parse_urls(results())[1] == []

    def test_壊れたURLを拾う(self):
        payload = results(videos=[{"title": TITLE_1, "url": "https://example.com/x"}])
        ids, bad = verify_search.parse_urls(payload)
        assert bad == ["https://example.com/x"]

    def test_壊れたURLはIDに混ぜない(self):
        payload = results(videos=[{"title": TITLE_1, "url": "https://example.com/x"}])
        assert verify_search.parse_urls(payload)[0] == []

    def test_一部だけ壊れていても続ける(self):
        payload = results(videos=[{"title": TITLE_1, "url": URL_1}, {"title": TITLE_2, "url": "x"}])
        ids, bad = verify_search.parse_urls(payload)
        assert ids == [VIDEO_ID_1] and bad == ["x"]

    def test_search側と同じ判定を使う(self):
        # URL の組み立てと取り出しが別実装だと、両方間違っていても往復して通る。
        assert verify_search.parse_urls(results())[0][0] == search_videos.video_id_from_url(URL_1)


# ---------------------------------------------------------------- 手元でできる照合


class TestLocalChecks:
    def test_全部一致(self):
        checks = verify_search.build_local_checks(results(), expected_keyword=KEYWORD, expected_count=2)
        assert verify_search.all_ok(checks)

    def test_キーワード違いを見つける(self):
        # 昨日の結果ファイルを渡したまま気づかない、を防ぐ。
        checks = verify_search.build_local_checks(results(), expected_keyword="別の語", expected_count=2)
        assert not ok(checks, "検索キーワード")

    def test_件数違いを見つける(self):
        checks = verify_search.build_local_checks(results(), expected_keyword=KEYWORD, expected_count=5)
        assert not ok(checks, "結果の件数")

    def test_記録された件数の食い違いを見つける(self):
        # ファイルを手で削ったときに出る。
        payload = results(count=5)
        checks = verify_search.build_local_checks(payload, expected_keyword=KEYWORD, expected_count=2)
        assert not ok(checks, "記録された件数")

    def test_重複を見つける(self):
        payload = results(videos=[{"title": TITLE_1, "url": URL_1}, {"title": TITLE_2, "url": URL_1}])
        checks = verify_search.build_local_checks(payload, expected_keyword=KEYWORD, expected_count=2)
        assert not ok(checks, "動画IDの重複")

    def test_壊れたURLを見つける(self):
        payload = results(videos=[{"title": TITLE_1, "url": "https://example.com/x"}], count=1)
        checks = verify_search.build_local_checks(payload, expected_keyword=KEYWORD, expected_count=1)
        assert not ok(checks, "URLの形式")

    def test_正しいURLは通る(self):
        checks = verify_search.build_local_checks(results(), expected_keyword=KEYWORD, expected_count=2)
        assert ok(checks, "URLの形式")

    def test_食い違いの中身を書く(self):
        checks = verify_search.build_local_checks(results(), expected_keyword="別の語", expected_count=2)
        for check in checks:
            if check.label.startswith("検索キーワード"):
                assert KEYWORD in check.detail and "別の語" in check.detail


# ---------------------------------------------------------------- 読み直して照合


class TestFetchVideos:
    def test_一度だけ呼ぶ(self):
        service = FakeVideos()
        verify_search.fetch_videos(service, [VIDEO_ID_1, VIDEO_ID_2])
        assert len(service.calls) == 1

    def test_partにsnippetを渡す(self):
        service = FakeVideos()
        verify_search.fetch_videos(service, [VIDEO_ID_1])
        assert service.calls[0]["part"] == "snippet"

    def test_IDをカンマ区切りで渡す(self):
        service = FakeVideos()
        verify_search.fetch_videos(service, [VIDEO_ID_1, VIDEO_ID_2])
        assert service.calls[0]["id"] == f"{VIDEO_ID_1},{VIDEO_ID_2}"

    def test_IDで引ける形で返す(self):
        fetched = verify_search.fetch_videos(FakeVideos(), [VIDEO_ID_1, VIDEO_ID_2])
        assert fetched[VIDEO_ID_1]["snippet"]["title"] == TITLE_1

    def test_順序が入れ替わっても引ける(self):
        # videos.list は渡した順で返すと保証していない。位置で取ると入れ替わる。
        response = videos_response((VIDEO_ID_2, TITLE_2), (VIDEO_ID_1, TITLE_1))
        fetched = verify_search.fetch_videos(FakeVideos(response), [VIDEO_ID_1, VIDEO_ID_2])
        assert fetched[VIDEO_ID_1]["snippet"]["title"] == TITLE_1

    def test_欠けても落ちない(self):
        # ここでは落とさない。欠けたことは照合の項目として報告する。
        response = videos_response((VIDEO_ID_1, TITLE_1))
        fetched = verify_search.fetch_videos(FakeVideos(response), [VIDEO_ID_1, VIDEO_ID_2])
        assert VIDEO_ID_2 not in fetched

    def test_IDが空なら落ちる(self):
        with pytest.raises(verify_search.VerifyError):
            verify_search.fetch_videos(FakeVideos(), [])

    def test_50件を超えたら落ちる(self):
        # videos.list の id は最大 50 件。黙って切り捨てると、確かめていない
        # 動画が「一致した」に混ざる。
        with pytest.raises(verify_search.VerifyError):
            verify_search.fetch_videos(FakeVideos(), [VIDEO_ID_1] * 51)

    def test_itemsが無ければ落ちる(self):
        with pytest.raises(verify_search.VerifyError):
            verify_search.fetch_videos(FakeVideos({"kind": "youtube#videoListResponse"}), [VIDEO_ID_1])

    def test_エラーは翻訳して返す(self):
        error = http_error(403, "quota exceeded", "quotaExceeded")
        with pytest.raises(verify_search.VerifyError):
            verify_search.fetch_videos(FakeVideos(error=error), [VIDEO_ID_1])

    def test_内部表現を見せない(self):
        error = http_error(500, "Backend Error", "backendError")
        with pytest.raises(verify_search.VerifyError) as caught:
            verify_search.fetch_videos(FakeVideos(error=error), [VIDEO_ID_1])
        assert "when requesting" not in str(caught.value)

    def test_鍵を伏せる(self):
        # **応答本文に載った鍵**で確かめる。URI に入れただけだと、そもそも
        # str(error) を使っていないので伏せ字が無くても通ってしまい、
        # 「api_key を渡し忘れた」を検出できない（2026-08-16 に素通りで発覚）。
        error = http_error(400, f"API key not valid: {API_KEY}", "badRequest")
        with pytest.raises(verify_search.VerifyError) as caught:
            verify_search.fetch_videos(FakeVideos(error=error), [VIDEO_ID_1], api_key=API_KEY)
        assert API_KEY not in str(caught.value)

    def test_URIに載った鍵も出さない(self):
        uri = f"https://youtube.googleapis.com/youtube/v3/videos?id=x&key={API_KEY}"
        error = http_error(403, "quota exceeded", "quotaExceeded", uri=uri)
        with pytest.raises(verify_search.VerifyError) as caught:
            verify_search.fetch_videos(FakeVideos(error=error), [VIDEO_ID_1], api_key=API_KEY)
        assert API_KEY not in str(caught.value)


class TestRemoteChecks:
    def test_全部一致(self):
        fetched = verify_search.fetch_videos(FakeVideos(), [VIDEO_ID_1, VIDEO_ID_2])
        checks = verify_search.build_remote_checks(results(), fetched)
        assert verify_search.all_ok(checks)

    def test_実在を確かめる(self):
        fetched = verify_search.fetch_videos(FakeVideos(), [VIDEO_ID_1, VIDEO_ID_2])
        assert ok(verify_search.build_remote_checks(results(), fetched), "動画の実在")

    def test_欠けた動画を見つける(self):
        # **videos.list は存在しない ID を黙って落として 200 で返す。**
        # 件数を見ないと「返ってこなかった」が「一致した」になる。
        fetched = verify_search.fetch_videos(FakeVideos(videos_response((VIDEO_ID_1, TITLE_1))), [VIDEO_ID_1, VIDEO_ID_2])
        assert not ok(verify_search.build_remote_checks(results(), fetched), "動画の実在")

    def test_欠けた動画のIDを書く(self):
        fetched = verify_search.fetch_videos(FakeVideos(videos_response((VIDEO_ID_1, TITLE_1))), [VIDEO_ID_1, VIDEO_ID_2])
        checks = verify_search.build_remote_checks(results(), fetched)
        detail = " ".join(c.detail for c in checks)
        assert VIDEO_ID_2 in detail

    def test_タイトル一致を確かめる(self):
        fetched = verify_search.fetch_videos(FakeVideos(), [VIDEO_ID_1, VIDEO_ID_2])
        assert ok(verify_search.build_remote_checks(results(), fetched), "タイトル一致 [1]")

    def test_タイトル違いを見つける(self):
        response = videos_response((VIDEO_ID_1, "まったく別の題"), (VIDEO_ID_2, TITLE_2))
        fetched = verify_search.fetch_videos(FakeVideos(response), [VIDEO_ID_1, VIDEO_ID_2])
        assert not ok(verify_search.build_remote_checks(results(), fetched), "タイトル一致 [1]")

    def test_2件目のタイトル違いも見つける(self):
        response = videos_response((VIDEO_ID_1, TITLE_1), (VIDEO_ID_2, "別の題"))
        fetched = verify_search.fetch_videos(FakeVideos(response), [VIDEO_ID_1, VIDEO_ID_2])
        checks = verify_search.build_remote_checks(results(), fetched)
        assert ok(checks, "タイトル一致 [1]") and not ok(checks, "タイトル一致 [2]")

    def test_実体参照を解いてから比べる(self):
        # search.list 側は解いて保存してある。videos.list 側も解かないと
        # 同じ動画なのに永久に一致しない。
        payload = results(videos=[{"title": "A & B", "url": URL_1}], count=1)
        fetched = verify_search.fetch_videos(FakeVideos(videos_response((VIDEO_ID_1, "A &amp; B"))), [VIDEO_ID_1])
        assert ok(verify_search.build_remote_checks(payload, fetched), "タイトル一致 [1]")

    def test_返ってこなかった動画のタイトルは不一致(self):
        fetched = verify_search.fetch_videos(FakeVideos(videos_response((VIDEO_ID_1, TITLE_1))), [VIDEO_ID_1, VIDEO_ID_2])
        assert not ok(verify_search.build_remote_checks(results(), fetched), "タイトル一致 [2]")

    def test_タイトルが空なら不一致(self):
        # 空文字を「一致した」にしない。
        fetched = verify_search.fetch_videos(FakeVideos(videos_response((VIDEO_ID_1, ""))), [VIDEO_ID_1])
        payload = results(videos=[{"title": TITLE_1, "url": URL_1}], count=1)
        assert not ok(verify_search.build_remote_checks(payload, fetched), "タイトル一致 [1]")

    def test_両方空でも一致にしない(self):
        # 「空 == 空」で通してはいけない。両側とも取れていないだけで、
        # 同じ動画を指している証拠にはならない。
        fetched = verify_search.fetch_videos(FakeVideos(videos_response((VIDEO_ID_1, ""))), [VIDEO_ID_1])
        payload = results(videos=[{"title": "", "url": URL_1}], count=1)
        assert not ok(verify_search.build_remote_checks(payload, fetched), "タイトル一致 [1]")

    def test_全件ぶんの項目を作る(self):
        fetched = verify_search.fetch_videos(FakeVideos(), [VIDEO_ID_1, VIDEO_ID_2])
        checks = verify_search.build_remote_checks(results(), fetched)
        labels = [c.label for c in checks]
        assert sum(1 for label in labels if label.startswith("タイトル一致")) == 2


class TestAllOk:
    def test_全部OKならTrue(self):
        checks = [verify_search.Check("a", True), verify_search.Check("b", True)]
        assert verify_search.all_ok(checks)

    def test_1つでもNGならFalse(self):
        checks = [verify_search.Check("a", True), verify_search.Check("b", False)]
        assert not verify_search.all_ok(checks)

    def test_空はFalse(self):
        # all([]) は True。「何も確かめていない」が「全部一致」に化ける。
        assert not verify_search.all_ok([])


class TestFormatChecks:
    def test_OKを出す(self):
        assert "OK" in verify_search.format_checks([verify_search.Check("a", True)])

    def test_NGを出す(self):
        assert "NG" in verify_search.format_checks([verify_search.Check("a", False)])

    def test_項目名を出す(self):
        assert "タイトル一致" in verify_search.format_checks([verify_search.Check("タイトル一致", True)])

    def test_食い違いの中身を出す(self):
        text = verify_search.format_checks([verify_search.Check("a", False, "期待 X / 実際 Y")])
        assert "期待 X" in text


# ---------------------------------------------------------------- 入口


class TestParseArgs:
    def test_結果ファイルは必須(self):
        with pytest.raises(SystemExit):
            verify_search.parse_args(["--keyword", KEYWORD, "--expect-count", "2"])

    def test_キーワードは必須(self):
        # 期待値を応答から埋める逃げ道を作らない。
        with pytest.raises(SystemExit):
            verify_search.parse_args(["--results", "x.json", "--expect-count", "2"])

    def test_件数は必須(self):
        with pytest.raises(SystemExit):
            verify_search.parse_args(["--results", "x.json", "--keyword", KEYWORD])

    def test_APIキーはコマンドラインから渡せない(self):
        with pytest.raises(SystemExit):
            verify_search.parse_args(
                ["--results", "x.json", "--keyword", KEYWORD, "--expect-count", "2", "--api-key", API_KEY]
            )


class TestMain:
    def args(self, path: Path, keyword: str = KEYWORD, count: int = 2) -> list[str]:
        return ["--results", str(path), "--keyword", keyword, "--expect-count", str(count)]

    def test_全部一致なら0(self, tmp_path):
        path = write_results(tmp_path)
        assert verify_search.main(self.args(path), service_factory=lambda args: FakeVideos()) == 0

    def test_一致した旨を出す(self, tmp_path, capsys):
        path = write_results(tmp_path)
        verify_search.main(self.args(path), service_factory=lambda args: FakeVideos())
        # 「一致」だけを見ると、項目名の「タイトル一致 [1]」に当たって常に通る。
        assert "すべて一致" in capsys.readouterr().out

    def test_タイトル違いなら1(self, tmp_path):
        path = write_results(tmp_path)
        response = videos_response((VIDEO_ID_1, "別の題"), (VIDEO_ID_2, TITLE_2))
        assert verify_search.main(self.args(path), service_factory=lambda args: FakeVideos(response)) == 1

    def test_キーワード違いなら1(self, tmp_path):
        path = write_results(tmp_path)
        assert verify_search.main(self.args(path, keyword="別の語"), service_factory=lambda args: FakeVideos()) == 1

    def test_手元の照合が落ちたらAPIを呼ばない(self, tmp_path):
        # 落ちると分かっている実行でネットワークに出ない。
        called = []

        def factory(args):
            called.append(args)
            return FakeVideos()

        verify_search.main(self.args(write_results(tmp_path), keyword="別の語"), service_factory=factory)
        assert called == []

    def test_壊れたURLならAPIを呼ばない(self, tmp_path):
        called = []

        def factory(args):
            called.append(args)
            return FakeVideos()

        payload = results(videos=[{"title": TITLE_1, "url": "https://example.com/x"}], count=1)
        path = write_results(tmp_path, payload)
        verify_search.main(self.args(path, count=1), service_factory=factory)
        assert called == []

    def test_ファイルが無ければ1(self, tmp_path):
        args = self.args(tmp_path / "missing.json")
        assert verify_search.main(args, service_factory=lambda a: FakeVideos()) == 1

    def test_APIが落ちたら1(self, tmp_path):
        path = write_results(tmp_path)
        error = http_error(403, "quota exceeded", "quotaExceeded")
        assert verify_search.main(self.args(path), service_factory=lambda a: FakeVideos(error=error)) == 1

    def test_書き換えない(self, tmp_path):
        # このスクリプトは読むだけ。結果ファイルを更新しない。
        path = write_results(tmp_path)
        before = path.read_bytes()
        verify_search.main(self.args(path), service_factory=lambda args: FakeVideos())
        assert path.read_bytes() == before

    def test_NGのときは理由を出す(self, tmp_path, capsys):
        path = write_results(tmp_path)
        verify_search.main(self.args(path, keyword="別の語"), service_factory=lambda args: FakeVideos())
        assert "NG" in capsys.readouterr().out
