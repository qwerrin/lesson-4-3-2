"""drive_upload のテスト。

この課題は「Google ドライブに実際に上がること」がゴールなので、
最後の一歩だけは手で確かめるしかない。そこで検証できる範囲を3層に分けた。

1. 引数の組み立て … 送るファイル名・MIME タイプ・親フォルダを、送る前に確定させる
2. API の呼び方   … 偽の service を渡し、files().create に何を渡したかを記録して照合する
3. 画面と終了コード … main が結果を「印字する」ことと、失敗時に 1 を返すことを別々に見る

2 で本物の Google に繋がないのは、認証が本人の手作業でしか通せないから。
その代わり「呼び方が正しいか」はここで全部固定して、
残った「本当に上がるか」だけをスクリーンショットで示す。

期待値は要件（ローカルのファイルを Google ドライブにアップロードする）から書いた。
実装を読んで数字を合わせにいかない。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import drive_upload  # noqa: E402


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
    def __init__(self, files: "FakeFiles") -> None:
        self._files = files

    def execute(self):
        if self._files.raises is not None:
            raise self._files.raises
        return self._files.result


class FakeFiles:
    """files().create(...).execute() の呼ばれ方を記録するだけの偽物。"""

    def __init__(self, result=None, raises=None) -> None:
        self.result = result if result is not None else {"id": "ID", "name": "NAME"}
        self.raises = raises
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeRequest(self)


class FakeService:
    def __init__(self, files: FakeFiles) -> None:
        self._files = files

    def files(self):
        return self._files


@pytest.fixture
def local_file(tmp_path: Path) -> Path:
    path = tmp_path / "報告書.txt"
    path.write_text("ひとつめの行\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- 1. 引数の組み立て


class TestValidateLocalFile:
    def test_実在するファイルはそのまま返る(self, local_file: Path):
        assert drive_upload.validate_local_file(local_file) == local_file

    def test_文字列で渡してもPathで返る(self, local_file: Path):
        assert drive_upload.validate_local_file(str(local_file)) == local_file

    def test_存在しないファイルはエラーになりパスが出る(self, tmp_path: Path):
        missing = tmp_path / "ない.txt"
        with pytest.raises(drive_upload.UploadError) as exc:
            drive_upload.validate_local_file(missing)
        assert "ない.txt" in str(exc.value)

    def test_ディレクトリを渡すとエラーになる(self, tmp_path: Path):
        with pytest.raises(drive_upload.UploadError) as exc:
            drive_upload.validate_local_file(tmp_path)
        assert "ファイル" in str(exc.value)


class TestResolveUploadName:
    def test_既定ではローカルのファイル名をそのまま使う(self, local_file: Path):
        assert drive_upload.resolve_upload_name(local_file) == "報告書.txt"

    def test_明示した名前が優先される(self, local_file: Path):
        assert drive_upload.resolve_upload_name(local_file, "別名.txt") == "別名.txt"

    def test_前後の空白は落とす(self, local_file: Path):
        assert drive_upload.resolve_upload_name(local_file, "  別名.txt  ") == "別名.txt"

    def test_空白だけの名前はエラーになる(self, local_file: Path):
        with pytest.raises(drive_upload.UploadError):
            drive_upload.resolve_upload_name(local_file, "   ")


class TestGuessMimeType:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("a.txt", "text/plain"),
            ("a.csv", "text/csv"),
            ("a.png", "image/png"),
            ("a.pdf", "application/pdf"),
            ("a.json", "application/json"),
        ],
    )
    def test_拡張子から判定する(self, tmp_path: Path, filename: str, expected: str):
        assert drive_upload.guess_mime_type(tmp_path / filename) == expected

    def test_知らない拡張子はoctet_streamにする(self, tmp_path: Path):
        assert drive_upload.guess_mime_type(tmp_path / "a.zzz") == "application/octet-stream"

    def test_拡張子が無い場合もoctet_streamにする(self, tmp_path: Path):
        assert drive_upload.guess_mime_type(tmp_path / "README") == "application/octet-stream"


class TestBuildMetadata:
    def test_フォルダ未指定なら親を付けない(self):
        body = drive_upload.build_metadata("a.txt")
        assert body == {"name": "a.txt"}

    def test_フォルダ指定なら親に入れる(self):
        body = drive_upload.build_metadata("a.txt", "FOLDER123")
        assert body == {"name": "a.txt", "parents": ["FOLDER123"]}

    def test_空文字のフォルダIDは指定なしとして扱う(self):
        assert "parents" not in drive_upload.build_metadata("a.txt", "")
        assert "parents" not in drive_upload.build_metadata("a.txt", "   ")


# ---------------------------------------------------------------- 2. API の呼び方


class TestUploadFile:
    def test_ファイル名とMIMEタイプを載せてcreateを呼ぶ(self, local_file: Path):
        files = FakeFiles(result={"id": "ID1", "name": "報告書.txt"})
        drive_upload.upload_file(FakeService(files), local_file)

        assert len(files.calls) == 1
        call = files.calls[0]
        assert call["body"] == {"name": "報告書.txt"}
        assert call["media_body"].mimetype() == "text/plain"

    def test_IDとリンクを取り出せるようfieldsを要求する(self, local_file: Path):
        files = FakeFiles()
        drive_upload.upload_file(FakeService(files), local_file)

        fields = files.calls[0]["fields"]
        for key in ("id", "name", "webViewLink"):
            assert key in fields

    def test_APIの戻り値をそのまま返す(self, local_file: Path):
        expected = {"id": "ID1", "name": "報告書.txt", "webViewLink": "https://drive.example/1"}
        files = FakeFiles(result=expected)
        assert drive_upload.upload_file(FakeService(files), local_file) == expected

    def test_名前を指定するとその名前で作られる(self, local_file: Path):
        files = FakeFiles()
        drive_upload.upload_file(FakeService(files), local_file, name="別名.txt")
        assert files.calls[0]["body"]["name"] == "別名.txt"

    def test_フォルダを指定すると親に入る(self, local_file: Path):
        files = FakeFiles()
        drive_upload.upload_file(FakeService(files), local_file, folder_id="FOLDER123")
        assert files.calls[0]["body"]["parents"] == ["FOLDER123"]

    def test_中断しても再開できるようresumableで送る(self, local_file: Path):
        files = FakeFiles()
        drive_upload.upload_file(FakeService(files), local_file)
        assert files.calls[0]["media_body"].resumable() is True

    def test_送る前に存在チェックをする(self, tmp_path: Path):
        files = FakeFiles()
        with pytest.raises(drive_upload.UploadError):
            drive_upload.upload_file(FakeService(files), tmp_path / "ない.txt")
        assert files.calls == []

    def test_404はフォルダIDとスコープの話に翻訳する(self, local_file: Path):
        files = FakeFiles(raises=make_http_error(404, "File not found: FOLDER123."))
        with pytest.raises(drive_upload.UploadError) as exc:
            drive_upload.upload_file(FakeService(files), local_file, folder_id="FOLDER123")

        message = str(exc.value)
        assert "FOLDER123" in message
        assert "404" in message
        assert "drive.file" in message

    def test_403は権限の話として出す(self, local_file: Path):
        files = FakeFiles(raises=make_http_error(403, "Insufficient permission"))
        with pytest.raises(drive_upload.UploadError) as exc:
            drive_upload.upload_file(FakeService(files), local_file)

        message = str(exc.value)
        assert "403" in message
        assert "権限" in message

    def test_その他のHTTPエラーはAPIの文言を残す(self, local_file: Path):
        files = FakeFiles(raises=make_http_error(500, "Backend Error"))
        with pytest.raises(drive_upload.UploadError) as exc:
            drive_upload.upload_file(FakeService(files), local_file)

        message = str(exc.value)
        assert "500" in message
        assert "Backend Error" in message


# ---------------------------------------------------------------- 3. 認証の分岐


def write_token(path: Path, *, scopes: list[str], expiry: str = "2099-01-01T00:00:00Z") -> Path:
    """token.json を書く。

    expiry を省略すると google-auth は「保存されていない＝期限切れ」とみなすので、
    既定で未来の日時を入れておく。ここを空にすると、有効なトークンのテストが
    リフレッシュ経路に落ちて意味を失う。
    """
    payload = {
        "token": "ACCESS",
        "refresh_token": "REFRESH",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "CLIENT",
        "client_secret": "SECRET",
        "scopes": scopes,
        "expiry": expiry,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class FakeFlow:
    """InstalledAppFlow の代わり。ブラウザを開かずに呼ばれたことだけ残す。"""

    instances: list["FakeFlow"] = []

    def __init__(self, client_secrets_file, scopes) -> None:
        self.client_secrets_file = client_secrets_file
        self.scopes = scopes
        self.ran = False
        FakeFlow.instances.append(self)

    def run_local_server(self, **kwargs):
        self.ran = True
        from google.oauth2.credentials import Credentials

        return Credentials(
            token="NEW_ACCESS",
            refresh_token="NEW_REFRESH",
            token_uri="https://oauth2.googleapis.com/token",
            client_id="CLIENT",
            client_secret="SECRET",
            scopes=list(self.scopes),
        )


@pytest.fixture(autouse=True)
def _reset_flow():
    FakeFlow.instances = []
    yield
    FakeFlow.instances = []


class TestLoadCredentials:
    def test_有効なトークンがあればブラウザを開かない(self, tmp_path: Path):
        token = write_token(tmp_path / "token.json", scopes=list(drive_upload.DEFAULT_SCOPES))

        creds = drive_upload.load_credentials(
            credentials_path=tmp_path / "credentials.json",
            token_path=token,
            scopes=drive_upload.DEFAULT_SCOPES,
            flow_factory=FakeFlow,
        )

        assert creds.token == "ACCESS"
        assert FakeFlow.instances == []

    def test_トークンが無ければ同意フローを走らせて保存する(self, tmp_path: Path):
        credentials = tmp_path / "credentials.json"
        credentials.write_text("{}", encoding="utf-8")
        token_path = tmp_path / "token.json"

        creds = drive_upload.load_credentials(
            credentials_path=credentials,
            token_path=token_path,
            scopes=drive_upload.DEFAULT_SCOPES,
            flow_factory=FakeFlow,
        )

        assert creds.token == "NEW_ACCESS"
        assert FakeFlow.instances[0].ran is True
        assert token_path.exists()
        assert json.loads(token_path.read_text(encoding="utf-8"))["refresh_token"] == "NEW_REFRESH"

    def test_期限切れならリフレッシュしてブラウザを開かない(self, tmp_path: Path):
        token = write_token(
            tmp_path / "token.json",
            scopes=list(drive_upload.DEFAULT_SCOPES),
            expiry="2000-01-01T00:00:00Z",
        )
        refreshed: list[object] = []

        def fake_refresher(creds):
            refreshed.append(creds)
            creds.token = "REFRESHED"
            creds.expiry = None

        drive_upload.load_credentials(
            credentials_path=tmp_path / "credentials.json",
            token_path=token,
            scopes=drive_upload.DEFAULT_SCOPES,
            flow_factory=FakeFlow,
            refresher=fake_refresher,
        )

        assert len(refreshed) == 1
        assert FakeFlow.instances == []

    def test_リフレッシュ後のトークンを保存し直す(self, tmp_path: Path):
        token = write_token(
            tmp_path / "token.json",
            scopes=list(drive_upload.DEFAULT_SCOPES),
            expiry="2000-01-01T00:00:00Z",
        )

        def fake_refresher(creds):
            creds.token = "REFRESHED"
            creds.expiry = None

        drive_upload.load_credentials(
            credentials_path=tmp_path / "credentials.json",
            token_path=token,
            scopes=drive_upload.DEFAULT_SCOPES,
            flow_factory=FakeFlow,
            refresher=fake_refresher,
        )

        assert json.loads(token.read_text(encoding="utf-8"))["token"] == "REFRESHED"

    def test_権限が足りないトークンは同意を取り直す(self, tmp_path: Path):
        credentials = tmp_path / "credentials.json"
        credentials.write_text("{}", encoding="utf-8")
        token = write_token(
            tmp_path / "token.json",
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )

        drive_upload.load_credentials(
            credentials_path=credentials,
            token_path=token,
            scopes=drive_upload.DEFAULT_SCOPES,
            flow_factory=FakeFlow,
        )

        assert FakeFlow.instances[0].ran is True

    def test_壊れたトークンは捨てて取り直す(self, tmp_path: Path):
        credentials = tmp_path / "credentials.json"
        credentials.write_text("{}", encoding="utf-8")
        token = tmp_path / "token.json"
        token.write_text("これはJSONではない", encoding="utf-8")

        creds = drive_upload.load_credentials(
            credentials_path=credentials,
            token_path=token,
            scopes=drive_upload.DEFAULT_SCOPES,
            flow_factory=FakeFlow,
        )

        assert creds.token == "NEW_ACCESS"

    def test_credentials_jsonが無ければ手順つきで止まる(self, tmp_path: Path):
        with pytest.raises(drive_upload.UploadError) as exc:
            drive_upload.load_credentials(
                credentials_path=tmp_path / "credentials.json",
                token_path=tmp_path / "token.json",
                scopes=drive_upload.DEFAULT_SCOPES,
                flow_factory=FakeFlow,
            )

        message = str(exc.value)
        assert "credentials.json" in message
        assert "OAuth" in message
        assert FakeFlow.instances == []


# ---------------------------------------------------------------- 4. 画面と終了コード


class TestParseArgs:
    def test_既定の資格情報パスは相対で持つ(self):
        args = drive_upload.parse_args(["a.txt"])
        assert not Path(args.credentials).is_absolute()
        assert not Path(args.token).is_absolute()

    def test_既定は最小権限のスコープを使う(self):
        args = drive_upload.parse_args(["a.txt"])
        assert drive_upload.scopes_for(args) == drive_upload.DEFAULT_SCOPES

    def test_full_drive_scopeで広い権限に切り替わる(self):
        args = drive_upload.parse_args(["a.txt", "--full-drive-scope"])
        assert drive_upload.scopes_for(args) == drive_upload.FULL_SCOPES


class TestFormatResult:
    def test_名前とIDとリンクを並べる(self):
        text = drive_upload.format_result(
            {"id": "ID1", "name": "報告書.txt", "webViewLink": "https://drive.example/1"}
        )
        assert "報告書.txt" in text
        assert "ID1" in text
        assert "https://drive.example/1" in text

    def test_リンクが返らなくても落ちない(self):
        text = drive_upload.format_result({"id": "ID1", "name": "報告書.txt"})
        assert "ID1" in text


class TestMain:
    def test_成功したら結果を画面に出して0を返す(self, local_file: Path, capsys):
        files = FakeFiles(
            result={"id": "ID1", "name": "報告書.txt", "webViewLink": "https://drive.example/1"}
        )
        code = drive_upload.main(
            [str(local_file)], service_factory=lambda args: FakeService(files)
        )

        out = capsys.readouterr().out
        assert code == 0
        assert "報告書.txt" in out
        assert "ID1" in out
        assert "https://drive.example/1" in out

    def test_失敗したら理由を出して1を返す(self, tmp_path: Path, capsys):
        files = FakeFiles()
        code = drive_upload.main(
            [str(tmp_path / "ない.txt")], service_factory=lambda args: FakeService(files)
        )

        captured = capsys.readouterr()
        assert code == 1
        assert "ない.txt" in captured.err

    def test_認証で止まったときも1を返す(self, local_file: Path, capsys):
        def broken_factory(args):
            raise drive_upload.UploadError("credentials.json が見つかりません")

        code = drive_upload.main([str(local_file)], service_factory=broken_factory)

        assert code == 1
        assert "credentials.json" in capsys.readouterr().err

    def test_コマンドラインの指定が実際の呼び出しに届く(self, local_file: Path):
        files = FakeFiles()
        drive_upload.main(
            [str(local_file), "--name", "別名.txt", "--folder-id", "FOLDER123"],
            service_factory=lambda args: FakeService(files),
        )

        body = files.calls[0]["body"]
        assert body["name"] == "別名.txt"
        assert body["parents"] == ["FOLDER123"]
