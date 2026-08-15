# lesson-4-3-2 — API 連携実践課題

AIエンジニア講座 Section 4-3「API 連携実践」の課題（全10本）。
課題ごとに `taskN/` を作る。認証ファイルはリポジトリのルートで共有する。

課題をまたいで使う部品は `common/` に置く。**認証は相手ごとに別ファイルにする**
（`google_auth.py` / `zoom_auth.py`）。同意画面とリフレッシュがある Google と、
毎回取り直す Zoom の Server-to-Server OAuth では形が違いすぎて、共通の抽象を
被せるとどちらの説明も嘘になる。

課題1・課題2は当時のコード（各自のコピー）のまま残してある。

| # | 課題 | フォルダ | 状態 |
|---|---|---|---|
| 1 | Google ドライブ API | [`task1/`](task1/README.md) | 実装・テスト64件・実機で MD5 まで照合済み |
| 2 | Google ドキュメント API | [`task2/`](task2/README.md) | 実装・テスト143件・実機で本文と段落まで照合済み |
| 3 | Google ミート API | [`task3/`](task3/README.md) | 実装・テスト120件・実機で参加リンクと整合まで照合済み |
| 4 | Zoom API | [`task4/`](task4/README.md) | 実装・テスト153件・実機で作成と読み返し照合まで確認済み |
| 5 | Gmail API | [`task5/`](task5/README.md) | 実装・テスト176件・実機で送信と読み返し照合まで確認済み |
| 6 | YouTube API | — | 未着手 |
| 7 | Slack API | — | 未着手 |
| 8 | Discord | — | 未着手 |
| 9 | LINE Messaging API | — | 未着手 |
| 10 | 連携した API に機能を追加 | — | 未着手 |

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

課題5は**トークンを2本に分けている**（`task5/token-send.json` / `task5/token-verify.json`）。
送信と読み取りでスコープが違い、1本を共有すると `common/google_auth.py` が
権限の足りないトークンを捨てて取り直すため、実行のたびに同意画面が出る。
`.gitignore` の `token*.json` はスラッシュを含まないので、どの階層でも効く。

Zoom（課題4）はファイルではなく環境変数で渡す。**リポジトリに置く場所を作らない**のが
いちばん漏れにくい。

```powershell
$env:ZOOM_ACCOUNT_ID="..."; $env:ZOOM_CLIENT_ID="..."; $env:ZOOM_CLIENT_SECRET="..."
```

## テスト

```bash
.venv\Scripts\python.exe -m pytest -v --no-header
```
