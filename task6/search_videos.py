"""YouTube Data API で、キーワードに合う動画を検索してタイトルと URL を表示する。

課題6: YouTube Data API を利用して、特定のキーワードに基づく動画検索を行う
スクリプトを作成する。検索結果から動画のタイトルと URL を抽出し、コンソールに表示する。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task6\\search_videos.py --keyword "Python 入門"
    .venv\\Scripts\\python.exe task6\\search_videos.py --keyword "Python 入門" --json-out task6/results.json

**前の5課題と前提が違う。この課題は「作る」ではなく「読む」。**

課題1〜5は、こちらが作った物が相手側に残った。だから「実物を1回読み返して閉じる」で
確かめられた。検索は何も作らないので、その手が使えない。

代わりに罠になるのが**トートロジー**である。読み取り API は「取ってきた値どうしを
比べる」形になりやすく、それだと何も確かめていないのに全部一致してしまう。
そこでこのスクリプトの責務は**表示するところまで**に閉じ、突合は verify_search.py が
**別のエンドポイント（videos.list）**を使って行う。

**API キーは URL のクエリに載る**（課題1〜5の資格情報と違うところ）。
エラーの文面は必ず youtube_auth.redact() を通してから表に出す。

**search.list は 1 日 100 回しか呼べない。** 10,000 ユニットの枠とは別枠の上限で、
「ユニットを節約する」では回避できない（公式のクォータ表で確認）。落ちると分かって
いる実行でネットワークに出ないよう、引数の検証を先に済ませてから service を作る。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import parse_qs, urlparse

from googleapiclient.errors import HttpError

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common import youtube_auth  # noqa: E402


# search.list に渡す固定値。
SEARCH_PART = "snippet"

# type を絞らないと既定で video,channel,playlist が返る。チャンネルの項目は
# videoId を持たないので、絞らないまま抽出すると欠落として落ちる。
SEARCH_TYPE = "video"

DEFAULT_MAX_RESULTS = 5

# 公式が認める上限。
MAX_RESULTS_LIMIT = 50

# 公式は 0 も受け付けるが、こちらの都合で 1 以上に狭める。
# 0 件の結果に「全部一致」を出すと、何も確かめていない実行が成功に化ける。
MIN_MAX_RESULTS = 1

DEFAULT_ORDER = "relevance"
VALID_ORDERS: tuple[str, ...] = ("date", "rating", "relevance", "title", "videoCount", "viewCount")

# search.list だけが持つ別枠の上限（1 日あたりの呼び出し回数）。
# 他のエンドポイントが使う 10,000 ユニットの枠とは別物で、
# 「軽い呼び方に変える」では回避できない。
DAILY_SEARCH_CALL_LIMIT = 100

WATCH_URL_SCHEME = "https"
WATCH_URL_HOST = "www.youtube.com"
WATCH_URL_PATH = "/watch"
WATCH_URL_QUERY_KEY = "v"
WATCH_URL_PREFIX = f"{WATCH_URL_SCHEME}://{WATCH_URL_HOST}{WATCH_URL_PATH}?{WATCH_URL_QUERY_KEY}="

# YouTube の動画 ID は 11 文字の英数字と - _。
VIDEO_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{11}\Z")


class SearchError(Exception):
    """利用者にそのまま見せられる失敗。"""


@dataclass(frozen=True)
class Video:
    video_id: str
    title: str
    url: str


# ---------------------------------------------------------------- 入力の確定


def normalize_keyword(value: str | None) -> str:
    """検索語を確定する。

    前後の空白だけ落とす。語の間の空白は検索語の一部なので潰さない
    （"Python 入門" と "Python入門" は別の検索）。
    """
    keyword = (value or "").strip()
    if not keyword:
        raise SearchError("検索キーワードが空です。--keyword で指定してください")
    return keyword


def normalize_max_results(value: int) -> int:
    """取得件数を確定する。"""
    try:
        count = int(value)
    except (TypeError, ValueError) as error:
        raise SearchError(f"取得件数が数値ではありません: {value!r}") from error

    if count < MIN_MAX_RESULTS or count > MAX_RESULTS_LIMIT:
        raise SearchError(
            f"取得件数は {MIN_MAX_RESULTS}〜{MAX_RESULTS_LIMIT} の範囲で指定してください: {count}"
        )
    return count


def normalize_order(value: str | None) -> str:
    """並び順を確定する。公式が認める値だけを通す。

    知らない値をそのまま渡すと API 側が 400 を返す。
    1 日 100 回しか呼べないので、弾けるものは呼ぶ前に弾く。
    """
    order = (value or "").strip()
    if order not in VALID_ORDERS:
        raise SearchError(
            f"並び順が不正です: {order!r}\n使える値: " + " / ".join(VALID_ORDERS)
        )
    return order


# ---------------------------------------------------------------- URL


def watch_url(video_id: str) -> str:
    """動画 ID から視聴 URL を組む。

    形を確認してから組む。確認しないと、壊れた ID でも「それらしい URL」が
    できてしまい、実際に開くまで間違いに気づけない。
    """
    identifier = (video_id or "").strip()
    if not identifier:
        raise SearchError("動画IDが空です")
    if not VIDEO_ID_PATTERN.match(identifier):
        raise SearchError(
            f"動画IDの形式が不正です: {identifier!r}\n"
            "YouTube の動画IDは英数字と - _ からなる11文字です。"
        )
    return WATCH_URL_PREFIX + identifier


def video_id_from_url(url: str | None) -> str | None:
    """視聴 URL から動画 ID を取り出す。取り出せなければ None。

    watch_url() と対になる。verify_search.py はこちらを使って、**表示した URL から**
    動画 ID を復元する。結果ファイルに ID を持たせていないので、URL の組み立てが
    間違っていればそこで初めて露見する。

    厳しく見る。このスクリプトが組む URL は ``?v=<ID>`` ひとつだけなので、
    ホストが違う・パラメータが増えている・http である、はすべて
    「誰かが書き換えた」を意味する。
    """
    if not url or not isinstance(url, str):
        return None

    parsed = urlparse(url)
    if parsed.scheme != WATCH_URL_SCHEME:
        return None
    if parsed.netloc != WATCH_URL_HOST:
        return None
    if parsed.path != WATCH_URL_PATH:
        return None

    query = parse_qs(parsed.query)
    if set(query) != {WATCH_URL_QUERY_KEY}:
        return None

    values = query[WATCH_URL_QUERY_KEY]
    if len(values) != 1:
        return None

    identifier = values[0]
    return identifier if VIDEO_ID_PATTERN.match(identifier) else None


# ---------------------------------------------------------------- 応答の読み方


def clean_title(raw: str | None) -> str:
    """タイトルの HTML 実体参照を解く。

    YouTube Data API は ``&amp;`` ``&#39;`` ``&quot;`` の形で返す。解かずに表示すると、
    画面に出る文字列が実際のタイトルと違う。照合するときも、両側に同じ処理を
    掛けないと同じ動画なのに永久に一致しない。
    """
    if raw is None:
        return ""
    return html.unescape(str(raw)).strip()


def extract_videos(response: dict) -> list[Video]:
    """検索結果からタイトルと URL を取り出す。

    欠けている項目は落とす。黙って読み飛ばすと、抽出できていないことが
    「該当が少なかった」に見えてしまう。
    """
    if not isinstance(response, dict) or "items" not in response:
        raise SearchError(
            "応答に items がありません。検索結果を読み取れませんでした。\n"
            f"受け取ったキー: {', '.join(sorted(response)) if isinstance(response, dict) else type(response).__name__}"
        )

    items = response.get("items")
    if not isinstance(items, list):
        raise SearchError(f"応答の items がリストではありません: {type(items).__name__}")

    videos: list[Video] = []
    for position, entry in enumerate(items, start=1):
        if not isinstance(entry, dict):
            raise SearchError(f"{position} 件目の形式が不正です: {type(entry).__name__}")

        identifier = entry.get("id")
        if not isinstance(identifier, dict):
            raise SearchError(f"{position} 件目に id がありません")

        video_id = str(identifier.get("videoId") or "").strip()
        if not video_id:
            raise SearchError(
                f"{position} 件目に動画IDがありません。\n"
                f"type={SEARCH_TYPE} で絞っていますが、動画以外の項目が混ざった可能性があります。"
            )

        snippet = entry.get("snippet")
        if not isinstance(snippet, dict):
            raise SearchError(f"{position} 件目に snippet がありません")

        title = clean_title(snippet.get("title"))
        if not title:
            # 空文字を「タイトルが取れた」にしない。画面に URL だけが並び、
            # 抽出できていないことが成功に見える。
            raise SearchError(f"{position} 件目のタイトルが空です（動画ID {video_id}）")

        try:
            url = watch_url(video_id)
        except SearchError as error:
            raise SearchError(f"{position} 件目: {error}") from error

        videos.append(Video(video_id=video_id, title=title, url=url))

    return videos


# ---------------------------------------------------------------- エラーの翻訳


def _api_payload(error: HttpError) -> dict:
    try:
        payload = json.loads(error.content.decode("utf-8"))
    except (ValueError, AttributeError, UnicodeDecodeError):
        return {}
    return payload.get("error", {}) if isinstance(payload, dict) else {}


def _api_message(error: HttpError) -> str:
    return str(_api_payload(error).get("message", ""))


def _api_reason(error: HttpError) -> str:
    errors = _api_payload(error).get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("reason", ""))
    return ""


def _status_of(error: HttpError) -> int | None:
    return getattr(getattr(error, "resp", None), "status", None)


def _looks_like_api_disabled(detail: str, reason: str) -> bool:
    lowered = detail.lower()
    return (
        reason == "accessNotConfigured"
        or "has not been used" in lowered
        or "is disabled" in lowered
    )


def _looks_like_quota(detail: str, reason: str) -> bool:
    return reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded") or "quota" in detail.lower()


def _looks_like_bad_key(detail: str, reason: str) -> bool:
    lowered = detail.lower()
    return "api key not valid" in lowered or "api key" in lowered or reason == "keyInvalid"


def translate_http_error(error: HttpError, api_key: str | None = None) -> SearchError:
    """Google が返す英語を、原因と対処に置き換える。

    **str(error) は使わない。** HttpError の文字列表現には失敗したリクエストの URI が
    入っていて、そこに ``key=<APIキー>`` が載る。応答の JSON から読んだ理由だけを見せる。

    403 には原因が2種類ある（未有効化・クォータ）。混ぜると、どちらを直せばよいのか
    読み取れなくなるので、未有効化を先に判定する。
    """
    status = _status_of(error)
    detail = youtube_auth.redact(_api_message(error), api_key)
    reason = _api_reason(error)

    if _looks_like_api_disabled(detail, reason):
        return SearchError(
            f"YouTube Data API v3 が有効になっていません（HTTP {status}）。\n"
            "Google Cloud コンソールの「APIとサービス」→「ライブラリ」で "
            "YouTube Data API v3 を検索して有効にしてください。\n"
            "課題1で有効にしたのは Drive API、課題2は Docs API、課題3は Meet API、"
            "課題5は Gmail API で、どれも別の API です。\n"
            "反映に数分かかることがあります。\n"
            f"応答: {detail}"
        )

    if _looks_like_quota(detail, reason):
        return SearchError(
            f"YouTube Data API の利用上限に達しました（HTTP {status}）。\n"
            f"search.list は 1 日 {DAILY_SEARCH_CALL_LIMIT} 回までという別枠の上限があります"
            "（他のエンドポイントが使う 10,000 ユニットの枠とは別）。\n"
            "日付が変わるまで待つか、Google Cloud コンソールから割り当ての引き上げを申請してください。\n"
            f"応答: {detail}"
        )

    if _looks_like_bad_key(detail, reason):
        return SearchError(
            f"API キーが受け付けられませんでした（HTTP {status}）。\n"
            f"環境変数 {youtube_auth.API_KEY_ENV} の値を確認してください。\n"
            "キーに「APIの制限」を掛けている場合は、YouTube Data API v3 が"
            "許可されているかも確認してください。\n"
            f"応答: {detail}"
        )

    return SearchError(
        f"動画の検索に失敗しました（HTTP {status}）。\n"
        f"応答: {detail or '(応答の本文を読み取れませんでした)'}"
    )


# ---------------------------------------------------------------- 検索する


def search(
    service,
    keyword: str,
    max_results: int,
    order: str,
    *,
    api_key: str | None = None,
) -> dict:
    """動画を検索する。応答をそのまま返す。

    api_key は伏せ字にするためだけに受け取る（リクエストには使わない。
    鍵は service に埋まっている）。
    """
    try:
        return (
            service.search()
            .list(
                part=SEARCH_PART,
                q=keyword,
                type=SEARCH_TYPE,
                maxResults=max_results,
                order=order,
            )
            .execute()
        )
    except HttpError as error:
        raise translate_http_error(error, api_key) from error


# ---------------------------------------------------------------- 画面と保存


def format_videos(videos: Sequence[Video]) -> str:
    """コンソールに出す形にする。タイトルと URL を並べる。"""
    if not videos:
        return "（該当する動画はありませんでした）"

    lines = [f"検索結果 {len(videos)} 件:"]
    for position, video in enumerate(videos, start=1):
        lines.append(f"  {position}. {video.title}")
        lines.append(f"     {video.url}")
    return "\n".join(lines)


def build_results(keyword: str, order: str, videos: Sequence[Video]) -> dict:
    """verify_search.py に渡す内容を組む。

    **動画IDを書かない。** 書いておくと verify 側がそれを読んで済ませてしまい、
    URL の組み立てが正しいかを一度も確かめないまま「一致した」が出る。
    タイトルと URL だけ渡し、ID は URL から取り出させる。
    """
    return {
        "keyword": keyword,
        "order": order,
        "count": len(videos),
        "videos": [{"title": video.title, "url": video.url} for video in videos],
    }


def write_results(path: str | Path, payload: dict) -> Path:
    """結果を JSON で保存する。

    ensure_ascii=False で日本語をそのまま書く（読めないファイルにしない）。
    newline="\\n" を指定するのは、Windows の既定だと書き込み時に LF が CRLF へ
    変換され、同じ内容のファイルが環境によって別物になるため。
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination


