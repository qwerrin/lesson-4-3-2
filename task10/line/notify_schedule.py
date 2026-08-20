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

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

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
        summary = str(item.get("summary") or "").strip() or NO_TITLE

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
