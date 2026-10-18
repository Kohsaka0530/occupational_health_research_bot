# 10日間の試用をGitHub Actionsで動かす

Codex環境のAPIキーはGitHub Actionsには自動で渡りません。GitHub側に次の3つを登録してください。キーはこのチャットやGitには貼りません。

1. リポジトリの **Settings → Secrets and variables → Actions** を開きます。
2. **New repository secret** で、次の3つをそれぞれ登録します。

| Name | Secretに入れる値 |
|---|---|
| `RESEARCH_LLM_API_KEY` | 今回動作確認したOpenAIのAPIキー |
| `RESEARCH_MAIL_API_KEY` | 今回送信に成功したResendのAPIキー |
| `DIGEST_TO` | 初回メールを受信した、Resend登録先と同じGmail |

送信元は試用用の `onboarding@resend.dev` を初期値にしているため、`MAIL_FROM` の追加登録は不要です。

3. **Actions → Occupational health research brief → Run workflow** を実行します。同日分を既に送った場合は、追加送信せず正常に終了します。
4. スケジュールは毎日22:00 UTC＝日本時間の翌朝7:00です。GitHubの都合による遅延はあり得ます。

## 今回の期間と履歴

初回は2026年10月18日に送信済みです。試用期間は日本時間で10月18日〜27日、10月28日以降はアプリが送信を停止します。固定終了日も設定しているため、GitHubのキャッシュが失われても期間を延長しません。GitHubの起動自体は残りますが、終了後は情報収集・API呼出し・送信を行いません。

`bootstrap_state.json` は初回分の再送を防ぐためのハッシュと日付だけを持っています。APIキー、メールアドレス、本文は含みません。以後のSQLite履歴はGitHubのキャッシュに保存します。キャッシュ消失時は初回以降の一部の履歴が失われ、既報を再選定する可能性があります。長期運用では永続ストレージへの移行が必要です。

送信内容を含むoutboxはキャッシュやGitに保存しません。送信未確定の履歴だけが残った場合は、重複を避けるため新規送信を停止し、Resendでの履歴確認を求めます。

このファイルとコードを配置しただけでは、Secrets登録やGitHub Actionsの起動成功を証明しません。初回のActions実行結果で確認してください。
