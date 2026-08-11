# 課題1: Google ドライブ API

> Google Drive API を利用して、ローカルのファイルを Google ドライブにアップロードするコードを作成してください。

ローカルのファイルを1つ受け取り、Google ドライブにアップロードして、
できたファイルの ID とリンクを表示する。

## 先に済ませること（Google Cloud 側・手作業）

コードを動かす前に、Google Cloud の設定が要る。**ここは AI に任せられない**
（資格情報の入力と同意はアカウント本人の操作でしかできない）。

1. [Google Cloud コンソール](https://console.cloud.google.com/) でプロジェクトを作る
2. 「API とサービス」→「ライブラリ」で **Google Drive API** を検索して有効にする
3. OAuth 同意画面を設定する
   - User Type は **外部**
   - アプリ名・ユーザーサポートメール・デベロッパーの連絡先を埋める
   - スコープはここで足さなくてよい（プログラムが要求する）
   - **テストユーザーに自分の Google アカウントを追加する**。ここを忘れると同意画面で弾かれる
4. 「API とサービス」→「認証情報」→「認証情報を作成」→ **OAuth クライアント ID**
   - アプリケーションの種類は **デスクトップアプリ**
5. 作られたクライアントの JSON をダウンロードし、**リポジトリのルートに `credentials.json`** という名前で置く

`credentials.json` と `token.json` は `.gitignore` に入れてある。
このリポジトリは public なので、**追跡されていないことを push 前に必ず確認する**。

```bash
git check-ignore -v credentials.json token.json
```

## 環境

```bash
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install pytest google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

## 実行方法

**すべてリポジトリのルート（`lesson-4-3-2/`）で実行する。**
`.venv\Scripts\python.exe` を直接呼ぶ。素の `python` は venv の外を掴む。

```bash
.venv\Scripts\python.exe task1\drive_upload.py task1\data\sample.txt
```

初回だけ既定のブラウザが開いて Google の同意画面が出る。
許可すると `token.json` ができて、次からはブラウザが開かない。

成功するとこう出る（ID とリンクは実行ごとに変わる）。

```
アップロードしました
  ファイル名: sample.txt
  ファイルID: 1AbCdEfGhIjKlMnOpQrStUvWxYz
  リンク    : https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/view
```

### オプション

| オプション | 意味 |
|---|---|
| `--name` | ドライブ上での名前を変える（既定: 元のファイル名） |
| `--folder-id` | 保存先フォルダの ID（既定: マイドライブ直下） |
| `--credentials` | OAuth クライアントの JSON（既定: `credentials.json`） |
| `--token` | トークンの保存先（既定: `token.json`） |
| `--full-drive-scope` | 既にあるフォルダに入れるときに指定する |

```bash
.venv\Scripts\python.exe task1\drive_upload.py task1\data\sample.txt --name 提出用.txt
```

## テスト

```bash
.venv\Scripts\python.exe -m pytest task1\tests -v --no-header
```

64件。分類は3つ。

| 層 | 見ているもの |
|---|---|
| 送る前 | ファイルの実在・名前・MIME タイプ・親フォルダの組み立て |
| API の呼び方 | 偽の service を渡し、`files().create` に何を渡したかを記録して照合する |
| 画面と終了コード | `main` が結果を**印字する**こと、失敗時に 1 を返すこと |

本物の Google には繋がない。認証が本人の手作業でしか通せないため。
その代わり「呼び方が正しいか」をここで全部固定する。

## テストで閉じない穴は、実物を1回読んで閉じた

偽の `service` で固定できるのは**呼び方まで**。`fields` の綴りが違っていても、
MIME タイプが嘘でも、64件は全部通ってしまう。

そこで `verify_upload.py` を作った。アップロードしたファイルを
**Drive から読み返して、ローカルの実体と突き合わせる**。読むだけで何も変更しない。

```bash
.venv\Scripts\python.exe task1\verify_upload.py <ファイルID> task1\data\sample.txt
```

実測（2026-08-11）:

```
OK  ファイル名が一致  sample.txt / sample.txt
OK  MIME タイプが一致  text/plain / text/plain
OK  サイズが一致  140 / 140
OK  中身が一致（MD5）  e817d9e2a19b034ed5f0b64859e409a6 / e817d9e2a19b034ed5f0b64859e409a6
OK  ゴミ箱に入っていない
OK  リンクが取得できる  https://drive.google.com/file/d/1E-kscfR5W9BlYTo0jTdfIObhLczisGBA/view?usp=drivesdk
```

**サイズだけだと「同じ長さの別データ」を見逃す。** MD5 まで見て初めて
中身が化けていないことが言える。テストにも
「サイズは同じで MD5 だけ違う」ケースを入れてある。

もうひとつ気をつけたのは、**値が返ってこなかった項目を OK にしないこと**。
Google ドキュメント形式のファイルには `md5Checksum` が無い。
`meta.get("md5Checksum", ローカルのMD5)` のように既定値を入れると、
照合できなかったケースが全部「一致」になり、確かめた気になるだけになる。

## つまずいたところ

### 最小権限を選ぶと、権限エラーが 404 で返る

既定のスコープは `drive.file`（このプログラムが作ったファイルだけ触れる）。
この状態で他人が作った既存フォルダを `--folder-id` に指定すると、
**403 ではなく 404「File not found」が返る**。権限が無いことすら教えてもらえない。

エラーメッセージにこの事情と `--full-drive-scope` の案内を入れてある。
ID の打ち間違いを疑って何度も見直す、という時間を潰すため。

### `parents` は空リストを送ってはいけない

保存先フォルダを指定しないとき、`{"parents": []}` を送ると
「マイドライブ直下」ではなく「親を消す」意味に取られる。**キーごと省く。**

### token.json に `expiry` が無いと、常に期限切れ扱いになる

`google-auth` は `expiry` が保存されていない場合、
「今 − リフレッシュ猶予」を期限として埋める。つまり**読み込んだ瞬間から期限切れ**。

テストで「有効なトークンならブラウザを開かない」を書いたとき、
`expiry` を省いたせいで実際にはリフレッシュ経路を通っていた。
**実装を書く前にテストを走らせたので気づけた**（テストのほうが間違っていた）。

### 同意画面が「テスト」のままだと、7日でトークンが切れる

OAuth 同意画面の公開ステータスが「テスト」の間、Google は
**リフレッシュトークンを7日で失効させる**。`token.json` があるのに
`invalid_grant` で失敗したらこれ。`token.json` を消してもう一度実行すれば、
同意画面から取り直せる。

```bash
del token.json
.venv\Scripts\python.exe task1\drive_upload.py task1\data\sample.txt
```

### `from_authorized_user_file` に scopes を渡すと権限不足を検出できない

`Credentials.from_authorized_user_file(path, scopes)` は、
ファイルに書かれた実際の権限を**引数で上書きする**。
渡してしまうと「保存済みトークンの権限が足りているか」の判定が常に真になり、
`--full-drive-scope` を付けても同意を取り直さない。引数なしで読む。

## わざと壊して確かめた（25か所・穴ゼロ）

テストが通っていることは、守られている範囲を意味しない。
1か所ずつ壊して、落ちることを確認した。

### drive_upload.py（13か所）

| # | 壊した内容 | 結果 |
|---|---|---|
| 1 | 親フォルダを未指定でも `parents` に付ける | 3件 落ちた |
| 2 | MIME タイプのフォールバックを外す | 2件 落ちた |
| 3 | `resumable` を外す | 1件 落ちた |
| 4 | トークン読み込みで `scopes` を渡す | 1件 落ちた |
| 5 | `main` が結果を印字しない | 1件 落ちた |
| 6 | `webViewLink` を要求しない | 1件 落ちた |
| 7 | フォルダを渡してもアップロードできることにする | 1件 落ちた |
| 8 | エラーからステータスコードを落とす | 1件 落ちた |
| 9 | リフレッシュ後にトークンを保存し直さない | 1件 落ちた |
| 10 | 送る前の存在チェックをやめる | 2件 落ちた |
| 11 | `--name` の前後の空白を落とさない | 2件 落ちた |
| 12 | 既定の資格情報パスを絶対パスにする | 1件 落ちた |
| 13 | `--full-drive-scope` を無視する | 1件 落ちた |

5番と12番は、前の課題で穴になったパターンをそのまま持ち込んだもの。
**組み立てられることと画面に出ることは別**（5番）。
**公開する実行画面に絶対パスを写さない**（12番）。

### verify_upload.py（12か所）

「照合してるフリ」に寄せて壊した。素通りしたら、このスクリプトの存在意義が消える。

| # | 壊した内容 | 結果 |
|---|---|---|
| 1 | MD5 を見ずサイズだけで一致とみなす | 3件 落ちた |
| 2 | MD5 が返らなければ一致扱いにする | 1件 落ちた |
| 3 | サイズが返らなければ一致扱いにする | 1件 落ちた |
| 4 | ゴミ箱の判定をやめて常に OK にする | 1件 落ちた |
| 5 | MIME タイプを自分自身と比べる（トートロジー） | 1件 落ちた |
| 6 | ファイル名の比較をやめる | 3件 落ちた |
| 7 | リンクの有無を見ない | 1件 落ちた |
| 8 | API に `md5Checksum` を要求しない | 1件 落ちた |
| 9 | `all_ok` が常に `True` | 2件 落ちた |
| 10 | `format_checks` が全部 OK と印字する | 2件 落ちた |
| 11 | 食い違っても終了コード 0 を返す | 1件 落ちた |
| 12 | ローカルファイルの実在を見ない | 2件 落ちた |

5番の「自分自身と比べる」は、書いてしまうと目視でまず気づけない形。
`drive_mime == drive_mime` は**必ず真**なので、照合を1行残したまま中身が空になる。
