"""task6 を1か所ずつ壊して、テストが落ちることを確認する。

通っているテストの数は、守られている範囲を意味しない。
落ちなかった行は「テストが見ていない場所」なので、そこだけ手当てする。

壊しかたを足すのは**コードを書いた直後**。まとめて最後にやると穴が出て、直後にやると出ない。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task6\\tools\\mutate.py

**このスクリプトはソースファイルを一時的に書き換える。**
1件ごとに元へ戻し、開始時に `.mutate_backup/` へ控えを取る。
atexit は強制終了では走らないので（2026-08-14 に課題4で実際に踏んだ）、
復旧を「人が思い出して打つコマンド」ではなく道具側の責任にしている。

**対象に common/youtube_auth.py を含む**ので、テストは task6/tests だけでなく
common/tests も回す。共有モジュールは、壊れると落ちるのが使う側の課題なので、
使う側のテストだけ回していると原因が遠くなる（課題3の教訓）。
"""

from __future__ import annotations

import atexit
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

SEARCH = ROOT / "task6" / "search_videos.py"
VERIFY = ROOT / "task6" / "verify_search.py"
AUTH = ROOT / "common" / "youtube_auth.py"

TARGETS = tuple(p for p in (SEARCH, VERIFY, AUTH) if p.exists())

TEST_DIRS = [d for d in (ROOT / "task6" / "tests", ROOT / "common" / "tests") if d.is_dir()]

BACKUP_DIR = Path(__file__).resolve().parent / ".mutate_backup"

