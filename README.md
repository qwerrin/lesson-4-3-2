# lesson-4-3-2 — API 連携実践課題

AIエンジニア講座 Section 4-3「API 連携実践」の課題（全10本）。
課題ごとに `taskN/` を作る。認証ファイルはリポジトリのルートで共有する。

| # | 課題 | フォルダ | 状態 |
|---|---|---|---|
| 1 | Google ドライブ API | [`task1/`](task1/README.md) | 実装・テスト64件・実機で MD5 まで照合済み |
| 2 | Google ドキュメント API | [`task2/`](task2/README.md) | 実装・テスト143件・実機で本文と段落まで照合済み |
| 3 | Google ミート API | — | 未着手 |
| 4 | Zoom API | — | 未着手 |
| 5 | Gmail API | — | 未着手 |
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

`credentials.json` / `token.json` は `.gitignore` で追跡対象から外してある。
このリポジトリは public なので、**push 前に必ず確認する**。

```bash
git check-ignore -v credentials.json token.json
```

出力が無い（＝無視されていない）場合は絶対に push しない。

## テスト

```bash
.venv\Scripts\python.exe -m pytest -v --no-header
```
