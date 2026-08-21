#!/usr/bin/env python3
"""その日の予定を Google カレンダーから取って、LINE に送る。

課題10（連携した API に機能を追加）の LINE 側。課題9で作った push に
**送る前の確認**と**予定というデータ源**を足す。

課題9との違い
------------------------------------------------------------------

課題9の主題は「送れたことを、どう確かめるか」だった。LINE には bot が
送ったテキストを読み返す API が無いので、通数の増分と ``basicId`` という
**別エンドポイント2本**を物差しにした。それでも「何が届いたか」は言えない。

**読み返せないなら、送る前に確かめるしかない。** ここが課題10で足すもの。

============================== ================================================
送る前に確かめること             なぜ必要か
============================== ================================================
宛先が友だちか                  **未友だち・ブロックへの push も 200 を返す。**
                                 判別できるのは ``GET /v2/bot/profile/{userId}``
                                 が 404 を返すことだけで、専用の判定 API は無い
残り通数                        枠を使い切ると送れない。``quota`` は送信前に読める
============================== ================================================

「静かに減る」失敗を4つ潰す
------------------------------------------------------------------

カレンダー側にも、**エラーにならず件数だけ減る**失敗がある。
0 件が正常値でありうるこの課題では、減っても気づけない。

============================== ================================================
失敗                            対策
============================== ================================================
繰り返し予定が展開されない       ``singleEvents=True`` を必ず載せる
終日予定を取りこぼす             ``start.date`` と ``start.dateTime`` の両方を見る
``summary`` の無い予定を落とす   代わりの見出しを入れて**残す**
``start`` が未知の形の予定を落とす 同上。落とさずに「時刻不明」として残す
============================== ================================================

**0 件でも送る。** 送らないと、通知が来ない日が「予定が無い」のか
「動いていない」のか、受け取る側から区別できない。

日付は外から渡す
------------------------------------------------------------------

``now`` を引数で受け取る。実行時刻に依存させると、日付が変わった瞬間に
挙動が変わり、テストで境界を確かめられない。00:01 に走らせて当日ぶんを
送る運用なので、**境界のすぐ内側で動く**ことになる。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Callable, Sequence

# common/ と task9/ を import する前に、リポジトリのルートを sys.path へ通す。
# **この順番でないと動かない**——スクリプトとして直接実行すると sys.path の先頭は
# task10/line/ になるため、関数の中で足しても遅い（module 直下の import 文が先に走る）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TASK9 = _REPO_ROOT / "task9"
for _extra_path in (_REPO_ROOT, _TASK9):
    if str(_extra_path) not in sys.path:
        sys.path.insert(0, str(_extra_path))

from googleapiclient.discovery import build  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402

from common import env_file, google_auth, line_auth  # noqa: E402

# **課題9の送信をそのまま使う。同じ処理を2度書かない。**
#
# task9/ は提出済みなので1行も変更しない。**読むだけの依存**にすることで、
# 「連携済みのシステムに機能を追加する」という課題の要件どおり、既存に手を
# 入れずに足せる。加えて、課題9のフィードバックで送信側が直れば、
# こちらにもそのまま効く（反映漏れが構造的に起きない）。
import send_push  # noqa: E402

# 表示上の既定値。**空にしない。**
# 空にすると「予定はあるが行が空」になり、0 件との区別が付かなくなる。
NO_TITLE = "(タイトルなし)"
ALL_DAY_LABEL = "終日"
UNKNOWN_TIME_LABEL = "時刻不明"

CALENDAR_ID = "primary"

# 1日ぶんの予定にしては多すぎる件数だが、**上限で切ると静かに減る**ので
# 余裕を持たせる。ここに当たったら本当に予定が多い日である。
MAX_RESULTS = 50


@dataclass(frozen=True)
class Event:
    """1件の予定を、本文に出せる形まで還元したもの。

    ``all_day`` を持つのは、終日と時刻付きで**受け取る側の関心が違う**ため。
    「10:00 から」と「その日いっぱい」を同じ行で書くと、時刻が無いのか
    取り損ねたのかが分からない。
    """

    summary: str
    all_day: bool
    start_label: str


def resolve_target_date(now: datetime, explicit: date | None = None) -> date:
    """どの日の予定を送るかを決める。

    既定は**実行した日**。00:01 に走らせて当日ぶんを送る運用に合わせる。
    ``explicit`` を渡せばそちらが勝つ（撮り直しと再現のために要る）。
    """
    if explicit is not None:
        return explicit
    return now.date()


def build_time_window(target_date: date, tz: tzinfo) -> tuple[str, str]:
    """``timeMin`` / ``timeMax`` を RFC3339 で組む。

    **オフセットは必須。** 付け忘れると API 側の解釈になり、
    エラーは出ないまま1日ずれた予定が返る。
    """
    start = datetime.combine(target_date, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def build_list_params(
    target_date: date,
    tz: tzinfo,
    *,
    calendar_id: str = CALENDAR_ID,
    max_results: int = MAX_RESULTS,
) -> dict:
    """``events.list`` に渡すパラメータ。

    ``singleEvents`` を省くと繰り返し予定が展開されず、**毎週の予定が
    その日の分として出てこない**。エラーは出ないので、単発の予定1件で
    試している限り永久に気づけない。``orderBy=startTime`` は
    ``singleEvents`` が真のときだけ使える。
    """
    time_min, time_max = build_time_window(target_date, tz)
    return {
        "calendarId": calendar_id,
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": max_results,
    }


def _time_label(raw: str) -> str:
    """``2026-08-21T10:00:00+09:00`` を ``10:00`` にする。

    **タイムゾーンの変換はしない。** カレンダーが返した時刻をそのまま出す
    ——利用者が画面で見ている時刻と一致するのがこの通知の目的だから。
    パースできなければ生の文字列を返す。**落とさない**。
    """
    try:
        return datetime.fromisoformat(raw).strftime("%H:%M")
    except ValueError:
        return raw


def extract_events(payload: dict) -> list[Event]:
    """``events.list`` の応答から予定を取り出す。

    **1件も落とさない。** 落ちても件数が減るだけでエラーにならず、
    0 件が正常値でありうるこの課題では「予定が無い日」と見分けが付かない。
    形が想定外でも、分かる範囲で残して本文に出す。
    """
    events: list[Event] = []
    for item in payload.get("items") or []:
        start = item.get("start") or {}
        # **改行を畳む。** 予定名に改行が入ると本文の「1行=1件」が崩れ、
        # 件数と行数が合わなくなる。エラーは出ないので気づけない。
        summary = " ".join(str(item.get("summary") or "").split()) or NO_TITLE

        if "date" in start:
            events.append(Event(summary=summary, all_day=True, start_label=ALL_DAY_LABEL))
        elif "dateTime" in start:
            events.append(
                Event(
                    summary=summary,
                    all_day=False,
                    start_label=_time_label(str(start["dateTime"])),
                )
            )
        else:
            # start が無い／未知の形。**捨てない。**
            events.append(
                Event(summary=summary, all_day=False, start_label=UNKNOWN_TIME_LABEL)
            )
    return events


def build_message(events: list[Event], target_date: date) -> str:
    """LINE に送る本文。

    **対象日を必ず書く。** 送信が遅れたり撮り直したりすると、届いた時刻と
    対象日はずれる。本文に日付が無いと、受け取った側は「いつの予定か」を
    届いた時刻から推測することになる。
    """
    header = f"{target_date.isoformat()} の予定"
    if not events:
        return f"{header}\n\n予定はありません。"

    lines = [f"- {event.start_label} {event.summary}" for event in events]
    return header + "\n\n" + "\n".join(lines)


# ================================================================== 送信前ガード
#
# **課題10で足す主題がここ。** push は未友だち・ブロックの相手にも 200 を返すので、
# 送った後では区別が付かない。判定できるのは送る前だけである。

#: これを下回ったら警告するが、**止めはしない**。止める境界と混ぜない。
LOW_REMAINING = 5


class NotifyError(Exception):
    """この課題が出す失敗。利用者にそのまま見せられる。"""


@dataclass(frozen=True)
class Gate:
    """送る前の確認の結果。

    ``blocks`` と ``notes`` を分けるのは、**止める理由と、伝えるだけの話を
    混ぜないため**。混ぜると「警告が出たから止まったのか」が読めなくなる。
    """

    ok: bool
    blocks: tuple[str, ...]
    notes: tuple[str, ...]


def judge_send(
    reachability: line_auth.Reachability,
    remaining: int | None,
    *,
    needed: int = 1,
) -> Gate:
    """送ってよいかを決める。**通信をしない純粋な判定**にしてある。

    理由は**全部集める**。1つ返して止めると、直して再実行したら次の理由で
    止まる、を繰り返させることになる。

    ``remaining`` の ``None`` は**無制限**であって 0 ではない。0 は
    「上限はあるが使い切った」で、真逆の意味になる。ここを混ぜると
    ガードが**無制限のアカウントで送信を止める**。
    """
    blocks: list[str] = []
    notes: list[str] = []

    if not reachability.reachable:
        # 理由が空でも黙って通さない。**止めた事実だけは必ず残す。**
        blocks.append(
            reachability.reason or "宛先に届きません（理由が記録されていません）。"
        )

    if remaining is None:
        notes.append("今月の送信上限は設定されていません（無制限）。")
    elif remaining < needed:
        # 負の値もそのまま出す。丸めると「あと 0 通」と「12 通ぶん超過」が
        # 同じ文面になり、直しかたの見当が付かなくなる。
        blocks.append(
            f"今月の残り通数が足りません: 残り {remaining} 通 / 必要 {needed} 通。"
        )
    elif remaining <= LOW_REMAINING:
        notes.append(f"今月の残り通数がわずかです: あと {remaining} 通。")

    return Gate(ok=not blocks, blocks=tuple(blocks), notes=tuple(notes))


def format_gate(gate: Gate) -> tuple[str, ...]:
    """ガードの結果を、画面に出す行の並びにする。

    **1本の文字列にせず行で返す。** 呼ぶ側が好きに出せるうえ、
    「何が出たか」をテストで1行ずつ確かめられる。
    """
    lines = [f"  [注意] {note}" for note in gate.notes]
    lines += [f"  [中止] {reason}" for reason in gate.blocks]
    lines.append("  送信前の確認: " + ("通過" if gate.ok else "中止"))
    return tuple(lines)


# ============================================================== カレンダーを読む

#: 予定を**読むだけ**。書き込み権限を取ると、事故で消せる範囲が広がる。
CALENDAR_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/calendar.readonly",
)

#: 窓は1日ぶんなので、ここに当たるのは異常。**黙って切らずに落とす**ための
#: 上限であって、切り捨てる件数の設定ではない。
MAX_PAGES = 10

#: 問い合わせ窓に使うタイムゾーン。**取り出した予定の時刻は変換しない**
#: （利用者が画面で見ている時刻と一致させるため）。ここで効くのは境界だけ。
DEFAULT_TIMEZONE = timezone(timedelta(hours=9))

DEFAULT_CREDENTIALS = str(_REPO_ROOT / "credentials.json")

#: **課題3の token.json と共有しない。** 共有すると、スコープが足りない側が
#: 相手のトークンを捨てて取り直し、動いていた課題3が止まる。
DEFAULT_TOKEN = str(Path(__file__).resolve().parent / "token-calendar.json")

DEFAULT_RESULTS = str(Path(__file__).resolve().parent / "results.json")


def build_service(credentials):
    return build("calendar", "v3", credentials=credentials)


def translate_http_error(error: HttpError) -> NotifyError:
    """Google の HttpError を、次に何をすればよいか分かる形に訳す。

    生のまま出すと URL とスタックトレースだけが画面に残る。
    **403 は「API を有効にしていない」がいちばん多い**ので名指しする。
    本文はそのまま流さない——エラーの本文に問い合わせ内容が載りうる。
    """
    status = getattr(getattr(error, "resp", None), "status", None)

    if status == 403:
        return NotifyError(
            "Google カレンダー API に拒否されました（403）。\n"
            "credentials.json と同じ Google Cloud プロジェクトで、"
            "「API とサービス」→「ライブラリ」→ Google Calendar API が"
            "有効になっているかを確認してください。"
        )
    if status == 404:
        return NotifyError(
            f"カレンダーが見つかりません（404）: {CALENDAR_ID}\n"
            "同意したアカウントを取り違えていないかを確認してください。"
        )
    return NotifyError(f"Google カレンダー API がエラーを返しました（HTTP {status}）。")


def fetch_all_events(service, params: dict) -> dict:
    """``events.list`` を**最後のページまで**読む。

    ページを追わないと、``nextPageToken`` が付いた日だけ件数が減る。
    エラーは出ず、**0 件が正常値**のこの課題では「予定が無い日」と
    見分けが付かない。

    上限に当たったら**切らずに落とす**。切ると、減ったことに気づく手段が
    どこにも残らない。
    """
    items: list = []
    page_token: str | None = None

    for _ in range(MAX_PAGES):
        page = dict(params)
        if page_token:
            # **初回は載せない。** 空の pageToken を渡すと API 側の解釈になる。
            page["pageToken"] = page_token

        try:
            payload = service.events().list(**page).execute()
        except HttpError as error:
            raise translate_http_error(error) from error

        if not isinstance(payload, dict):
            raise NotifyError(
                "events.list の応答を辞書として読めませんでした。"
                "予定の件数を数えられないため中断します。"
            )

        items.extend(payload.get("items") or [])
        page_token = payload.get("nextPageToken")
        if not page_token:
            return {"items": items}

    raise NotifyError(
        f"カレンダーの応答が {MAX_PAGES} ページを超えました。"
        "件数を静かに減らさないため、ここで中断します。"
    )


def parse_target_date(value: str) -> date:
    """``--date`` を日付にする。

    **壊れた値を「今日」に倒さない。** 倒すと、指定したつもりの日とは
    違う日の予定が、エラーなしで送られる。
    """
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise NotifyError(
            f"--date の形式が違います: {value}\n"
            "YYYY-MM-DD で指定してください（例: 2026-08-21）。"
        ) from error


# ====================================================================== 記録


def build_record(
    *,
    info: line_auth.BotInfo,
    to: str,
    target_date: date,
    events: list[Event],
    text: str,
    message_id: str,
    request_id: str,
    usage_before: int,
    usage_after: int,
    remaining: int | None,
) -> dict:
    """``verify_notify.py`` が読む記録を組む。

    課題9の記録に ``target_date`` と ``event_count`` を足した形。
    **件数を残すのは、あとからカレンダーを読み直して突き合わせるため。**
    LINE 側には読み返す経路が無いので、照合できる相手はカレンダーしかない。
    """
    return {
        "bot": {
            "user_id": info.user_id,
            "basic_id": info.basic_id,
            "display_name": info.display_name,
            "chat_mode": info.chat_mode,
            "mark_as_read_mode": info.mark_as_read_mode,
        },
        "to_masked": send_push.mask_destination(to),
        "target_date": target_date.isoformat(),
        "event_count": len(events),
        "all_day_count": sum(1 for event in events if event.all_day),
        "text": text,
        "message_id": message_id,
        "request_id": request_id,
        "usage_before": usage_before,
        "usage_after": usage_after,
        # **無制限は null のまま書く。0 にしない**（0 は「使い切った」で真逆）。
        # 超過（負の値）も丸めない。
        "remaining": remaining,
    }


def _display_path(path: str | Path) -> str:
    """画面に出すためのパス。リポジトリの中なら相対にする。

    絶対パスにはホームディレクトリ名が入る。**実行画面は課題の提出物として
    公開される**ので、出す必要のない情報を最初から出さない。

    課題9の ``send_push.py`` にも同じものがあるが、あちらは非公開名のうえ
    提出済みのファイルなので触らない。3行の重複を選ぶ。
    """
    try:
        return str(Path(path).resolve().relative_to(_REPO_ROOT))
    except ValueError:
        # リポジトリの外を指している。隠すとどこに書いたか分からなくなるので出す。
        return str(path)


# ====================================================================== 実行


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="その日の予定を Google カレンダーから取って LINE に送る（課題10）"
    )
    parser.add_argument(
        "--date", help="対象日を YYYY-MM-DD で指定する（既定: 実行した日）"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="送らずに、送信前の確認の結果と本文だけを出す（通数を消費しない）",
    )
    parser.add_argument(
        "--results",
        default=DEFAULT_RESULTS,
        help="送信の記録を書くファイル（verify_notify.py が読む）",
    )
    parser.add_argument(
        "--env",
        default=str(_REPO_ROOT / env_file.ENV_FILENAME),
        help=f"LINE の資格情報が入った {env_file.ENV_FILENAME}",
    )
    parser.add_argument(
        "--credentials",
        default=DEFAULT_CREDENTIALS,
        help="Google の OAuth クライアント（課題1のものをそのまま使う）",
    )
    parser.add_argument(
        "--token",
        default=DEFAULT_TOKEN,
        help="カレンダー専用のトークン。課題3の token.json とは共有しない",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: Callable | None = None,
    service_factory: Callable | None = None,
) -> int:
    """送る前に確かめてから送る。

    **順番に意味がある。LINE 側のガードを、カレンダーを読む前に置く。**
    後ろに置くと、送らないと決まっている実行でも Google の認可を通すことに
    なり、同意画面が開く。届かない相手のために同意を求めない。
    """
    args = parse_args(argv)

    # **手元で分かることは通信の前に済ませる。** 壊れた日付なら1回も叩かない。
    explicit = parse_target_date(args.date) if args.date else None
    target_date = resolve_target_date(datetime.now(DEFAULT_TIMEZONE), explicit)

    env = env_file.load(args.env)
    token = line_auth.read_channel_access_token(env)
    to = line_auth.read_user_id(env)
    secrets = (token,)

    session = (
        session_factory(token) if session_factory else line_auth.build_session(token)
    )

    info = line_auth.fetch_bot_info(session, secrets=secrets)
    print(f"チャネル: {info.display_name} ({info.basic_id})  chatMode={info.chat_mode}")

    reachability = line_auth.fetch_profile(session, to, secrets=secrets)
    quota = line_auth.fetch_quota(session, secrets=secrets)
    consumption = line_auth.fetch_consumption(session, secrets=secrets)
    remaining = line_auth.remaining_messages(quota, consumption)

    gate = judge_send(reachability, remaining)
    for line in format_gate(gate):
        print(line)
    if not gate.ok:
        return 1

    service = (
        service_factory()
        if service_factory
        else build_service(
            google_auth.load_credentials(args.credentials, args.token, CALENDAR_SCOPES)
        )
    )
    payload = fetch_all_events(service, build_list_params(target_date, DEFAULT_TIMEZONE))
    events = extract_events(payload)
    text = build_message(events, target_date)

    print()
    print(text)
    print()

    if args.dry_run:
        print("--dry-run のため送信していません（通数は消費していません）。")
        return 0

    # ガードで読んだ値をそのまま「送信前の通数」にする。**もう一度叩かない**
    # ——同じものを2回取ると、間に何も起きていないのに差が出る余地を作る。
    usage_before = consumption
    response = send_push.push(
        session, send_push.build_payload(to=to, text=text), secrets=secrets
    )
    sent = send_push.read_send_result(response)
    usage_after = line_auth.fetch_consumption(session, secrets=secrets)

    print(f"送信しました: message_id={sent.message_id}")
    print(f"通数: {usage_before} -> {usage_after}")
    if sent.request_id:
        print(f"x-line-request-id: {sent.request_id}")

    record = build_record(
        info=info,
        to=to,
        target_date=target_date,
        events=events,
        text=text,
        message_id=sent.message_id,
        request_id=sent.request_id,
        usage_before=usage_before,
        usage_after=usage_after,
        remaining=remaining,
    )
    send_push.write_record(args.results, record)
    print(f"記録: {_display_path(args.results)}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except (
        NotifyError,
        line_auth.LineError,
        env_file.EnvFileError,
        google_auth.AuthError,
        send_push.SendError,
    ) as error:
        print(f"失敗: {error}")
        raise SystemExit(1) from error
