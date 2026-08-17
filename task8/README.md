# 課題8 Discord API — 特定のサーバーのチャンネルに自動通知を送る

> Discord API を使って、ボットを作成し、特定のサーバー内のチャンネルに自動通知を送る機能を実装してください。
>
> 【ヒント】webhook を使うと簡単です。

## 要件とヒントが噛み合っていない、という話から始める

要件は「**ボットを作成し**」と言っている。ヒントは「**webhook を使うと簡単**」と言っている。
この2つは同じものではない。webhook はボットではなく、**チャンネルに紐づいた投稿専用の URL** で、
アプリもボットユーザーも作らずにメッセージを送れる。

そこで**両方を実装して、違いを実測で確かめる**ことにした。片方だけ作ると、
「なぜそちらを選んだのか」を書けない。

| | Bot Token | Webhook |
|---|---|---|
| 要件の「ボットを作成し」 | 満たす | 満たさない |
| 認証 | `Authorization: Bot <token>` ヘッダ | **不要**（URL 自体が資格情報） |
| 送信先 | トークンが見える**どのチャンネルにも**送れる | **その webhook を作ったチャンネルだけ** |
| 既定の応答 | 作成したメッセージが返る | **204 No Content**（何も返らない） |
| 読み返し | `GET /channels/{id}/messages/{id}` | `GET /webhooks/{id}/{token}/messages/{id}`（自分が送った分だけ） |

## Discord 側の準備（全部この課題の利用者の手作業）

トークンや URL を AI に入力させることはできないので、ここは必ず人が通る工程になる。

> **画面の文言は、この文書より実物が正しい。**
> 課題7（Slack）では公式が案内していない「Blank app」への改名を実物で踏んだ。
> 下の手順は 2026-08-17 に公式ドキュメントで確認した内容だが、違っていたら実物に従う。

### 1. サーバー（ギルド）を作る

**課題専用に新規作成する。** 既存のサーバーを使うと、公開するスクリーンショットに
他人の名前・アイコン・チャンネル名が写る。課題7で Slack のワークスペースを
新規作成したのと同じ理由で、**構造的に事故が起きない側を選ぶ**。

### 2. アプリを作る

1. <https://discord.com/developers/applications> を開く
2. 「**Create App**」→ 名前を入れて「**Create**」

### 3. Bot Token を取る

1. 左メニューの「**Bot**」ページを開く
2. 「**Reset Token**」を押してトークンを発行し、コピーする

> 公式ドキュメントの警告をそのまま守る——
> トークンは API リクエストを認可するもので、**極めて機微**。
> 共有しない、バージョン管理に入れない。**この課題では環境変数で渡す。**

### 4. サーバーに追加する（スコープと権限）

1. 左メニューの「**Installation**」ページを開く
2. **Installation Contexts** で「**Guild Install**」を有効にする
3. **Install Link** で「**Discord Provided Link**」を選ぶ
4. 現れた **Default Install Settings** の Guild Install に、スコープ `bot` を追加する
5. `bot` を選ぶと **Permissions** のメニューが出るので、次の3つを付ける

   | 権限 | 何に使うか |
   |---|---|
   | **View Channels** | チャンネルを見る（投稿・読み返しの両方の前提） |
   | **Send Messages** | `POST /channels/{channel.id}/messages` |
   | **Read Message History** | `GET /channels/{channel.id}/messages/{message.id}` |

6. 生成された **Install Link** を開き、作ったサーバーを選んで追加する

> **`Read Message History` を飛ばすと、エラーではなく「何も返らない」。**
> 公式ドキュメントは「If the current user is missing the `READ_MESSAGE_HISTORY`
> permission in the channel, then no messages will be returned.」と書いている。
> **落ちないほうが厄介**で、読み返しが「0件だったので照合する対象がありません」に化ける。

> **MESSAGE CONTENT 特権インテントは要らない。**
> 公式ドキュメントは、インテントを持たないアプリは `content` が空で返るとしたうえで、
> 例外に「**Content in messages that an app sends**」を挙げている。
> この課題で読み返すのは**自分が送ったメッセージ**なので対象外。
> ただし「要らないはず」で書かず、**空で返ってきたら失敗として落とす**。

### 5. チャンネル ID を取る

1. Discord 本体の ユーザー設定 → 詳細設定 → **開発者モード** を ON
2. 対象チャンネルを右クリック →「**チャンネル ID をコピー**」

**チャンネルは ID で指定する。** ID は変わらないが名前は変わる。

### 6. Webhook URL を取る

