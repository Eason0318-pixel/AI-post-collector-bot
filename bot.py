# 🤖 一鍵啟動提示詞

複製以下提示詞，貼給 Claude（需連接 Notion）即可啟動整個建立流程。

---

## 📋 提示詞（直接複製這整段）

我想為一個新的 Notion 資料庫建立一個 Telegram Bot 收藏系統，流程跟我之前建立的「AI工具收藏貼文」Bot 完全一樣。請幫我執行以下步驟：

1. 請讀取我指定的 Notion 資料庫，自動抓取所有欄位名稱和現有選項
2. 根據欄位結構，生成一份完整的 bot.py 程式碼（基於 v12 架構：純狀態機、無 Gemini、任何連結直接記住、YouTube 自動抓標題、選項從 Notion 動態讀取、✅完成按鈕在最上方、➕新增按鈕在最下方）
3. 告訴我需要在 Railway 新增哪些環境變數
4. 提醒我把新的 Notion Integration 連接到這個資料庫

我的 Notion 資料庫連結是：【貼上你的 Notion 資料庫網址】

---

## 📖 使用說明

### 如何取得 Notion 資料庫網址
打開你的 Notion 資料庫頁面 → 複製瀏覽器網址列的網址，格式長這樣：
https://www.notion.so/你的workspace/資料庫名稱-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

直接複製整個網址貼進提示詞就好 🙂

---

## 🗺️ 完整流程地圖（給零基礎朋友參考）

Claude 會自動幫你完成以下流程，你只需要跟著指示操作：

Step 1　建立 Telegram Bot → 取得 Token
Step 2　建立 Notion Integration → 取得 Token，連接資料庫
Step 3　取得 YouTube API Key（選用）
Step 4　Claude 自動生成 bot.py 程式碼
Step 5　上傳程式碼到 GitHub
Step 6　在 Railway 部署，填入環境變數
Step 7　測試完成 ✅

---

## 🔑 需要自己取得的三個金鑰

### 1. Telegram Bot Token
- 網址：https://t.me/BotFather
- 操作：傳送 /newbot（斜線newbot）
- 按鈕：照指示輸入 Bot 名稱後，會收到 Token
- 格式：1234567890:AAF...
- ⚠️ 注意：Token 只顯示一次，請立刻複製儲存，且不要貼在對話中

### 2. Notion Integration Token（內部整合密鑰）
- 網址：https://www.notion.so/my-integrations
- 操作：點「+ New integration」（新增整合）
- 名稱：隨意填，例如 TelegramBot
- 複製：「Internal Integration Token」（內部整合密鑰），格式 secret_xxx...
- 最後：回到 Notion 資料庫頁面 → 右上角「⋯」→「連結至 / Connect to」→ 選剛建立的 Integration

### 3. YouTube Data API Key（選用，只有要收藏 YouTube 才需要）
- 網址：https://console.cloud.google.com
- 操作：
  1. 建立新專案（New Project）
  2. 搜尋「YouTube Data API v3」→ 啟用（Enable）
  3. 左側「憑證 / Credentials」→「建立憑證 / Create Credentials」→「API 金鑰 / API Key」
  4. 複製那串 Key

---

## 📁 需要自己操作的兩個平台

### GitHub（儲存程式碼）
1. 登入 https://github.com
2. 右上角「+」→「New repository」（新增倉庫）
3. 名稱隨意，選「Public」，不需要勾選任何選項
4. 建立後，點「上傳一個現有的文件 / uploading an existing file」
5. 把 bot.py 和 requirements.txt 拖進去
6. 點「提交更改 / Commit changes」

### Railway（執行程式）
1. 登入 https://railway.app（用 GitHub 帳號登入）
2. 點「New Project」→「GitHub Repository」
3. 選你剛建的倉庫
4. 點上方「Variables」→「+ New Variable」，依序填入：

| 變數名稱 | 說明 |
|---|---|
| TELEGRAM_TOKEN | Telegram Bot Token |
| NOTION_TOKEN | Notion Integration Token（secret_xxx） |
| YOUTUBE_API_KEY | YouTube API Key（若有要收藏 YouTube）|

5. 填完後 Railway 自動重新部署，等卡片變綠色即完成 ✅

---

## 🧪 測試方式

部署完成後：
1. 打開 Telegram，找到你的 Bot
2. 傳送 /start
3. 貼上任何連結（Threads / IG / YouTube）
4. 跟著 Bot 的問題完成分類
5. 確認後查看 Notion 是否新增了一筆記錄

---

## ❓ 常見問題

Q：Bot 沒有反應？
→ 去 Railway → Deployments → View logs，截圖給 Claude 看

Q：Notion 寫入失敗？
→ 確認 Notion Integration 有「連結至 / Connect to」你的資料庫

Q：之後在 Notion 新增了選項分類，Bot 會自動更新嗎？
→ 會！程式碼每次成功寫入後都會自動從 Notion 重新讀取最新選項

Q：要更新程式碼怎麼做？
→ 在 GitHub 覆蓋上傳新的 bot.py，Railway 會自動重新部署

Q：想為另一個 Notion 表格建立新的 Bot？
→ 複製本文最上面的提示詞，換上新的 Notion 資料庫網址，貼給 Claude 即可
