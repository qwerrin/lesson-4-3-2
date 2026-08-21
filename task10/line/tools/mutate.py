#!/usr/bin/env python3
"""notify_schedule.py を1か所ずつ壊して、テストが落ちることを確かめる。

**テストが通っていることは、守られていることの証拠にならない。**
課題9では、主題そのものである「確認できないことを出す」行が素通りしていた
（2つの assert が両方とも、無関係な場所の文字列で満たされていた）。

この課題で守りたいのは「**エラーにならず、件数だけ静かに減る**」失敗である。
0 件が正常値でありうるので、減っても「予定が無い日」と見分けが付かない。
だから件数に関わる分岐は、壊したら必ず落ちる状態にしておく。

使い方::

    .venv\\Scripts\\python.exe task10\\line\\tools\\mutate.py

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

NOTIFY = "task10/line/notify_schedule.py"
AUTH = "common/line_auth.py"

IGNORE = shutil.ignore_patterns(
    ".venv", ".git", "__pycache__", ".pytest_cache", "docs", "*.png", "node_modules"
)

# common/ を壊すので、**課題9のテストも一緒に回す**。
# 共有部品に足したものが既存を壊していないことを、ここでも確かめる。
TEST_PATHS = ("task10/line/tests", "common/tests")

# (対象ファイル, 壊した内容, 置換前, 置換後)
MUTATIONS: list[tuple[str, str, str, str]] = [
    # ==================================== 問い合わせパラメータ：静かに減る側
    (
        NOTIFY,
        "繰り返し予定を展開しない（singleEvents を落とす）",
        '"singleEvents": True,',
        '"singleEvents": False,',
    ),
    (
        NOTIFY,
        "開始時刻順の指定を落とす",
        '"orderBy": "startTime",',
        '"orderBy": "updated",',
    ),
    (
        NOTIFY,
        "問い合わせ窓の下限を渡さない",
        '"timeMin": time_min,',
        '"timeMin": "",',
    ),
    (
        NOTIFY,
        "問い合わせ窓の上限を渡さない",
        '"timeMax": time_max,',
        '"timeMax": "",',
    ),
    # ================================================ 問い合わせ窓：1日ずれる
    (
        NOTIFY,
        "窓の幅を1日から0日にする（その日の予定が1件も入らない）",
        "    end = start + timedelta(days=1)",
        "    end = start + timedelta(days=0)",
    ),
    (
        NOTIFY,
        "タイムゾーンを付けない（RFC3339 のオフセットが消える）",
        "    start = datetime.combine(target_date, time.min, tzinfo=tz)",
        "    start = datetime.combine(target_date, time.min)",
    ),
    # ============================================================== 対象日
    (
        NOTIFY,
        "明示指定を無視して常に実行日を使う",
        "    if explicit is not None:",
        "    if False:",
    ),
    (
        NOTIFY,
        "対象日を翌日にずらす",
        "    return now.date()",
        "    return now.date() + timedelta(days=1)",
    ),
    # ============================================ 予定の取り出し：静かに減る側
    (
        NOTIFY,
        "終日予定を落とす（start.date を見ない）",
        '        if "date" in start:',
        "        if False:",
    ),
    (
        NOTIFY,
        "時刻付き予定を落とす（start.dateTime を見ない）",
        '        elif "dateTime" in start:',
        "        elif False:",
    ),
    (
        NOTIFY,
        "start が未知の形の予定を捨てる",
        "            events.append(\n                Event(summary=summary, all_day=False, start_label=UNKNOWN_TIME_LABEL)\n            )",
        "            pass",
    ),
    (
        NOTIFY,
        "タイトルの無い予定を空文字で通す（行が空になり0件と紛れる）",
        'NO_TITLE = "(タイトルなし)"',
        'NO_TITLE = ""',
    ),
    (
        NOTIFY,
        "終日の目印を消す（時刻付きと区別できなくなる）",
        'ALL_DAY_LABEL = "終日"',
        'ALL_DAY_LABEL = ""',
    ),
    (
        NOTIFY,
        "items が無いときに落ちる（0件を正常値として扱わない）",
        '    for item in payload.get("items") or []:',
        '    for item in payload["items"]:',
    ),
    # ================================================================ 本文
    (
        NOTIFY,
        "0件のとき何も言わない（予定なしと動いていないが区別できなくなる）",
        "    if not events:",
        "    if False:",
    ),
    (
        NOTIFY,
        "本文から対象日を消す（いつの予定か分からなくなる）",
        '    header = f"{target_date.isoformat()} の予定"',
        '    header = "予定"',
    ),
    (
        NOTIFY,
        "予定の見出しだけ出して時刻を落とす",
        '    lines = [f"- {event.start_label} {event.summary}" for event in events]',
        '    lines = [f"- {event.summary}" for event in events]',
    ),
    (
        NOTIFY,
        "時刻の整形をやめて生の文字列を出す",
        '        return datetime.fromisoformat(raw).strftime("%H:%M")',
        "        return raw",
    ),
    # ================================ 送信前ガード：宛先（common/line_auth.py）
    (
        AUTH,
        "404 を見落とす（未友だち・ブロックを検出できなくなる）",
        '    if getattr(response, "status_code", None) == 404:',
        "    if False:",
    ),
    (
        AUTH,
        "404 なのに「届く」と答える",
        "            reachable=False,",
        "            reachable=True,",
    ),
    (
        AUTH,
        "届かない理由を丸ごと空にする（止めた理由が利用者に伝わらない）",
        '            reason=(\n                "この宛先はプロフィールを返しませんでした（404）。"\n                "友だち追加されていないか、ブロックされています。"\n                "push はこの相手にも 200 を返すため、送信前に止めます。"\n            ),',
        '            reason="",',
    ),
    (
        AUTH,
        "404 以外のエラーを素通りさせる（401 が「届かない」に化ける）",
        "            profile=None,\n        )\n\n    raise_for_line_error(response, *secrets)",
        "            profile=None,\n        )\n\n    pass",
    ),
    (
        AUTH,
        "プロフィールの userId が空でも通す（宛先を確かめずに進む）",
        "    if not profile_user_id:",
        "    if False:",
    ),
    # ================================ 送信前ガード：通数（common/line_auth.py）
    (
        AUTH,
        "上限の種別を見ない（無制限を上限ありとして扱う）",
        '    limited = str(payload.get("type") or "") == "limited"',
        "    limited = True",
    ),
    (
        AUTH,
        "上限が無いときに 0 とみなす（無制限が「使い切った」に化ける）",
        "    limit = int(raw_limit) if limited and raw_limit is not None else None",
        "    limit = int(raw_limit) if limited and raw_limit is not None else 0",
    ),
    (
        AUTH,
        "残数の「無制限」を 0 で返す",
        "    if not quota.limited or quota.limit is None:\n        return None",
        "    if not quota.limited or quota.limit is None:\n        return 0",
    ),
    (
        AUTH,
        "残数の負値を 0 に丸める（超過と使い切りが同じ表示になる）",
        "    return quota.limit - consumption",
        "    return max(0, quota.limit - consumption)",
    ),
    (
        AUTH,
        "totalUsage が無くても中断しない",
        "    if raw_usage is None:",
        "    if False:",
    ),
]


VERIFY = "task10/line/verify_notify.py"
CHECKDOCS = "task10/line/tools/check_docs.py"

# 課題10の後半（送信前ガードの統合・送信・照合・自己検査）で足したぶん。
#
# **どれも1行の置換にしてある。** 複数行のパターンは改行の扱いで一度も
# マッチしないことがあり、「置換先なし」は素通りと同じ扱いなので、
# 壊しかたが悪いのか照合器が壊れているのか区別が付かないまま数字だけ出る。
MUTATIONS += [
    # ============================================ 送信前ガード：止める判断
    (
        NOTIFY,
        "届かないと分かっても止めない",
        "    if not reachability.reachable:",
        "    if False:",
    ),
    (
        NOTIFY,
        "止めた理由を空にする（利用者が何を直せばよいか分からなくなる）",
        '            reachability.reason or "宛先に届きません（理由が記録されていません）。"',
        '            ""',
    ),
    (
        NOTIFY,
        "無制限を「使い切った」として止める（真逆の判断）",
        '        notes.append("今月の送信上限は設定されていません（無制限）。")',
        '        blocks.append("今月の残り通数が足りません: 残り 0 通。")',
    ),
    (
        NOTIFY,
        "最後の1通を送らせない（境界を1つ内側にする）",
        "    elif remaining < needed:",
        "    elif remaining <= needed:",
    ),
    (
        NOTIFY,
        "超過を 0 に丸める（使い切りと区別が付かなくなる）",
        '            f"今月の残り通数が足りません: 残り {remaining} 通 / 必要 {needed} 通。"',
        '            f"今月の残り通数が足りません: 残り {max(0, remaining)} 通 / 必要 {needed} 通。"',
    ),
    (
        NOTIFY,
        "止める理由を最初の1つで打ち切る（直して再実行を繰り返させる）",
        "    return Gate(ok=not blocks, blocks=tuple(blocks), notes=tuple(notes))",
        "    return Gate(ok=not blocks, blocks=tuple(blocks[:1]), notes=tuple(notes))",
    ),
    (
        NOTIFY,
        "注意を捨てる（無制限・残りわずかが伝わらなくなる）",
        "    return Gate(ok=not blocks, blocks=tuple(blocks), notes=tuple(notes))",
        "    return Gate(ok=not blocks, blocks=tuple(blocks), notes=())",
    ),
    # ================================================ カレンダー：静かに減る側
    (
        NOTIFY,
        "次のページを追わない（件数が静かに減る）",
        '        page_token = payload.get("nextPageToken")',
        "        page_token = None",
    ),
    (
        NOTIFY,
        "初回から pageToken を載せる（API 側の解釈になる）",
        "        if page_token:",
        "        if True:",
    ),
    (
        NOTIFY,
        "ページ上限を1にする（2ページ目の予定が丸ごと消える）",
        "MAX_PAGES = 10",
        "MAX_PAGES = 1",
    ),
    (
        NOTIFY,
        "応答の形を検査しない",
        "        if not isinstance(payload, dict):",
        "        if False:",
    ),
    (
        NOTIFY,
        "Google のエラーを訳さず素通しする",
        "            raise translate_http_error(error) from error",
        "            raise",
    ),
    (
        NOTIFY,
        "403 の案内を消す（API を有効にしていないことが分からなくなる）",
        "    if status == 403:",
        "    if False:",
    ),
    # ============================================================== 対象日
    (
        NOTIFY,
        "壊れた日付を今日に倒す（別の日の予定が黙って送られる）",
        "        return date.fromisoformat(value)",
        "        return date.today()",
    ),
    (
        NOTIFY,
        "対象日を無視して常に今日で引く",
        "    payload = fetch_all_events(service, build_list_params(target_date, DEFAULT_TIMEZONE))",
        "    payload = fetch_all_events(service, build_list_params(date.today(), DEFAULT_TIMEZONE))",
    ),
    # ================================================================ 記録
    (
        NOTIFY,
        "対象日を記録しない（いつの予定を送ったか分からなくなる）",
        '        "target_date": target_date.isoformat(),',
        '        "target_date": "",',
    ),
    (
        NOTIFY,
        "件数を記録しない（カレンダーと突き合わせられなくなる）",
        '        "event_count": len(events),',
        '        "event_count": 0,',
    ),
    (
        NOTIFY,
        "無制限を 0 として記録する（照合側で「使い切った」に化ける）",
        '        "remaining": remaining,',
        '        "remaining": remaining or 0,',
    ),
    (
        NOTIFY,
        "宛先を伏せずに記録する（public リポジトリに素で残る）",
        '        "to_masked": send_push.mask_destination(to),',
        '        "to_masked": to,',
    ),
    # ======================================================== 実行の順番と停止
    (
        NOTIFY,
        "ガードが中止と言っても送る",
        "    if not gate.ok:",
        "    if False:",
    ),
    (
        NOTIFY,
        "--dry-run でも送る（通数を消費する）",
        "    if args.dry_run:",
        "    if False:",
    ),
    # ================================================ 照合：確認できないこと
    (
        VERIFY,
        "確認できないことを出さない（合格が「全部確認した」に見える）",
        '        lines.append(f"  ・{note}")',
        "        pass",
    ),
    (
        VERIFY,
        "本文を読み返せないという注記を消す",
        '        "本文が届いたかどうか。LINE には bot が送ったテキストを読み返す API が無い"',
        '        ""',
    ),
    (
        VERIFY,
        "カレンダー照合の時点ズレの注記を消す",
        '        "カレンダーの照合が見ているのは「いま」の予定であって、送信した瞬間のもの"',
        '        ""',
    ),
    (
        VERIFY,
        "送信前ガードが届いた証拠にならない、という注記を消す",
        '        "送信前ガードが「届く」と答えたことは、届いたことを意味しない。"',
        '        ""',
    ),
    # ================================================ 照合：手元の検査
    (
        VERIFY,
        "記録に欠けた項目があっても照合を始める",
        "    missing = [key for key in REQUIRED_KEYS if key not in payload]",
        "    missing = []",
    ),
    (
        VERIFY,
        "通数を「1 増えた」ではなく「増えた」で見る",
        '        _compare("通数の増分", 1, delta),',
        '        Check(label="通数の増分", expected="1 以上", actual=delta, ok=_is_int(delta) and delta >= 1),',
    ),
    (
        VERIFY,
        "本文の行数と件数を突き合わせない",
        '        _compare("予定の行数と件数", record.get("event_count"), count_event_lines(text)),',
        '        _compare("予定の行数と件数", True, True),',
    ),
    (
        VERIFY,
        "予定の行を数えず、本文の全行を数える",
        "    return sum(1 for line in text.splitlines() if line.startswith(EVENT_LINE_PREFIX))",
        "    return len(text.splitlines())",
    ),
    (
        VERIFY,
        "本文に対象日があるかを見ない",
        '            "本文に対象日がある", True, bool(target_date) and target_date in text',
        '            "本文に対象日がある", True, True',
    ),
    (
        VERIFY,
        "無制限（null）の残数を不合格にする",
        '            "残数が読める形", True, remaining is None or _is_int(remaining)',
        '            "残数が読める形", True, _is_int(remaining)',
    ),
    # ================================================ 照合：遠隔の検査
    (
        VERIFY,
        "API を叩き直さず記録どうしを比べる（トートロジー）",
        '        _compare("basicId（API と記録）", bot.get("basic_id"), info.basic_id),',
        '        _compare("basicId（API と記録）", bot.get("basic_id"), bot.get("basic_id")),',
    ),
    (
        VERIFY,
        "いまも宛先に届くかを見ない",
        "            ok=reachability.reachable,",
        "            ok=True,",
    ),
    (
        VERIFY,
        "通数が減っていても通す",
        "        usage_ok = current >= after",
        "        usage_ok = True",
    ),
    (
        VERIFY,
        "カレンダーを記録の対象日ではなく今日で引く",
        "        target_date = date.fromisoformat(raw)",
        "        target_date = date.today()",
    ),
    (
        VERIFY,
        "カレンダーの件数を照合しない",
        '        _compare("カレンダーの件数（いま）", record.get("event_count"), len(events)),',
        '        _compare("カレンダーの件数（いま）", True, True),',
    ),
    (
        VERIFY,
        "--local-only でも API を叩く",
        "    if not args.local_only:",
        "    if True:",
    ),
    (
        VERIFY,
        "--no-calendar を無視してカレンダーを読む",
        "        if not args.no_calendar:",
        "        if True:",
    ),
    (
        NOTIFY,
        "予定名の改行を畳まない（本文の 1行=1件 が崩れる）",
        '        summary = " ".join(str(item.get("summary") or "").split()) or NO_TITLE',
        '        summary = str(item.get("summary") or "").strip() or NO_TITLE',
    ),
    (
        VERIFY,
        "記録の宛先といまの宛先を照合しない",
        '            record.get("to_masked"),',
        "            record.get(\"to_masked\") if False else send_push.mask_destination(to),",
    ),
    (
        VERIFY,
        "壊れた対象日で生の ValueError を出す",
        "    except ValueError as error:",
        "    except ZeroDivisionError as error:",
    ),
    # ============================== 自己検査（課題9から持ち越した宿題）
    (
        CHECKDOCS,
        "照合項目数に自分自身を数えない（README が常に1つ少なくなる）",
        "    actual = checks_so_far + 1",
        "    actual = checks_so_far",
    ),
    (
        CHECKDOCS,
        "自分自身を二重に数える",
        "    actual = checks_so_far + 1",
        "    actual = checks_so_far + 2",
    ),
    (
        CHECKDOCS,
        "README が項目数を名乗っていなくても合格にする",
        "        return False, (",
        "        return True, (",
    ),
    (
        CHECKDOCS,
        "名乗りが無いのを 0 と読む（1件も検査していない、と区別が付かない）",
        "    return int(match.group(1)) if match else None",
        "    return int(match.group(1)) if match else 0",
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
            print("壊す前からテストが落ちています。先にそちらを直してください。", file=sys.stderr)
            return 1

        killed: list[str] = []
        survived: list[str] = []
        not_found: list[str] = []

        for index, (target, label, before, after) in enumerate(MUTATIONS, start=1):
            path = work / target
            # 復元用にはバイトをそのまま持つ（newline="" は改行を変換しない）。
            original = path.read_text(encoding="utf-8", newline="")

            # **照合と書き込みは LF に正規化した文字列で行う。**
            # このリポジトリは core.autocrlf=true で、チェックアウトすると
            # .py は CRLF になる。newline="" のまま `\n` を含むパターンを探すと
            # **複数行のパターンは構造的に一度もマッチしない**——そして
            # 「置換先なし」は素通りと同じ扱いなので、壊し方が悪いのか
            # 照合器が壊れているのか区別が付かないまま数字だけ出る。
            # 課題9で実際に4件がこれで NOT FOUND になった。
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
    print(f"kill {len(killed)} / SURVIVED {len(survived)} / NOT FOUND {len(not_found)}"
          f"  （全 {len(MUTATIONS)} 件）")

    if survived:
        print()
        print("素通り（テストが守っていない）:")
        for item in survived:
            print(f"  - {item}")
    if not_found:
        print()
        print("置換先が見つからない（壊しかたが実装とずれている）:")
        for item in not_found:
            print(f"  - {item}")

    return 0 if not survived and not not_found else 1


if __name__ == "__main__":
    raise SystemExit(main())
