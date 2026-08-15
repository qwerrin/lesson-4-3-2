"""task5 を1か所ずつ壊して、テストが落ちることを確認する。

通っているテストの数は、守られている範囲を意味しない。
落ちなかった行は「テストが見ていない場所」なので、そこだけ手当てする。

壊しかたを足すのは**コードを書いた直後**。まとめて最後にやると穴が出て、直後にやると出ない。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task5\\tools\\mutate.py

**このスクリプトはソースファイルを一時的に書き換える。**
1件ごとに元へ戻し、開始時に `.mutate_backup/` へ控えを取る。
atexit は強制終了では走らないので（2026-08-14 に課題4で実際に踏んだ）、
復旧を「人が思い出して打つコマンド」ではなく道具側の責任にしている。
"""

from __future__ import annotations

import atexit
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

SEND = ROOT / "task5" / "send_mail.py"
VERIFY = ROOT / "task5" / "verify_mail.py"

TARGETS = tuple(p for p in (SEND, VERIFY) if p.exists())

TEST_DIRS = [d for d in (ROOT / "task5" / "tests",) if d.is_dir()]

BACKUP_DIR = Path(__file__).resolve().parent / ".mutate_backup"

# (対象ファイル, 壊した内容, 置換前, 置換後)
MUTATIONS: list[tuple[Path, str, str, str]] = [
    # ------------------------------------------------- スコープ・既定値
    (
        SEND,
        "読み取り専用のスコープを要求する（送信できない）",
        'SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/gmail.send",)',
        'SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/gmail.readonly",)',
    ),
    (
        SEND,
        "全権限のスコープを要求する（受信箱も削除も可能になる）",
        'SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/gmail.send",)',
        'SCOPES: tuple[str, ...] = ("https://mail.google.com/",)',
    ),
    (
        SEND,
        "token を課題間で共有する（毎回同意画面が出る）",
        'DEFAULT_TOKEN = "task5/token-send.json"',
        'DEFAULT_TOKEN = "token.json"',
    ),
    (
        SEND,
        "送信用と読み取り用で token を共有する（権限を消し合う）",
        'DEFAULT_TOKEN = "task5/token-send.json"',
        'DEFAULT_TOKEN = "task5/token-verify.json"',
    ),
    (
        SEND,
        "認証済みユーザー以外として送る",
        'GMAIL_USER_ID = "me"',
        'GMAIL_USER_ID = "you@example.com"',
    ),
    # ------------------------------------------------- ヘッダインジェクション
    (
        SEND,
        "ヘッダの改行チェックをやめる",
        "    for char in FORBIDDEN_IN_HEADER:",
        "    for char in ():",
    ),
    (
        SEND,
        "CR を危険な文字から外す",
        'FORBIDDEN_IN_HEADER = ("\\r", "\\n")',
        'FORBIDDEN_IN_HEADER = ("\\n",)',
    ),
    (
        SEND,
        "LF を危険な文字から外す",
        'FORBIDDEN_IN_HEADER = ("\\r", "\\n")',
        'FORBIDDEN_IN_HEADER = ("\\r",)',
    ),
    # ------------------------------------------------- normalize_address
    (
        SEND,
        "宛先の前後の空白を落とさない",
        '    address = (value or "").strip()',
        '    address = value or ""',
    ),
    (
        SEND,
        "空の宛先を通す",
        "    if not address:\n        raise MailError",
        "    if False:\n        raise MailError",
    ),
    (
        SEND,
        "宛先の内部の空白を許す",
        "    if any(char.isspace() for char in address):",
        "    if False:",
    ),
    (
        SEND,
        "アットマークの個数を見ない",
        '    if address.count("@") != 1:',
        "    if False:",
    ),
    (
        SEND,
        "アットマークが2つ以上でも通す",
        '    if address.count("@") != 1:',
        '    if address.count("@") < 1:',
    ),
    (
        SEND,
        "ローカル部・ドメイン部の空を許す",
        "    if not local or not domain:",
        "    if False:",
    ),
    (
        SEND,
        "ローカル部の空だけ見逃す",
        "    if not local or not domain:",
        "    if not domain:",
    ),
    # ------------------------------------------------- normalize_subject
    (
        SEND,
        "空の件名を既定値で埋める",
        '    subject = (value or "").strip()',
        "    subject = (value or DEFAULT_SUBJECT).strip()",
    ),
    (
        SEND,
        "空の件名を通す",
        "    if not subject:\n        raise MailError",
        "    if False:\n        raise MailError",
    ),
    (
        SEND,
        "件名の改行を見ない",
        '    _reject_line_breaks(subject, "件名")\n',
        "",
    ),
    (
        SEND,
        "宛先の改行を見ない",
        '    _reject_line_breaks(address, "宛先")\n',
        "",
    ),
    # ------------------------------------------------- resolve_body
    (
        SEND,
        "--body と --body-file の同時指定を許す",
        "    if text is not None and path is not None:",
        "    if False:",
    ),
    (
        SEND,
        "本文ファイルの存在を確認しない",
        "        if not source.exists():",
        "        if False:",
    ),
    (
        SEND,
        "見つからないファイル名を伝えない",
        '            raise MailError(f"本文のファイルが見つかりません: {source}")',
        '            raise MailError("本文のファイルが見つかりません")',
    ),
    (
        SEND,
        "本文ファイルを既定のエンコーディングで読む",
        '            body = source.read_text(encoding="utf-8")',
        "            body = source.read_text()",
    ),
    (
        SEND,
        "空の本文を通す",
        "    if not body.strip():",
        "    if False:",
    ),
    (
        SEND,
        "本文の前後の改行を落とす",
        "    return body\n",
        "    return body.strip()\n",
    ),
    (
        SEND,
        "本文を指定しなくても空文字にする",
        "        return DEFAULT_BODY",
        '        return ""',
    ),
    # ------------------------------------------------- build_message
    (
        SEND,
        "既定のポリシーで組む（行末が LF のまま）",
        "    message = EmailMessage(policy=policy.SMTP)",
        "    message = EmailMessage()",
    ),
    (
        SEND,
        "転送エンコードを既定にする（本文が 8bit のまま）",
        '    message.set_content(body, cte="base64")',
        "    message.set_content(body)",
    ),
    (
        SEND,
        "転送エンコードを quoted-printable にする",
        '    message.set_content(body, cte="base64")',
        '    message.set_content(body, cte="quoted-printable")',
    ),
    (
        SEND,
        "組み立て時に宛先を検証しない",
        "    to = normalize_address(to)\n",
        "",
    ),
    (
        SEND,
        "組み立て時に件名を検証しない",
        "    subject = normalize_subject(subject)\n",
        "",
    ),
    (
        SEND,
        "送信元を指定していなくても From を付ける",
        "    if sender is not None:",
        "    if True:",
    ),
    (
        SEND,
        "送信元を検証せずに入れる",
        '        message["From"] = normalize_address(sender)',
        '        message["From"] = sender',
    ),
    (
        SEND,
        "宛先を To ではなく Bcc に入れる",
        '    message["To"] = to',
        '    message["Bcc"] = to',
    ),
    # ------------------------------------------------- base64url
    (
        SEND,
        "標準 base64 で符号化する（+ と / が混ざる）",
        "    return base64.urlsafe_b64encode(message.as_bytes()).decode(\"ascii\")",
        '    return base64.b64encode(message.as_bytes()).decode("ascii")',
    ),
    (
        SEND,
        "76文字で折り返す base64 を使う",
        "    return base64.urlsafe_b64encode(message.as_bytes()).decode(\"ascii\")",
        '    return base64.encodebytes(message.as_bytes()).decode("ascii")',
    ),
    (
        SEND,
        "復号でパディングを補わない",
        '    padded = raw + "=" * (-len(raw) % 4)',
        "    padded = raw",
    ),
    (
        SEND,
        "標準 base64 で復号する",
        "    return base64.urlsafe_b64decode(padded)",
        "    return base64.b64decode(padded)",
    ),
    (
        SEND,
        "送信 body に余計な項目を混ぜる",
        '    return {"raw": encode_raw(build_message(to, subject, body, sender))}',
        '    return {"raw": encode_raw(build_message(to, subject, body, sender)), "threadId": "x"}',
    ),
    # ------------------------------------------------- 件名・本文の読み戻し
    (
        SEND,
        "旧ポリシーで解析する（件名が RFC 2047 のまま返り、照合が永久に不一致になる）",
        "    return BytesParser(policy=policy.SMTP).parsebytes(raw)",
        "    return BytesParser().parsebytes(raw)",
    ),
    # ------------------------------------------------- send_message
    (
        SEND,
        "応答の必須項目を確認しない",
        "    for key, label in REQUIRED_FIELDS:",
        "    for key, label in ():",
    ),
    (
        SEND,
        "メッセージIDを必須から外す",
        '    ("id", "メッセージID"),\n',
        "",
    ),
    (
        SEND,
        "スレッドIDを必須から外す",
        '    ("threadId", "スレッドID"),\n',
        "",
    ),
    (
        SEND,
        "空文字を「返ってきた」とみなす",
        '    if value is None or str(value).strip() == "":',
        "    if value is None:",
    ),
    (
        SEND,
        "HTTP エラーを翻訳せず素通りさせる",
        "    except HttpError as error:\n        raise _translate_http_error(error) from error",
        "    except () as error:\n        raise _translate_http_error(error) from error",
    ),
    # ------------------------------------------------- エラーの翻訳
    (
        SEND,
        "未有効化の判定をやめる",
        "    if _looks_like_api_disabled(detail):",
        "    if False:",
    ),
    (
        SEND,
        "未有効化を権限不足として案内する",
        "    if _looks_like_api_disabled(detail):",
        "    if _looks_like_scope_problem(detail):",
    ),
    (
        SEND,
        "権限不足の判定をやめる",
        "    if _looks_like_scope_problem(detail):\n        return MailError(\n            f\"権限（スコープ）",
        "    if False:\n        return MailError(\n            f\"権限（スコープ）",
    ),
    (
        SEND,
        "権限不足の案内に要求スコープを載せない",
        '            f"このスクリプトが要求するのは {SCOPES[0]} です。\\n"',
        '            "要求している権限が足りません。\\n"',
    ),
    (
        SEND,
        "相手が言っている理由を載せない",
        "    detail = _api_message(error) or str(error)",
        '    detail = ""',
    ),
    (
        SEND,
        "応答からエラーメッセージを読まない",
        '        return payload.get("error", {}).get("message", "")',
        '        return ""',
    ),
    (
        SEND,
        "壊れた JSON の解析失敗を握りつぶさない（生の例外が外に出る）",
        "    except (ValueError, AttributeError, UnicodeDecodeError):\n        return \"\"",
        '    except ():\n        return ""',
    ),
    (
        SEND,
        "回数制限のときステータスコードを載せない",
        '            f"送信の回数制限に掛かりました（{status}）。\\n"',
        '            "送信の回数制限に掛かりました。\\n"',
    ),
    (
        SEND,
        "その他の失敗でステータスコードを載せない",
        '    return MailError(f"メールの送信に失敗しました（HTTP {status}）。\\n応答: {detail}")',
        '    return MailError(f"メールの送信に失敗しました。\\n応答: {detail}")',
    ),
    # ------------------------------------------------- 画面まわり
    (
        SEND,
        "dry-run の出力に「送信していません」と書かない",
        '            "--- 組み立てた内容（まだ送信していません）---",',
        '            "--- 組み立てた内容 ---",',
    ),
    (
        SEND,
        "dry-run で宛先を出さない",
        '            f"  宛先: {to}",\n',
        "",
    ),
    (
        SEND,
        "dry-run で本文を出さない",
        '            *[f"    {line}" for line in body.splitlines()],\n',
        "",
    ),
    (
        SEND,
        "結果にメッセージIDを出さない",
        '            f"  メッセージID  : {message_id}",\n',
        "",
    ),
    (
        SEND,
        "結果に宛先を出さない",
        '            f"  宛先          : {to}",\n',
        "",
    ),
    (
        SEND,
        "読み返しの手順を案内しない",
        '            f"  .venv\\\\Scripts\\\\python.exe task5\\\\verify_mail.py --message-id {message_id}",\n',
        "",
    ),
    # ------------------------------------------------- 引数
    (
        SEND,
        "宛先を任意の引数にする",
        '    parser.add_argument("--to", required=True, help="宛先のメールアドレス")',
        '    parser.add_argument("--to", required=False, help="宛先のメールアドレス")',
    ),
    (
        SEND,
        "dry-run の既定を有効にする",
        '        "--dry-run",\n        action="store_true",',
        '        "--dry-run",\n        action="store_false",',
    ),
    (
        SEND,
        "資格情報の既定を絶対パスにする（実行画面に自宅のパスが写る）",
        '    parser.add_argument("--credentials", default="credentials.json", help="OAuth クライアントの JSON")',
        '    parser.add_argument("--credentials", default=str(_REPO_ROOT / "credentials.json"), help="OAuth クライアントの JSON")',
    ),
    # ------------------------------------------------- main
    (
        SEND,
        "dry-run でも送信する",
        "    if args.dry_run:",
        "    if False:",
    ),
    (
        SEND,
        "組み立てより先に認証する（落ちると分かっている実行で同意画面が開く）",
        "    try:\n        # 送る内容を先に確定させる。",
        "    factory(args)\n    try:\n        # 送る内容を先に確定させる。",
    ),
    (
        SEND,
        "dry-run でも認証する",
        "    if args.dry_run:\n        # service を作らずに戻る。",
        "    if args.dry_run:\n        factory(args)\n        # service を作らずに戻る。",
    ),
    (
        SEND,
        "組み立てに失敗しても 0 を返す",
        "    except MailError as error:\n        print(error, file=sys.stderr)\n        return 1",
        "    except MailError as error:\n        print(error, file=sys.stderr)\n        return 0",
    ),
    (
        SEND,
        "送信に失敗しても 0 を返す",
        "    except (MailError, google_auth.AuthError) as error:\n        print(error, file=sys.stderr)\n        return 1",
        "    except (MailError, google_auth.AuthError) as error:\n        print(error, file=sys.stderr)\n        return 0",
    ),
    (
        SEND,
        "失敗の理由を標準出力に出す",
        "    except (MailError, google_auth.AuthError) as error:\n        print(error, file=sys.stderr)",
        "    except (MailError, google_auth.AuthError) as error:\n        print(error)",
    ),
    (
        SEND,
        "結果を印字しない",
        "    print(format_result(sent, to, subject))\n",
        "",
    ),
    # ================================================= verify_mail.py
    # ------------------------------------------------- スコープ・既定値
    (
        VERIFY,
        "確認なのに送信スコープを要求する",
        'SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/gmail.readonly",)',
        'SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/gmail.send",)',
    ),
    (
        VERIFY,
        "確認なのに全権限を要求する",
        'SCOPES: tuple[str, ...] = ("https://www.googleapis.com/auth/gmail.readonly",)',
        'SCOPES: tuple[str, ...] = ("https://mail.google.com/",)',
    ),
    (
        VERIFY,
        "送信側とトークンを共有する（権限を消し合う）",
        'DEFAULT_TOKEN = "task5/token-verify.json"',
        "DEFAULT_TOKEN = send_mail.DEFAULT_TOKEN",
    ),
    (
        VERIFY,
        "本文の返らない形式で読む",
        'MESSAGE_FORMAT = "full"',
        'MESSAGE_FORMAT = "metadata"',
    ),
    (
        VERIFY,
        "送信済みラベルの名前を取り違える",
        'SENT_LABEL = "SENT"',
        'SENT_LABEL = "DRAFT"',
    ),
    (
        VERIFY,
        "本文として HTML を読む",
        'TEXT_MIME_TYPE = "text/plain"',
        'TEXT_MIME_TYPE = "text/html"',
    ),
    # ------------------------------------------------- header_value
    (
        VERIFY,
        "ヘッダ名の大文字小文字を区別する",
        '        if str(header.get("name", "")).lower() == wanted:',
        '        if header.get("name") == name:',
    ),
    (
        VERIFY,
        "ヘッダが無い応答で落ちる",
        '    for header in (payload or {}).get("headers", []) or []:',
        '    for header in payload["headers"]:',
    ),
    # ------------------------------------------------- decode_subject
    (
        VERIFY,
        "件名の文字コードを無視して復号する",
        '            parts.append(chunk.decode(charset or "ascii", errors="replace"))',
        '            parts.append(chunk.decode("ascii", errors="replace"))',
    ),
    (
        VERIFY,
        "件名が無いとき既定の文字列を返す",
        '    if value is None:\n        return ""',
        '    if value is None:\n        return "(不明)"',
    ),
    # ------------------------------------------------- 本文の取り出し
    (
        VERIFY,
        "添付として分離された本文を空文字として扱う",
        '    if body.get("attachmentId"):\n        return None',
        '    if body.get("attachmentId"):\n        return ""',
    ),
    (
        VERIFY,
        "本文が無いときに空文字を返す（届いていないのに一致しうる）",
        "    data = body.get(\"data\")\n    if not data:\n        return None",
        "    data = body.get(\"data\")\n    if not data:\n        return \"\"",
    ),
    (
        VERIFY,
        "マルチパートを見ない",
        '    parts = payload.get("parts")',
        "    parts = None",
    ),
    (
        VERIFY,
        "マルチパートで最初のパートを本文にする（HTML が混ざる）",
        '        if part.get("mimeType") == TEXT_MIME_TYPE:',
        "        if True:",
    ),
    # ------------------------------------------------- normalize_newlines
    (
        VERIFY,
        "改行を正規化しない",
        '    return text.replace("\\r\\n", "\\n").replace("\\r", "\\n")',
        "    return text",
    ),
    (
        VERIFY,
        "CRLF をまとめて扱わない（空行が増える）",
        '    return text.replace("\\r\\n", "\\n").replace("\\r", "\\n")',
        '    return text.replace("\\r", "\\n")',
    ),
    (
        VERIFY,
        "CR 単独を直さない",
        '    return text.replace("\\r\\n", "\\n").replace("\\r", "\\n")',
        '    return text.replace("\\r\\n", "\\n")',
    ),
    # ------------------------------------------------- _present / _compare
    (
        VERIFY,
        "空文字を「返ってきた」とみなす",
        '    return value is not None and str(value).strip() != ""',
        "    return value is not None",
    ),
    (
        VERIFY,
        "返ってこなかった項目を一致扱いにする",
        "    if not _present(actual):",
        "    if False:",
    ),
    (
        VERIFY,
        "照合を常に一致にする",
        "    ok = str(actual) == expected",
        "    ok = True",
    ),
    (
        VERIFY,
        "部分一致で照合する",
        "    ok = str(actual) == expected",
        "    ok = expected in str(actual)",
    ),
    (
        VERIFY,
        "食い違ったときに期待値と実際を出さない",
        '    detail = "" if ok else f"期待 {expected!r} / 実際 {str(actual)!r}"',
        '    detail = ""',
    ),
    # ------------------------------------------------- build_checks
    (
        VERIFY,
        "メッセージIDを照合しない",
        '    checks.append(_compare("メッセージID", message_id, message.get("id")))\n',
        "",
    ),
    (
        VERIFY,
        "メッセージIDの物差しを応答から取る（トートロジー）",
        '    checks.append(_compare("メッセージID", message_id, message.get("id")))',
        '    checks.append(_compare("メッセージID", str(message.get("id")), message.get("id")))',
    ),
    (
        VERIFY,
        "ラベルが返らなくても通す",
        "    if labels is None:",
        "    if False:",
    ),
    (
        VERIFY,
        "送信済みラベルを確認しない",
        '                SENT_LABEL in labels,\n                "" if SENT_LABEL in labels else',
        '                True,\n                "" if SENT_LABEL in labels else',
    ),
    (
        VERIFY,
        "宛先を照合しない",
        '    checks.append(_compare("宛先", expected_to, header_value(payload, "To")))\n',
        "",
    ),
    (
        VERIFY,
        "宛先ではなく Bcc を見る",
        '    checks.append(_compare("宛先", expected_to, header_value(payload, "To")))',
        '    checks.append(_compare("宛先", expected_to, header_value(payload, "Bcc")))',
    ),
    (
        VERIFY,
        "件名の符号化を解かずに比べる",
        '    checks.append(_compare("件名", expected_subject, decode_subject(header_value(payload, "Subject"))))',
        '    checks.append(_compare("件名", expected_subject, header_value(payload, "Subject")))',
    ),
    (
        VERIFY,
        "本文が返らなくても通す",
        "    if actual_body is None:",
        "    if False:",
    ),
    (
        VERIFY,
        "本文の改行を正規化しない",
        '        want = normalize_newlines(expected_body).rstrip("\\n")',
        '        want = expected_body.rstrip("\\n")',
    ),
    (
        VERIFY,
        "本文を前方一致で照合する",
        '        checks.append(Check("本文", want == got,',
        '        checks.append(Check("本文", got.startswith(want),',
    ),
    (
        VERIFY,
        "送信元を確認しない",
        '        Check("送信元", _present(sender),',
        '        Check("送信元", True,',
    ),
    (
        VERIFY,
        "スレッドIDを確認しない",
        '            "スレッドID",\n            _present(thread_id),',
        '            "スレッドID",\n            True,',
    ),
    # ------------------------------------------------- all_ok / format_checks
    (
        VERIFY,
        "照合ゼロ件を「全部一致」にする",
        "    if not checks:\n        return False",
        "    if False:\n        return False",
    ),
    (
        VERIFY,
        "常に一致と判定する",
        "    return all(check.ok for check in checks)",
        "    return True",
    ),
    (
        VERIFY,
        "1つでも一致すれば通す",
        "    return all(check.ok for check in checks)",
        "    return any(check.ok for check in checks)",
    ),
    (
        VERIFY,
        "全部 OK と印字する",
        '        mark = "OK" if check.ok else "NG"',
        '        mark = "OK"',
    ),
    (
        VERIFY,
        "食い違いの詳細を印字しない",
        '        if check.detail:\n            line += f"  {check.detail}"\n',
        "",
    ),
    # ------------------------------------------------- fetch_message
    (
        VERIFY,
        "認証済みユーザー以外のメールを読む",
        "            .get(userId=send_mail.GMAIL_USER_ID, id=message_id, format=MESSAGE_FORMAT)",
        '            .get(userId="all", id=message_id, format=MESSAGE_FORMAT)',
    ),
    (
        VERIFY,
        "読み取りのつもりで本文を送る（書き換えうる）",
        "            .get(userId=send_mail.GMAIL_USER_ID, id=message_id, format=MESSAGE_FORMAT)",
        "            .get(userId=send_mail.GMAIL_USER_ID, id=message_id, format=MESSAGE_FORMAT, body={})",
    ),
    (
        VERIFY,
        "指定と別のメッセージを読む",
        "            .get(userId=send_mail.GMAIL_USER_ID, id=message_id, format=MESSAGE_FORMAT)",
        '            .get(userId=send_mail.GMAIL_USER_ID, id="latest", format=MESSAGE_FORMAT)',
    ),
    (
        VERIFY,
        "読み取り失敗からメッセージIDを落とす",
        '            f"メールを読み取れませんでした（HTTP {status}）: メッセージID {message_id}\\n"',
        '            f"メールを読み取れませんでした（HTTP {status}）\\n"',
    ),
    (
        VERIFY,
        "読み取り失敗からステータスコードを落とす",
        '            f"メールを読み取れませんでした（HTTP {status}）: メッセージID {message_id}\\n"',
        '            f"メールを読み取れませんでした: メッセージID {message_id}\\n"',
    ),
    (
        VERIFY,
        "HTTP エラーを翻訳せず素通りさせる",
        "    except HttpError as error:\n        status = getattr",
        "    except () as error:\n        status = getattr",
    ),
    # ------------------------------------------------- 引数・main
    (
        VERIFY,
        "メッセージIDを任意の引数にする",
        'parser.add_argument("--message-id", required=True,',
        'parser.add_argument("--message-id", required=False,',
    ),
    (
        VERIFY,
        "宛先を任意の引数にする（期待値を応答から埋める余地ができる）",
        'parser.add_argument("--to", required=True, help="送ったときの宛先")',
        'parser.add_argument("--to", required=False, help="送ったときの宛先")',
    ),
    (
        VERIFY,
        "食い違っても 0 を返す",
        "    if not all_ok(checks):",
        "    if False:",
    ),
    (
        VERIFY,
        "照合の結果を印字しない",
        "    print(format_checks(checks))\n",
        "",
    ),
    (
        VERIFY,
        "期待値の検証より先に認証する",
        "    try:\n        # 期待値を先に確定させる。",
        "    factory(args)\n    try:\n        # 期待値を先に確定させる。",
    ),
    (
        VERIFY,
        "読み取りに失敗しても 0 を返す",
        "    except (VerifyError, send_mail.MailError, google_auth.AuthError) as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 1",
        "    except (VerifyError, send_mail.MailError, google_auth.AuthError) as error:\n"
        "        print(error, file=sys.stderr)\n"
        "        return 0",
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