# (対象ファイル, 壊した内容, 置換前, 置換後)
MUTATIONS: list[tuple[Path, str, str, str]] = [
    # =============================================================== 認証（APIキー）
    (
        AUTH,
        "鍵の前後の空白を落とさない（空白だけの値が通る）",
        '    value = (env.get(API_KEY_ENV) or "").strip()',
        '    value = env.get(API_KEY_ENV) or ""',
    ),
    (
        AUTH,
        "未設定でも落とさず空の鍵を返す",
        "    if value:\n        return value",
        "    if True:\n        return value",
    ),
    (
        AUTH,
        "エラーに環境変数名を書かない",
        # 1行だけ差し替えても、末尾の PowerShell の例に API_KEY_ENV が残るので
        # 検査は通ってしまう。raise ごと置き換える。
        '    raise AuthError(\n'
        '        f"API キーが設定されていません: {API_KEY_ENV}\\n"\n'
        '        "Google Cloud コンソールの「APIとサービス」→「認証情報」で API キーを作成し、"\n'
        '        "環境変数に設定してください。手順は README を参照。\\n"\n'
        "        'PowerShell: $env:' + API_KEY_ENV + ' = \"<APIキー>\"'\n"
        "    )",
        '    raise AuthError("API キーが設定されていません。")',
    ),
    (
        AUTH,
        "鍵を伏せない（実行画面に鍵が出る）",
        "    return text.replace(api_key, REDACTED)",
        "    return text",
    ),
    (
        AUTH,
        "伏せ字を空文字にする（伏せたことが分からない）",
        'REDACTED = "***"',
        'REDACTED = ""',
    ),
    (
        AUTH,
        "鍵が空でも replace する（全文字の間に伏せ字が入る）",
        "    if not api_key:\n        return text",
        "    if False:\n        return text",
    ),
    (
        AUTH,
        "空の鍵でもサービスを組む",
        "    if not key:",
        "    if False:",
    ),
    (
        AUTH,
        "discovery のキャッシュ探索を切らない",
        "        cache_discovery=False,",
        "        cache_discovery=True,",
    ),
    (
        AUTH,
        "developerKey に鍵を渡さない",
        "        developerKey=key,",
        "        developerKey=None,",
    ),
    (
        AUTH,
        "別の API のサービス名を使う",
        'API_SERVICE_NAME = "youtube"',
        'API_SERVICE_NAME = "youtubeAnalytics"',
    ),
    (
        AUTH,
        "API のバージョンを間違える",
        'API_VERSION = "v3"',
        'API_VERSION = "v2"',
    ),
    # =============================================================== 検索の定数
    (
        SEARCH,
        "part に snippet を渡さない（タイトルが返らない）",
        'SEARCH_PART = "snippet"',
        'SEARCH_PART = "id"',
    ),
    (
        SEARCH,
        "type を絞らない（チャンネルや再生リストが混ざる）",
        'SEARCH_TYPE = "video"',
        'SEARCH_TYPE = "video,channel,playlist"',
    ),
    (
        SEARCH,
        "既定の取得件数を変える",
        "DEFAULT_MAX_RESULTS = 5",
        "DEFAULT_MAX_RESULTS = 10",
    ),
    (
        SEARCH,
        "取得件数の上限を公式より広げる",
        "MAX_RESULTS_LIMIT = 50",
        "MAX_RESULTS_LIMIT = 100",
    ),
    (
        SEARCH,
        "取得件数 0 を許す（0件を全部確認したことにできる）",
        "MIN_MAX_RESULTS = 1",
        "MIN_MAX_RESULTS = 0",
    ),
    (
        SEARCH,
        "既定の並び順を変える",
        'DEFAULT_ORDER = "relevance"',
        'DEFAULT_ORDER = "date"',
    ),
    (
        SEARCH,
        "公式が認める並び順を1つ落とす",
        'VALID_ORDERS: tuple[str, ...] = ("date", "rating", "relevance", "title", "videoCount", "viewCount")',
        'VALID_ORDERS: tuple[str, ...] = ("date", "rating", "title", "videoCount", "viewCount")',
    ),
    (
        SEARCH,
        "1日100回の別枠上限をユニット数と取り違える",
        "DAILY_SEARCH_CALL_LIMIT = 100",
        "DAILY_SEARCH_CALL_LIMIT = 10000",
    ),
    (
        SEARCH,
        "視聴URLのホストを間違える",
        'WATCH_URL_HOST = "www.youtube.com"',
        'WATCH_URL_HOST = "youtube.com"',
    ),
    (
        SEARCH,
        "視聴URLを http で組む",
        'WATCH_URL_SCHEME = "https"',
        'WATCH_URL_SCHEME = "http"',
    ),
    (
        SEARCH,
        "動画IDの検査から前後の固定を外す（長すぎるIDが通る）",
        r'VIDEO_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{11}\Z")',
        r'VIDEO_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{11}")',
    ),
    (
        SEARCH,
        "動画IDから記号を締め出す（正当な動画を落とす）",
        r'VIDEO_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{11}\Z")',
        r'VIDEO_ID_PATTERN = re.compile(r"\A[A-Za-z0-9]{11}\Z")',
    ),
    (
        SEARCH,
        "動画IDの文字数を見ない",
        r'VIDEO_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{11}\Z")',
        r'VIDEO_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{1,20}\Z")',
    ),
    # =============================================================== 入力の確定
    (
        SEARCH,
        "キーワードの前後の空白を落とさない",
        '    keyword = (value or "").strip()',
        '    keyword = value or ""',
    ),
    (
        SEARCH,
        "空のキーワードで検索する",
        "    if not keyword:\n        raise SearchError",
        "    if False:\n        raise SearchError",
    ),
    (
        SEARCH,
        "取得件数の範囲を見ない",
        "    if count < MIN_MAX_RESULTS or count > MAX_RESULTS_LIMIT:",
        "    if False:",
    ),
    (
        SEARCH,
        "取得件数の下限だけ見ない",
        "    if count < MIN_MAX_RESULTS or count > MAX_RESULTS_LIMIT:",
        "    if count > MAX_RESULTS_LIMIT:",
    ),
    (
        SEARCH,
        "取得件数の上限だけ見ない",
        "    if count < MIN_MAX_RESULTS or count > MAX_RESULTS_LIMIT:",
        "    if count < MIN_MAX_RESULTS:",
    ),
    (
        SEARCH,
        "取得件数のエラーに範囲を書かない",
        '            f"取得件数は {MIN_MAX_RESULTS}〜{MAX_RESULTS_LIMIT} の範囲で指定してください: {count}"',
        '            "取得件数が不正です"',
    ),
    (
        SEARCH,
        "知らない並び順をそのまま API に渡す",
        "    if order not in VALID_ORDERS:",
        "    if False:",
    ),
    (
        SEARCH,
        "並び順のエラーに使える値を書かない",
        '            f"並び順が不正です: {order!r}\\n使える値: " + " / ".join(VALID_ORDERS)',
        '            f"並び順が不正です: {order!r}"',
    ),
    # =============================================================== URL の組み立て
    (
        SEARCH,
        "空の動画IDを「形式が不正」で片付ける",
        '    if not identifier:\n        raise SearchError("動画IDが空です")',
        '    if False:\n        raise SearchError("動画IDが空です")',
    ),
    (
        SEARCH,
        "動画IDの形を確かめずにURLを組む",
        "    if not VIDEO_ID_PATTERN.match(identifier):",
        "    if False:",
    ),
    (
        SEARCH,
        "URL から ID を取り出すとき scheme を見ない",
        "    if parsed.scheme != WATCH_URL_SCHEME:",
        "    if False:",
    ),
    (
        SEARCH,
        "URL から ID を取り出すときホストを見ない",
        "    if parsed.netloc != WATCH_URL_HOST:",
        "    if False:",
    ),
    (
        SEARCH,
        "URL から ID を取り出すときパスを見ない",
        "    if parsed.path != WATCH_URL_PATH:",
        "    if False:",
    ),
    (
        SEARCH,
        "余分なクエリパラメータを許す",
        "    if set(query) != {WATCH_URL_QUERY_KEY}:",
        "    if WATCH_URL_QUERY_KEY not in query:",
    ),
    (
        SEARCH,
        "v が複数あっても最初を採る",
        "    if len(values) != 1:",
        "    if False:",
    ),
    (
        SEARCH,
        "取り出した ID の形を確かめない",
        "    return identifier if VIDEO_ID_PATTERN.match(identifier) else None",
        "    return identifier",
    ),
    (
        SEARCH,
        "None の URL で落ちる",
        "    if not url or not isinstance(url, str):",
        "    if False:",
    ),
    # =============================================================== タイトル
    (
        SEARCH,
        "HTML 実体参照を解かない（&amp; がそのまま出る）",
        "    return html.unescape(str(raw)).strip()",
        "    return str(raw).strip()",
    ),
    (
        SEARCH,
        "タイトルの前後の空白を落とさない",
        "    return html.unescape(str(raw)).strip()",
        "    return html.unescape(str(raw))",
    ),
    (
        SEARCH,
        "None のタイトルを 'None' という文字列にする",
        '    if raw is None:\n        return ""',
        '    if False:\n        return ""',
    ),
    # =============================================================== 応答の読み方
    (
        SEARCH,
        "items が無い応答を素通りさせる",
        '    if not isinstance(response, dict) or "items" not in response:',
        "    if False:",
    ),
    (
        SEARCH,
        "items の型を見ない",
        "    if not isinstance(items, list):",
        "    if False:",
    ),
    (
        SEARCH,
        "何件目かを 0 から数える",
        "    for position, entry in enumerate(items, start=1):",
        "    for position, entry in enumerate(items):",
    ),
    (
        SEARCH,
        "id の型を見ない",
        "        if not isinstance(identifier, dict):",
        "        if False:",
    ),
    (
        SEARCH,
        "動画IDの欠落を素通りさせる",
        "        if not video_id:",
        "        if False:",
    ),
    (
        SEARCH,
        "snippet の欠落を素通りさせる",
        "        if not isinstance(snippet, dict):",
        "        if False:",
    ),
    (
        SEARCH,
        "空のタイトルを「取れた」ことにする",
        "        if not title:",
        "        if False:",
    ),
    (
        SEARCH,
        "URL ではなくタイトルを URL として持たせる",
        "        videos.append(Video(video_id=video_id, title=title, url=url))",
        "        videos.append(Video(video_id=video_id, title=title, url=title))",
    ),
    # =============================================================== 検索の呼び方
    (
        SEARCH,
        "part を id にして呼ぶ",
        "                part=SEARCH_PART,",
        '                part="id",',
    ),
    (
        SEARCH,
        "キーワードを切り詰めて渡す",
        "                q=keyword,",
        "                q=keyword[:3],",
    ),
    (
        SEARCH,
        "type を渡さない",
        "                type=SEARCH_TYPE,\n",
        "",
    ),
    (
        SEARCH,
        "指定された件数ではなく既定値を渡す",
        "                maxResults=max_results,",
        "                maxResults=DEFAULT_MAX_RESULTS,",
    ),
    (
        SEARCH,
        "指定された並び順ではなく既定値を渡す",
        "                order=order,",
        "                order=DEFAULT_ORDER,",
    ),
    # =============================================================== エラーの翻訳
    (
        SEARCH,
        "エラー本文の鍵を伏せない",
        "    detail = youtube_auth.redact(_api_message(error), api_key)",
        "    detail = _api_message(error)",
    ),
    (
        SEARCH,
        "API 未有効化を見分けない",
        "    if _looks_like_api_disabled(detail, reason):",
        "    if False:",
    ),
    (
        SEARCH,
        "未有効化のときにクォータの話も混ぜる",
        '            "反映に数分かかることがあります。\\n"',
        '            "クォータ超過の可能性もあります。\\n"',
    ),
    (
        SEARCH,
        "クォータ超過を見分けない",
        "    if _looks_like_quota(detail, reason):",
        "    if False:",
    ),
    (
        SEARCH,
        "鍵の不正を見分けない",
        '    return "api key not valid" in lowered or "api key" in lowered or reason == "keyInvalid"',
        "    return False",
    ),
    (
        SEARCH,
        "本文が読めないとき例外の内部表現に逃げる（URI の鍵が出る）",
        "        f\"応答: {detail or '(応答の本文を読み取れませんでした)'}\"",
        '        f"応答: {detail or str(error)}"',
    ),
    # =============================================================== 画面と保存
    (
        SEARCH,
        "タイトルを表示しない",
        '        lines.append(f"  {position}. {video.title}")',
        '        lines.append(f"  {position}.")',
    ),
    (
        SEARCH,
        "URL を表示しない",
        '        lines.append(f"     {video.url}")\n',
        "",
    ),
    (
        SEARCH,
        "先頭の1件しか表示しない",
        "    for position, video in enumerate(videos, start=1):",
        "    for position, video in enumerate(videos[:1], start=1):",
    ),
    (
        SEARCH,
        "結果ファイルにキーワードを書かない",
        '        "keyword": keyword,',
        '        "keyword": "",',
    ),
    (
        SEARCH,
        "結果ファイルの並び順を既定値で埋める",
        '        "order": order,',
        '        "order": DEFAULT_ORDER,',
    ),
    (
        SEARCH,
        "結果ファイルの件数を 0 にする",
        '        "count": len(videos),',
        '        "count": 0,',
    ),
    (
        SEARCH,
        "結果ファイルに動画IDを書く（照合が URL を経由しなくなる）",
        '        "videos": [{"title": video.title, "url": video.url} for video in videos],',
        '        "videos": [{"title": video.title, "url": video.url, "video_id": video.video_id} for video in videos],',
    ),
    (
        SEARCH,
        "保存先の親フォルダを作らない",
        "    destination.parent.mkdir(parents=True, exist_ok=True)\n",
        "",
    ),
    (
        SEARCH,
        "日本語を \\uXXXX に潰して保存する",
        '        json.dumps(payload, ensure_ascii=False, indent=2) + "\\n",',
        '        json.dumps(payload, indent=2) + "\\n",',
    ),
    # =============================================================== 入口（検索）
    (
        SEARCH,
        "キーワードを任意の引数にする",
        '    parser.add_argument("--keyword", required=True, help="検索キーワード")',
        '    parser.add_argument("--keyword", required=False, help="検索キーワード")',
    ),
    (
        SEARCH,
        "API キーをコマンドラインから受け取れるようにする",
        "    return parser.parse_args(argv)",
        '    parser.add_argument("--api-key", default=None)\n    return parser.parse_args(argv)',
    ),
    (
        SEARCH,
        "引数を確かめる前に認証する（不正な実行でも API に出る）",
        "    try:\n        # 検索する内容を先に確定させる。",
        "    factory(args)\n    try:\n        # 検索する内容を先に確定させる。",
    ),
    (
        SEARCH,
        "該当0件でも成功として終わる",
        # format_videos にも同じ行があるので、直後のコメントまで含めて特定する。
        "    if not videos:\n        # 「対象が尽きた」を成功で終わらせない。",
        "    if False:\n        # 「対象が尽きた」を成功で終わらせない。",
    ),
    (
        SEARCH,
        "--json-out が無くても保存しようとする",
        "    results_path = None\n    if args.json_out:",
        "    results_path = None\n    if True:",
    ),
    (
        SEARCH,
        "検索に失敗しても 0 を返す",
        "    except (SearchError, youtube_auth.AuthError) as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 1",
        "    except (SearchError, youtube_auth.AuthError) as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 0",
    ),
    (
        SEARCH,
        "検索結果を印字しない",
        "    print(format_videos(videos))\n",
        "",
    ),
    (
        SEARCH,
        "次にやることを案内しない",
        "    print(format_next_step(keyword, len(videos), results_path))\n",
        "",
    ),
    # =============================================================== 照合（結果ファイル）
    (
        VERIFY,
        "読み直しで part に snippet を渡さない",
        'VIDEOS_PART = "snippet"',
        'VIDEOS_PART = "id"',
    ),
    (
        VERIFY,
        "一度に渡せる ID の上限を公式より広げる",
        "VIDEOS_ID_LIMIT = 50",
        "VIDEOS_ID_LIMIT = 500",
    ),
    (
        VERIFY,
        "結果ファイルの存在を確かめない",
        "    if not source.exists():",
        "    if False:",
    ),
    (
        VERIFY,
        "JSON として読めないファイルを翻訳しない",
        "    except (ValueError, UnicodeDecodeError) as error:",
        "    except (UnicodeDecodeError,) as error:",
    ),
    (
        VERIFY,
        "結果ファイルが辞書かどうかを見ない",
        "    if not isinstance(payload, dict):",
        "    if False:",
    ),
    (
        VERIFY,
        "結果ファイルの keyword の欠落を見ない",
        "    if not isinstance(keyword, str) or not keyword.strip():",
        "    if False:",
    ),
    (
        VERIFY,
        "結果ファイルの count の欠落を見ない",
        "    if not isinstance(count, int) or isinstance(count, bool):",
        "    if False:",
    ),
    (
        VERIFY,
        "結果ファイルの videos の型を見ない",
        "    if not isinstance(videos, list):",
        "    if False:",
    ),
    (
        VERIFY,
        "空の結果ファイルを受け入れる（0件すべて一致が出る）",
        "    if not videos:",
        "    if False:",
    ),
    (
        VERIFY,
        "各項目の title の欠落を見ない",
        '        _require_text(record, "title", position)\n',
        "",
    ),
    (
        VERIFY,
        "各項目の url の欠落を見ない",
        '        _require_text(record, "url", position)\n',
        "",
    ),
    # =============================================================== 照合（URL→ID）
    (
        VERIFY,
        "URL をそのまま動画IDとして扱う",
        "        video_id = search_videos.video_id_from_url(url)",
        "        video_id = url",
    ),
    (
        VERIFY,
        "取り出せなかった URL を ID の側に混ぜる",
        "        if video_id is None:",
        "        if False:",
    ),
    (
        VERIFY,
        "壊れた URL を記録しない",
        "            bad.append(url)\n",
        "            pass\n",
    ),
    # =============================================================== 照合（手元）
    (
        VERIFY,
        "キーワードをファイル自身と比べる（トートロジー）",
        '    checks.append(_compare("検索キーワード", expected_keyword, payload.get("keyword")))',
        '    checks.append(_compare("検索キーワード", payload.get("keyword"), payload.get("keyword")))',
    ),
    (
        VERIFY,
        "件数をファイル自身と比べる（トートロジー）",
        '    checks.append(_compare("結果の件数", expected_count, len(videos)))',
        '    checks.append(_compare("結果の件数", len(videos), len(videos)))',
    ),
    (
        VERIFY,
        "記録された件数を自分自身と比べる（トートロジー）",
        '    checks.append(_compare("記録された件数", len(videos), payload.get("count")))',
        '    checks.append(_compare("記録された件数", payload.get("count"), payload.get("count")))',
    ),
    (
        VERIFY,
        "動画IDの重複を見ない",
        "    duplicates = sorted({video_id for video_id in ids if ids.count(video_id) > 1})",
        "    duplicates = []",
    ),
    (
        VERIFY,
        "壊れた URL があっても OK にする",
        "            not bad,",
        "            True,",
    ),
    (
        VERIFY,
        "照合を常に一致とする",
        # build_remote_checks 側の 8 スペース版が部分一致で引っかかるので、
        # 関数の定義行まで含めて特定する。
        "def _compare(label: str, expected, actual) -> Check:\n    ok = expected == actual",
        "def _compare(label: str, expected, actual) -> Check:\n    ok = True",
    ),
    (
        VERIFY,
        "食い違いの中身を書かない",
        '    detail = "" if ok else f"期待 {expected!r} / 実際 {actual!r}"',
        '    detail = ""',
    ),
    # =============================================================== 照合（読み直し）
    (
        VERIFY,
        "読み直す対象が空でも API を呼ぶ",
        "    if not ids:",
        "    if False:",
    ),
    (
        VERIFY,
        "50件を超えても黙って投げる",
        "    if len(ids) > VIDEOS_ID_LIMIT:",
        "    if False:",
    ),
    (
        VERIFY,
        "先頭の1件しか読み直さない",
        '            .list(part=VIDEOS_PART, id=",".join(ids))',
        "            .list(part=VIDEOS_PART, id=ids[0])",
    ),
    (
        VERIFY,
        "読み直しの応答の items を確かめない",
        "    items = response.get(\"items\")\n    if not isinstance(items, list):",
        "    items = response.get(\"items\")\n    if False:",
    ),
    (
        VERIFY,
        "読み直した動画を位置で持つ（順序が入れ替わると崩れる）",
        '            fetched[str(item["id"])] = item',
        "            fetched[str(len(fetched))] = item",
    ),
    (
        VERIFY,
        "読み直しのエラーで鍵を伏せない",
        "    translated = search_videos.translate_http_error(error, api_key)",
        "    translated = search_videos.translate_http_error(error, None)",
    ),
    (
        VERIFY,
        "返ってこなかった動画を数えない",
        "    missing = [video_id for video_id in ids if video_id not in fetched]",
        "    missing = []",
    ),
    (
        VERIFY,
        "実在の判定を常に OK にする",
        "            not missing and bool(ids),",
        "            True,",
    ),
    (
        VERIFY,
        "返ってこなかった動画で落ちる",
        "        if item is None:",
        "        if False:",
    ),
    (
        VERIFY,
        "読み直した側の実体参照を解かない",
        '        actual = search_videos.clean_title((item.get("snippet") or {}).get("title"))',
        '        actual = (item.get("snippet") or {}).get("title")',
    ),
    (
        VERIFY,
        "空のタイトルどうしを一致にする",
        "        if not actual:",
        "        if False:",
    ),
    (
        VERIFY,
        "タイトルの照合を常に一致とする",
        "        ok = expected == actual",
        "        ok = True",
    ),
    (
        VERIFY,
        "照合の項目番号をずらす",
        '        label = f"タイトル一致 [{position}]"',
        '        label = f"タイトル一致 [{position + 1}]"',
    ),
    (
        VERIFY,
        "先頭の1件しか照合しない",
        "    for position, record in enumerate(videos, start=1):\n"
        '        label = f"タイトル一致 [{position}]"',
        "    for position, record in enumerate(videos[:1], start=1):\n"
        '        label = f"タイトル一致 [{position}]"',
    ),
    # =============================================================== 報告
    (
        VERIFY,
        "ゼロ件の照合を「全部一致」にする",
        "    if not checks:\n        return False",
        "    if False:\n        return False",
    ),
    (
        VERIFY,
        "1つでも一致すれば全部一致とする",
        "    return all(check.ok for check in checks)",
        "    return any(check.ok for check in checks)",
    ),
    (
        VERIFY,
        "NG を OK と表示する",
        '        mark = "OK" if check.ok else "NG"',
        '        mark = "OK"',
    ),
    (
        VERIFY,
        "食い違いの中身を印字しない",
        "        if check.detail:\n            line += f\"  {check.detail}\"\n",
        "",
    ),
    # =============================================================== 入口（照合）
    (
        VERIFY,
        "結果ファイルを任意の引数にする",
        '    parser.add_argument("--results", required=True, help="search_videos.py が --json-out で書いたファイル")',
        '    parser.add_argument("--results", required=False, help="search_videos.py が --json-out で書いたファイル")',
    ),
    (
        VERIFY,
        "キーワードを任意の引数にする（期待値をファイルから埋める余地ができる）",
        '    parser.add_argument("--keyword", required=True, help="検索したときのキーワード")',
        '    parser.add_argument("--keyword", required=False, default="", help="検索したときのキーワード")',
    ),
    (
        VERIFY,
        "件数を任意の引数にする",
        '    parser.add_argument("--expect-count", required=True, type=int, help="表示されたはずの件数")',
        '    parser.add_argument("--expect-count", required=False, type=int, default=0, help="表示されたはずの件数")',
    ),
    (
        VERIFY,
        "API キーをコマンドラインから受け取れるようにする",
        "    # --api-key は用意しない（search_videos.py と同じ理由）。\n    return parser.parse_args(argv)",
        '    parser.add_argument("--api-key", default=None)\n    return parser.parse_args(argv)',
    ),
    (
        VERIFY,
        "手元の照合が落ちても API を呼ぶ",
        "    if not all_ok(local):",
        "    if False:",
    ),
    (
        VERIFY,
        "読み直しの照合が落ちても 0 を返す",
        "    if not all_ok(remote):",
        "    if False:",
    ),
    (
        VERIFY,
        "手元の照合の結果を印字しない",
        "    print(format_checks(local))\n",
        "",
    ),
    (
        VERIFY,
        "読み直しに失敗しても 0 を返す",
        "    except (VerifyError, youtube_auth.AuthError) as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 1",
        "    except (VerifyError, youtube_auth.AuthError) as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 0",
    ),
    (
        VERIFY,
        "照合のあとに結果ファイルを書き換える（読むだけではなくなる）",
        '    print("\\nすべて一致しました。")\n    return 0',
        '    print("\\nすべて一致しました。")\n    Path(args.results).write_text("x", encoding="utf-8")\n    return 0',
    ),
    (
        VERIFY,
        "すべて一致した旨を印字しない",
        '    print("\\nすべて一致しました。")\n',
        "",
    ),
]