def format_next_step(keyword: str, count: int, results_path: str | None) -> str:
    """照合のしかたを案内する。

    「検索できた」は「表示した URL が本物を指している」を意味しない。
    そこは別のエンドポイントで読み直さないと閉じない。
    """
    if results_path is None:
        return (
            "\n表示した URL が本当にその動画を指しているかは、まだ確かめていません。\n"
            "照合するには --json-out で結果を保存してから verify_search.py を実行してください:\n"
            f'  .venv\\Scripts\\python.exe task6\\search_videos.py --keyword "{keyword}" '
            "--json-out task6/results.json"
        )
    return (
        "\n表示した URL が本当にその動画を指しているかは、まだ確かめていません。\n"
        "videos.list（検索とは別のエンドポイント）で読み直して照合するには:\n"
        f"  .venv\\Scripts\\python.exe task6\\verify_search.py --results {results_path} "
        f'--keyword "{keyword}" --expect-count {count}'
    )


# ---------------------------------------------------------------- 入口


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="YouTube Data API で、キーワードに合う動画のタイトルと URL を表示します。"
    )
    parser.add_argument("--keyword", required=True, help="検索キーワード")
    parser.add_argument(
        "--max-results",
        type=int,
        default=DEFAULT_MAX_RESULTS,
        help=f"取得件数（{MIN_MAX_RESULTS}〜{MAX_RESULTS_LIMIT}・既定 {DEFAULT_MAX_RESULTS}）",
    )
    parser.add_argument(
        "--order",
        default=DEFAULT_ORDER,
        help="並び順（" + " / ".join(VALID_ORDERS) + f"・既定 {DEFAULT_ORDER}）",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="照合用に結果を保存するパス（verify_search.py に渡す）",
    )
    # --api-key は**わざと用意していない**。コマンドラインに書いた鍵は
    # シェルの履歴に残り、実行画面のスクリーンショットにも写る。
    # 鍵は環境変数からしか受け取らない。
    return parser.parse_args(argv)