対象チャンネルの設定 → 連携サービス → ウェブフック → 新しいウェブフックを作り、
「**ウェブフック URL をコピー**」する。

> **この URL は、それ自体が資格情報。**
> `Authorization` ヘッダを付けずに投稿できる＝**URL を知っている人は誰でも、
> そのチャンネルに好きな内容を投稿できる**。Bot Token と危険度は変わらないのに、
> 「URL」という見た目のせいで軽く扱われやすい。
> **スクリーンショットにも記事にも絶対に写さない。**

### 環境変数

トークンも URL も環境変数で渡す。**コマンドライン引数は履歴と `ps` に残る**ので使わない。

```powershell
$env:DISCORD_BOT_TOKEN = "..."
$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/..."
```

## 作ったもの

| ファイル | 役割 |
|---|---|
| `task8/send_notification.py` | 通知を1件送る。`--via bot` は `POST /channels/{id}/messages`、`--via webhook` は `POST /webhooks/{id}/{token}?wait=true` |
| `task8/verify_notification.py` | 送った通知を読み直して突き合わせる（**読むだけ**） |
| `common/discord_auth.py` | Bot Token と Webhook URL の読み込み・セッション組み立て・`GET /users/@me`・エラーの訳し分け |
| `task8/tools/mutate.py` | 実装を1か所ずつ壊して、テストが落ちることを確認する |
| `task8/tools/check_docs.py` | この README とコードを機械照合する |

**ライブラリは `requests` だけ**（`requirements.txt` に既にある 2.34.2）。Slack では公式の
`slack_sdk` を使ったが、**Discord は公式の Python SDK を出していない**。よく使われる
`discord.py` はコミュニティ製で、常時接続の Gateway を前提にしている。今回は
「1件送って読み返す」だけなので、常駐させる理由がない。

## 使い方

リポジトリのルートで実行する。資格情報は環境変数で渡す（**コマンドライン引数は履歴と `ps` に残る**）。

```powershell
$env:DISCORD_BOT_TOKEN = "..."

.venv\Scripts\python.exe task8\send_notification.py --via bot `
    --guild <サーバーID> --channel <チャンネルID> `
    --text "課題8: Discord への自動通知テスト & <embed> 無し" `
    --json-out task8/results-bot.json

.venv\Scripts\python.exe task8\verify_notification.py --results task8/results-bot.json `
    --guild <サーバーID> --channel <チャンネルID> `
    --expect-text "課題8: Discord への自動通知テスト & <embed> 無し"
