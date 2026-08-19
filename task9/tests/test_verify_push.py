"""task9/verify_push.py のテスト。

**このモジュールのいちばん重要な仕事は「確認できないことを確認できないと言う」こと。**

課題1〜8の verify は、送った文字列を別経路で取り直して突き合わせていた。
LINE にはその経路が無い。だから「全部 OK」と出したら、それは
**送った文面が届いたことを意味しない**。

ここで素直に「全部 OK」とだけ出すと、次のような読み違いが起きる:

    検査結果: すべて一致しました  →  読み手は「文面が届いた」と思う

実際に確かめたのは「1通ぶん通数が増えた」「意図したチャネルだった」の2つだけである。
**確かめていないことを、確かめた顔で出さない。** そのために、検査の合否とは別に
「確認できないこと」の一覧を必ず表示し、全部 OK のときも消えないようにする。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import verify_push  # noqa: E402
from common import line_auth  # noqa: E402


BOT_USER_ID = "U" + "1" * 32
BASIC_ID = "@fake0000"
MESSAGE_ID = "627984934547751122"


def record(**overrides):
    base = {
        "bot": {
            "user_id": BOT_USER_ID,
            "basic_id": BASIC_ID,
            "display_name": "開発テスト",
            "chat_mode": "bot",
            "mark_as_read_mode": "auto",
        },
        "to_masked": "U8…88",
        "text": "やっほー",
        "message_id": MESSAGE_ID,
        "request_id": "req-1",
        "usage_before": 0,
        "usage_after": 1,
    }
    base.update(overrides)
    return base


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = ""
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("応答が JSON ではありません")
        return self._payload


class FakeSession:
    """URL ごとに別の応答を返す偽セッション。"""

    def __init__(self, *, info=None, consumption=None, quota=None):
        self.headers = {}
        self.info = info if info is not None else {
            "userId": BOT_USER_ID,
            "basicId": BASIC_ID,
            "displayName": "開発テスト",
            "chatMode": "bot",
            "markAsReadMode": "auto",
        }
        self.consumption = consumption if consumption is not None else {"totalUsage": 1}
        self.quota = quota if quota is not None else {"type": "limited", "value": 200}

    def get(self, url, **kwargs):
        if url.endswith("/v2/bot/info"):
            return FakeResponse(payload=self.info)
        if url.endswith("/quota/consumption"):
            return FakeResponse(payload=self.consumption)
        if url.endswith("/v2/bot/message/quota"):
            return FakeResponse(payload=self.quota)
        raise AssertionError(f"想定外の URL: {url}")


# ================================================================== 記録を読む


def test_loads_a_valid_record(tmp_path):
    path = tmp_path / "results.json"
    path.write_text(json.dumps(record(), ensure_ascii=False), encoding="utf-8")

    assert verify_push.load_results(path)["message_id"] == MESSAGE_ID


def test_missing_file_is_a_readable_failure(tmp_path):
    with pytest.raises(verify_push.VerifyError) as error:
        verify_push.load_results(tmp_path / "nope.json")

    assert "send_push.py" in str(error.value)


def test_broken_json_is_a_readable_failure(tmp_path):
    path = tmp_path / "results.json"
    path.write_text("{壊れている", encoding="utf-8")

    with pytest.raises(verify_push.VerifyError):
        verify_push.load_results(path)


def test_json_that_is_not_a_dict_is_a_failure(tmp_path):
    """JSON として正しくても辞書でなければ失敗にする。

    リストが入っていると ``payload.get`` が AttributeError になり、
    **利用者に読めない例外**が出る。課題8で「本文が JSON でないことは実際に起きる」を
    踏んだのと同じ形で、こちらは「JSON ではあるが形が違う」版。
    """
    path = tmp_path / "results.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(verify_push.VerifyError) as error:
        verify_push.load_results(path)

    # **形が違うと名指しできていることまで見る。** リストに対しては
    # 「必須項目が全部ありません」も成立するので、型の検査を消しても素通りする
    # （2026-08-19・ミューテーションで検出）。
    assert "辞書ではありません" in str(error.value)


def test_bot_that_is_not_a_dict_is_a_failure(tmp_path):
    path = tmp_path / "results.json"
    path.write_text(json.dumps(record(bot="開発テスト"), ensure_ascii=False), encoding="utf-8")

    with pytest.raises(verify_push.VerifyError):
        verify_push.load_results(path)


@pytest.mark.parametrize("missing", ["message_id", "usage_before", "usage_after", "bot"])
def test_record_missing_a_required_key_is_a_failure(tmp_path, missing):
    """**欠けた項目を「無いので OK」にしない。**

    課題8で踏んだ「0 件を不一致として捕まえる」と同じ。項目が無いまま
    検査を組むと、その検査は必ず通る（比べる相手がいないので）。
    """
    data = record()
    data.pop(missing)
    path = tmp_path / "results.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(verify_push.VerifyError):
        verify_push.load_results(path)


# ================================================================== 手元の検査


def test_usage_delta_of_one_passes():
    checks = verify_push.build_local_checks(record())

    assert verify_push.all_ok(checks)


def test_usage_delta_of_zero_fails():
    """**通数が increase しなかったら失敗。**

    HTTP 200 が返っても通数が動かない状態は「送ったつもり」であって、
    LINE 側が送信対象として数えていない。ここが唯一の「実際に送られたか」の手掛かり。
    """
    checks = verify_push.build_local_checks(record(usage_before=1, usage_after=1))

    assert not verify_push.all_ok(checks)


def test_usage_delta_of_two_fails():
    """2 でも失敗にする。**多ければ良いわけではない。**

    1通しか送っていないのに 2 増えたなら、別の送信が混ざっている。
    「増えた」ではなく「1 増えた」で見る。
    """
    checks = verify_push.build_local_checks(record(usage_before=0, usage_after=2))

    assert not verify_push.all_ok(checks)


def test_usage_going_backwards_fails():
    """**増分は向きまで見る。** 減っていたら不一致。

    ``after - before`` を ``abs()`` で包んでも上の2件は通ってしまう
    （0 と 2 はどちらも符号を変えても同じ）。**減少を1件用意して初めて向きが縛れる**
    （2026-08-19・ミューテーションで検出）。通数が減るのは記録か対象チャネルが違う証拠。
    """
    checks = verify_push.build_local_checks(record(usage_before=2, usage_after=1))

    assert not verify_push.all_ok(checks)


def test_missing_chat_mode_fails():
    """``chatMode`` が空なら不合格。

    これは「意図したチャネルを叩いたか」を後から人が確かめるための記録で、
    空のまま合格にすると**記録として役に立たない**。
    """
    data = record()
    data["bot"] = {**data["bot"], "chat_mode": ""}

    assert not verify_push.all_ok(verify_push.build_local_checks(data))


def test_non_numeric_message_id_fails():
    checks = verify_push.build_local_checks(record(message_id="abc"))

    assert not verify_push.all_ok(checks)


def test_basic_id_without_at_sign_fails():
    data = record()
    data["bot"] = {**data["bot"], "basic_id": "687jseqd"}

    assert not verify_push.all_ok(verify_push.build_local_checks(data))


def test_empty_text_fails():
    assert not verify_push.all_ok(
        verify_push.build_local_checks(record(text=""))
    )


def test_unmasked_destination_in_the_record_fails():
    """記録に生の宛先IDが残っていたら失敗にする。

    ``results.json`` は public リポジトリに入る。**伏せ忘れを検査で捕まえる**。
    課題8で「伏せ字は呼び出し側に任せない」と決めたのの、記録版。
    """
    checks = verify_push.build_local_checks(record(to_masked="U" + "8" * 32))

    assert not verify_push.all_ok(checks)


# ================================================================== 遠隔の検査


def test_remote_checks_pass_against_matching_channel():
    checks = verify_push.build_remote_checks(FakeSession(), record())

    assert verify_push.all_ok(checks)


def test_remote_check_fails_when_basic_id_differs():
    """**別のチャネルを叩いていたら気づく。**

    チャネルを2つ作って ``.env`` を差し替え忘れた、という事故はここでしか出ない。
    送信そのものは成功してしまうため。
    """
    session = FakeSession(info={
        "userId": BOT_USER_ID, "basicId": "@other000",
        "displayName": "別", "chatMode": "bot", "markAsReadMode": "auto",
    })

    assert not verify_push.all_ok(
        verify_push.build_remote_checks(session, record())
    )


def test_remote_check_fails_when_bot_user_id_differs():
    session = FakeSession(info={
        "userId": "U" + "9" * 32, "basicId": BASIC_ID,
        "displayName": "開発テスト", "chatMode": "bot", "markAsReadMode": "auto",
    })

    assert not verify_push.all_ok(
        verify_push.build_remote_checks(session, record())
    )


def test_remote_usage_below_the_record_fails():
    """いま数えた通数が記録より少ないのはおかしい。

    通数は月内で単調に増える。**減っていたら、記録か対象チャネルのどちらかが違う。**
    「一致」ではなく「以上」で見るのは、検証までの間に別の送信が入りうるため。
    """
    session = FakeSession(consumption={"totalUsage": 0})

    assert not verify_push.all_ok(
        verify_push.build_remote_checks(session, record())
    )


def test_remote_usage_that_is_not_an_integer_fails():
    """通数が整数として読めなければ不合格。**「読めなかった」を合格に倒さない。**

    課題6で踏んだ「空が正常値の欄はバグが静かな側に倒れる」と同じ形。
    0 も正当な通数なので、読めなかったことを 0 や True に寄せてはいけない。
    """
    session = FakeSession(consumption={"totalUsage": "たくさん"})

    assert not verify_push.all_ok(
        verify_push.build_remote_checks(session, record())
    )


def test_remote_usage_above_the_record_passes():
    session = FakeSession(consumption={"totalUsage": 5})

    assert verify_push.all_ok(
        verify_push.build_remote_checks(session, record())
    )


def test_remote_checks_use_a_second_call_not_the_record():
    """**記録の中の値どうしを比べない。**

    記録だけで閉じると「自分で書いた値を自分で確かめる」トートロジーになる
    （課題4・6・7・8で繰り返し踏んだ形）。遠隔検査は必ず API を叩く。
    """
    calls = []

    class Counting(FakeSession):
        def get(self, url, **kwargs):
            calls.append(url)
            return super().get(url, **kwargs)

    verify_push.build_remote_checks(Counting(), record())

    assert any(u.endswith("/v2/bot/info") for u in calls)
    assert any(u.endswith("/quota/consumption") for u in calls)


# ================================================================== 確認できないこと


def test_unverifiable_notes_are_never_empty():
    """**「確認できないこと」の一覧は必ず存在する。**

    空にできる作りにすると、いつか空になって「全部確認した」に見える。
    LINE に読み返す API が無い以上、この一覧が消えることはない。
    """
    assert verify_push.unverifiable_notes()


def test_unverifiable_notes_mention_the_message_body():
    notes = " ".join(verify_push.unverifiable_notes())

    assert "本文" in notes


def test_report_shows_unverifiable_even_when_everything_passes():
    """**全部 OK のときも「未確認」を消さない。ここが課題9の主題。**

    合格のときだけ注記が消えると、読み手が見るのは常に「すべて一致しました」になる。
    確かめていないことは、成功したときにこそ書く。
    """
    checks = verify_push.build_local_checks(record())
    report = verify_push.format_report(checks)

    assert verify_push.all_ok(checks)

    # **注記の本体を1件ずつ突き合わせる。**
    # 最初はこう書いていた::
    #
    #     assert "確認できない" in report
    #     assert "本文" in report
    #
    # これは**両方とも無関係な場所で満たされる**。前者は見出し行
    # 「--- この検査で確認できないこと ---」に、後者は検査ラベル「本文が空でない」に
    # 含まれていた。``for note in unverifiable_notes()`` を ``for note in ()`` に
    # 変えても55件全部通った（2026-08-19・ミューテーションで検出）。
    # **守るために書いたテストが、守っていなかった。**
    for note in verify_push.unverifiable_notes():
        assert note in report


def test_report_marks_failures_visibly():
    checks = verify_push.build_local_checks(record(usage_after=0, usage_before=0))
    report = verify_push.format_report(checks)

    assert "NG" in report


def test_report_contains_every_check_label():
    checks = verify_push.build_local_checks(record())
    report = verify_push.format_report(checks)

    for check in checks:
        assert check.label in report


# ================================================================== 引数


def test_results_path_has_a_default():
    assert verify_push.parse_args([]).results.endswith("results.json")


def test_local_only_flag_exists():
    """ネットワークを使わずに手元の検査だけ回せるようにする。

    トークンが無い環境（採点者の手元など）でも、記録の形は確かめられる。
    """
    assert verify_push.parse_args(["--local-only"]).local_only is True
