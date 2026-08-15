"""検索結果を videos.list で読み直して、表示した内容と突き合わせる。

**読むだけ。何も作らないし、書き換えない。**

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task6\\verify_search.py --results task6/results.json \\
        --keyword "Python 入門" --expect-count 5

search_videos.py が成功しても、それは「API が応答を返した」までしか意味しない。
表示した URL が本当にその動画を指しているか、タイトルが正しく取れているかは、
別のところから読み直さないと閉じない。

**この課題の設計上の山場はここにある。**

課題1〜5は、作った物を読み返せば閉じた。検索は何も作らないので、同じ手が使えない。
しかも読み取り API は「取ってきた値どうしを比べる」形になりやすく、それだと
何も確かめていないのに全部一致してしまう（トートロジー）。課題4で `join_url` を
応答の `id` と比べかけたのと同じ形である。

そこで物差しを3つとも応答の外から取る。

1. **キーワードと件数** — 人間がコマンドラインで渡す
2. **動画ID** — search_videos.py が**表示した URL から取り出す**。
   結果ファイルに ID を書いていないので、URL の組み立てが正しくないとここで落ちる
3. **タイトル** — videos.list（search.list とは**別のエンドポイント**）から取り直す

**videos.list には固有の罠がある。存在しない ID を渡してもエラーにならず、
その分が items から黙って抜けて 200 で返る。** 件数を見ないと
「返ってこなかった」が「一致した」に化ける。

費用の面でもこの割り当てが正しい。search.list は 1 日 100 回の別枠上限だが、
videos.list は 10,000 ユニット枠の 1 ユニットで、しかも 50 件を 1 回で引ける。
**確認は安い側でやる。**
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from googleapiclient.errors import HttpError

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import search_videos  # noqa: E402
from common import youtube_auth  # noqa: E402


VIDEOS_PART = "snippet"

# videos.list の id に渡せる上限。
VIDEOS_ID_LIMIT = 50


class VerifyError(Exception):
    """利用者にそのまま見せられる失敗。"""


@dataclass
class Check:
    label: str
    ok: bool
    detail: str = ""


# ---------------------------------------------------------------- 結果ファイル


def _require_text(record: dict, key: str, position: int) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise VerifyError(f"結果ファイルの {position} 件目に {key} がありません")
    return value


def load_results(path: str | Path) -> dict:
    """search_videos.py が書いた結果ファイルを読む。

    形が違うファイルを黙って受け入れない。中身が空のまま照合に進むと
    「0 件すべて一致」が出る。
    """
    source = Path(path)
    if not source.exists():
        raise VerifyError(
            f"結果ファイルが見つかりません: {source}\n"
            "search_videos.py を --json-out 付きで実行してください。"
        )

    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise VerifyError(f"結果ファイルを JSON として読めません: {source}") from error

    if not isinstance(payload, dict):
        raise VerifyError(f"結果ファイルの形式が不正です（辞書ではありません）: {source}")

    keyword = payload.get("keyword")
    if not isinstance(keyword, str) or not keyword.strip():
        raise VerifyError("結果ファイルに keyword がありません")

    count = payload.get("count")
    if not isinstance(count, int) or isinstance(count, bool):
        # search_videos.py が必ず書く項目。無いなら別物のファイルを渡している。
        raise VerifyError("結果ファイルに count がありません（整数である必要があります）")

    videos = payload.get("videos")
    if not isinstance(videos, list):
        raise VerifyError("結果ファイルの videos がリストではありません")
    if not videos:
        raise VerifyError("結果ファイルの videos が空です。照合する対象がありません")

    for position, record in enumerate(videos, start=1):
        if not isinstance(record, dict):
            raise VerifyError(f"結果ファイルの {position} 件目の形式が不正です")
        _require_text(record, "title", position)
        _require_text(record, "url", position)

    return payload


def parse_urls(payload: dict) -> tuple[list[str], list[str]]:
    """記録された URL から動画 ID を取り出す。

    戻り値は (取り出せた ID, 取り出せなかった URL)。

    **記録された ID を読むのではなく、URL から取り出す。** 結果ファイルに ID を
    書いていないのはこのためで、URL の組み立てが間違っていればここで露見する。
    判定は search_videos.video_id_from_url をそのまま使う。別実装にすると、
    組み立てと取り出しが両方間違っていても往復して通ってしまう。
    """
    ids: list[str] = []
    bad: list[str] = []
    for record in payload.get("videos", []):
        url = record.get("url")
        video_id = search_videos.video_id_from_url(url)
        if video_id is None:
            bad.append(url)
        else:
            ids.append(video_id)
    return ids, bad


# ---------------------------------------------------------------- 手元でできる照合


def _compare(label: str, expected, actual) -> Check:
    ok = expected == actual
    detail = "" if ok else f"期待 {expected!r} / 実際 {actual!r}"
    return Check(label, ok, detail)


def build_local_checks(payload: dict, *, expected_keyword: str, expected_count: int) -> list[Check]:
    """API を呼ばずに確かめられることを先に済ませる。

    ここが落ちる実行はネットワークに出す価値がない。
    """
    videos = payload.get("videos", [])
    ids, bad = parse_urls(payload)

    checks: list[Check] = []

    # 1. どの検索の結果か。昨日のファイルを渡したまま気づかない、を防ぐ。
    checks.append(_compare("検索キーワード", expected_keyword, payload.get("keyword")))

    # 2. 何件あるはずか。人間が指定した値と突き合わせる。
    checks.append(_compare("結果の件数", expected_count, len(videos)))

    # 3. ファイル自身が申告している件数と、実際の件数。手で削ると食い違う。
    checks.append(_compare("記録された件数", len(videos), payload.get("count")))

    # 4. 同じ動画が二重に並んでいないか。
    duplicates = sorted({video_id for video_id in ids if ids.count(video_id) > 1})
    checks.append(
        Check(
            "動画IDの重複",
            not duplicates,
            "" if not duplicates else f"重複: {', '.join(duplicates)}",
        )
    )

    # 5. 表示した URL から動画 ID を取り出せるか。
    checks.append(
        Check(
            "URLの形式",
            not bad,
            "" if not bad else f"動画IDを取り出せない URL: {', '.join(str(url) for url in bad)}",
        )
    )

    return checks


# ---------------------------------------------------------------- 読み直す


def _translate_http_error(error: HttpError, api_key: str | None) -> VerifyError:
    """search 側と同じ方針。str(error) は使わない（URI に鍵が載る）。"""
    translated = search_videos.translate_http_error(error, api_key)
    return VerifyError(str(translated))


def fetch_videos(service, video_ids: Sequence[str], *, api_key: str | None = None) -> dict[str, dict]:
    """動画を読み直す。動画 ID で引ける形にして返す。

    **位置ではなく ID で引けるようにする。** videos.list は渡した順で返すと
    保証していないので、位置で取ると静かに入れ替わる。

    欠けた分はここでは落とさない（照合の項目として報告する）。
    """
    ids = list(video_ids)
    if not ids:
        raise VerifyError("読み直す動画IDがありません")
    if len(ids) > VIDEOS_ID_LIMIT:
        # 黙って切り捨てると、確かめていない動画が「一致した」に混ざる。
        raise VerifyError(
            f"一度に読み直せるのは {VIDEOS_ID_LIMIT} 件までです（{len(ids)} 件を渡されました）"
        )

    try:
        response = (
            service.videos()
            .list(part=VIDEOS_PART, id=",".join(ids))
            .execute()
        )
    except HttpError as error:
        raise _translate_http_error(error, api_key) from error

    items = response.get("items")
    if not isinstance(items, list):
        raise VerifyError("応答に items がありません。動画を読み直せませんでした")

    fetched: dict[str, dict] = {}
    for item in items:
        if isinstance(item, dict) and item.get("id"):
            fetched[str(item["id"])] = item
    return fetched


def build_remote_checks(payload: dict, fetched: dict[str, dict]) -> list[Check]:
    """読み直した内容と、表示した内容を突き合わせる。"""
    videos = payload.get("videos", [])
    ids, _ = parse_urls(payload)

    checks: list[Check] = []

    # 1. 渡した ID が全部返ってきたか。
    #    **videos.list は存在しない ID を黙って落として 200 を返す。**
    #    ここを見ないと「返ってこなかった」が「一致した」になる。
    missing = [video_id for video_id in ids if video_id not in fetched]
    checks.append(
        Check(
            "動画の実在",
            not missing and bool(ids),
            ""
            if not missing and ids
            else f"読み直せなかった動画ID: {', '.join(missing) if missing else '(対象なし)'}",
        )
    )

    # 2. タイトルが一致するか。search.list と videos.list は別のエンドポイントなので、
    #    ここで初めて「同じ動画を指している」が確かめられる。
    for position, record in enumerate(videos, start=1):
        label = f"タイトル一致 [{position}]"
        video_id = search_videos.video_id_from_url(record.get("url"))
        item = fetched.get(video_id) if video_id else None

        if item is None:
            checks.append(
                Check(label, False, "動画が返ってきませんでした（削除・非公開・IDの誤りのいずれか）")
            )
            continue

        expected = search_videos.clean_title(record.get("title"))
        actual = search_videos.clean_title((item.get("snippet") or {}).get("title"))
        if not actual:
            # 空文字を「一致した」にしない。
            checks.append(Check(label, False, f"期待 {expected!r} / 実際 タイトルが返ってきませんでした"))
            continue

        ok = expected == actual
        checks.append(Check(label, ok, "" if ok else f"期待 {expected!r} / 実際 {actual!r}"))

    return checks


# ---------------------------------------------------------------- 報告


def all_ok(checks: Sequence[Check]) -> bool:
    """全部一致したか。

    空のリストに all() を掛けると True になる。「何も確かめていない」が
    「全部一致」に化けるので、ゼロ件は False にする。
    """
    if not checks:
        return False
    return all(check.ok for check in checks)


def format_checks(checks: Sequence[Check]) -> str:
    lines = []
    for check in checks:
        mark = "OK" if check.ok else "NG"
        line = f"  [{mark}] {check.label}"
        if check.detail:
            line += f"  {check.detail}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------- 入口


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="検索結果を videos.list で読み直して、表示した内容と突き合わせます（読むだけ）。"
    )
    parser.add_argument("--results", required=True, help="search_videos.py が --json-out で書いたファイル")
    # 期待値は応答の外から取る。必須にして、ファイルの値で埋める逃げ道を作らない。
    parser.add_argument("--keyword", required=True, help="検索したときのキーワード")
    parser.add_argument("--expect-count", required=True, type=int, help="表示されたはずの件数")
    # --api-key は用意しない（search_videos.py と同じ理由）。
    return parser.parse_args(argv)


def _default_service_factory(args: argparse.Namespace):
    api_key = youtube_auth.read_api_key(os.environ)
    return youtube_auth.build_service(api_key)


def main(argv: Sequence[str] | None = None, *, service_factory: Callable | None = None) -> int:
    args = parse_args(argv)
    factory = service_factory or _default_service_factory

    try:
        payload = load_results(args.results)
    except VerifyError as error:
        print(error, file=sys.stderr)
        return 1

    local = build_local_checks(
        payload, expected_keyword=args.keyword, expected_count=args.expect_count
    )
    print("結果ファイルの照合（API を呼ばずに確かめられること）:")
    print(format_checks(local))

    if not all_ok(local):
        # ここで落ちる実行はネットワークに出す価値がない。
        print("\n食い違いがあります。上の NG を確認してください。", file=sys.stderr)
        return 1

    api_key = (os.environ.get(youtube_auth.API_KEY_ENV) or "").strip() or None

    try:
        service = factory(args)
        ids, _ = parse_urls(payload)
        fetched = fetch_videos(service, ids, api_key=api_key)
    except (VerifyError, youtube_auth.AuthError) as error:
        print(error, file=sys.stderr)
        return 1

    remote = build_remote_checks(payload, fetched)
    print("\nvideos.list で読み直した内容との照合（search.list とは別のエンドポイント）:")
    print(format_checks(remote))

    if not all_ok(remote):
        print("\n食い違いがあります。上の NG を確認してください。", file=sys.stderr)
        return 1

    print("\nすべて一致しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