def _api_key_for_redaction() -> str | None:
    """伏せ字に使う鍵を読む。ここでは検証しない（認証の失敗は factory 側で出す）。"""
    return (os.environ.get(youtube_auth.API_KEY_ENV) or "").strip() or None


def _default_service_factory(args: argparse.Namespace):
    api_key = youtube_auth.read_api_key(os.environ)
    return youtube_auth.build_service(api_key)


def main(argv: Sequence[str] | None = None, *, service_factory: Callable | None = None) -> int:
    args = parse_args(argv)
    factory = service_factory or _default_service_factory

    try:
        # 検索する内容を先に確定させる。ここで落ちる実行は API に届かない。
        # search.list は 1 日 100 回しか呼べないので、弾けるものは呼ぶ前に弾く。
        keyword = normalize_keyword(args.keyword)
        max_results = normalize_max_results(args.max_results)
        order = normalize_order(args.order)
    except SearchError as error:
        print(error, file=sys.stderr)
        return 1

    api_key = _api_key_for_redaction()

    try:
        service = factory(args)
        response = search(service, keyword, max_results, order, api_key=api_key)
        videos = extract_videos(response)
    except (SearchError, youtube_auth.AuthError) as error:
        print(error, file=sys.stderr)
        return 1

    print(format_videos(videos))

    if not videos:
        # 「対象が尽きた」を成功で終わらせない。0 を返すと、毎回成功しているのに
        # 何も表示しない状態が起きても気づけない。
        print(
            f"\n検索キーワード {keyword!r} に該当する動画がありませんでした。",
            file=sys.stderr,
        )
        return 1

    results_path = None
    if args.json_out:
        results_path = str(write_results(args.json_out, build_results(keyword, order, videos)))
        print(f"\n照合用に保存しました: {results_path}")

    print(format_next_step(keyword, len(videos), results_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