def read_source(path: Path) -> str:
    """改行コードを変換せずに読む。

    既定の text mode は CRLF を LF に読み替え、書き戻すとき OS の既定に直す。
    素直に read/write すると、書き換えていないファイルの改行コードだけが静かに変わる。
    """
    return path.read_text(encoding="utf-8", newline="")


def write_source(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def _backup_path(path: Path) -> Path:
    return BACKUP_DIR / f"{path.parent.name}__{path.name}"


def restore_leftovers() -> int:
    """前回が強制終了していたら、ここで元に戻す。"""
    restored = 0
    for path in TARGETS:
        backup = _backup_path(path)
        if not backup.exists():
            continue
        saved = read_source(backup)
        if read_source(path) != saved:
            write_source(path, saved)
            print(f"! 前回の中断で {path.name} が壊れたまま残っていたので元に戻した")
            restored += 1
        backup.unlink()
    return restored


def _install_restore_guard() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    originals = {}
    for path in TARGETS:
        text = read_source(path)
        originals[path] = text
        write_source(_backup_path(path), text)

    def restore() -> None:
        for path, text in originals.items():
            if read_source(path) != text:
                write_source(path, text)
                print(f"! 中断されたため {path.name} を元に戻した")
            backup = _backup_path(path)
            if backup.exists():
                backup.unlink()

    atexit.register(restore)


def run_tests() -> int:
    proc = subprocess.run(
        [str(PYTHON), "-m", "pytest", *[str(d) for d in TEST_DIRS], "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    failed = re.search(r"(\d+) failed", tail)
    errors = re.search(r"(\d+) error", tail)
    return (int(failed.group(1)) if failed else 0) + (int(errors.group(1)) if errors else 0)


def main() -> int:
    # 控えの確認は、今回ぶんの控えを取る前にやる。順番が逆だと
    # 「壊れた状態」を正しい中身として保存してしまう。
    restore_leftovers()
    _install_restore_guard()

    if run_tests():
        print("! 壊す前からテストが落ちている。先にそちらを直すこと")
        return 2

    survivors: list[str] = []
    print(f"{'#':>3}  {'落ちた件数':>10}  対象  壊した内容")
    print("-" * 82)

    for index, (path, description, old, new) in enumerate(MUTATIONS, start=1):
        if not path.exists():
            print(f"{index:>3}  {'対象なし':>10}  {path.name}  {description}")
            survivors.append(f"{index}. {description}（対象ファイルが無い）")
            continue

        original = read_source(path)
        occurrences = original.count(old)
        if occurrences != 1:
            # 置換できないミューテーションは「守られている証拠」にならない。
            # 素通りと同じ扱いにして必ず目に入れる。
            print(f"{index:>3}  {'置換不能':>10}  {path.name}  {description}（一致 {occurrences} 件）")
            survivors.append(f"{index}. {description}（置換できなかった）")
            continue

        write_source(path, original.replace(old, new, 1))
        try:
            caught = run_tests()
        finally:
            write_source(path, original)

        print(f"{index:>3}  {caught:>10}  {path.name}  {description}{'' if caught else '  ← 素通り'}")
        if not caught:
            survivors.append(f"{index}. {description}")

    print("-" * 82)
    if survivors:
        print(f"素通りが {len(survivors)} 件:")
        for line in survivors:
            print(f"  - {line}")
        return 1

    print(f"素通りゼロ（{len(MUTATIONS)} か所）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
