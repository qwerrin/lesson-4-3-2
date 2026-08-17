"""task8 を1か所ずつ壊して、テストが落ちることを確認する。

通っているテストの数は、守られている範囲を意味しない。
落ちなかった行は「テストが見ていない場所」なので、そこだけ手当てする。

壊しかたを足すのは**コードを書いた直後**。まとめて最後にやると穴が出て、直後にやると出ない。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task8\\tools\\mutate.py

方式は課題7と同じ（実ファイルを触らない）
------------------------------------------------------------------

**リポジトリを丸ごと一時ディレクトリへ写して、写した側だけを壊す。**
成果物のファイルは1バイトも触らないので、途中で強制終了しても事故が起きない。
復旧手順が要らなくなる＝**復旧手順が間違っている可能性も消える**
（課題4で ``atexit`` が走らず ``if False:`` が残った事故の対策）。

**置換先が見つからなかったものは「素通り」と同じ扱いにする。**
コードを直して壊しかたを直し忘れると、何も壊さずに全部通って「穴ゼロ」と出てしまう。

**対象に common/discord_auth.py を含む**ので、テストは task8/tests だけでなく
common/tests も回す。共有モジュールは壊れると落ちるのが使う側の課題なので、
使う側のテストだけ回していると原因が遠くなる（課題3の教訓）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

AUTH = "common/discord_auth.py"
SEND = "task8/send_notification.py"
VERIFY = "task8/verify_notification.py"

IGNORE = shutil.ignore_patterns(
    ".venv", ".git", "__pycache__", ".pytest_cache", "docs", "*.png", "node_modules"
)

TEST_PATHS = ("task8/tests", "common/tests")

# (対象ファイル, 壊した内容, 置換前, 置換後)
MUTATIONS: list[tuple[str, str, str, str]] = [
    # =========================================================== 認証：Bot Token を読む
    (
        AUTH,
        "トークンの前後の空白を落とさない",
        '    value = (env.get(BOT_TOKEN_ENV) or "").strip()',
        '    value = env.get(BOT_TOKEN_ENV) or ""',
    ),
    (
        AUTH,
        "未設定でも落とさない",
        '    value = (env.get(BOT_TOKEN_ENV) or "").strip()\n    if not value:',
        '    value = (env.get(BOT_TOKEN_ENV) or "").strip()\n    if False:',
    ),
    (
        AUTH,
        "「Bot 」付きで貼られた値を素通りさせる",
        '    if value.lower().startswith("bot "):',
        "    if False:",
    ),
    (
        AUTH,
        "「Bot 」の判定を大小区別ありにする",
        '    if value.lower().startswith("bot "):',
        '    if value.startswith("bot "):',
    ),
    (
        AUTH,
        "Webhook URL との取り違えを検出しない",
        '    if value.lower().startswith(("http://", "https://")):',
        "    if False:",
    ),
    (
        AUTH,
        "Webhook URL が未設定でも落とさない",
        '    value = (env.get(WEBHOOK_URL_ENV) or "").strip()\n    if not value:',
        '    value = (env.get(WEBHOOK_URL_ENV) or "").strip()\n    if False:',
    ),
    (
        AUTH,
        "読み込んだ Webhook URL の形を確かめない",
        "    parse_webhook_url(value)  # 形が違えば AuthError（値は載らない）\n    return value",
        "    return value",
    ),
    # =========================================================== 認証：Webhook URL の解析
    (
        AUTH,
        "http を許す（資格情報が平文で流れる）",
        '    if parts.scheme != "https":',
        "    if False:",
    ),
    (
        AUTH,
        "ホストを検査しない",
        "    if parts.hostname not in _WEBHOOK_HOSTS:",
        "    if False:",
    ),
    (
        AUTH,
        "ホストを前方一致で通す（discord.com.evil.example が通る）",
        "    if parts.hostname not in _WEBHOOK_HOSTS:",
        '    if not any((parts.hostname or "").startswith(_h) for _h in _WEBHOOK_HOSTS):',
    ),
    (
        AUTH,
        "認証情報部（user:pass@）を素通りさせる",
        "    if parts.username or parts.password:",
        "    if False:",
    ),
    (
        AUTH,
        "ポート指定を素通りさせる",
        "    if parts.port is not None:",
        "    if False:",
    ),
    (
        AUTH,
        "バージョン付きのパスを畳まない（/api/v10/... が通らない）",
        '    if len(segments) == 5 and segments[0] == "api" and segments[1].startswith("v"):',
        "    if False:",
    ),
    (
        AUTH,
        "パスの形を検査しない",
        '    if len(segments) != 4 or segments[0] != "api" or segments[1] != "webhooks":',
        "    if False:",
    ),
    (
        AUTH,
        "Webhook ID が数字かを見ない",
        "    if not webhook_id.isdigit():",
        "    if False:",
    ),
    (
        AUTH,
        "空のパス要素を落とさない（末尾スラッシュで要素数がずれる）",
        '    path_segments = [segment for segment in parts.path.split("/") if segment]',
        '    path_segments = parts.path.split("/")',
    ),
    (
        AUTH,
        "URL を正規化しない（末尾スラッシュとクエリが残る）",
        '    normalized = f"{parts.scheme}://{parts.hostname}/" + "/".join(path_segments)',
        '    normalized = f"{parts.scheme}://{parts.hostname}{parts.path}"',
    ),
    # =========================================================== 認証：伏せ字
    (
        AUTH,
        "空の秘密でも replace する（全文字の間に伏せ字が入る）",
        "        if secret:",
        "        if True:",
    ),
    (
        AUTH,
        "資格情報を伏せない（実行画面に出る）",
        "            text = text.replace(secret, REDACTED)",
        "            text = text",
    ),
    (
        AUTH,
        "伏せ字を空文字にする（伏せたことが分からない）",
        'REDACTED = "***"',
        'REDACTED = ""',
    ),
    # =========================================================== 認証：セッション
    (
        AUTH,
        "必須の User-Agent を付けない",
        '    session.headers.update({"User-Agent": USER_AGENT})',
        "    session.headers.update({})",
    ),
    (
        AUTH,
        "User-Agent を Discord が求める形にしない",
        'USER_AGENT = "DiscordBot (https://github.com/qwerrin/lesson-4-3-2, 1.0)"',
        'USER_AGENT = "python-requests/2.34.2"',
    ),
    (
        AUTH,
        "API のバージョンを固定しない",
        'API_BASE = "https://discord.com/api/v10"',
        'API_BASE = "https://discord.com/api"',
    ),
    (
        AUTH,
        "空のトークンでもセッションを組む",
        '    value = (token or "").strip()\n    if not value:',
        '    value = (token or "").strip()\n    if False:',
    ),
    (
        AUTH,
        "認証方式を Bearer にする",
        '    session.headers.update({"Authorization": f"Bot {value}"})',
        '    session.headers.update({"Authorization": f"Bearer {value}"})',
    ),
    (
        AUTH,
        "Webhook 用のセッションにも Authorization を付ける",
        "    return _new_session(factory)",
        '    session = _new_session(factory)\n    session.headers.update({"Authorization": "Bot x"})\n    return session',
    ),
    # =========================================================== 認証：エラーの訳し分け
    (
        AUTH,
        "2xx 以外でも素通りさせる",
        "    if 200 <= status < 300:\n        return",
        "    if True:\n        return",
    ),
    (
        AUTH,
        "エラーコードを出さない（公式ドキュメントを引けない）",
        '        message += f" / code {code}"',
        '        message += ""',
    ),
    (
        AUTH,
        "直しかたの案内を出さない",
        "    hint = _ERROR_HINTS.get(code)\n    if hint:",
        "    hint = _ERROR_HINTS.get(code)\n    if False:",
    ),
    (
        AUTH,
        "レート制限の説明を出さない",
        "    if status == 429:",
        "    if False:",
    ),
    (
        AUTH,
        "待ち時間の秒数を出さない",
        '            message += f"\\nレート制限です。{retry_after} 秒待ってから再実行してください。"',
        '            message += "\\nレート制限です。しばらく待ってください。"',
    ),
    (
        AUTH,
        "エラー本文の資格情報を伏せない",
        "    raise ApiError(redact(message, *secrets))",
        "    raise ApiError(message)",
    ),
    (
        AUTH,
        "権限不足の案内で必要な権限名を出さない",
        '    50013: (\n        "Bot に操作の権限がありません。"\n        "アプリの権限に Send Messages（投稿）と Read Message History（読み返し）が"\n        "あるかを確認してください。"\n    ),',
        '    50013: ("権限がありません。設定を見直してください。"),',
    ),
    (
        AUTH,
        "アクセス不可の案内で必要な権限名を出さない",
        '    50001: (\n        "Bot がそのチャンネルを見られません。"\n        "アプリの権限に View Channels があるか、チャンネル個別の権限で"\n        "上書きされていないかを確認してください。"\n    ),',
        '    50001: ("権限がありません。設定を見直してください。"),',
    ),
    (
        AUTH,
        "チャンネル未検出の案内で対象を名指ししない",
        '    10003: (\n        "チャンネルが見つかりません。チャンネル ID を確認してください"\n        "（開発者モードを ON にして、チャンネルを右クリック →「チャンネル ID をコピー」）。"\n    ),',
        '    10003: ("対象が見つかりません。ID を確認してください。"),',
    ),
    (
        AUTH,
        "JSON でない本文で素の例外を出す",
        "    except Exception:  # noqa: BLE001 - 本文が JSON でないことは実際に起きる\n        return None",
        "    except Exception:  # noqa: BLE001 - 本文が JSON でないことは実際に起きる\n        raise",
    ),
    # =========================================================== 認証：自分が誰か
    (
        AUTH,
        "/users/@me ではないところを叩く",
        '    response = session.get(f"{base}/users/@me")',
        '    response = session.get(f"{base}/users/me")',
    ),
    (
        AUTH,
        "HTTP エラーを見ずに本文を読む",
        "    try:\n        raise_for_discord_error(response, *secrets)",
        "    try:\n        pass",
    ),
    (
        AUTH,
        "bot かどうかを見ない（ユーザートークンが通る）",
        '    if payload.get("bot") is not True:',
        "    if False:",
    ),
    (
        AUTH,
        "bot が無いのを「たぶん bot」に倒す",
        '    if payload.get("bot") is not True:',
        '    if payload.get("bot") is False:',
    ),
    (
        AUTH,
        "id が空でも進む（照合の物差しが空になる）",
        '    user_id = str(payload.get("id") or "").strip()\n    if not user_id:',
        '    user_id = str(payload.get("id") or "").strip()\n    if False:',
    ),
    (
        AUTH,
        "応答が辞書でなくても進む",
        '    if not isinstance(payload, dict):\n        raise AuthError("応答を JSON として読めませんでした（/users/@me）。")',
        '    if False:\n        raise AuthError("応答を JSON として読めませんでした（/users/@me）。")',
    ),
    # =========================================================== 送信：本文の組み立て
    (
        SEND,
        "本文が空でも送る",
        '    if not (text or "").strip():',
        "    if False:",
    ),
    (
        SEND,
        "空白だけの本文を本文とみなす",
        '    if not (text or "").strip():',
        "    if not text:",
    ),
    (
        SEND,
        "本文を勝手に整形する（記録と送信がずれる）",
        '    payload: dict = {"content": text}',
        '    payload: dict = {"content": text.strip()}',
    ),
    (
        SEND,
        "メンションの抑止を既定から外す（@everyone が飛ぶ）",
        '    if not allow_mentions:\n        payload["allowed_mentions"] = MENTIONS_SUPPRESSED',
        '    if allow_mentions:\n        payload["allowed_mentions"] = MENTIONS_SUPPRESSED',
    ),
    (
        SEND,
        "抑止のつもりでユーザーのメンションだけ通す",
        'MENTIONS_SUPPRESSED = {"parse": []}',
        'MENTIONS_SUPPRESSED = {"parse": ["users"]}',
    ),
    # =========================================================== 送信：ID の検査
    (
        SEND,
        "ID が未指定でも進む",
        '    text = (value or "").strip()\n    if not text:',
        '    text = (value or "").strip()\n    if False:',
    ),
    (
        SEND,
        "ID の前後の空白を落とさない",
        '    text = (value or "").strip()',
        '    text = value or ""',
    ),
    (
        SEND,
        "ID が数字かを見ない（チャンネル名が通る）",
        "    if not text.isdigit():",
        "    if False:",
    ),
    (
        SEND,
        "どの項目が悪いのか名指ししない",
        '            f"{label} が数字ではありません。"',
        '            "ID が数字ではありません。"',
    ),
    # =========================================================== 送信：応答の検査
    (
        SEND,
        "204（本文なし）を素通りさせる",
        "    if response.status_code == 204:",
        "    if False:",
    ),
    (
        SEND,
        "id が返らなくても成功にする",
        '    message_id = str(payload.get("id") or "").strip()\n    if not message_id:',
        '    message_id = str(payload.get("id") or "").strip()\n    if False:',
    ),
    (
        SEND,
        "要求したチャンネルと照合しない",
        "    if expected_channel is not None:",
        "    if False:",
    ),
    (
        SEND,
        "応答のチャンネルを自分自身と比べる（トートロジー）",
        "        if returned != expected_channel:",
        "        if returned != returned:",
    ),
    (
        SEND,
        "author が無くても進む",
        "    if not isinstance(author, dict):",
        "    if False:",
    ),
    (
        SEND,
        "author の id が空でも進む",
        '    author_id = str(author.get("id") or "").strip()\n    if not author_id:',
        '    author_id = str(author.get("id") or "").strip()\n    if False:',
    ),
    (
        SEND,
        "投稿者を照合しない",
        "    if author_id != expected:",
        "    if False:",
    ),
    (
        SEND,
        "guild_id が無いチャンネル（DM）を素通りさせる",
        '    value = str(guild_id or "").strip()\n    if not value:',
        '    value = str(guild_id or "").strip()\n    if False:',
    ),
    (
        SEND,
        "別のサーバーのチャンネルでも通す",
        "    if value != expected:",
        "    if False:",
    ),
    # =========================================================== 送信：エンドポイント
    (
        SEND,
        "チャンネル情報のエンドポイントを間違える",
        '    response = session.get(f"{discord_auth.API_BASE}/channels/{channel}")',
        '    response = session.get(f"{discord_auth.API_BASE}/channel/{channel}")',
    ),
    (
        SEND,
        "投稿のエンドポイントを間違える",
        '        f"{discord_auth.API_BASE}/channels/{channel}/messages", json=payload',
        '        f"{discord_auth.API_BASE}/channels/{channel}/message", json=payload',
    ),
    (
        SEND,
        "JSON ではなくフォームで送る",
        '        f"{discord_auth.API_BASE}/channels/{channel}/messages", json=payload',
        '        f"{discord_auth.API_BASE}/channels/{channel}/messages", data=payload',
    ),
    (
        SEND,
        "webhook で wait を付けない（204 で何も返らない）",
        '    response = session.post(webhook.url, params={"wait": "true"}, json=payload)',
        "    response = session.post(webhook.url, json=payload)",
    ),
    (
        SEND,
        "webhook の wait を false にする",
        '    response = session.post(webhook.url, params={"wait": "true"}, json=payload)',
        '    response = session.post(webhook.url, params={"wait": "false"}, json=payload)',
    ),
    (
        SEND,
        "webhook の投稿でトークンを伏せない",
        '    response = session.post(webhook.url, params={"wait": "true"}, json=payload)\n    discord_auth.raise_for_discord_error(response, webhook.token, *secrets)',
        '    response = session.post(webhook.url, params={"wait": "true"}, json=payload)\n    discord_auth.raise_for_discord_error(response, *secrets)',
    ),
    (
        SEND,
        "Webhook 情報のエンドポイントを間違える",
        "    response = session.get(webhook.url)",
        '    response = session.get(webhook.url + "/messages")',
    ),
    # =========================================================== 送信：リンクと記録
    (
        SEND,
        "リンクのサーバーとチャンネルを入れ替える",
        '    return f"https://discord.com/channels/{guild}/{channel}/{message_id}"',
        '    return f"https://discord.com/channels/{channel}/{guild}/{message_id}"',
    ),
    (
        SEND,
        "投稿者を記録しない",
        '        "author_id": author_id,',
        '        "author_id": "",',
    ),
    (
        SEND,
        "リンクを記録しない",
        '        "link": message_link(guild=guild, channel=channel, message_id=message_id),\n    }',
        "    }",
    ),
    (
        SEND,
        "記録を UTF-8 以外で書く",
        '        json.dumps(record, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8"',
        '        json.dumps(record, ensure_ascii=False, indent=2) + "\\n", encoding="cp932"',
    ),
    (
        SEND,
        "結果ファイルを指定しなくても書く",
        "    if args.json_out:",
        "    if True:",
    ),
    # =========================================================== 送信：通しの流れ
    (
        SEND,
        "送る前にサーバーを確かめない",
        '    require_guild(channel_object.get("guild_id"), expected=guild)',
        '    channel_object.get("guild_id")',
    ),
    (
        SEND,
        "投稿者を応答どうしで比べる（トートロジー・bot 経路）",
        "    author_id = require_author(message, expected=identity.user_id)",
        "    author_id = require_author(message, expected=read_author_id(message))",
    ),
    (
        SEND,
        "webhook が別のチャンネルを向いていても送る",
        "    if target != channel:",
        "    if False:",
    ),
    (
        SEND,
        "webhook の guild_id を確かめない",
        '    if webhook_object.get("guild_id") is not None:',
        "    if False:",
    ),
    (
        SEND,
        "投稿者を応答どうしで比べる（トートロジー・webhook 経路）",
        "    author_id = require_author(message, expected=webhook.id)",
        "    author_id = require_author(message, expected=read_author_id(message))",
    ),
    (
        SEND,
        "webhook 経由の印（webhook_id）を見ない",
        "    if marker != webhook.id:",
        "    if False:",
    ),
    (
        SEND,
        "失敗しても成功として終わる",
        "    except (discord_auth.DiscordError, SendError) as error:\n        print(error, file=sys.stderr)\n        return 1",
        "    except (discord_auth.DiscordError, SendError) as error:\n        print(error, file=sys.stderr)\n        return 0",
    ),
    # =========================================================== 読み返し：結果ファイル
    (
        VERIFY,
        "必要な項目が無くても読み込む",
        "        if not isinstance(value, str) or not value.strip():",
        "        if False:",
    ),
    (
        VERIFY,
        "数値の ID を受け入れる（snowflake の精度が落ちる）",
        "        if not isinstance(value, str) or not value.strip():",
        "        if not value:",
    ),
    (
        VERIFY,
        "必須の項目を減らす",
        '_REQUIRED_KEYS = ("via", "guild", "channel", "text", "message_id", "author_id", "link")',
        '_REQUIRED_KEYS = ("via",)',
    ),
    # =========================================================== 読み返し：手元の照合
    (
        VERIFY,
        "サーバーを照合しない",
        '    checks.append(_compare("サーバー", expected_guild, payload.get("guild")))',
        '    checks.append(Check("サーバー", True))',
    ),
    (
        VERIFY,
        "チャンネルを照合しない",
        '    checks.append(_compare("チャンネル", expected_channel, payload.get("channel")))',
        '    checks.append(Check("チャンネル", True))',
    ),
    (
        VERIFY,
        "期待値を結果ファイルの値で埋める（トートロジー）",
        '    checks.append(_compare("送った本文", expected_text, payload.get("text")))',
        '    checks.append(_compare("送った本文", payload.get("text"), payload.get("text")))',
    ),
    (
        VERIFY,
        "知らない送信経路を通す",
        "            via in _KNOWN_ROUTES,",
        "            True,",
    ),
    (
        VERIFY,
        "リンクの指す先を見ない",
        "    missing = [part for part in parts if not part or part not in link]",
        "    missing = []",
    ),
    (
        VERIFY,
        "メッセージIDの形式を見ない",
        "    ok = message_id.isdigit()",
        "    ok = True",
    ),
    # =========================================================== 読み返し：取得
    (
        VERIFY,
        "1件取得ではなく一覧を叩く",
        '        f"{discord_auth.API_BASE}/channels/{channel}/messages/{message_id}"',
        '        f"{discord_auth.API_BASE}/channels/{channel}/messages"',
    ),
    (
        VERIFY,
        "Get Webhook Message のパスを間違える",
        '    response = session.get(f"{webhook.url}/messages/{message_id}")',
        '    response = session.get(f"{webhook.url}/{message_id}")',
    ),
    (
        VERIFY,
        "読み返しでトークンを伏せない",
        '    response = session.get(f"{webhook.url}/messages/{message_id}")\n    discord_auth.raise_for_discord_error(response, webhook.token, *secrets)',
        '    response = session.get(f"{webhook.url}/messages/{message_id}")\n    discord_auth.raise_for_discord_error(response, *secrets)',
    ),
    # =========================================================== 読み返し：照合の中身
    (
        VERIFY,
        "実行中の主体を照合しない",
        '    checks.append(_compare("実行中の主体", payload.get("author_id"), actor_id))',
        '    checks.append(Check("実行中の主体", True))',
    ),
    (
        VERIFY,
        "チャンネルの所属サーバーを照合しない",
        '    checks.append(_compare("チャンネルの所属サーバー", payload.get("guild"), guild_id))',
        '    checks.append(Check("チャンネルの所属サーバー", True))',
    ),
    (
        VERIFY,
        "読み返せなかったことを検出しない",
        "    if not message:",
        "    if False:",
    ),
    (
        VERIFY,
        "読み返せなくても一致とみなす",
        '                "メッセージの実在",\n                False,',
        '                "メッセージの実在",\n                True,',
    ),
    (
        VERIFY,
        "メッセージIDを照合しない（別のメッセージが通る）",
        '    checks.append(_compare("メッセージID", payload.get("message_id"), str(message.get("id") or "")))',
        '    checks.append(Check("メッセージID", True))',
    ),
    (
        VERIFY,
        "載ったチャンネルを照合しない",
        '    checks.append(_compare("チャンネル", payload.get("channel"), str(message.get("channel_id") or "")))',
        '    checks.append(Check("チャンネル", True))',
    ),
    (
        VERIFY,
        "投稿者を照合しない",
        '    checks.append(_compare("投稿者", actor_id, author_id))',
        '    checks.append(Check("投稿者", True))',
    ),
    (
        VERIFY,
        "投稿者を応答どうしで比べる（トートロジー）",
        '    checks.append(_compare("投稿者", actor_id, author_id))',
        '    checks.append(_compare("投稿者", author_id, author_id))',
    ),
    (
        VERIFY,
        "本文が空でも一致とみなす",
        '        checks.append(Check("本文", False, _INTENT_HINT))',
        '        checks.append(Check("本文", True, _INTENT_HINT))',
    ),
    (
        VERIFY,
        "本文が空のときも「不一致」で片付ける（原因が分からない）",
        "    if not actual:",
        "    if False:",
    ),
    (
        VERIFY,
        "空だった理由の案内から特権インテントの話を落とす",
        '_INTENT_HINT = (\n    "本文が空で返りました。MESSAGE CONTENT 特権インテントが必要な状態かもしれません"\n    "（自分が送ったメッセージは本来この制限の例外です）。"\n)',
        '_INTENT_HINT = "本文が一致しません。"',
    ),
    (
        VERIFY,
        "本文を照合しない",
        '        checks.append(_compare("本文", expected, actual))',
        '        checks.append(Check("本文", True))',
    ),
    # =========================================================== 読み返し：判定と入口
    (
        VERIFY,
        "確かめた項目がゼロでも一致とみなす",
        "    if not checks:\n        return False",
        "    if False:\n        return False",
    ),
    (
        VERIFY,
        "全部OKでなくても一致とみなす（all を any にする）",
        "    return all(check.ok for check in checks)",
        "    return any(check.ok for check in checks)",
    ),
    (
        VERIFY,
        "手元の照合が落ちてもAPIを呼ぶ",
        '    if not all_ok(local):\n        print("\\n食い違いがあります。上の NG を確認してください。", file=sys.stderr)\n        return 1\n\n    try:',
        '    if False:\n        print("\\n食い違いがあります。上の NG を確認してください。", file=sys.stderr)\n        return 1\n\n    try:',
    ),
    (
        VERIFY,
        "読み返しの照合が落ちても成功にする",
        '    remote = build_remote_checks(payload, message, actor_id=actor_id, guild_id=guild_id)\n    print(f"\\n{endpoint} で読み直した内容との照合（送信とは別のエンドポイント）:")\n    print(format_checks(remote))\n\n    if not all_ok(remote):',
        '    remote = build_remote_checks(payload, message, actor_id=actor_id, guild_id=guild_id)\n    print(f"\\n{endpoint} で読み直した内容との照合（送信とは別のエンドポイント）:")\n    print(format_checks(remote))\n\n    if False:',
    ),
    (
        VERIFY,
        "所属サーバーを取りに行かない",
        '    guild_id = fetch_guild_id(session, channel=payload["channel"], secrets=secrets)',
        '    guild_id = payload["guild"]',
    ),
    (
        VERIFY,
        "確かめていない範囲を書かない",
        '    print(\n        "確かめていないこと: 画面上の見え方 / 通知が誰に飛んだか / 添付とリンクの展開。"\n        "この確認が見ているのは、上に並べた項目だけです。"\n    )',
        '    print("すべての項目を検証しました。")',
    ),
]


def run_tests(work: Path) -> bool:
    """写した側でテストを回す。1件でも落ちたら True。"""
    proc = subprocess.run(
        [str(PYTHON), "-m", "pytest", *TEST_PATHS, "-x", "-q", "--no-header"],
        cwd=work,
        capture_output=True,
        text=True,
    )
    return proc.returncode != 0


def main() -> int:
    if not PYTHON.exists():
        print(f"仮想環境の Python が見つかりません: {PYTHON}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="mutate-task8-") as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(ROOT, work, ignore=IGNORE)

        # 壊す前に、写した側がそのままで通ることを確かめる。ここが落ちていると
        # 「全部 kill」が出るが、それは壊したからではない。
        if run_tests(work):
            print("写した側のテストが最初から落ちています。ミューテーションを始めません。", file=sys.stderr)
            return 1

        killed: list[str] = []
        survived: list[str] = []
        not_found: list[str] = []

        for index, (target, label, before, after) in enumerate(MUTATIONS, start=1):
            path = work / target
            # **newline="" で読み書きする。** 既定の text mode は読むとき CRLF→LF、
            # 書くとき LF→CRLF に直すので、書き換えていない行の改行まで入れ替わる
            # （課題2で実際に踏んだ）。
            original = path.read_text(encoding="utf-8", newline="")

            if before not in original:
                # **置換先が無いものは素通りと同じ扱い。** コードを直して壊しかたを
                # 直し忘れると、何も壊さずに全部通って「穴ゼロ」と出てしまう。
                not_found.append(f"{target}: {label}")
                print(f"[{index:3}/{len(MUTATIONS)}] NOT FOUND  {label}")
                continue

            path.write_text(original.replace(before, after, 1), encoding="utf-8", newline="")
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
        f"合計 {len(MUTATIONS)} 件 / kill {len(killed)} 件 / "
        f"素通り {len(survived)} 件 / 置換先なし {len(not_found)} 件"
    )

    if survived:
        print("\n素通り（テストが見ていない場所）:")
        for item in survived:
            print(f"  - {item}")

    if not_found:
        print("\n置換先が見つからない（壊しかたが古い。素通りと同じ扱い）:")
        for item in not_found:
            print(f"  - {item}")

    return 0 if not survived and not not_found else 1


if __name__ == "__main__":
    raise SystemExit(main())
