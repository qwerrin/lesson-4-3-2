"""verify_upload のテスト。

このスクリプトは「テストで閉じられない穴」を閉じるために作った。
だからここで確かめるのは、照合そのものが甘くないこと。

いちばん大事なのは「サイズが同じで中身が違う」を落とせるかどうか。
サイズだけ見る実装でも、素直なテストデータでは差が出ない。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import verify_upload  # noqa: E402


@pytest.fixture
def local_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.txt"
    path.write_bytes(b"hello drive\n")
    return path


def meta_for(local_path: Path, **overrides) -> dict:
    raw = local_path.read_bytes()
    meta = {
        "id": "FILE_ID",
        "name": local_path.name,
        "mimeType": "text/plain",
        "size": str(len(raw)),
        "md5Checksum": hashlib.md5(raw).hexdigest(),
        "trashed": False,
        "webViewLink": "https://drive.example/1",
    }
    meta.update(overrides)
    return meta


def failed_labels(checks) -> list[str]:
    return [c.label for c in checks if not c.ok]


class TestLocalFingerprint:
    def test_名前とサイズとMD5を取る(self, local_file: Path):
        fingerprint = verify_upload.local_fingerprint(local_file)
        assert fingerprint["name"] == "sample.txt"
        assert fingerprint["size"] == 12
        assert fingerprint["md5"] == hashlib.md5(b"hello drive\n").hexdigest()

    def test_無いファイルはエラーになる(self, tmp_path: Path):
        with pytest.raises(verify_upload.VerifyError):
            verify_upload.local_fingerprint(tmp_path / "ない.txt")


class TestCompareWithLocal:
    def test_一致していれば全部OK(self, local_file: Path):
        checks = verify_upload.compare_with_local(meta_for(local_file), local_file)
        assert failed_labels(checks) == []
        assert len(checks) >= 5

    def test_名前が違えば落ちる(self, local_file: Path):
        checks = verify_upload.compare_with_local(meta_for(local_file, name="別名.txt"), local_file)
        assert any("ファイル名" in label for label in failed_labels(checks))

    def test_サイズが違えば落ちる(self, local_file: Path):
        checks = verify_upload.compare_with_local(meta_for(local_file, size="999"), local_file)
        assert any("サイズ" in label for label in failed_labels(checks))

    def test_サイズが同じでも中身が違えば落ちる(self, local_file: Path):
        # ここがこのスクリプトの存在理由。サイズだけ見る実装はここで死ぬ。
        same_size_other_content = hashlib.md5(b"HELLO DRIVE\n").hexdigest()
        checks = verify_upload.compare_with_local(
            meta_for(local_file, md5Checksum=same_size_other_content), local_file
        )
        failed = failed_labels(checks)
        assert any("MD5" in label for label in failed)
        assert not any("サイズ" in label for label in failed)

    def test_MIMEタイプが違えば落ちる(self, local_file: Path):
        checks = verify_upload.compare_with_local(
            meta_for(local_file, mimeType="application/octet-stream"), local_file
        )
        assert any("MIME" in label for label in failed_labels(checks))

    def test_ゴミ箱に入っていたら落ちる(self, local_file: Path):
        checks = verify_upload.compare_with_local(meta_for(local_file, trashed=True), local_file)
        assert any("ゴミ箱" in label for label in failed_labels(checks))

    def test_リンクが無ければ落ちる(self, local_file: Path):
        meta = meta_for(local_file)
        del meta["webViewLink"]
        checks = verify_upload.compare_with_local(meta, local_file)
        assert any("リンク" in label for label in failed_labels(checks))

    def test_MD5が返ってこない場合は成功にしない(self, local_file: Path):
        # Google ドキュメント形式のファイルには md5Checksum が無い。
        # 「照合できなかった」を黙って OK にすると、確かめた気になるだけになる。
        meta = meta_for(local_file)
        del meta["md5Checksum"]
        checks = verify_upload.compare_with_local(meta, local_file)
        assert any("MD5" in label for label in failed_labels(checks))

    def test_サイズが返ってこない場合も成功にしない(self, local_file: Path):
        meta = meta_for(local_file)
        del meta["size"]
        checks = verify_upload.compare_with_local(meta, local_file)
        assert any("サイズ" in label for label in failed_labels(checks))


class TestAllOk:
    def test_全部OKならTrue(self, local_file: Path):
        assert verify_upload.all_ok(verify_upload.compare_with_local(meta_for(local_file), local_file))

    def test_1件でも落ちればFalse(self, local_file: Path):
        checks = verify_upload.compare_with_local(meta_for(local_file, name="別名"), local_file)
        assert not verify_upload.all_ok(checks)


class TestFormatChecks:
    def test_OKとNGが見分けられる(self, local_file: Path):
        checks = verify_upload.compare_with_local(meta_for(local_file, name="別名"), local_file)
        text = verify_upload.format_checks(checks)
        assert "NG" in text
        assert "OK" in text

    def test_全項目が1行ずつ出る(self, local_file: Path):
        checks = verify_upload.compare_with_local(meta_for(local_file), local_file)
        text = verify_upload.format_checks(checks)
        assert len(text.splitlines()) == len(checks)


class FakeFiles:
    def __init__(self, meta: dict) -> None:
        self.meta = meta
        self.calls: list[dict] = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        return self.meta


class FakeService:
    def __init__(self, files: FakeFiles) -> None:
        self._files = files

    def files(self):
        return self._files


class TestFetchMetadata:
    def test_照合に必要な項目をまとめて要求する(self, local_file: Path):
        files = FakeFiles(meta_for(local_file))
        verify_upload.fetch_metadata(FakeService(files), "FILE_ID")

        fields = files.calls[0]["fields"]
        for key in ("name", "size", "md5Checksum", "trashed", "webViewLink"):
            assert key in fields

    def test_指定したIDで問い合わせる(self, local_file: Path):
        files = FakeFiles(meta_for(local_file))
        verify_upload.fetch_metadata(FakeService(files), "FILE_ID")
        assert files.calls[0]["fileId"] == "FILE_ID"


class TestMain:
    def test_一致していれば結果を出して0を返す(self, local_file: Path, capsys):
        files = FakeFiles(meta_for(local_file))
        code = verify_upload.main(
            ["FILE_ID", str(local_file)], service_factory=lambda args: FakeService(files)
        )

        out = capsys.readouterr().out
        assert code == 0
        assert "OK" in out
        assert "NG" not in out

    def test_食い違いがあれば1を返す(self, local_file: Path, capsys):
        files = FakeFiles(meta_for(local_file, md5Checksum="0" * 32))
        code = verify_upload.main(
            ["FILE_ID", str(local_file)], service_factory=lambda args: FakeService(files)
        )

        assert code == 1
        assert "NG" in capsys.readouterr().out

    def test_ローカルファイルが無ければ1を返す(self, tmp_path: Path, capsys):
        files = FakeFiles({})
        code = verify_upload.main(
            ["FILE_ID", str(tmp_path / "ない.txt")],
            service_factory=lambda args: FakeService(files),
        )

        assert code == 1
        assert "ない.txt" in capsys.readouterr().err