```

Webhook 経路は `--via webhook` に変えて、`$env:DISCORD_WEBHOOK_URL` を設定する。
結果ファイルは経路ごとに分ける（`results-bot.json` / `results-webhook.json`）。

**本文に `&` と `<>` をわざと入れてある。** 課題7では Slack がこれらを HTML エンティティに
変換して保存していた。Discord も同じかどうかは**実機で確かめる**（和文だけで試すと、
変換があっても永久に気づけない）。

## 設計 — 読み返しの物差しをどこから取るか

送信が成功しても、それは「API が 2xx を返した」までしか意味しない。
**同じ応答の中で値どうしを比べるのはトートロジーで、何も確かめていない。**
そこで物差しを全部、応答の外から取る。

| 何を確かめるか | 物差しの出どころ |
|---|---|
| サーバー・チャンネル・本文 | **人間がコマンドラインで渡す**（`--guild` / `--channel` / `--expect-text`） |
| メッセージID | 送信時に記録した値と、読み返した値を突き合わせる |
| 投稿者 | `GET /users/@me`（Bot）／`GET /webhooks/{id}/{token}`（Webhook）。**どちらもメッセージとは別のエンドポイント** |
| チャンネルの所属サーバー | `GET /channels/{id}`。**メッセージの応答に `guild_id` は入らない** |

最後の1行がこの課題で増えた観点。要件が「**特定のサーバー内の**チャンネル」と言っているのに、
メッセージの応答だけ見ても、そのチャンネルがどのサーバーのものかは分からない。

### 送る前に確かめられることは、送る前に確かめる

投稿は消せるが、**通知が飛ぶのは取り消せない**。だから両経路とも、送信の前に1回 GET する。

- `--via bot`: `GET /channels/{id}` で `guild_id` が `--guild` と一致するか
- `--via webhook`: `GET /webhooks/{id}/{token}` で、その URL がどのチャンネルを向いているか

**Webhook の宛先は URL 側で決まる。** `--channel` に何を書いても送り先は変わらないので、
食い違っていたら送らずに止める。

## 公式ドキュメントで確認したこと（2026-08-17）

**実測ではなくドキュメントの記載**。実機で確かめた結果は下の「実測でわかったこと」に分けて書く。

- **User-Agent は必須。** `DiscordBot ($url, $versionNumber)` の形を要求し、妥当な
  User-Agent が無いリクエストは「may be blocked and return a Cloudflare error」。
  `requests` の既定 UA では要件を満たさない。**付け忘れても手元のテストは通る。**
- **snowflake は文字列で返る。** 「64bit なので、整数の桁が足りない言語で溢れるのを防ぐため」。
  課題7の「`ts` を float にすると別のメッセージを指す」と同型。
- **`READ_MESSAGE_HISTORY` が無いと、エラーではなく空。**
  「If the current user is missing the `READ_MESSAGE_HISTORY` permission in the channel,
  then no messages will be returned.」**落ちてくれないほうが厄介。**
- **`MESSAGE_CONTENT` 特権インテントの制限は HTTP API にも及ぶ。**
  「HTTP API restrictions are independent of Gateway restrictions.」
  ただし例外に「**Content in messages that an app sends**」があるので、
  自分が送ったメッセージの読み返しは対象外。**「対象外のはず」を根拠に空を素通りさせない。**
- **`allowed_mentions` の既定は経路によって違う。** 通常のメッセージは
  `{"parse": ["users","roles","everyone"]}` 相当、「In interactions and webhooks,
  only user mentions are parsed」。**既定に頼ると経路を変えた日に挙動が変わる**ので、
  このスクリプトは常に明示する（既定で全抑止）。
- **Webhook は認証不要。** Execute Webhook も Get Webhook Message も「the webhook token
  alone is sufficient」。つまり **URL を知っている人は誰でもそのチャンネルに投稿できる**。
- **`wait` の既定は `false`** で、そのときの応答は **204 No Content**。
  「waits for server confirmation of message send before response, and returns the created
  message body」が `wait=true` の説明。**返らないと message id が無く、読み返せない。**
- **レート制限：全 Bot 共通で毎秒 50 リクエスト。** 429 の本文の `retry_after` は**秒**（小数あり）。
  ミリ秒と取り違えると待ち時間が 1000 倍ずれる。
- **`content` の最大長は、読んだリファレンスの範囲では明記が見つからなかった。**
  なので**この実装は長さを自前で検査しない**。確かめていない数字を定数に置くくらいなら、
  API が返す `50035` を訳して見せるほうが正しい。

## 実測でわかったこと（2026-08-17）

上の「公式ドキュメントで確認したこと」は**書いてあったこと**で、こちらは**走らせて見たこと**。
分けてあるのは、ドキュメントに書いてあることと自分の環境で起きることが別だから
（課題4で「1ページの空振りで『書かれていない』と決めない」を踏んだのの裏返しで、
今度は「書いてあるから起きる」と決めない）。

### 実行した2本

| | `--via bot` | `--via webhook` |
|---|---|---|
| サーバー | `1538970587412828163` | 同じ |
| チャンネル | `1538970588784361474` | 同じ |
| メッセージID | `1538979979424174283` | `1538980232533647401` |
| 投稿者ID | `1538970817512214568`（Bot `test-bot`） | `1538973374376714306`（Webhook `Captain Hook`） |
| 読み返しに使ったエンドポイント | `GET /channels/{id}/messages/{id}` | `GET /webhooks/{id}/{token}/messages/{id}` |
| 照合 | **13項目すべて一致** | **13項目すべて一致** |

### 1. Discord は本文を変換しないで保存する（Slack との違い）

本文に `&` と `<embed>` をわざと入れて送り、読み返して**そのまま一致した**。

課題7の Slack は同じことをすると `&` が `&amp;` に、`<` が `&lt;` になって保存され、
照合のために送信側と同じ変換をかけ直す必要があった。**同じ「チャンネルにメッセージを
投稿する API」でも、送った文字列がそのまま返る API と返らない API がある。**

だから照合の書きかたも変わる。Slack では「変換してから比べる」、Discord では「そのまま比べる」。
**ここで「どちらでも通す」判定にすると、照合が何も確かめなくなる。**
実際 `verify_notification.py` には「エンティティ化されていても通す」を**入れなかった**。

> 和文だけで試していたら、変換の有無はどちらの API でも永久に分からなかった。
> 課題6（YouTube のタイトルが HTML 実体参照で返る）から続けて3回目の同じ話。

### 2. MESSAGE CONTENT 特権インテント無しで、本文が読めた

Developer Portal で特権インテントを一切有効にしていない状態で、`content` が空にならず
そのまま返った。公式の例外「Content in messages that an app sends」が実際に効いている。

ただし**実装は「読めるはず」を前提にしていない**。空で返ったら「本文が違う」ではなく
「読めていない」として、intent の可能性に触れる案内を出す。
**確かめられたのは「この条件では読めた」ことだけで、「常に読める」ではない。**

### 3. 画面では見分けがつかないのに、中の人が違う

投稿者IDが `1538970817512214568`（Bot）と `1538973374376714306`（Webhook）で別。

**それなのに、Discord の画面では2件とも同じ「アプリ」バッジが付いて並ぶ**（`08-discord.png`）。
Discord は人間でない投稿者をまとめて「アプリ」と表示するので、**メッセージの行だけを見ると、
これが名前違いのボット2体なのか、ボットと webhook なのかは決まらない。**

ただし「画面からは絶対に分からない」ではない。**同じ画像のメンバー一覧には `test-bot` だけが
載っていて、`Captain Hook` は載っていない**——webhook はサーバーのメンバーではないため。
つまり手がかりは画面にもあるが、**投稿そのものを見て判定することはできない**。

| 見る場所 | 区別できるか |
|---|---|
| メッセージの行（名前とバッジ） | **できない**。どちらも「アプリ」 |
| メンバー一覧 | 手がかりになる（webhook は載らない）。ただし投稿と結びつけるのは人間の推測 |
| `author.id` | **決定的**。Bot のユーザーIDか、Webhook の ID か |

課題7で「画面の表示と保存値は違う」を踏んだが、今回は**画面の見え方と、投稿した主体が違う**。
形は同じで、ずれている対象が変わっただけ。**だから照合は画面ではなく ID で行う。**

要件が「**ボットを作成し**」と言っている以上、ここが効く。webhook で送ったメッセージは
ボットが投稿したものではない。ヒントの「webhook を使うと簡単です」に素直に従うと、
**要件の主語が入れ替わるのに、画面を見ても気づけない。**

### 4. Webhook は「送りっぱなし」ではなかった

`GET /webhooks/{id}/{token}/messages/{id}` が、**Bot Token を一切使わずに**通った。
webhook トークンだけで自分が送ったメッセージを読み返せる。

つまり webhook でも「送って終わりにしない」照合が組める。ただし読めるのは
**その webhook が送った分だけ**で、チャンネルの他の投稿は読めない。
権限の粒度が「チャンネル単位」ではなく「その webhook が作ったもの単位」になっている。

### 5. 送る前の確認が、両経路とも実際に効いた

実行画面の `（GET /channels で確認済み）` と `（GET /webhooks で確認済み）` が、
投稿の前に1回ずつ出ている。**投稿は消せるが、通知が飛ぶのは取り消せない**ので、
狙ったサーバーのチャンネルかどうかは送る前に閉じてある。

`--via webhook` では、`--channel` に何を書いても宛先は変わらない（**宛先は URL 側で決まる**）。
だから食い違ったら送らずに止める。この分岐はテストで「**POST が1回も飛んでいないこと**」まで
固定してある（`test_main_webhook_path_does_not_post_to_the_wrong_channel`）。

### 6. スクリーンショットを撮り直したら、ID が変わった

**この README は一度、実行画面と食い違った状態になった。**

先に1回走らせて動作を確認し、そのときの ID で README を書いた。あとからスクリーンショットを
撮るためにもう一度走らせたので、**新しいメッセージが作られて ID が変わった**。
結果ファイルは上書きされて新しい値になり、README だけが古い ID のまま残った。

課題5でまったく同じことが起きている（送信をやり直して、実行画面と照合画面の
スクリーンショットが別のメールを指した）。**同じ失敗が、対象を変えて再発した。**

気づけたのは、snowflake から投稿時刻を復元して突き合わせたから。

```python
DISCORD_EPOCH = 1420070400000
timestamp_ms = (int(snowflake) >> 22) + DISCORD_EPOCH
```

- `results-bot.json` の ID → 03:36:59
- `results-webhook.json` の ID → 03:38:00
- `08-discord.png` に写っている投稿 → **3:36 と 3:38**

3つが同じ時刻を指したので、結果ファイルとスクリーンショットは揃っていて、
**README だけがずれている**と確定できた。**「ID が違う」だけでは、どちらが正しいか決まらない。**

→ 再発を防ぐため、`check_docs.py` に「**README の実測表の ID が結果ファイルと一致するか**」を足した。
ただしこれで閉じるのは README と結果ファイルの間だけで、**スクリーンショットの中身は機械に見えない**。
そこは目で見るしかないので、この文書にそう書いておく。

## スクリーンショット

| ファイル | 何が写っているか |
|---|---|
| `docs/01-send-bot.png` | Bot Token 経路の送信。投稿の前に `GET /channels で確認済み` が出ている |
| `docs/02-verify-bot.png` | Bot Token 経路の読み返し。13項目すべて一致 |
| `docs/03-send-webhook.png` | Webhook 経路の送信。`GET /webhooks で確認済み` と、Bot とは違う投稿者ID |
| `docs/04-verify-webhook.png` | Webhook 経路の読み返し。**Bot Token を使わずに** 13項目すべて一致 |
| `docs/05-guard-wrong-channel.png` | Webhook の宛先と `--channel` が食い違うとき、**投稿せずに止まる** |
| `docs/06-tests-1.png` | テスト（1/4） |
| `docs/06-tests-2.png` | テスト（2/4） |
| `docs/06-tests-3.png` | テスト（3/4） |
| `docs/06-tests-4.png` | テスト（4/4）。163 件すべて通過 |
| `docs/07-mutate-1.png` | わざと壊す（1/3） |
| `docs/07-mutate-2.png` | わざと壊す（2/3） |
| `docs/07-mutate-3.png` | わざと壊す（3/3）。109 件すべて kill・素通り 0 |
| `docs/08-discord.png` | Discord の画面。**2件が並び、名前が違うのに同じ「アプリ」バッジ**。本文の `&` と `<embed>` がそのまま表示されている |

**画像の一覧はレンジ表記（`06-tests-1.png` 〜 `-4.png`）で書かない。**
まとめて書くと、README と実ファイルを突き合わせる検査が**末尾のファイル名を1つも見ない**。
課題7で実際に、撮った枚数が予定より少なかったのに書いたまま残しかけた。

## テスト

```powershell
.venv\Scripts\python.exe -m pytest task8 common/tests/test_discord_auth.py --no-header -q
```

| 対象 | 件数 |
|---|---|
| `task8/tests/test_send_notification.py` | 59 |
| `task8/tests/test_verify_notification.py` | 46 |
| `common/tests/test_discord_auth.py` | 58 |
| **この課題の合計** | **163** |
| リポジトリ全体 | 1166 |

**テストは実装より先に書いた。** `ModuleNotFoundError` で落ちることを確認してから実装に入っている。
先に書くと、期待値を実装から作れない。

### わざと壊して、落ちることを確認した

```powershell
.venv\Scripts\python.exe task8\tools\mutate.py
```

**109 か所・素通り 0 件・置換先なし 0 件。**

リポジトリを丸ごと一時ディレクトリへ写して、**写した側だけを壊す**。成果物のファイルは
1バイトも触らないので、途中で強制終了しても事故が起きない
（課題4で `atexit` が走らず `if False:` が残った事故の対策）。

**最初の素通りは8件で、8件とも同じ形だった**——「テストは落ちているが、
落ちている理由がこちらの狙いと違う」。

| 素通りした検査 | 実際に落としていたのは |
|---|---|
| Webhook URL の未設定判定 | 後段の「形が違います」 |
| `/users/@me` の応答が辞書か | 手前の HTTP エラー判定（2xx で壊れた本文を試していなかった） |
| Webhook の宛先チャンネル照合（2件） | 投稿応答の投稿者照合 |
| 結果ファイルのサーバー照合・チャンネル照合 | リンクの整合性検査 |
| メッセージIDの形式検査 | （そもそも非数値の ID を試していなかった） |
| 「実行中の主体」の照合 | 「投稿者」の照合 |

直しかたは2つ。**例外の型だけでなく文言まで見る**（「渡していない」と「形が違う」は
原因が別なので、同じ型で返すなら文言で区別する）。そして
**他の条件を全部満たしたうえで、狙った1点だけ違う入力にする**（`build_local_checks` の
テストは、リンクの辻褄も合わせたうえでサーバーだけ食い違わせている）。

### ガードを1つ消した

`parse_webhook_url` に `if not token:` を置いていたが、**外しても結果が1つも変わらない**。
パスの要素は空を落としてから数えているので、4要素あるなら token は必ず空でない。
テストを何件足しても kill できない種類の指摘で、ミューテーションでしか見えない。
課題7の `if not headers:` と同じ形。**守っているつもりの行が、実は何もしていない。**
