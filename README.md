# lesson-4-3-2 — API 連携実践課題

AIエンジニア講座 Section 4-3「API 連携実践」の課題（全10本）。
課題ごとに `taskN/` を作る。認証ファイルはリポジトリのルートで共有する。

課題をまたいで使う部品は `common/` に置く。**認証は相手ごとに別ファイルにする**
（`google_auth.py` / `zoom_auth.py` / `youtube_auth.py`）。同意画面とリフレッシュがある
Google の OAuth、毎回取り直す Zoom の Server-to-Server OAuth、公開データを読むだけで
認可する相手がいない YouTube の API キーでは形が違いすぎて、共通の抽象を被せると
どの説明も嘘になる。

課題1・課題2は当時のコード（各自のコピー）のまま残してある。

| # | 課題 | フォルダ | 状態 |
|---|---|---|---|
| 1 | Google ドライブ API | [`task1/`](task1/README.md) | 実装・テスト64件・実機で MD5 まで照合済み |
| 2 | Google ドキュメント API | [`task2/`](task2/README.md) | 実装・テスト143件・実機で本文と段落まで照合済み |
| 3 | Google ミート API | [`task3/`](task3/README.md) | 実装・テスト120件・実機で参加リンクと整合まで照合済み |
| 4 | Zoom API | [`task4/`](task4/README.md) | 実装・テスト153件・実機で作成と読み返し照合まで確認済み |
| 5 | Gmail API | [`task5/`](task5/README.md) | 実装・テスト176件・実機で送信と読み返し照合まで確認済み |
| 6 | YouTube API | [`task6/`](task6/README.md) | 実装・テスト205件・実機で検索と別エンドポイントでの照合まで確認済み |
| 7 | Slack API | [`task7/`](task7/README.md) | 実装・テスト142件・実機で投稿と読み返し照合まで確認済み |
| 8 | Discord | [`task8/`](task8/README.md) | 実装・テスト163件・実機で Bot と Webhook の2経路を照合まで確認済み |
| 9 | LINE Messaging API | [`task9/`](task9/README.md) | 実装・テスト118件・実機で送信と**別エンドポイントでの通数照合**まで確認済み（**本文の読み返しは API が無いので目視**） |
| 10 | 連携した API に機能を追加 | [`task10/line/`](task10/line/README.md) ／ [`task10/discord/`](task10/discord/README.md) ／ [`task10/probe/`](task10/probe/block_probe.py) | LINE側: 実装・テスト181件・わざと壊す検査73か所すべて kill・**実機で送信と照合14項目まで確認済み**（本文の到達は目視）／ Discord側: 実装・テスト134件・わざと壊す検査95か所すべて kill・**実機で3本を流し読み返しの照合24項目すべて一致**／ 探り道具: **ブロック中の 404 を3点測定で確定**（テスト11件・わざと壊す検査10か所すべて kill） |

## セットアップ

```bash
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

コマンドは**すべてこのルートで**、`.venv\Scripts\python.exe` を直接呼んで実行する。
素の `python` は venv の外を掴む。

## 資格情報の扱い

`credentials.json` / `token.json` / `.env` は `.gitignore` で追跡対象から外してある。
このリポジトリは public なので、**push 前に必ず確認する**。

```bash
git check-ignore -v credentials.json token.json .env task5/token-send.json task5/token-verify.json
```

出力が無い（＝無視されていない）場合は絶対に push しない。

**課題9だけは `.env` ファイルを使う**（課題4〜8 は PowerShell の環境変数）。
見本の `.env.example` は**逆に追跡されていないといけない**——値の入れ方が分かる
ファイルがリポジトリに無いと、採点者が動かせない。

```bash
git check-ignore -q .env          && echo ".env は無視: OK"
git check-ignore -q .env.example  || echo ".env.example は追跡: OK"
```

> `.gitignore` の `.env.*` が `.env.example` まで巻き込んでいた（2026-08-19 に発見）。
> **無視されるべきものと、されてはいけないものが同じ行で決まる。**
> `!.env.example` の例外を足して直してある。

課題5は**トークンを2本に分けている**（`task5/token-send.json` / `task5/token-verify.json`）。
送信と読み取りでスコープが違い、1本を共有すると `common/google_auth.py` が
権限の足りないトークンを捨てて取り直すため、実行のたびに同意画面が出る。
`.gitignore` の `token*.json` はスラッシュを含まないので、どの階層でも効く。

Zoom（課題4）はファイルではなく環境変数で渡す。**リポジトリに置く場所を作らない**のが
いちばん漏れにくい。

```powershell
$env:ZOOM_ACCOUNT_ID="..."; $env:ZOOM_CLIENT_ID="..."; $env:ZOOM_CLIENT_SECRET="..."
```

課題6（YouTube）も環境変数で渡す。

```powershell
$env:YOUTUBE_API_KEY="..."
```

**API キーは他の資格情報と危険の出かたが違う。URL のクエリに載る**ので、
失敗したリクエストの例外を印字しただけで漏れる（`str(HttpError)` に URI が入る）。
`common/youtube_auth.py` の `redact()` を通してから表に出すこと。

漏れていないかは、README ではなく**リポジトリ全体**を機械で見る。

```bash
.venv\Scripts\python.exe task6\tools\check_docs.py
```

## テスト

```bash
.venv\Scripts\python.exe -m pytest -v --no-header
```
