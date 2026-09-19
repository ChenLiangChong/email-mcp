# email-mcp

一支檔案的 email MCP server：用 IMAP 收信、SMTP 寄信，支援多個帳號。只依賴 Python 標準函式庫和 `mcp` SDK。

## 工具

| 工具 | 用途 |
|---|---|
| `email_list_accounts` | 列出已設定的帳號 |
| `email_list_mailboxes` | 列出信箱資料夾 |
| `email_get_messages` | 最新信件列表 |
| `email_get_unread` | 未讀信件 |
| `email_get_message` | 讀單封信完整內容 |
| `email_search` | 用 IMAP 搜尋語法找信 |
| `email_get_count` | 信件總數與未讀數 |
| `email_send` | 寄信（純文字或 HTML） |
| `email_reply` | 回信（自動帶 `Re:`、`In-Reply-To`、`References`） |
| `email_mark_read` | 標記已讀 |
| `email_delete` | 刪信 |

寄出的信會 APPEND 到 IMAP 的寄件備份匣（依序試 `Sent Items`、`Sent`、`INBOX.Sent`、`[Gmail]/Sent Mail`），所以在一般郵件軟體裡也看得到。

## 安裝

需要 Python 3.10 以上。

```bash
git clone https://github.com/ChenLiangChong/email-mcp.git
cd email-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 設定

在 MCP 用戶端的設定檔（Claude Code 是專案的 `.mcp.json`）加上，路徑請用絕對路徑：

```json
{
  "mcpServers": {
    "email": {
      "command": "/path/to/email-mcp/.venv/bin/python",
      "args": ["/path/to/email-mcp/server.py"],
      "env": {
        "EMAIL_ACCOUNTS": "[{\"name\":\"work\",\"email\":\"you@example.com\",\"password\":\"your-app-password\",\"imap_host\":\"imap.example.com\",\"smtp_host\":\"smtp.example.com\"}]"
      }
    }
  }
}
```

`EMAIL_ACCOUNTS` 是一個 JSON 陣列，每個帳號的欄位：

| 欄位 | 必填 | 預設 |
|---|---|---|
| `email` | ✅ | |
| `password` | ✅ | |
| `name` | | email 的 `@` 前半段 |
| `imap_host` | | `imap.mail.us-east-1.awsapps.com`（AWS WorkMail） |
| `imap_port` | | `993` |
| `smtp_host` | | `smtp.mail.us-east-1.awsapps.com`（AWS WorkMail） |
| `smtp_port` | | `465` |

工具的 `account` 參數可以填 `name` 或 email，留空就用第一個帳號。IMAP 和 SMTP 都走 SSL。

## 注意

- **密碼不要進版控。** 有密碼的 `.mcp.json` 請加進 `.gitignore`，或改成啟動時從系統鑰匙圈讀出來再塞進環境變數。Gmail 這類信箱請用應用程式密碼。
- **`uid` 是 IMAP UID**，信件被刪除後不會位移；指到已不存在的信會回錯誤，不會對到別封。只接受單一數字，`1:*` 這類範圍會被擋掉。
- `email_delete` 用的是 EXPUNGE，會一併清掉同一個資料夾裡其他已被標記 `\Deleted` 的信。
- `email_send`、`email_reply`、`email_delete` 會真的寄信和刪信，建議在用戶端把這三個工具設成每次都要確認。

## 授權

MIT
