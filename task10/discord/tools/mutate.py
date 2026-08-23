#!/usr/bin/env python3
"""relay_uploads.py を1か所ずつ壊して、テストが落ちることを確かめる。

**テストが通っていることは、守られていることの証拠にならない。**
課題9では、主題そのものである行が素通りしていた。課題10（LINE）でも
73か所を壊して初めて穴が無いと言えた。

この課題で守りたいのは「**繰り返し動くうちに、静かにずれていく**」失敗である。

============================== ================================================
壊して確かめたいこと             なぜ静かなのか
============================== ================================================
時刻の取り違え                   API は 200 を返す。順番が変わるだけ
並び順への依存                   たいていの日は正しく動く。崩れた日だけ壊れる
重複と取りこぼし                 取りこぼしは**届かない**ので、誰も気づかない
状態ファイルの扱い               空に化けると全件が流れ直す。1回で分かるが遅い
上限で切ったことの報告            黙って切ると「全部流した」と読めてしまう
============================== ================================================

使い方::

    .venv\\Scripts\\python.exe task10\\discord\\tools\\mutate.py

リポジトリを一時ディレクトリへ写し、**写した側だけ**を壊す。
成果物には触らないので、途中で強制終了しても壊れたまま残らない。

**置換先が見つからない（NOT FOUND）は素通りと同じ扱いにする。**
実装を直して壊しかたを直し忘れると、何も壊さずに全部通って
「穴ゼロ」と出てしまうため。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

RELAY = "task10/discord/relay_uploads.py"

IGNORE = shutil.ignore_patterns(
    ".venv", ".git", "__pycache__", ".pytest_cache", "docs", "*.png", "node_modules"
)

# 課題8の送信をそのまま使っているので、**あちらのテストも一緒に回す**。
# 借りている部品を壊していないことを、ここでも確かめる。
TEST_PATHS = ("task10/discord/tests", "task8/tests", "common/tests")

# (対象ファイル, 壊した内容, 置換前, 置換後)
MUTATIONS: list[tuple[str, str, str, str]] = [
    # ==================================== 時刻：静かに順番が変わる
    (
        RELAY,
        "公開時刻ではなく『再生リストに追加された時刻』を使う",
        'content.get("videoPublishedAt"), label="contentDetails.videoPublishedAt"',
        'snippet.get("publishedAt"), label="contentDetails.videoPublishedAt"',
    ),
    (
        RELAY,
        "videoPublishedAt が無くても落とさない",
        '    if "videoPublishedAt" not in content:',
        "    if False:",
    ),
    (
        RELAY,
        "タイムゾーンの無い時刻を通す",
        "    if parsed.tzinfo is None:",
        "    if False:",
    ),
    (
        RELAY,
        "UTC へ揃えない（ローカル時刻のまま比べる）",
        "    return parsed.astimezone(timezone.utc)",
        "    return parsed",
    ),
    # ==================================== 読み取り：混ざりものを通す
    (
        RELAY,
        "動画以外の項目も通す",
        '    if kind != "youtube#video":',
        "    if False:",
    ),
    (
        RELAY,
        "空の videoId を通す",
        '    video_id = _text(content.get("videoId")).strip()\n    if not video_id:',
        '    video_id = _text(content.get("videoId")).strip()\n    if False:',
    ),
    (
        RELAY,
        "タイトルの HTML エンティティを戻さない（&amp; がそのまま出る）",
        '        title=html.unescape(_text(snippet.get("title"))),',
        '        title=_text(snippet.get("title")),',
    ),
    # ==================================== 並び順：公式が保証していない
    (
        RELAY,
        "取得した順のまま送る（並べ直さない）",
        "    return sorted(picked.values(), key=lambda u: (u.when(key), u.video_id))",
        "    return list(picked.values())",
    ),
    (
        RELAY,
        "新しい順に送る（読む側が話数を追えない）",
        "    return sorted(picked.values(), key=lambda u: (u.when(key), u.video_id))",
        "    return sorted(picked.values(), key=lambda u: (u.when(key), u.video_id), reverse=True)",
    ),
    # ==================================== 重複と取りこぼし
    (
        RELAY,
        "送信済みの除外をやめる（毎回もう一度送る）",
        "        if item.video_id in already or item.video_id in picked:",
        "        if item.video_id in picked:",
    ),
    (
        RELAY,
        "ページ跨ぎの重複を畳まない",
        "        if item.video_id in already or item.video_id in picked:",
        "        if item.video_id in already:",
    ),
    (
        RELAY,
        "水位が下がる（古い動画を送ると遡る範囲が壊れる）",
        "    if watermark is None or stamp > watermark:",
        "    if watermark is None or stamp < watermark:",
    ),
    (
        RELAY,
        "覚える ID を打ち切らない（状態ファイルが際限なく育つ）",
        "    ids = (ids + (upload.video_id,))[-keep:] if keep > 0 else ()",
        "    ids = ids + (upload.video_id,)",
    ),
    (
        RELAY,
        "覚える件数の既定を 0 にする",
        "DEFAULT_KEEP_IDS = 200",
        "DEFAULT_KEEP_IDS = 0",
    ),
    # ==================================== 状態ファイル
    (
        RELAY,
        "状態を常に空として読む（再生リスト全件が流れ直す）",
        "    if not target.exists():",
        "    if True:",
    ),
    (
        RELAY,
        "壊れた JSON を RelayError にしない",
        "    except (OSError, ValueError) as error:",
        "    except OSError as error:",
    ),
    (
        RELAY,
        "sent_ids の型を確かめない",
        '        raise RelayError("状態ファイルの sent_ids が文字列の配列ではありません")',
        "        raw_ids = []",
    ),
    (
        RELAY,
        "別名で書いたまま置き換えない（状態が保存されない）",
        "    os.replace(temporary, target)",
        "    pass",
    ),
    # ==================================== ページを遡る
    (
        RELAY,
        "ページに1件でも古いものがあれば打ち切る（並びが崩れた日に取りこぼす）",
        "        if floor is not None and page and all(u.when(key) < floor for u in page):",
        "        if floor is not None and page and any(u.when(key) < floor for u in page):",
    ),
    (
        RELAY,
        "水位ちょうどで切る（余白を取らない）",
        "    return state.watermark - WATERMARK_MARGIN",
        "    return state.watermark",
    ),
    (
        RELAY,
        "床そのものを効かせない（遡りも受け付けも無制限になる）",
        "    return state.watermark - WATERMARK_MARGIN",
        "    return None",
    ),
    (
        RELAY,
        "余白を 0 にする",
        "WATERMARK_MARGIN = timedelta(days=1)",
        "WATERMARK_MARGIN = timedelta(0)",
    ),
    (
        RELAY,
        "打ち切ったことを報告しない",
        "    if on_note is not None and page_token:",
        "    if False:",
    ),
    (
        RELAY,
        "遡るページ数の既定を 1 にする",
        "DEFAULT_MAX_PAGES = 5",
        "DEFAULT_MAX_PAGES = 1",
    ),
    (
        RELAY,
        "1ページの件数を減らす（呼ぶ回数が増えて高くつく）",
        "DEFAULT_PAGE_SIZE = 50",
        "DEFAULT_PAGE_SIZE = 10",
    ),
    (
        RELAY,
        "maxResults を無視して決め打ちにする",
        "                    maxResults=page_size,",
        "                    maxResults=5,",
    ),
    (
        RELAY,
        "contentDetails を要求しない（videoPublishedAt が取れなくなる）",
        '                    part="snippet,contentDetails",',
        '                    part="snippet",',
    ),
    (
        RELAY,
        "チャンネル照会で余計な part を頼む（コストが上がる）",
        '        response = service.channels().list(part="contentDetails", **query).execute()',
        '        response = service.channels().list(part="snippet,contentDetails", **query).execute()',
    ),
    (
        RELAY,
        "見つからないチャンネルを素通りさせる",
        "    if not items:",
        "    if False:",
    ),
    (
        RELAY,
        "アップロード再生リストが空でも進む",
        "    if not uploads:",
        "    if False:",
    ),
    # ==================================== 1本ずつ送る
    (
        RELAY,
        "1本ごとに記録しない（落ちたときの重複が全件になる）",
        "        persist(current)",
        "        pass",
    ),
    (
        RELAY,
        "送っても記録しない",
        "        current = remember(current, item, keep=keep, key=key)",
        "        pass",
    ),
    (
        RELAY,
        "送った結果を返さない",
        "        done.append(result)",
        "        pass",
    ),
    # ==================================== CLI の安全弁
    (
        RELAY,
        "1回の上限を無くす（初回に再生リスト全件が流れる）",
        "        if args.limit > 0 and len(fresh) > args.limit:",
        "        if False:",
    ),
    (
        RELAY,
        "上限で切らずに全部送る",
        "            fresh = fresh[: args.limit]",
        "            fresh = fresh",
    ),
    (
        RELAY,
        "上限の既定を大きくする",
        "DEFAULT_LIMIT = 5",
        "DEFAULT_LIMIT = 50",
    ),
    (
        RELAY,
        "--dry-run でも送る",
        "        if args.dry_run:",
        "        if False:",
    ),
    (
        RELAY,
        "--init でも送ってしまう",
        "        if args.init:",
        "        if False:",
    ),
    (
        RELAY,
        "--init を2回目でも許す（未送信ぶんを黙って捨てる）",
        "        if args.init and state_path.exists():",
        "        if False:",
    ),
    (
        RELAY,
        "--init が記録しない",
        "            save_state(state_path, current)",
        "            pass",
    ),
    # ==================================== 資格情報
    (
        RELAY,
        "API キーを先に読まない（落ちると分かっていて相手に接続する）",
        "        api_key = youtube_auth.read_api_key(resolved_env)",
        '        api_key = resolved_env.get("YOUTUBE_API_KEY", "dummy")',
    ),
    (
        RELAY,
        "エラーの文面から API キーを伏せない（公開事故）",
        "        say(f\"エラー: {youtube_auth.redact(str(error), locals().get('api_key'))}\")",
        '        say(f"エラー: {error}")',
    ),
    (
        RELAY,
        "チャンネル照会の例外から API キーを伏せない",
        "    return RelayError(youtube_auth.redact(str(error), api_key))",
        "    return RelayError(str(error))",
    ),
    (
        RELAY,
        "Bot Token を読まずに空で組む",
        "    token = discord_auth.read_bot_token(env)",
        '    token = ""',
    ),
    (
        RELAY,
        "メンションを抑止しない（他人が付けたタイトルで全員に通知が飛ぶ）",
        "            build_message(item), allow_mentions=False",
        "            build_message(item), allow_mentions=True",
    ),
    (
        RELAY,
        "テストに渡した env を無視して環境変数を混ぜる",
        "    if env is not None:\n        return env",
        "    if False:\n        return env",
    ),
    # ==================================== 本文
    (
        RELAY,
        "視聴 URL の形を変える",
        'WATCH_URL = "https://www.youtube.com/watch?v="',
        'WATCH_URL = "https://www.youtube.com/watch?video="',
    ),
    (
        RELAY,
        "本文に URL を入れない",
        '    return f"{WATCH_URL}{video_id}"',
        "    return WATCH_URL",
    ),
    (
        RELAY,
        "本文にタイトルを入れない",
        '    return f"{heading}\\n{upload.title}\\n{video_url(upload.video_id)}\\n(公開 {when})"',
        '    return f"{heading}\\n{video_url(upload.video_id)}\\n(公開 {when})"',
    ),
]

# ==================================== 照合（verify_relay.py）
#
# **照合そのものが壊れると、いちばん質が悪い。** 落ちるべきものが通り、
# しかも「照合 N 項目 / NG 0 件」という*合格した証拠*が出力される。

VERIFY = "task10/discord/verify_relay.py"

MUTATIONS += [
    (
        VERIFY,
        "載ったチャンネルを確かめない",
        '        _equals(_text(message.get("channel_id")), channel, label="チャンネル"),',
        '        Check(label="チャンネル", expected="", actual="", ok=True),',
    ),
    (
        VERIFY,
        "投稿者を確かめない（人間の投稿を bot の送信と取り違える）",
        '        _equals(_text(author), author_id, label="投稿者"),',
        '        Check(label="投稿者", expected="", actual="", ok=True),',
    ),
    (
        VERIFY,
        "メッセージIDを確かめない（別のメッセージを読んで合格にする）",
        '        _equals(_text(message.get("id")), _text(record.get("message_id")), label="メッセージID"),',
        '        Check(label="メッセージID", expected="", actual="", ok=True),',
    ),
    (
        VERIFY,
        "本文に動画URLがあるかを確かめない",
        "        _contains(content, relay_uploads.video_url(video_id), label=\"動画URL\"),",
        '        Check(label="動画URL", expected="", actual="", ok=True),',
    ),
    (
        VERIFY,
        "別エンドポイントのタイトルと突き合わせない（照合の主題そのもの）",
        '        _contains(content, title, label="タイトル"),',
        '        Check(label="タイトル", expected="", actual="", ok=True),',
    ),
    (
        VERIFY,
        "チャンネル名を突き合わせない",
        '        _contains(content, channel_title, label="チャンネル名"),',
        '        Check(label="チャンネル名", expected="", actual="", ok=True),',
    ),
    (
        VERIFY,
        "公開時刻を突き合わせない",
        '        _contains(content, f"{published:%Y-%m-%d %H:%M UTC}", label="公開時刻"),',
        '        Check(label="公開時刻", expected="", actual="", ok=True),',
    ),
    (
        VERIFY,
        "状態に記録されたかを確かめない（次回もう一度送るのに合格が出る）",
        "            ok=video_id in state.sent_ids,",
        "            ok=True,",
    ),
    (
        VERIFY,
        "空の期待値を合格にする（相手が空を返した瞬間に照合が無意味になる）",
        "        ok=bool(needle) and needle in haystack,",
        "        ok=needle in haystack,",
    ),
    (
        VERIFY,
        "照合側でタイトルのエスケープを戻さない（正しいのに毎回 NG が出る）",
        '    title = html.unescape(_text(snippet.get("title")))',
        '    title = _text(snippet.get("title"))',
    ),
    (
        VERIFY,
        "照合側でチャンネル名のエスケープを戻さない",
        '    channel_title = html.unescape(_text(snippet.get("channelTitle")))',
        '    channel_title = _text(snippet.get("channelTitle"))',
    ),
    (
        VERIFY,
        "動画が返ってこなくても合格にする（確かめられなかったを合格にする）",
        "    if not items:",
        "    if False:",
    ),
    (
        VERIFY,
        "違う動画が返っても合格にする",
        "    if returned != video_id:",
        "    if False:",
    ),
    (
        VERIFY,
        "名乗る照合項目数をずらす（README と食い違っても気づけなくなる）",
        "CHECKS_PER_VIDEO = 8",
        "CHECKS_PER_VIDEO = 7",
    ),
    (
        VERIFY,
        "投稿者の物差しを、照合対象そのものから取る（トートロジー）",
        "                author_id=identity.user_id,",
        '                author_id=_text(message.get("author", {}).get("id")),',
    ),
    (
        VERIFY,
        "記録が無いときのメッセージを絶対パスに戻す",
        '                f"記録に送信ぶんがありません: {relay_uploads.shown_path(args.results)}\\n"',
        '                f"記録に送信ぶんがありません: {args.results}\\n"',
    ),
]

# ==================================== 「新着」の基準と、記憶より広い窓
#
# あとから足した2つの守り。**足した理由がテストで固定されているか**を確かめる。

MUTATIONS += [
    (
        RELAY,
        "追加時刻を読まない（--new-by added が使えなくなる）",
        '        added_at = parse_time(snippet.get("publishedAt"), label="snippet.publishedAt")',
        "        added_at = None",
    ),
    (
        RELAY,
        "基準を無視して常に公開時刻で選ぶ",
        "        if key == NEW_BY_PUBLISHED:\n            return self.published_at",
        "        if True:\n            return self.published_at",
    ),
    (
        RELAY,
        "追加時刻が無いのを既定値で埋める（順序が静かに壊れる）",
        "            if self.added_at is None:",
        "            if False:",
    ),
    (
        RELAY,
        "知らない基準を黙って通す",
        '        raise RelayError(f"知らない新着の基準です: {key}")',
        "        return self.published_at",
    ),
    (
        RELAY,
        "並べ替えだけ基準に従い、水位は公開時刻で進める（噛み合わなくなる）",
        "    stamp = upload.when(key)",
        "    stamp = upload.published_at",
    ),
    (
        RELAY,
        "select_new で水位の床を効かせない（古い動画が新着として流れる）",
        "        if floor is not None and item.when(key) < floor:\n            continue",
        "        if False:\n            continue",
    ),
    (
        RELAY,
        "覚える件数を遡る窓に合わせない（こぼれたぶんが再送される）",
        "    return max(DEFAULT_KEEP_IDS, max_pages * page_size)",
        "    return DEFAULT_KEEP_IDS",
    ),
    (
        RELAY,
        "遡りの打ち切りを基準に従わせない",
        "        if floor is not None and page and all(u.when(key) < floor for u in page):",
        "        if floor is not None and page and all(u.published_at < floor for u in page):",
    ),
    (
        RELAY,
        "見る先が「ちょうど1つ」であることを確かめない",
        "        if sum(1 for t in targets if t) != 1:",
        "        if False:",
    ),
    (
        RELAY,
        "見る先が2つ以上あっても通す",
        "        if sum(1 for t in targets if t) != 1:",
        "        if sum(1 for t in targets if t) < 1:",
    ),
    (
        RELAY,
        "チャンネルIDとハンドルの排他を確かめない",
        "    if bool(wanted) == bool(wanted_handle):",
        "    if False:",
    ),
    (
        RELAY,
        "フィルタを2つ載せる（公式は「ちょうど1つ」と明記している）",
        '    query = {"id": wanted} if wanted else {"forHandle": wanted_handle}',
        '    query = {"id": wanted, "forHandle": wanted_handle}',
    ),
    (
        RELAY,
        "ハンドルの @ を揃えない",
        '    return text if text.startswith("@") else f"@{text}"',
        "    return text",
    ),
    (
        RELAY,
        "送る前の一覧に videoId を出さない（同名の動画を区別できない）",
        '                f"[{item.video_id}]  {item.title}"',
        '                f"{item.title}"',
    ),
    (
        RELAY,
        "一覧の時刻を、選んだ基準ではなく公開時刻で出す",
        '                f"  - {item.when(args.new_by):%Y-%m-%d %H:%M UTC}  "',
        '                f"  - {item.published_at:%Y-%m-%d %H:%M UTC}  "',
    ),
    # ==================================== 実行画面も提出物である
    (
        RELAY,
        "画面に絶対パスを出す（利用者の名前が公開される）",
        "        return target.resolve().relative_to(_REPO_ROOT).as_posix()",
        "        return str(target)",
    ),
    (
        RELAY,
        "リポジトリの外のパスを、名前だけにせず丸ごと出す",
        "        return target.name",
        "        return str(target)",
    ),
    (
        RELAY,
        "--init の拒否メッセージを絶対パスに戻す",
        '                f"状態ファイルが既にあります: {shown_path(state_path)}\\n"',
        '                f"状態ファイルが既にあります: {state_path}\\n"',
    ),
    (
        RELAY,
        "状態が壊れたときのメッセージを絶対パスに戻す",
        '            f"状態ファイルを読めませんでした: {shown_path(target)}\\n"',
        '            f"状態ファイルを読めませんでした: {target}\\n"',
    ),
    (
        RELAY,
        "--playlist-id を無視してチャンネルを引きに行く",
        "        if args.playlist_id:\n            playlist_id = args.playlist_id",
        "        if False:\n            playlist_id = args.playlist_id",
    ),
    (
        RELAY,
        "選んだ基準を選択へ渡さない",
        "        fresh = select_new(found, state, key=args.new_by)",
        "        fresh = select_new(found, state)",
    ),
    (
        RELAY,
        "既定の基準を追加時刻にする",
        "DEFAULT_NEW_BY = NEW_BY_PUBLISHED",
        "DEFAULT_NEW_BY = NEW_BY_ADDED",
    ),
]

# ==================================== 文章の照合（check_docs.py）
#
# **検査が壊れると、いちばん質が悪い。** 落ちるべきものが通り、しかも
# 「照合 N 項目 / NG 0 件」という*合格した証拠*が出力される。

CHECKDOCS = "task10/discord/tools/check_docs.py"

MUTATIONS += [
    (
        CHECKDOCS,
        "名乗りが無いのを 0 と読む（1件も検査していない、と区別が付かない）",
        "    match = re.search(SELF_COUNT_PATTERN, readme)\n    return int(match.group(1)) if match else None",
        "    match = re.search(SELF_COUNT_PATTERN, readme)\n    return int(match.group(1)) if match else 0",
    ),
    (
        CHECKDOCS,
        "自分を数えない（README には常に1つ少ない数が載り、しかも一致する）",
        "    actual = checks_so_far + 1",
        "    actual = checks_so_far",
    ),
    (
        CHECKDOCS,
        "自分を二重に数える",
        "    actual = checks_so_far + 1",
        "    actual = checks_so_far + 2",
    ),
    (
        CHECKDOCS,
        "名乗っていない README を合格にする",
        '        return False, f"README が照合項目数を名乗っていない（実際 {actual}）"',
        '        return True, f"README が照合項目数を名乗っていない（実際 {actual}）"',
    ),
    (
        CHECKDOCS,
        "自宅パスの検査を効かなくする",
        r'HOME_PATH_PATTERN = re.compile(r"C:\\Users\\[A-Za-z0-9_.-]+", re.IGNORECASE)',
        r'HOME_PATH_PATTERN = re.compile(r"^\\b$")',
    ),
    (
        CHECKDOCS,
        "API キーの検査を効かなくする",
        'API_KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z_-]{10,}")',
        r'API_KEY_PATTERN = re.compile(r"^\\b$")',
    ),
    (
        CHECKDOCS,
        "PowerShell で壊れるハンドル指定を見逃す",
        "    return UNQUOTED_HANDLE_PATTERN.search(readme) is None",
        "    return True",
    ),
    (
        CHECKDOCS,
        "ハンドルの検査が説明文まで弾く（書けなくなる）",
        r'UNQUOTED_HANDLE_PATTERN = re.compile(r"--handle\s+@")',
        r'UNQUOTED_HANDLE_PATTERN = re.compile(r"@")',
    ),
]


def run_tests(work: Path) -> bool:
    """写した側でテストを回す。1件でも落ちたら True。"""
    proc = subprocess.run(
        [str(PYTHON), "-m", "pytest", *TEST_PATHS, "-x", "-q", "--no-header"],
        cwd=work,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode != 0


def main() -> int:
    if not PYTHON.exists():
        print(f"仮想環境の Python が見つかりません: {PYTHON}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(ROOT, work, ignore=IGNORE)

        if run_tests(work):
            print(
                "壊す前からテストが落ちています。先にそちらを直してください。",
                file=sys.stderr,
            )
            return 1

        killed: list[str] = []
        survived: list[str] = []
        not_found: list[str] = []

        for index, (target, label, before, after) in enumerate(MUTATIONS, start=1):
            path = work / target
            original = path.read_text(encoding="utf-8", newline="")

            # **照合と書き込みは LF に正規化した文字列で行う。**
            # このリポジトリは core.autocrlf=true なので、チェックアウトすると
            # .py は CRLF になる。改行を含むパターンは構造的に一度もマッチせず、
            # 「置換先なし」として静かに素通りする（課題9で4件踏んだ）。
            haystack = original.replace("\r\n", "\n")

            if before not in haystack:
                not_found.append(f"{target}: {label}")
                print(f"[{index:3}/{len(MUTATIONS)}] NOT FOUND  {label}")
                continue

            path.write_text(
                haystack.replace(before, after, 1), encoding="utf-8", newline=""
            )
            failed = run_tests(work)
            path.write_text(original, encoding="utf-8", newline="")

            if failed:
                killed.append(label)
                print(f"[{index:3}/{len(MUTATIONS)}] kill       {label}")
            else:
                survived.append(f"{target}: {label}")
                print(f"[{index:3}/{len(MUTATIONS)}] SURVIVED   {label}")

    print()
    print(
        f"kill {len(killed)} / SURVIVED {len(survived)} / NOT FOUND {len(not_found)}"
    )

    for line in survived:
        print(f"  SURVIVED  {line}")
    for line in not_found:
        print(f"  NOT FOUND {line}")

    # **NOT FOUND も失敗として扱う。** 何も壊さずに「穴ゼロ」と出るのを防ぐ。
    return 0 if not survived and not not_found else 1


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

    raise SystemExit(main())
