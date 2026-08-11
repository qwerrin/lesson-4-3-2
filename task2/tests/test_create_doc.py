"""create_doc のテスト。

課題1（ドライブ）と同じ3層に分けた。本物の Google には繋がない。
認証が本人の手作業でしか通せないので、偽の service で「呼び方」までを固定し、
残った「本当にドキュメントができるか」だけをスクリーンショットで示す。

1. 送る前に決めること … タイトル・テキストの取り込み・リクエストの組み立て
2. API の呼び方       … documents().create / batchUpdate に何を渡したかを記録して照合する
3. 画面と終了コード   … main が結果を「印字する」ことと、失敗時に 1 を返すことを別々に見る

期待値は要件（Docs API で新しいドキュメントを作成し、指定したテキストを挿入する）
と Docs API の仕様から書いた。実装を読んで数字を合わせにいかない。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import create_doc  # noqa: E402


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


class FakeRequest:
    def __init__(self, result, raises) -> None:
        self._result = result
        self._raises = raises

    def execute(self):
        if self._raises is not None:
            raise self._raises
        return self._result


class FakeDocuments:
    """documents().create / batchUpdate / get の呼ばれ方を記録するだけの偽物。"""

    def __init__(
        self,
        create_result=None,
        batch_result=None,
        get_result=None,
        create_raises=None,
        batch_raises=None,
        get_raises=None,
    ) -> None:
        self.create_result = (
            create_result if create_result is not None else {"documentId": "DOC_ID", "title": "T"}
        )
        self.batch_result = batch_result if batch_result is not None else {"replies": [{}]}
        self.get_result = get_result if get_result is not None else {}
        self.create_raises = create_raises
        self.batch_raises = batch_raises
        self.get_raises = get_raises
        self.create_calls: list[dict] = []
        self.batch_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return FakeRequest(self.create_result, self.create_raises)

    def batchUpdate(self, **kwargs):  # noqa: N802  Google のメソッド名に合わせる
        self.batch_calls.append(kwargs)
        return FakeRequest(self.batch_result, self.batch_raises)

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return FakeRequest(self.get_result, self.get_raises)


class FakeService:
    def __init__(self, documents: FakeDocuments) -> None:
        self._documents = documents

    def documents(self):
        return self._documents


@pytest.fixture
def documents() -> FakeDocuments:
    return FakeDocuments()


@pytest.fixture
def service(documents: FakeDocuments) -> FakeService:
    return FakeService(documents)


@pytest.fixture
def text_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.txt"
    path.write_text("ファイルから読んだ本文", encoding="utf-8")
    return path


# ================================================================ 1. 送る前に決めること
# ---------------------------------------------------------------- タイトル


class TestResolveTitle:
    def test_指定したタイトルをそのまま使う(self):
        assert create_doc.resolve_title("課題2のドキュメント") == "課題2のドキュメント"

    def test_前後の空白を落とす(self):
        assert create_doc.resolve_title("  課題2  ") == "課題2"

    def test_空文字は拒否する(self):
        with pytest.raises(create_doc.DocError):
            create_doc.resolve_title("")

    def test_空白だけのタイトルは拒否する(self):
        with pytest.raises(create_doc.DocError):
            create_doc.resolve_title("   ")

    def test_未指定でテキストファイルがあればファイル名を使う(self, text_file: Path):
        assert create_doc.resolve_title(None, text_file) == "sample"

    def test_未指定でテキストファイルの拡張子は外す(self, tmp_path: Path):
        path = tmp_path / "議事録.md"
        path.write_text("本文", encoding="utf-8")
        assert create_doc.resolve_title(None, path) == "議事録"

    def test_未指定でテキストファイルも無ければ既定のタイトルになる(self):
        assert create_doc.resolve_title(None, None) == create_doc.DEFAULT_TITLE

    def test_既定のタイトルは空でない(self):
        assert create_doc.DEFAULT_TITLE.strip()


# ---------------------------------------------------------------- テキストの取り込み


class TestResolveText:
    def test_引数のテキストをそのまま使う(self):
        assert create_doc.resolve_text(text="こんにちは") == "こんにちは"

    def test_ファイルの中身を読む(self, text_file: Path):
        assert create_doc.resolve_text(text_file=text_file) == "ファイルから読んだ本文"

    def test_両方指定したら拒否する(self, text_file: Path):
        with pytest.raises(create_doc.DocError):
            create_doc.resolve_text(text="こんにちは", text_file=text_file)

    def test_どちらも指定しなければ拒否する(self):
        with pytest.raises(create_doc.DocError):
            create_doc.resolve_text()

    def test_ファイルが無ければ拒否する(self, tmp_path: Path):
        with pytest.raises(create_doc.DocError) as caught:
            create_doc.resolve_text(text_file=tmp_path / "ない.txt")
        # 「フォルダは読めません」に化けさせない。探す場所が変わってしまう。
        assert "見つかりません" in str(caught.value)

    def test_フォルダを渡したら拒否する(self, tmp_path: Path):
        with pytest.raises(create_doc.DocError) as caught:
            create_doc.resolve_text(text_file=tmp_path)
        assert "フォルダ" in str(caught.value)

    def test_空文字は拒否する(self):
        # Docs API は空文字の insertText を 400 で弾く。手前で止める。
        with pytest.raises(create_doc.DocError):
            create_doc.resolve_text(text="")

    def test_空のファイルは拒否する(self, tmp_path: Path):
        path = tmp_path / "空.txt"
        path.write_text("", encoding="utf-8")
        with pytest.raises(create_doc.DocError):
            create_doc.resolve_text(text_file=path)

    def test_空白だけのテキストは通す(self):
        # 意味のある空白かもしれないので、空文字とは区別する。
        assert create_doc.resolve_text(text="   ") == "   "

    def test_UTF8のBOMを落とす(self, tmp_path: Path):
        # Windows のメモ帳は BOM を付けて保存する。先頭に \ufeff が残ると
        # ドキュメントの1文字目が見えないゴミになる。
        path = tmp_path / "bom.txt"
        path.write_text("BOM付き", encoding="utf-8-sig")
        assert create_doc.resolve_text(text_file=path) == "BOM付き"

    def test_CRLFをLFに直す(self, tmp_path: Path):
        # Docs の改行は LF。CR をそのまま送ると本文に余分な文字が残る。
        path = tmp_path / "crlf.txt"
        path.write_bytes("1行目\r\n2行目\r\n".encode("utf-8"))
        assert create_doc.resolve_text(text_file=path) == "1行目\n2行目\n"

    def test_単独のCRもLFに直す(self):
        assert create_doc.resolve_text(text="1行目\r2行目") == "1行目\n2行目"

    def test_LFはそのまま残す(self):
        assert create_doc.resolve_text(text="1行目\n2行目") == "1行目\n2行目"


class TestNormalizeNewlines:
    def test_CRLFをLFにする(self):
        assert create_doc.normalize_newlines("a\r\nb") == "a\nb"

    def test_CRをLFにする(self):
        assert create_doc.normalize_newlines("a\rb") == "a\nb"

    def test_LFは変えない(self):
        assert create_doc.normalize_newlines("a\nb") == "a\nb"

    def test_改行が無ければ変えない(self):
        assert create_doc.normalize_newlines("あいうえお") == "あいうえお"


# ---------------------------------------------------------------- リクエストの組み立て


class TestBuildInsertRequests:
    def test_insertTextを1つ作る(self):
        requests = create_doc.build_insert_requests("本文")
        assert len(requests) == 1
        assert "insertText" in requests[0]

    def test_挿入位置は1(self):
        # 0 は本文の外（sectionBreak の位置）。段落の中でないと挿入できず 400 になる。
        requests = create_doc.build_insert_requests("本文")
        assert requests[0]["insertText"]["location"]["index"] == 1

    def test_テキストをそのまま入れる(self):
        requests = create_doc.build_insert_requests("あいう\nえお")
        assert requests[0]["insertText"]["text"] == "あいう\nえお"

    def test_endOfSegmentLocationは使わない(self):
        # 末尾追記ではなく先頭挿入で固定する。位置がぶれると照合できない。
        requests = create_doc.build_insert_requests("本文")
        assert "endOfSegmentLocation" not in requests[0]["insertText"]

    def test_本文開始インデックスの定数が1(self):
        assert create_doc.BODY_START_INDEX == 1


# ================================================================ 2. API の呼び方
# ---------------------------------------------------------------- ドキュメントの作成


class TestCreateDocument:
    def test_documentsのcreateを呼ぶ(self, service, documents):
        create_doc.create_document(service, "タイトル")
        assert len(documents.create_calls) == 1

    def test_bodyにタイトルを入れる(self, service, documents):
        create_doc.create_document(service, "タイトル")
        assert documents.create_calls[0]["body"]["title"] == "タイトル"

    def test_作成時に本文を入れない(self, service, documents):
        # Docs API の documents.create は title 以外を無視する。
        # 入れると「送ったのに反映されない」形の勘違いが起きる。
        create_doc.create_document(service, "タイトル")
        assert "body" not in documents.create_calls[0]["body"]

    def test_作成結果を返す(self, service, documents):
        documents.create_result = {"documentId": "ABC", "title": "タイトル"}
        assert create_doc.create_document(service, "タイトル")["documentId"] == "ABC"

    def test_documentIdが返らなければ失敗にする(self, service, documents):
        documents.create_result = {"title": "タイトル"}
        with pytest.raises(create_doc.DocError):
            create_doc.create_document(service, "タイトル")

    def test_documentIdが空文字なら失敗にする(self, service, documents):
        documents.create_result = {"documentId": "", "title": "タイトル"}
        with pytest.raises(create_doc.DocError):
            create_doc.create_document(service, "タイトル")


# ---------------------------------------------------------------- テキストの挿入


class TestInsertText:
    def test_batchUpdateを呼ぶ(self, service, documents):
        create_doc.insert_text(service, "DOC", "本文")
        assert len(documents.batch_calls) == 1

    def test_documentIdを渡す(self, service, documents):
        create_doc.insert_text(service, "DOC", "本文")
        assert documents.batch_calls[0]["documentId"] == "DOC"

    def test_requestsにinsertTextを渡す(self, service, documents):
        create_doc.insert_text(service, "DOC", "本文")
        requests = documents.batch_calls[0]["body"]["requests"]
        assert requests == create_doc.build_insert_requests("本文")

    def test_挿入結果を返す(self, service, documents):
        documents.batch_result = {"documentId": "DOC", "replies": [{}]}
        assert create_doc.insert_text(service, "DOC", "本文")["documentId"] == "DOC"


# ---------------------------------------------------------------- 作成と挿入をまとめる


class TestCreateDocumentWithText:
    def test_作成してから挿入する(self, service, documents):
        create_doc.create_document_with_text(service, "タイトル", "本文")
        assert len(documents.create_calls) == 1
        assert len(documents.batch_calls) == 1

    def test_作成で得たIDに挿入する(self, service, documents):
        documents.create_result = {"documentId": "REAL_ID", "title": "タイトル"}
        create_doc.create_document_with_text(service, "タイトル", "本文")
        assert documents.batch_calls[0]["documentId"] == "REAL_ID"

    def test_作成に失敗したら挿入しない(self, service, documents):
        documents.create_raises = make_http_error(403, "denied")
        with pytest.raises(create_doc.DocError):
            create_doc.create_document_with_text(service, "タイトル", "本文")
        assert documents.batch_calls == []

    def test_挿入に失敗したら残ったドキュメントのIDを伝える(self, service, documents):
        # 作成は通って挿入だけ落ちると、空のドキュメントがドライブに残る。
        # ID を出さないと、どれを消せばいいか分からない。
        documents.create_result = {"documentId": "LEFT_BEHIND", "title": "T"}
        documents.batch_raises = make_http_error(403, "denied")
        with pytest.raises(create_doc.DocError) as caught:
            create_doc.create_document_with_text(service, "タイトル", "本文")
        assert "LEFT_BEHIND" in str(caught.value)

    def test_挿入に失敗しても元のエラー内容を残す(self, service, documents):
        documents.batch_raises = make_http_error(403, "denied")
        with pytest.raises(create_doc.DocError) as caught:
            create_doc.create_document_with_text(service, "タイトル", "本文")
        assert "403" in str(caught.value)

    def test_戻り値にドキュメントIDが入る(self, service, documents):
        documents.create_result = {"documentId": "REAL_ID", "title": "タイトル"}
        result = create_doc.create_document_with_text(service, "タイトル", "本文")
        assert result["documentId"] == "REAL_ID"

    def test_戻り値にタイトルが入る(self, service, documents):
        documents.create_result = {"documentId": "REAL_ID", "title": "タイトル"}
        result = create_doc.create_document_with_text(service, "タイトル", "本文")
        assert result["title"] == "タイトル"

    def test_戻り値にリンクが入る(self, service, documents):
        documents.create_result = {"documentId": "REAL_ID", "title": "タイトル"}
        result = create_doc.create_document_with_text(service, "タイトル", "本文")
        assert result["url"] == create_doc.document_url("REAL_ID")


class TestDocumentUrl:
    def test_ドキュメントIDからリンクを組む(self):
        assert create_doc.document_url("ABC") == "https://docs.google.com/document/d/ABC/edit"


# ---------------------------------------------------------------- エラーの翻訳


class TestErrors:
    def test_403は権限とAPI有効化を案内する(self, service, documents):
        documents.create_raises = make_http_error(403, "The caller does not have permission")
        with pytest.raises(create_doc.DocError) as caught:
            create_doc.create_document(service, "タイトル")
        assert "Docs API" in str(caught.value)

    def test_APIが無効なら有効化の手順を案内する(self, service, documents):
        # 「権限が足りない」と同じ文面にしない。原因が別なので、探す場所も別になる。
        documents.create_raises = make_http_error(
            403, "Google Docs API has not been used in project 123 before or it is disabled"
        )
        with pytest.raises(create_doc.DocError) as caught:
            create_doc.create_document(service, "タイトル")
        assert "ライブラリ" in str(caught.value)

    def test_権限不足に有効化の手順を混ぜない(self, service, documents):
        documents.create_raises = make_http_error(403, "The caller does not have permission")
        with pytest.raises(create_doc.DocError) as caught:
            create_doc.create_document(service, "タイトル")
        assert "ライブラリ" not in str(caught.value)

    def test_404はドキュメントが見つからないと伝える(self, service, documents):
        documents.batch_raises = make_http_error(404, "Requested entity was not found.")
        with pytest.raises(create_doc.DocError) as caught:
            create_doc.insert_text(service, "DOC", "本文")
        assert "見つかりません" in str(caught.value)

    def test_ステータスコードを残す(self, service, documents):
        documents.create_raises = make_http_error(400, "Invalid requests[0].insertText")
        with pytest.raises(create_doc.DocError) as caught:
            create_doc.create_document(service, "タイトル")
        assert "400" in str(caught.value)

    def test_APIの説明文を残す(self, service, documents):
        documents.create_raises = make_http_error(500, "Internal error encountered.")
        with pytest.raises(create_doc.DocError) as caught:
            create_doc.create_document(service, "タイトル")
        assert "Internal error encountered." in str(caught.value)

    def test_知らないステータスでも落ちない(self, service, documents):
        documents.create_raises = make_http_error(429, "Quota exceeded")
        with pytest.raises(create_doc.DocError):
            create_doc.create_document(service, "タイトル")

    def test_知らないステータスでもコードと説明文を残す(self, service, documents):
        # 分岐に無いステータスこそ、手がかりが本文しかない。
        documents.create_raises = make_http_error(429, "Quota exceeded")
        with pytest.raises(create_doc.DocError) as caught:
            create_doc.create_document(service, "タイトル")
        assert "429" in str(caught.value)
        assert "Quota exceeded" in str(caught.value)


# ================================================================ 認証


class FakeCredentials:
    def __init__(self, *, valid=True, expired=False, refresh_token="R", scopes=None) -> None:
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self._scopes = list(scopes or create_doc.DEFAULT_SCOPES)
        self.refreshed = False

    def has_scopes(self, wanted) -> bool:
        return set(wanted).issubset(set(self._scopes))

    def to_json(self) -> str:
        return json.dumps({"token": "T", "scopes": self._scopes})


def write_token(path: Path, scopes) -> None:
    path.write_text(json.dumps({"token": "T", "scopes": list(scopes)}), encoding="utf-8")


class TestLoadCredentials:
    @pytest.fixture(autouse=True)
    def _patch_reader(self, monkeypatch):
        """token.json の読み込みだけ差し替える。google-auth の実物は通さない。"""
        self.stored: dict[str, FakeCredentials] = {}

        def fake_read(token_path: Path):
            return self.stored.get(str(token_path))

        monkeypatch.setattr(create_doc, "_read_token", fake_read)

    def test_有効なトークンならブラウザを開かない(self, tmp_path: Path):
        token = tmp_path / "token.json"
        self.stored[str(token)] = FakeCredentials(valid=True)
        called = []

        create_doc.load_credentials(
            tmp_path / "credentials.json",
            token,
            flow_factory=lambda *a, **k: called.append(1),
        )
        assert called == []

    def test_期限切れならリフレッシュする(self, tmp_path: Path):
        token = tmp_path / "token.json"
        credentials = FakeCredentials(valid=False, expired=True, refresh_token="R")
        self.stored[str(token)] = credentials

        def refresher(c):
            c.refreshed = True
            c.valid = True

        create_doc.load_credentials(
            tmp_path / "credentials.json", token, refresher=refresher
        )
        assert credentials.refreshed

    def test_リフレッシュしたトークンを保存し直す(self, tmp_path: Path):
        token = tmp_path / "token.json"
        credentials = FakeCredentials(valid=False, expired=True, refresh_token="R")
        self.stored[str(token)] = credentials

        create_doc.load_credentials(
            tmp_path / "credentials.json", token, refresher=lambda c: None
        )
        assert token.exists()

    def test_権限が足りなければ同意を取り直す(self, tmp_path: Path):
        # 課題1で作った token.json は drive.file しか持っていない。
        # documents を要求したら必ず取り直しになる。ここが素通りすると
        # 「権限不足に気づかないまま 403 で落ちる」形になる。
        token = tmp_path / "token.json"
        self.stored[str(token)] = FakeCredentials(
            valid=True, scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        credentials_path = tmp_path / "credentials.json"
        credentials_path.write_text("{}", encoding="utf-8")
        called = []

        class FakeFlow:
            def run_local_server(self, port=0):
                called.append(port)
                return FakeCredentials(valid=True)

        create_doc.load_credentials(
            credentials_path, token, flow_factory=lambda *a, **k: FakeFlow()
        )
        assert called

    def test_要求するスコープをflowに渡す(self, tmp_path: Path):
        token = tmp_path / "token.json"
        credentials_path = tmp_path / "credentials.json"
        credentials_path.write_text("{}", encoding="utf-8")
        passed = []

        class FakeFlow:
            def run_local_server(self, port=0):
                return FakeCredentials(valid=True)

        def factory(path, scopes):
            passed.append(list(scopes))
            return FakeFlow()

        create_doc.load_credentials(credentials_path, token, flow_factory=factory)
        assert passed == [list(create_doc.DEFAULT_SCOPES)]

    def test_取り直したトークンを保存する(self, tmp_path: Path):
        token = tmp_path / "token.json"
        credentials_path = tmp_path / "credentials.json"
        credentials_path.write_text("{}", encoding="utf-8")

        class FakeFlow:
            def run_local_server(self, port=0):
                return FakeCredentials(valid=True)

        create_doc.load_credentials(
            credentials_path, token, flow_factory=lambda *a, **k: FakeFlow()
        )
        assert token.exists()

    def test_credentialsが無ければ案内つきで失敗する(self, tmp_path: Path):
        with pytest.raises(create_doc.DocError) as caught:
            create_doc.load_credentials(
                tmp_path / "credentials.json", tmp_path / "token.json"
            )
        assert "credentials.json" in str(caught.value)


class TestReadToken:
    def test_トークンが無ければNone(self, tmp_path: Path):
        assert create_doc._read_token(tmp_path / "token.json") is None

    def test_壊れたトークンはNone(self, tmp_path: Path):
        # 取り直せばいいので、読めないだけで落とさない。
        token = tmp_path / "token.json"
        token.write_text("これはJSONではない", encoding="utf-8")
        assert create_doc._read_token(token) is None


class TestScopes:
    def test_documentsスコープを要求する(self):
        assert "https://www.googleapis.com/auth/documents" in create_doc.DEFAULT_SCOPES

    def test_読み取り専用スコープでは作成できないので使わない(self):
        assert "https://www.googleapis.com/auth/documents.readonly" not in create_doc.DEFAULT_SCOPES


# ================================================================ 3. 画面と終了コード


class TestFormatResult:
    @pytest.fixture
    def created(self) -> dict:
        return {
            "documentId": "DOC_ID",
            "title": "課題2のドキュメント",
            "url": "https://docs.google.com/document/d/DOC_ID/edit",
        }

    def test_タイトルを出す(self, created):
        assert "課題2のドキュメント" in create_doc.format_result(created)

    def test_ドキュメントIDを出す(self, created):
        assert "DOC_ID" in create_doc.format_result(created)

    def test_リンクを出す(self, created):
        assert "https://docs.google.com/document/d/DOC_ID/edit" in create_doc.format_result(created)

    def test_文字数を出す(self, created):
        assert "5" in create_doc.format_result({**created, "insertedLength": 5})


class TestParseArgs:
    def test_既定の資格情報パスは相対パス(self):
        args = create_doc.parse_args(["--text", "本文"])
        # 公開する実行画面に C:\Users\... を写さないため、絶対パスにしない。
        assert not Path(args.credentials).is_absolute()

    def test_既定のトークンパスは相対パス(self):
        args = create_doc.parse_args(["--text", "本文"])
        assert not Path(args.token).is_absolute()

    def test_タイトルの既定はNone(self):
        assert create_doc.parse_args(["--text", "本文"]).title is None

    def test_テキストファイルを受け取る(self):
        assert create_doc.parse_args(["--text-file", "a.txt"]).text_file == "a.txt"


class TestMain:
    def _factory(self, service):
        return lambda args: service

    def test_成功したら0を返す(self, service, capsys):
        code = create_doc.main(
            ["--text", "本文", "--title", "T"], service_factory=self._factory(service)
        )
        assert code == 0

    def test_結果を印字する(self, service, documents, capsys):
        documents.create_result = {"documentId": "PRINTED_ID", "title": "T"}
        create_doc.main(
            ["--text", "本文", "--title", "T"], service_factory=self._factory(service)
        )
        assert "PRINTED_ID" in capsys.readouterr().out

    def test_失敗したら1を返す(self, service, documents):
        documents.create_raises = make_http_error(403, "denied")
        code = create_doc.main(
            ["--text", "本文", "--title", "T"], service_factory=self._factory(service)
        )
        assert code == 1

    def test_失敗はstderrに出す(self, service, documents, capsys):
        documents.create_raises = make_http_error(403, "denied")
        create_doc.main(
            ["--text", "本文", "--title", "T"], service_factory=self._factory(service)
        )
        assert "エラー" in capsys.readouterr().err

    def test_テキスト未指定は1を返す(self, service):
        code = create_doc.main(["--title", "T"], service_factory=self._factory(service))
        assert code == 1

    def test_テキスト未指定ならAPIを呼ばない(self, service, documents):
        create_doc.main(["--title", "T"], service_factory=self._factory(service))
        assert documents.create_calls == []

    def test_テキスト未指定なら認証もしない(self, service):
        # service を作る＝認証で、初回は本人のブラウザが開く。
        # 落ちると分かっている実行で同意画面を出さない。
        # API を呼んだかだけを見ていると、ここが素通りする。
        called = []

        def factory(args):
            called.append(args)
            return service

        create_doc.main(["--title", "T"], service_factory=factory)
        assert called == []

    def test_ファイルから読んだ本文を挿入する(self, service, documents, text_file: Path):
        create_doc.main(
            ["--text-file", str(text_file)], service_factory=self._factory(service)
        )
        inserted = documents.batch_calls[0]["body"]["requests"][0]["insertText"]["text"]
        assert inserted == "ファイルから読んだ本文"

    def test_ファイル名がタイトルになる(self, service, documents, text_file: Path):
        create_doc.main(
            ["--text-file", str(text_file)], service_factory=self._factory(service)
        )
        assert documents.create_calls[0]["body"]["title"] == "sample"
