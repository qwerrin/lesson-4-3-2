"""課題9: LINE Messaging API でメッセージを送る。

    .venv\\Scripts\\python.exe task9\\send_push.py --text "やっほー"

**この課題は、これまでの8課題と締め方が違う。**

課題1〜8は「送信 → 別経路で読み返して照合」で閉じていた。LINE には
bot が送ったテキストを読み返す API が無い（2026-08-18 に公式 OpenAPI 定義で確認。
``GET /v2/bot/message/{messageId}/content`` は**ユーザーが送った**画像・動画・音声専用）。

そこで送信側の仕事に「**あとから照合できる材料を残すこと**」を含める。

============================== ============================================
材料                            何を言えるか
============================== ============================================
``sentMessages[].id``           LINE がこの送信に ID を振った
``totalUsage`` の送信前後        **別のエンドポイント**が通数の増加を認めた
``/v2/bot/info`` の ``basicId``  意図したチャネルを叩いた
============================== ============================================

**3つとも「何を送ったか」は言わない。** 文面の一致は最後まで機械では確認できない。
そのことは verify_push.py が検査結果に明示する。ここでは
**材料が欠けたまま成功にしない**ことだけを守る。

なぜ ``broadcast`` ではなく ``push`` なのか
------------------------------------------------------------------

``POST /v2/bot/message/broadcast`` の応答は**空オブジェクト**である。
message ID が返らないので、上の材料が1つ消える。課題8で
「webhook の既定は 204 で何も返らない＝読み返して確かめられない」を理由に
Bot 経路を選んだのと同じ判断。**確認できない経路を選ぶと、確認の話ができなくなる。**

資格情報の渡し方（課題9だけ違う）
------------------------------------------------------------------

課題4〜8 は PowerShell の環境変数だったが、課題9は ``.env`` ファイルを使う。
理由と落とし穴は ``common/env_file.py`` の docstring を参照。
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

# common/ を import する前にリポジトリのルートを通す。**この順番でないと動かない**——
# スクリプトとして直接実行すると sys.path の先頭は task9/ になるため、
# 関数の中で足しても遅い（module 直下の import 文が先に走る）。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common import env_file, line_auth  # noqa: E402

ROOT = _REPO_ROOT
DEFAULT_RESULTS = str(Path(__file__).resolve().parent / "results.json")

PUSH_PATH = "/v2/bot/message/push"
CONSUMPTION_PATH = "/v2/bot/message/quota/consumption"

#: 伏せ字にするときに残す前後の文字数。
_MASK_KEEP = 2


class SendError(Exception):
    """送信まわりの失敗。利用者にそのまま見せられる。"""


@dataclass(frozen=True)
class Sent:
    """push の応答から読み取ったもの。

    ``quote_token`` は受け取るが**記録には書かない**。引用返信に使える値で、
    照合には要らない。「受け取ったものは全部書く」を既定にすると、
    API に項目が増えた日に黙って漏れる。
    """

    message_id: str
    quote_token: str
    request_id: str


# ------------------------------------------------------------------ 送る中身


def build_payload(*, to: str, text: str) -> dict:
    """push のリクエスト本文を組む。

    **本文は strip しない。** 空白だけを弾くのと、書いた空白を落とすのは別の話。
    落とすと「送った文字列」と「届いた文字列」が最初からずれる。

    **長さは検査しない。** 上限の数字をこのセッションで実物で確かめていない。
    確かめていない数字を定数に置くと、LINE 側が変えた日に**正しい送信を拒む**側で
    壊れる（課題8で ``content`` の最大長を持たないと決めたのと同じ）。
    """
    if not (text or "").strip():
        raise SendError(
            "本文が空です。--text に送る文字列を指定してください。\n"
            "（空のまま送ると API が 400 を返しますが、手元で止めれば通数を消費しません）"
        )

    # 1リクエストに5件まで載るが1件に固定する。totalUsage の増分は
    # 「送信対象になった人数」なので、件数を増やすと照合の解釈が難しくなる。
    return {"to": to, "messages": [{"type": "text", "text": text}]}


# ------------------------------------------------------------------ 応答を読む


def _payload_of(response) -> Any:
    try:
        return response.json()
    except Exception:  # noqa: BLE001 - 本文が JSON でないことは実際に起きる
        return None


def _request_id_of(response) -> str:
    headers = getattr(response, "headers", None) or {}
    for name, value in headers.items():
        if str(name).lower() == "x-line-request-id":
            return str(value)
    return ""


def read_send_result(response) -> Sent:
    """push の応答から message ID を取り出す。

    **HTTP 200 で ``sentMessages`` が空**という形を失敗にする。
    ID が無ければ記録に残す材料が無く、あとから何も言えない。
    「エラーにならない失敗」を成功として通さない（課題8「0 件は不一致」と同じ）。
    """
    payload = _payload_of(response)
    if not isinstance(payload, dict):
        raise SendError("push の応答を JSON として読めませんでした。")

    sent_messages = payload.get("sentMessages")
    if not isinstance(sent_messages, list) or not sent_messages:
        raise SendError(
            "push の応答に sentMessages がありません。\n"
            "HTTP は成功していますが、送信された証跡が取れないため失敗として扱います。"
        )

    first = sent_messages[0]
    if not isinstance(first, dict):
        raise SendError("push の応答の sentMessages の形が想定と違います。")

    message_id = str(first.get("id") or "").strip()
    if not message_id:
        raise SendError("push の応答に message ID がありません。")

    return Sent(
        message_id=message_id,
        quote_token=str(first.get("quoteToken") or ""),
        request_id=_request_id_of(response),
    )


# ------------------------------------------------------------------ 通数


def fetch_usage(session, *, base: str = line_auth.API_BASE, secrets: tuple = ()) -> int:
    """今月の送信通数を読む。

    **取れなかったときに 0 を返さない。** 0 は「まだ1通も送っていない」という
    正当な値なので、失敗を 0 に倒すと増分の照合が偽の成功に化ける
    （課題6で踏んだ「空が正常値の欄はバグが静かな側に倒れる」と同じ形）。
    """
    response = session.get(base + CONSUMPTION_PATH)
    line_auth.raise_for_line_error(response, *secrets)

    payload = _payload_of(response)
    if not isinstance(payload, dict) or "totalUsage" not in payload:
        raise SendError(f"{CONSUMPTION_PATH} が totalUsage を返しませんでした。")

    value = payload["totalUsage"]
    # bool は int の仲間なので、素朴な型検査を素通りする。通数として通さない。
    if isinstance(value, bool) or not isinstance(value, int):
        raise SendError(
            f"{CONSUMPTION_PATH} の totalUsage が整数ではありません: {type(value).__name__}"
        )

    return value


def push(
    session,
    payload: dict,
    *,
    base: str = line_auth.API_BASE,
    secrets: tuple = (),
    retry_key: str | None = None,
):
    """push を投げる。

    ``X-Line-Retry-Key`` を付ける。通信が切れて再実行したとき、同じキーなら
    **二重送信にならない**。無料プランは月200通なので、事故った再実行で
    通数を溶かさない意味もある。
    """
    headers = {"X-Line-Retry-Key": retry_key or str(uuid.uuid4())}
    response = session.post(base + PUSH_PATH, json=payload, headers=headers)
    line_auth.raise_for_line_error(response, *secrets)
    return response


# ------------------------------------------------------------------ 記録


def mask_destination(value: str) -> str:
    """記録に残す宛先を伏せる。

    ``results.json`` は public リポジトリに入る。宛先IDはチャネルに紐づく
    識別子で単体では他人が使えないが、**残す必要が無いものは残さない**。
    前後を少し残すのは、記録どうしを見比べて「同じ宛先か」を人が判断できるようにするため。
    """
    text = value or ""
    if len(text) <= _MASK_KEEP * 2:
        # 短い値に例外を作らない。例外を作ると、そこだけ丸ごと出る。
        return "…" * 3
    return f"{text[:_MASK_KEEP]}…{text[-_MASK_KEEP:]}"


def build_record(
    *,
    info: line_auth.BotInfo,
    to: str,
    text: str,
    message_id: str,
    request_id: str,
    usage_before: int,
    usage_after: int,
) -> dict:
    """verify_push.py が読む記録を組む。

    **quote_token は受け取らない。** 引数に無ければ、うっかり書き込めない。
    """
    return {
        "bot": {
            "user_id": info.user_id,
            "basic_id": info.basic_id,
            "display_name": info.display_name,
            "chat_mode": info.chat_mode,
            "mark_as_read_mode": info.mark_as_read_mode,
        },
        "to_masked": mask_destination(to),
        "text": text,
        "message_id": message_id,
        "request_id": request_id,
        "usage_before": usage_before,
        "usage_after": usage_after,
    }


def _display_path(path: str | Path) -> str:
    """画面に出すためのパス。リポジトリの中なら相対にする。

    絶対パスにはホームディレクトリ名が入る。**実行画面は課題の提出物として
    公開される**ので、出す必要のない情報を最初から出さない。
    """
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        # リポジトリの外を指している。隠すとどこに書いたか分からなくなるので出す。
        return str(path)


def write_record(path: str | Path, record: dict) -> None:
    """記録を UTF-8 の JSON で書く。

    ``ensure_ascii=False`` にする。**記録は人が読んで確かめるもの**で、
    日本語がエスケープされていると目視の照合ができない。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ------------------------------------------------------------------ 実行


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LINE Messaging API でメッセージを送る（課題9）"
    )
    parser.add_argument("--text", required=True, help="送る本文")
    parser.add_argument(
        "--results",
        default=DEFAULT_RESULTS,
        help="送信の記録を書くファイル（verify_push.py が読む）",
    )
    parser.add_argument(
        "--env",
        default=str(ROOT / env_file.ENV_FILENAME),
        help=f"資格情報の {env_file.ENV_FILENAME}",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: Callable | None = None,
) -> int:
    args = parse_args(argv)

    env = env_file.load(args.env)
    token = line_auth.read_channel_access_token(env)
    to = line_auth.read_user_id(env)
    secrets = (token,)

    session = (
        session_factory(token) if session_factory else line_auth.build_session(token)
    )

    # **手元で分かることは、通信の前に済ませる。** 本文が空なら1回も叩かない。
    payload = build_payload(to=to, text=args.text)

    # **送る前に「自分が誰か」を確かめる。** ここを後回しにすると、
    # 「トークンが無効」と「宛先が違う」が同じ 400 に見える。
    info = line_auth.fetch_bot_info(session, secrets=secrets)
    print(f"チャネル: {info.display_name} ({info.basic_id})  chatMode={info.chat_mode}")

    usage_before = fetch_usage(session, secrets=secrets)
    response = push(session, payload, secrets=secrets)
    sent = read_send_result(response)
    usage_after = fetch_usage(session, secrets=secrets)

    print(f"送信しました: message_id={sent.message_id}")
    print(f"通数: {usage_before} -> {usage_after}")
    if sent.request_id:
        print(f"x-line-request-id: {sent.request_id}")

    record = build_record(
        info=info,
        to=to,
        text=args.text,
        message_id=sent.message_id,
        request_id=sent.request_id,
        usage_before=usage_before,
        usage_after=usage_after,
    )
    write_record(args.results, record)
    # **画面に出すパスはリポジトリ相対にする。** 絶対パスはホームディレクトリ名を含み、
    # それが実行画面のスクリーンショットに写って公開される。課題3で
    # 「mutate.py から実ユーザー名を除く」と決めたのに、**画面出力だけ素通りしていた**
    # （2026-08-19 に撮ったスクショで発見）。リポジトリの外を指す場合はそのまま出す。
    print(f"記録: {_display_path(args.results)}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except (SendError, line_auth.LineError, env_file.EnvFileError) as error:
        print(f"失敗: {error}")
        raise SystemExit(1) from error
