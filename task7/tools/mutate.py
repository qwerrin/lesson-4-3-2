"""task7 を1か所ずつ壊して、テストが落ちることを確認する。

通っているテストの数は、守られている範囲を意味しない。
落ちなかった行は「テストが見ていない場所」なので、そこだけ手当てする。

壊しかたを足すのは**コードを書いた直後**。まとめて最後にやると穴が出て、直後にやると出ない。

使い方（リポジトリのルートで実行する）::

    .venv\\Scripts\\python.exe task7\\tools\\mutate.py

課題6までと方式を変えた（2026-08-16）
------------------------------------------------------------------

課題2〜6の mutate.py は**実ファイルを書き換えて 1 件ごとに戻す**形だった。
これは 2026-08-14（課題4）に実際に事故っている——強制終了すると ``atexit`` が
走らず、``verify_meeting.py`` に ``if False:`` が残ったまま止まった。
控えを取る・復旧手順を書く、という手当てをしても「**壊す道具は壊れたまま止まる**」
という性質そのものは消えない。

そこで**リポジトリを丸ごと一時ディレクトリへ写して、写した側だけを壊す**。
成果物のファイルは1バイトも触らないので、途中で落ちても事故が起きない。
復旧手順が要らなくなる＝**復旧手順が間違っている可能性も消える**。

**置換先が見つからなかったものは「素通り」と同じ扱いにする。**
コードを直して壊しかたを直し忘れると、何も壊さずに全部通って「穴ゼロ」と出てしまう。

**対象に common/slack_auth.py を含む**ので、テストは task7/tests だけでなく
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

AUTH = "common/slack_auth.py"
POST = "task7/post_message.py"
VERIFY = "task7/verify_message.py"

# 写さないもの。仮想環境と履歴は重く、画像は壊しても意味がない。
IGNORE = shutil.ignore_patterns(
    ".venv", ".git", "__pycache__", ".pytest_cache", "docs", "*.png", "node_modules"
)

TEST_PATHS = ("task7/tests", "common/tests")

# (対象ファイル, 壊した内容, 置換前, 置換後)
MUTATIONS: list[tuple[str, str, str, str]] = [
    # =========================================================== 認証（Bot Token）
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
        "ユーザートークン（xoxp-）を素通りさせる",
        "    if not value.startswith(BOT_TOKEN_PREFIX):",
        "    if False:",
    ),
    (
        AUTH,
        "期待するプレフィックスを間違える",
        'BOT_TOKEN_PREFIX = "xoxb-"',
        'BOT_TOKEN_PREFIX = "xox"',
    ),
    (
        AUTH,
        "トークンを伏せない（実行画面に出る）",
        "    return text.replace(token, REDACTED)",
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
        "トークンが空でも replace する（全文字の間に伏せ字が入る）",
        "    if not token:\n        return text",
        "    if False:\n        return text",
    ),
    (
        AUTH,
        "空のトークンでもクライアントを組む",
        '    value = (token or "").strip()\n    if not value:',
        '    value = (token or "").strip()\n    if False:',
    ),
    (
        AUTH,
        "auth.test の ok を見ない",
        '    if not response.get("ok"):\n        detail = response.get("error") or "(理由不明)"',
        '    if False:\n        detail = response.get("error") or "(理由不明)"',
    ),
    (
        AUTH,
        "bot_id を見ない（User Token が通る）",
        '    bot_id = str(response.get("bot_id") or "").strip()\n    if not bot_id:',
        '    bot_id = str(response.get("bot_id") or "").strip()\n    if False:',
    ),
    (
        AUTH,
        "user_id を見ない（照合の物差しが空のまま進む）",
        '    user_id = str(response.get("user_id") or "").strip()\n    if not user_id:',
        '    user_id = str(response.get("user_id") or "").strip()\n    if False:',
    ),
    (
        AUTH,
        "スコープのヘッダ名を大小そのままで比べる",
        "        if str(key).lower() != SCOPE_HEADER:",
        "        if str(key) != SCOPE_HEADER:",
    ),
    (
        AUTH,
        "スコープをカンマで割らない",
        '        return tuple(part.strip() for part in str(value).split(",") if part.strip())',
        "        return (str(value),)",
    ),
    (
        AUTH,
        "スコープの前後の空白を落とさない",
        '        return tuple(part.strip() for part in str(value).split(",") if part.strip())',
        '        return tuple(part for part in str(value).split(",") if part)',
    ),
    (
        AUTH,
        "ヘッダが無いとき空タプルを返す（不明と0個を混ぜる）",
        '        return tuple(part.strip() for part in str(value).split(",") if part.strip())\n\n    return None',
        '        return tuple(part.strip() for part in str(value).split(",") if part.strip())\n\n    return ()',
    ),
    # ここに「ヘッダが空でも読みに行く」（`if not headers: return None` を潰す）を
    # 置いていたが、**そのガードを外しても結果が変わらない**ことが分かったので
    # ガードごと削除した。テストを足しても kill できない種類の指摘で、
    # 「守っているつもりの行が、実は何もしていない」はミューテーションでしか見えない。
    (
        AUTH,
        "スコープが不明なとき足りている扱いにする",
        "        return ScopeCheck(known=False, missing=wanted, granted=None)",
        "        return ScopeCheck(known=False, missing=(), granted=None)",
    ),
    (
        AUTH,
        "スコープが不明でも known を真にする",
        "        return ScopeCheck(known=False, missing=wanted, granted=None)",
        "        return ScopeCheck(known=True, missing=wanted, granted=None)",
    ),
    (
        AUTH,
        "スコープを前方一致で通す（chat:write.public が chat:write に化ける）",
        "    missing = tuple(scope for scope in wanted if scope not in granted)",
        "    missing = tuple(\n        scope for scope in wanted if not any(g.startswith(scope) for g in granted)\n    )",
    ),
    (
        AUTH,
        "確認するスコープが空でも落とさない",
        '    if not wanted:\n        raise AuthError("確認するスコープが空です。呼び出し側で指定してください")',
        '    if False:\n        raise AuthError("確認するスコープが空です。呼び出し側で指定してください")',
    ),
    (
        AUTH,
        "付与済みスコープを持ち歩かない",
        "    return ScopeCheck(known=True, missing=missing, granted=tuple(identity.scopes))",
        "    return ScopeCheck(known=True, missing=missing, granted=None)",
    ),
    # =========================================================== 投稿：本文の変換
    (
        POST,
        "アンパサンドを変換しない",
        '    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")',
        '    return text.replace("<", "&lt;").replace(">", "&gt;")',
    ),
    (
        POST,
        "小なりを変換しない",
        '    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")',
        '    return text.replace("&", "&amp;").replace(">", "&gt;")',
    ),
    (
        POST,
        "大なりを変換しない",
        '    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")',
        '    return text.replace("&", "&amp;").replace("<", "&lt;")',
    ),
    (
        POST,
        "変換の順序を逆にする（二重変換になる）",
        '    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")',
        '    return text.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")',
    ),
    (
        POST,
        "何も変換しない",
        '    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")',
        "    return text",
    ),
    # =========================================================== 投稿：入口の検査
    (
        POST,
        "チャンネルが空でも送る",
        '    channel = (channel or "").strip()\n    if not channel:',
        '    channel = (channel or "").strip()\n    if False:',
    ),
    (
        POST,
        "本文が空でも送る",
        '    if not (text or "").strip():',
        "    if False:",
    ),
    (
        POST,
        "本文の空白だけを本文とみなす",
        '    if not (text or "").strip():',
        "    if not text:",
    ),
    # =========================================================== 投稿：応答の検査
    (
        POST,
        "投稿の ok を見ない",
        '    if not response.get("ok"):\n        detail = response.get("error") or "(理由不明)"\n        raise PostError(f"投稿に失敗しました: {detail}")',
        '    if False:\n        detail = response.get("error") or "(理由不明)"\n        raise PostError(f"投稿に失敗しました: {detail}")',
    ),
    (
        POST,
        "ts が返らなくても成功にする",
        '    ts = str(response.get("ts") or "").strip()\n    if not ts:',
        '    ts = str(response.get("ts") or "").strip()\n    if False:',
    ),
    (
        POST,
        "要求したチャンネルと違っても通す",
        '    returned = str(response.get("channel") or "").strip()\n    if returned != channel:',
        '    returned = str(response.get("channel") or "").strip()\n    if False:',
    ),
    (
        POST,
        "応答のチャンネルを自分自身と比べる（トートロジー）",
        '    returned = str(response.get("channel") or "").strip()\n    if returned != channel:',
        '    returned = str(response.get("channel") or "").strip()\n    if returned != returned:',
    ),
    (
        POST,
        "タイムスタンプを数値にする（精度が落ちる）",
        "    return Posted(channel=channel, ts=ts, text=text)",
        "    return Posted(channel=channel, ts=float(ts), text=text)",
    ),
    # =========================================================== 投稿：リンク
    (
        POST,
        "リンクの取得で message_ts ではなく ts を渡す",
        "    response = client.chat_getPermalink(channel=channel, message_ts=ts)",
        "    response = client.chat_getPermalink(channel=channel, ts=ts)",
    ),
    (
        POST,
        "リンクの ok を見ない",
        '    if not response.get("ok"):\n        detail = response.get("error") or "(理由不明)"\n        raise PostError(f"リンクの取得に失敗しました: {detail}")',
        '    if False:\n        detail = response.get("error") or "(理由不明)"\n        raise PostError(f"リンクの取得に失敗しました: {detail}")',
    ),
    (
        POST,
        "リンクが空でも成功にする",
        '    link = str(response.get("permalink") or "").strip()\n    if not link:',
        '    link = str(response.get("permalink") or "").strip()\n    if False:',
    ),
    # =========================================================== 投稿：エラーの翻訳
    (
        POST,
        "エラーコードを出さない",
        '    message = f"Slack API がエラーを返しました: {code}"',
        '    message = "Slack API がエラーを返しました"',
    ),
    (
        POST,
        "直しかたの案内を出さない",
        "    hint = _ERROR_HINTS.get(code)\n    if hint:",
        "    hint = _ERROR_HINTS.get(code)\n    if False:",
    ),
    (
        POST,
        "チャンネル未参加の案内でスコープの話をする",
        '    "not_in_channel": (\n        "Bot がチャンネルに参加していません。"\n        "Slack のチャンネルで `/invite @<アプリ名>` を送って招待してください。"\n    ),',
        '    "not_in_channel": ("スコープが足りない可能性があります。設定を見直してください。"),',
    ),
    (
        POST,
        "エラー本文のトークンを伏せない",
        "    return PostError(slack_auth.redact(message, token))",
        "    return PostError(message)",
    ),
    # =========================================================== 投稿：記録
    (
        POST,
        "投稿者を記録しない",
        '        "posted_by": identity.user_id,',
        '        "posted_by": "",',
    ),
    (
        POST,
        "送った本文を記録しない",
        '        "text": text,',
        '        "text": "",',
    ),
    (
        POST,
        "結果ファイルを指定しなくても書く",
        "    if args.json_out:",
        "    if True:",
    ),
    # =========================================================== 投稿：スコープの扱い
    (
        POST,
        "必要なスコープを間違える",
        'SCOPES = ("chat:write",)',
        'SCOPES = ("channels:history",)',
    ),
    (
        POST,
        "スコープが足りなくても投稿する",
        '    if check.known and check.missing:\n        print("\\n権限が足りないため投稿しません。", file=sys.stderr)',
        '    if False:\n        print("\\n権限が足りないため投稿しません。", file=sys.stderr)',
    ),
    (
        POST,
        "スコープが不明なだけで投稿を止める",
        "    if check.known and check.missing:",
        "    if check.missing:",
    ),
    (
        POST,
        "スコープを確認できないことを黙る",
        '    if not check.known:\n        return (\n            f"スコープ: 確認できません（応答に {slack_auth.SCOPE_HEADER} ヘッダがありませんでした）。"\n            "付与済みの権限を検査せずに続行します"\n        )',
        '    if not check.known:\n        return "スコープ: 足りています"',
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
        "数値のタイムスタンプを受け入れる",
        "        if not isinstance(value, str) or not value.strip():",
        "        if not value:",
    ),
    (
        VERIFY,
        "必須の項目を減らす",
        '_REQUIRED_KEYS = ("channel", "text", "ts", "permalink", "posted_by")',
        '_REQUIRED_KEYS = ("channel",)',
    ),
    # =========================================================== 読み返し：手元の照合
    (
        VERIFY,
        "チャンネルを照合しない",
        '    checks.append(_compare("チャンネル", expected_channel, payload.get("channel")))',
        '    checks.append(Check("チャンネル", True))',
    ),
    (
        VERIFY,
        "送った本文を照合しない",
        '    checks.append(_compare("送った本文", expected_text, payload.get("text")))',
        '    checks.append(Check("送った本文", True))',
    ),
    (
        VERIFY,
        "期待値を結果ファイルの値で埋める（トートロジー）",
        '    checks.append(_compare("送った本文", expected_text, payload.get("text")))',
        '    checks.append(_compare("送った本文", payload.get("text"), payload.get("text")))',
    ),
    (
        VERIFY,
        "リンクの指す先を見ない",
        "            bool(channel) and channel in permalink,",
        "            True,",
    ),
    (
        VERIFY,
        "タイムスタンプの形式を見ない",
        "    ok = bool(_TS_PATTERN.fullmatch(ts))",
        "    ok = True",
    ),
    (
        VERIFY,
        "タイムスタンプの形式を部分一致で見る",
        '_TS_PATTERN = re.compile(r"\\d+\\.\\d+")',
        '_TS_PATTERN = re.compile(r".*")',
    ),
    # =========================================================== 読み返し：取得
    (
        VERIFY,
        "inclusive を渡さない（自分自身が結果に入らない）",
        "        inclusive=True,",
        "        inclusive=False,",
    ),
    (
        VERIFY,
        "1件ではなく大量に取る",
        "        limit=1,",
        "        limit=100,",
    ),
    (
        VERIFY,
        "oldest ではなく latest で引く",
        "        oldest=ts,",
        "        latest=ts,",
    ),
    (
        VERIFY,
        "履歴の ok を見ない",
        '    if not response.get("ok"):\n        detail = response.get("error") or "(理由不明)"\n        raise VerifyError(f"履歴の取得に失敗しました: {detail}")',
        '    if False:\n        detail = response.get("error") or "(理由不明)"\n        raise VerifyError(f"履歴の取得に失敗しました: {detail}")',
    ),
    (
        VERIFY,
        "messages が無くても進む",
        "    if not isinstance(messages, list):",
        "    if False:",
    ),
    # =========================================================== 読み返し：照合の中身
    (
        VERIFY,
        "実行中のBotを照合しない",
        '    checks.append(_compare("実行中のBot", payload.get("posted_by"), identity.user_id))',
        '    checks.append(Check("実行中のBot", True))',
    ),
    (
        VERIFY,
        "読み返せなくても一致とみなす",
        '    if message is None:\n        # 0 件を「照合する対象が無い＝全部一致」にしない。\n        checks.append(\n            Check(\n                "メッセージの実在",\n                False,',
        '    if message is None:\n        # 0 件を「照合する対象が無い＝全部一致」にしない。\n        checks.append(\n            Check(\n                "メッセージの実在",\n                True,',
    ),
    (
        VERIFY,
        "タイムスタンプを照合しない（別のメッセージが通る）",
        '    checks.append(_compare("タイムスタンプ", payload.get("ts"), str(message.get("ts") or "")))',
        '    checks.append(Check("タイムスタンプ", True))',
    ),
    (
        VERIFY,
        "投稿者を照合しない",
        '    checks.append(_compare("投稿者", identity.user_id, str(message.get("user") or "")))',
        '    checks.append(Check("投稿者", True))',
    ),
    (
        VERIFY,
        "投稿者を応答どうしで比べる（トートロジー）",
        '    checks.append(_compare("投稿者", identity.user_id, str(message.get("user") or "")))',
        '    checks.append(\n        _compare("投稿者", str(message.get("user") or ""), str(message.get("user") or ""))\n    )',
    ),
    (
        VERIFY,
        "本文にSlackの変換を適用しない（& を含む本文で必ず外れる）",
        '    expected = post_message.escape_for_slack(payload.get("text") or "")',
        '    expected = payload.get("text") or ""',
    ),
    (
        VERIFY,
        "本文が空でも一致とみなす",
        '    actual = str(message.get("text") or "")\n    checks.append(_compare("本文", expected, actual))',
        '    actual = str(message.get("text") or "")\n    checks.append(Check("本文", not actual or expected == actual))',
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
        '    if not all_ok(local):\n        print("\\n食い違いがあります。上の NG を確認してください。", file=sys.stderr)\n        return 1\n\n    try:\n        client, identity, token = factory()',
        '    if False:\n        print("\\n食い違いがあります。上の NG を確認してください。", file=sys.stderr)\n        return 1\n\n    try:\n        client, identity, token = factory()',
    ),
    (
        VERIFY,
        "読み返しの照合が落ちても成功にする",
        '    if not all_ok(remote):\n        print("\\n食い違いがあります。上の NG を確認してください。", file=sys.stderr)\n        return 1',
        '    if False:\n        print("\\n食い違いがあります。上の NG を確認してください。", file=sys.stderr)\n        return 1',
    ),
    (
        VERIFY,
        "確かめていない範囲を書かない",
        '    print(\n        "確かめていないこと: 画面上の見え方 / 投稿の並び順 / チャンネルの表示名。"\n        "この確認が見ているのは、上に並べた項目だけです。"\n    )',
        '    print("すべての項目を検証しました。")',
    ),
    (
        VERIFY,
        "読み返しに必要なスコープを間違える",
        'SCOPES = ("channels:history",)',
        'SCOPES = ("chat:write",)',
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

    with tempfile.TemporaryDirectory(prefix="mutate-task7-") as tmp:
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
    print(f"合計 {len(MUTATIONS)} 件 / kill {len(killed)} 件 / 素通り {len(survived)} 件 / 置換先なし {len(not_found)} 件")

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
