"""アップロードしたファイルを Drive から読み返し、ローカルの実体と突き合わせる。

drive_upload のテストは偽の service を使うので、固定できるのは「呼び方」まで。
fields の綴りが違っていても、MIME タイプが嘘でも、テストは全部通ってしまう。
その穴は実物を1回読むことでしか閉じない。ここがその1回。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task1\\verify_upload.py <ファイルID> task1\\data\\sample.txt

読むだけで、Drive 側は一切変更しない。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from googleapiclient.errors import HttpError

import drive_upload

# 照合に必要な項目。md5Checksum が要になるので落とさない。
METADATA_FIELDS = "id, name, mimeType, size, md5Checksum, trashed, webViewLink"


class VerifyError(Exception):
    """利用者にそのまま見せられる失敗。"""


@dataclass(frozen=True)
class Check:
    label: str
    ok: bool
    detail: str = ""


def local_fingerprint(local_path: str | Path) -> dict:
    path = Path(local_path)
    if not path.is_file():
        raise VerifyError(f"ローカルのファイルが見つかりません: {path}")
    raw = path.read_bytes()
    return {"name": path.name, "size": len(raw), "md5": hashlib.md5(raw).hexdigest()}


def compare_with_local(meta: dict, local_path: str | Path) -> list[Check]:
    """Drive 側のメタデータとローカルの実体を項目ごとに突き合わせる。

    値が返ってこなかった項目は OK にしない。照合できなかったことと
    一致したことを同じ扱いにすると、確かめた気になるだけになる。
    """
    local = local_fingerprint(local_path)
    checks: list[Check] = []

    drive_name = meta.get("name")
    checks.append(
        Check("ファイル名が一致", drive_name == local["name"], f"{drive_name} / {local['name']}")
    )

    expected_mime = drive_upload.guess_mime_type(Path(local_path))
    drive_mime = meta.get("mimeType")
    checks.append(
        Check("MIME タイプが一致", drive_mime == expected_mime, f"{drive_mime} / {expected_mime}")
    )

    drive_size = meta.get("size")
    checks.append(
        Check(
            "サイズが一致",
            drive_size is not None and int(drive_size) == local["size"],
            f"{drive_size} / {local['size']}",
        )
    )

    drive_md5 = meta.get("md5Checksum")
    checks.append(
        Check(
            "中身が一致（MD5）",
            drive_md5 == local["md5"],
            f"{drive_md5 or '(返らなかった)'} / {local['md5']}",
        )
    )

    checks.append(Check("ゴミ箱に入っていない", meta.get("trashed") is False))
    checks.append(Check("リンクが取得できる", bool(meta.get("webViewLink")), meta.get("webViewLink", "")))

    return checks


def all_ok(checks: Sequence[Check]) -> bool:
    return all(check.ok for check in checks)


def format_checks(checks: Sequence[Check]) -> str:
    return "\n".join(
        f"{'OK ' if c.ok else 'NG '} {c.label}{('  ' + c.detail) if c.detail else ''}"
        for c in checks
    )


def fetch_metadata(service, file_id: str) -> dict:
    try:
        return service.files().get(fileId=file_id, fields=METADATA_FIELDS).execute()
    except HttpError as error:
        status = error.resp.status
        raise VerifyError(
            f"[{status}] ファイル情報を取得できませんでした（ID: {file_id}）"
        ) from error


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="アップロード済みのファイルを Drive から読み返し、ローカルの実体と突き合わせます。"
    )
    parser.add_argument("file_id", help="確認するファイルの ID（アップロード時に表示される）")
    parser.add_argument("path", help="突き合わせるローカルファイルのパス")
    parser.add_argument("--credentials", default="credentials.json")
    parser.add_argument("--token", default="token.json")
    return parser.parse_args(argv)


def _default_service_factory(args: argparse.Namespace):
    credentials = drive_upload.load_credentials(args.credentials, args.token)
    return drive_upload.build_service(credentials)


def main(argv: Sequence[str] | None = None, *, service_factory: Callable | None = None) -> int:
    args = parse_args(argv)
    factory = service_factory or _default_service_factory

    try:
        service = factory(args)
        meta = fetch_metadata(service, args.file_id)
        checks = compare_with_local(meta, args.path)
    except (VerifyError, drive_upload.UploadError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1

    print(format_checks(checks))
    return 0 if all_ok(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
