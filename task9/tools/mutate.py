"""task9 を1か所ずつ壊して、テストが落ちることを確認する。

通っているテストの数は、守られている範囲を意味しない。
落ちなかった行は「テストが見ていない場所」なので、そこだけ手当てする。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task9\\tools\\mutate.py

方式は課題7・課題8と同じ（実ファイルを触らない）
------------------------------------------------------------------

**リポジトリを丸ごと一時ディレクトリへ写して、写した側だけを壊す。**
成果物のファイルは1バイトも触らないので、途中で強制終了しても事故が起きない。
復旧手順が要らなくなる＝**復旧手順が間違っている可能性も消える**。

**置換先が見つからなかったものは「素通り」と同じ扱いにする。**
コードを直して壊しかたを直し忘れると、何も壊さずに全部通って「穴ゼロ」と出てしまう。

**対象に common/ の2つを含む**ので、テストは task9/tests だけでなく common/tests も回す。

この課題で実際に見つかった穴（2026-08-19）
------------------------------------------------------------------

初回は**4件が素通り**した。4件とも「例外は出た／文字列はあった」で満足していた形である。

1. ``.env`` 不在の検査 — 消しても「読めたが0件」の検査が代わりに例外を投げる
2. ディレクトリ検査 — 消しても「見つかりません」が代わりに投げる
3. **「確認できないこと」の表示** — ``assert "確認できない" in report`` が見出し行に、
   ``assert "本文" in report`` が別の検査ラベル「本文が空でない」にヒットしていた
4. 短い宛先の伏せ字 — ``!= "U123"`` は ``U1…23``（元の文字が全部読める）でも真になる

**3つ目がいちばん重い。** この課題の主題を守るための行が、丸ごと消えても誰も気づかなかった。
直し方は**例外の型ではなく文言まで見る**／**「違う文字列になった」ではなく
「読めなくなった」を書く**の2つ。課題8で8件踏んだ「落ちてはいるが理由が違う」の再演である。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

ENV = "common/env_file.py"
AUTH = "common/line_auth.py"
SEND = "task9/send_push.py"
VERIFY = "task9/verify_push.py"

IGNORE = shutil.ignore_patterns(
    ".venv", ".git", "__pycache__", ".pytest_cache", "docs", "*.png", "node_modules"
)

TEST_PATHS = ("task9/tests", "common/tests")

# (対象ファイル, 壊した内容, 置換前, 置換後)
MUTATIONS: list[tuple[str, str, str, str]] = [
    # =============================================== .env：沈黙を例外に変える
    (
        ENV,
        "ファイルが無くても落とさない（0件検査に肩代わりさせる）",
        "    if not target.is_file():",
        "    if False:",
    ),
    (
        ENV,
        "ファイル不在のメッセージから「見つかりません」と探した場所を消す",
        'f"{ENV_FILENAME} が見つかりません: {target}\\n"',
        '"設定を読めません\\n"',
    ),
    (
        ENV,
        "ファイル不在のメッセージから見本ファイルの案内を消す",
        'f"{EXAMPLE_FILENAME} をコピーして値を埋めてください。\\n"\n            f"  copy {EXAMPLE_FILENAME} {ENV_FILENAME}\\n"',
        '"見本をコピーして値を埋めてください。\\n"',
    ),
    (
        ENV,
        "ディレクトリでも落とさない（不在検査に肩代わりさせる）",
        "    if target.is_dir():",
        "    if False:",
    ),
    (
        ENV,
        "ディレクトリのメッセージから「ディレクトリ」と言うのをやめる",
        'f"{target} はディレクトリです。{ENV_FILENAME} はファイルとして作成してください。"',
        'f"{target} を読めません。"',
    ),
    (
        ENV,
        "読めたが0件でも成功にする",
        "    if not values:",
        "    if False:",
    ),
    (
        ENV,
        "${VAR} を os.environ から展開する（既定に戻す）",
        'dotenv_values(target, encoding="utf-8", interpolate=False)',
        'dotenv_values(target, encoding="utf-8")',
    ),
    (
        ENV,
        "値の無いキーを None のまま返す",
        '{key: ("" if value is None else value) for key, value in raw.items()}',
        "dict(raw)",
    ),
    (
        ENV,
        "呼び出しごとに同じ辞書を返す（呼び出し側の書き換えが漏れる）",
        "    return values",
        "    return raw",
    ),
    (
        ENV,
        "既定のファイル名を変える",
        'ENV_FILENAME = ".env"',
        'ENV_FILENAME = ".environment"',
    ),
    # =============================================== 認証：トークンを読む
    (
        AUTH,
        "トークンの前後の空白を落とさない",
        'value = (env.get(CHANNEL_ACCESS_TOKEN_ENV) or "").strip()',
        'value = env.get(CHANNEL_ACCESS_TOKEN_ENV) or ""',
    ),
    (
        AUTH,
        "トークンが空でも落とさない",
        '    if not value:\n        raise AuthError(\n            f"チャネルアクセストークンが設定されていません',
        '    if False:\n        raise AuthError(\n            f"チャネルアクセストークンが設定されていません',
    ),
    (
        AUTH,
        "未設定のメッセージから「いちばん下」を消す",
        '"**いちばん下**にある「チャネルアクセストークン（長期）」の［発行］で"',
        '"ある「チャネルアクセストークン（長期）」の［発行］で"',
    ),
    (
        AUTH,
        "Bearer 前置の検査を大文字小文字に依存させる",
        'if value.lower().startswith("bearer "):',
        'if value.startswith("Bearer "):',
    ),
    (
        AUTH,
        "チャネルシークレットとの取り違えを疑わない",
        "if len(value) == _CHANNEL_SECRET_LENGTH and all(char in _HEX for char in value):",
        "if False:",
    ),
    (
        AUTH,
        "チャネルシークレットの長さを取り違える",
        "_CHANNEL_SECRET_LENGTH = 32",
        "_CHANNEL_SECRET_LENGTH = 64",
    ),
    # =============================================== 認証：宛先IDを読む
    (
        AUTH,
        "宛先IDが空でも落とさない",
        '    if not value:\n        raise AuthError(\n            f"送信先のユーザーIDが設定されていません',
        '    if False:\n        raise AuthError(\n            f"送信先のユーザーIDが設定されていません',
    ),
    (
        AUTH,
        "ベーシックID（@ 始まり）の取り違えを検出しない",
        '    if value.startswith("@"):',
        "    if False:",
    ),
    (
        AUTH,
        "ベーシックIDのメッセージから名指しをやめる",
        'f"{USER_ID_ENV} にボットのベーシックID（{value}）が入っています。\\n"',
        'f"{USER_ID_ENV} の値が不正です。\\n"',
    ),
    (
        AUTH,
        "宛先IDに空白が入っていても通す",
        "    if any(char.isspace() for char in value):",
        "    if False:",
    ),
    (
        AUTH,
        "宛先IDの先頭文字を見ない",
        "    if not value.startswith(_DESTINATION_PREFIXES):",
        "    if False:",
    ),
    (
        AUTH,
        "宛先IDをユーザー（U）だけに絞る",
        '_DESTINATION_PREFIXES = ("U", "C", "R")',
        '_DESTINATION_PREFIXES = ("U",)',
    ),
    (
        AUTH,
        "宛先IDの誤りを値なしで報告する（何を直せばよいか分からない）",
        'f"{USER_ID_ENV} が送信先IDの形ではありません: {value}\\n"',
        'f"{USER_ID_ENV} が送信先IDの形ではありません。\\n"',
    ),
    # =============================================== 認証：伏せ字とセッション
    (
        AUTH,
        "空の秘密を素通りさせない（文章が壊れる）",
        "        if secret:\n            text = text.replace(secret, REDACTED)",
        "        if True:\n            text = text.replace(secret or '', REDACTED)",
    ),
    (
        AUTH,
        "伏せ字をやめる",
        "            text = text.replace(secret, REDACTED)",
        "            text = text",
    ),
    (
        AUTH,
        "伏せた印を残さない",
        'REDACTED = "***"',
        'REDACTED = ""',
    ),
    (
        AUTH,
        "Bearer を付けずに送る",
        '{"Authorization": f"Bearer {value}", "User-Agent": USER_AGENT}',
        '{"Authorization": value, "User-Agent": USER_AGENT}',
    ),
    (
        AUTH,
        "User-Agent を付けない",
        '{"Authorization": f"Bearer {value}", "User-Agent": USER_AGENT}',
        '{"Authorization": f"Bearer {value}"}',
    ),
    (
        AUTH,
        "空のトークンでもセッションを組む",
        '    value = (token or "").strip()\n    if not value:',
        '    value = (token or "").strip()\n    if False:',
    ),
    (
        AUTH,
        "API のホストを固定しない",
        'API_BASE = "https://api.line.me"',
        'API_BASE = "https://api.line.me/"',
    ),
    (
        AUTH,
        "環境変数の名前を見本とずらす",
        'CHANNEL_ACCESS_TOKEN_ENV = "LINE_CHANNEL_ACCESS_TOKEN"',
        'CHANNEL_ACCESS_TOKEN_ENV = "LINE_TOKEN"',
    ),
    (
        AUTH,
        "宛先の環境変数の名前を見本とずらす",
        'USER_ID_ENV = "LINE_USER_ID"',
        'USER_ID_ENV = "LINE_TO"',
    ),
    # =============================================== 認証：エラーの訳し分け
    (
        AUTH,
        "5xx 未満を成功として扱う",
        "    if 200 <= status < 300:\n        return",
        "    if status < 500:\n        return",
    ),
    (
        AUTH,
        "details の名指しを出さない",
        '            message += f"\\n  {where}: {what}".rstrip()',
        "            message += \"\"",
    ),
    (
        AUTH,
        "名指しがあっても候補を並べる",
        "    if status == 400 and not named:",
        "    if status == 400:",
    ),
    (
        AUTH,
        "名指しの有無を見ない（常に候補を出す）",
        "    named = isinstance(details, list) and bool(details)",
        "    named = False",
    ),
    (
        AUTH,
        "401 でトークンを案内しない",
        "    if status == 401:",
        "    if False:",
    ),
    (
        AUTH,
        "429 でレート制限を説明しない",
        "    if status == 429:",
        "    if False:",
    ),
    (
        AUTH,
        "ヘッダ名の大文字小文字を区別する",
        '        if str(name).lower() == "x-line-request-id":',
        '        if str(name) == "x-line-request-id":',
    ),
    (
        AUTH,
        "x-line-request-id を出さない",
        "    if request_id:",
        "    if False:",
    ),
    # =============================================== 認証：自分が誰か
    (
        AUTH,
        "別のパスを叩く",
        'response = session.get(f"{base}/v2/bot/info")',
        'response = session.get(f"{base}/v2/bot/information")',
    ),
    (
        AUTH,
        "応答が辞書でなくても進む",
        '    if not isinstance(payload, dict):\n        raise AuthError("応答を JSON として読めませんでした（/v2/bot/info）。")',
        '    if not isinstance(payload, dict):\n        payload = {}',
    ),
    (
        AUTH,
        "userId が空でも進む（照合の物差しが消える）",
        "    if not user_id:",
        "    if False:",
    ),
    (
        AUTH,
        "basicId が空でも進む（意図したチャネルか確かめられなくなる）",
        "    if not basic_id:",
        "    if False:",
    ),
    (
        AUTH,
        "chatMode を決め打ちにする（API の答えを見ない）",
        'chat_mode=str(payload.get("chatMode") or ""),',
        'chat_mode="bot",',
    ),
    # =============================================== 送信：中身を組む
    (
        SEND,
        "空の本文を手元で弾かない",
        '    if not (text or "").strip():',
        "    if False:",
    ),
    (
        SEND,
        "本文の前後の空白を落とす（送った文字列とずれる）",
        'return {"to": to, "messages": [{"type": "text", "text": text}]}',
        'return {"to": to, "messages": [{"type": "text", "text": text.strip()}]}',
    ),
    (
        SEND,
        "push のパスを間違える",
        'PUSH_PATH = "/v2/bot/message/push"',
        'PUSH_PATH = "/v2/bot/message/send"',
    ),
    (
        SEND,
        "リトライキーを付けない（二重送信を防げない）",
        '    headers = {"X-Line-Retry-Key": retry_key or str(uuid.uuid4())}',
        "    headers = {}",
    ),
    (
        SEND,
        "指定されたリトライキーを無視する",
        "retry_key or str(uuid.uuid4())",
        "str(uuid.uuid4())",
    ),
    (
        SEND,
        "本文を JSON で送らない",
        "session.post(base + PUSH_PATH, json=payload, headers=headers)",
        "session.post(base + PUSH_PATH, data=payload, headers=headers)",
    ),
    # =============================================== 送信：応答を読む
    (
        SEND,
        "sentMessages が空でも成功にする（HTTP 200 で証跡なし）",
        "    if not isinstance(sent_messages, list) or not sent_messages:",
        "    if False:\n        pass\n    if not isinstance(sent_messages, list):",
    ),
    (
        SEND,
        "message ID が空でも成功にする",
        "    if not message_id:",
        "    if False:",
    ),
    (
        SEND,
        "応答が JSON でなくても進む",
        '    if not isinstance(payload, dict):\n        raise SendError("push の応答を JSON として読めませんでした。")',
        "    if not isinstance(payload, dict):\n        payload = {}",
    ),
    (
        SEND,
        "x-line-request-id を拾わない",
        '        if str(name).lower() == "x-line-request-id":',
        '        if str(name) == "X-LINE-REQUEST-ID":',
    ),
    # =============================================== 送信：通数を読む
    (
        SEND,
        "totalUsage が無くても 0 として続行する",
        '    if not isinstance(payload, dict) or "totalUsage" not in payload:',
        "    if not isinstance(payload, dict):",
    ),
    (
        SEND,
        "True を通数として通す（bool は int の仲間）",
        "    if isinstance(value, bool) or not isinstance(value, int):",
        "    if not isinstance(value, int):",
    ),
    (
        SEND,
        "通数のパスを間違える",
        'CONSUMPTION_PATH = "/v2/bot/message/quota/consumption"',
        'CONSUMPTION_PATH = "/v2/bot/message/quota"',
    ),
    # =============================================== 送信：画面に出すパス
    (
        SEND,
        "画面に絶対パスを出す（ホームディレクトリ名がスクショに写る）",
        "        return str(Path(path).resolve().relative_to(ROOT))",
        "        return str(Path(path).resolve())",
    ),
    (
        SEND,
        "リポジトリ外のパスを隠す（どこに書いたか分からなくなる）",
        "        return str(path)",
        '        return "(外部)"',
    ),
    # =============================================== 送信：記録
    (
        SEND,
        "短い宛先をそのまま返す",
        "    if len(text) <= _MASK_KEEP * 2:",
        "    if False:",
    ),
    (
        SEND,
        "短い宛先の伏せ字をやめる",
        '        return "…" * 3',
        "        return text",
    ),
    (
        SEND,
        "宛先の伏せ字をやめる",
        'return f"{text[:_MASK_KEEP]}…{text[-_MASK_KEEP:]}"',
        "return text",
    ),
    (
        SEND,
        "記録に生の宛先を書く",
        '        "to_masked": mask_destination(to),',
        '        "to_masked": to,',
    ),
    (
        SEND,
        "日本語をエスケープして書く（人が読んで確かめられなくなる）",
        "json.dumps(record, ensure_ascii=False, indent=2)",
        "json.dumps(record, ensure_ascii=True, indent=2)",
    ),
    # =============================================== 照合：記録を読む
    (
        VERIFY,
        "記録が無くても落とさない",
        "    if not target.is_file():",
        "    if False:",
    ),
    (
        VERIFY,
        "記録が辞書でなくても進む",
        "    if not isinstance(payload, dict):\n        raise VerifyError",
        "    if False:\n        raise VerifyError",
    ),
    (
        VERIFY,
        "必須項目の欠落を見ない",
        "    missing = [key for key in REQUIRED_KEYS if key not in payload]",
        "    missing = []",
    ),
    (
        VERIFY,
        "bot が辞書でなくても進む",
        '    if not isinstance(payload["bot"], dict):',
        "    if False:",
    ),
    # =============================================== 照合：手元の検査
    (
        VERIFY,
        "増分を 1 と比べない（自分自身と比べる）",
        '        _compare("通数の増分", 1, delta),',
        '        _compare("通数の増分", delta, delta),',
    ),
    (
        VERIFY,
        "増分の向きを見ない（絶対値で比べる）",
        "        delta = after - before",
        "        delta = abs(after - before)",
    ),
    (
        VERIFY,
        "message ID が数字かどうかを見ない",
        "            bool(message_id) and message_id.isdigit(),",
        "            True,",
    ),
    (
        VERIFY,
        "basicId の形を見ない",
        '_compare("basicId が @ で始まる", True, basic_id.startswith("@")),',
        '_compare("basicId が @ で始まる", True, True),',
    ),
    (
        VERIFY,
        "chatMode の記録を見ない",
        '_compare("chatMode が記録されている", True, bool(bot.get("chat_mode"))),',
        '_compare("chatMode が記録されている", True, True),',
    ),
    (
        VERIFY,
        "本文が空でも通す",
        '_compare("本文が空でない", True, bool(str(record.get("text") or "").strip())),',
        '_compare("本文が空でない", True, True),',
    ),
    (
        VERIFY,
        "宛先の伏せ忘れを見ない",
        '            bool(masked) and ("…" in masked or "..." in masked),',
        "            True,",
    ),
    (
        VERIFY,
        "比較そのものをやめる（常に一致と出す）",
        "    return Check(label=label, expected=expected, actual=actual, ok=expected == actual)",
        "    return Check(label=label, expected=expected, actual=actual, ok=True)",
    ),
    # =============================================== 照合：遠隔の検査
    (
        VERIFY,
        "通数の単調性を見ない",
        "        usage_ok = current >= after",
        "        usage_ok = True",
    ),
    (
        VERIFY,
        "basicId を API どうしで比べる（記録と突き合わせない）",
        '_compare("basicId（API と記録）", bot.get("basic_id"), info.basic_id),',
        '_compare("basicId（API と記録）", info.basic_id, info.basic_id),',
    ),
    (
        VERIFY,
        "bot の userId を API どうしで比べる",
        '_compare("bot の userId（API と記録）", bot.get("user_id"), info.user_id),',
        '_compare("bot の userId（API と記録）", info.user_id, info.user_id),',
    ),
    (
        VERIFY,
        "通数が整数として読めなくても合格にする",
        "        usage_ok = False\n        usage_actual = \"整数として読めない\"",
        "        usage_ok = True\n        usage_actual = \"整数として読めない\"",
    ),
    # =============================================== 照合：確認できないこと
    (
        VERIFY,
        "「確認できないこと」を1件も出さない",
        "    for note in unverifiable_notes():",
        "    for note in ():",
    ),
    (
        VERIFY,
        "「確認できないこと」の中身を空にする（見出しだけ残す）",
        '        lines.append(f"  ・{note}")',
        '        lines.append("  ・")',
    ),
    (
        VERIFY,
        "注記の一覧を空にできるようにする",
        '        "本文が届いたかどうか。LINE には bot が送ったテキストを読み返す API が無い"',
        '        "（省略）"',
    ),
    (
        VERIFY,
        "不合格の印を出さない",
        '            mark = "OK" if check.ok else "NG"',
        '            mark = "OK"',
    ),
    (
        VERIFY,
        "全件の合否を見ない",
        "    return all(check.ok for check in checks)",
        "    return True",
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

    with tempfile.TemporaryDirectory(prefix="mutate-task9-") as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(ROOT, work, ignore=IGNORE)

        # 壊す前に、写した側がそのままで通ることを確かめる。ここが落ちていると
        # 「全部 kill」が出るが、それは壊したからではない。
        if run_tests(work):
            print(
                "写した側のテストが最初から落ちています。ミューテーションを始めません。",
                file=sys.stderr,
            )
            return 1

        killed: list[str] = []
        survived: list[str] = []
        not_found: list[str] = []

        for index, (target, label, before, after) in enumerate(MUTATIONS, start=1):
            path = work / target
            # **復元用にはバイトをそのまま持つ**（newline="" は改行を変換しない）。
            original = path.read_text(encoding="utf-8", newline="")

            # **照合と書き込みは LF に正規化した文字列で行う。**
            # このリポジトリの .py は全ファイル CRLF で保存されている。
            # newline="" のまま `\n` を含むパターンを探すと、**複数行のパターンは
            # 構造的に一度もマッチしない**——そして「置換先なし」は素通りと同じ扱いなので、
            # **壊し方が悪いのか照合器が壊れているのか区別が付かない**まま数字だけ出る。
            # 2026-08-19 に実際にこれで4件が NOT FOUND になった。
            # 一時ディレクトリの中なので、LF で書き戻しても成果物には影響しない。
            haystack = original.replace("\r\n", "\n")

            if before not in haystack:
                # **置換先が無いものは素通りと同じ扱い。** コードを直して壊しかたを
                # 直し忘れると、何も壊さずに全部通って「穴ゼロ」と出てしまう。
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
