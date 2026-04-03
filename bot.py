"""
社群貼文收藏 Bot v5 - 完整重寫乾淨版本
"""

import os, json, logging, re
import httpx
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
NOTION_TOKEN   = os.environ["NOTION_TOKEN"]
NOTION_DB_ID   = "336a2a4fd11380419b1ae2907a8ba216"

# ── 狀態常數 ──────────────────────────────────────────────────────────────────
ST_WAIT_CONTENT    = 1
ST_WAIT_IG_LINK    = 2
ST_SELECT_TOOLS    = 3
ST_INPUT_NEW_TOOL  = 4
ST_SELECT_FOCUS    = 5
ST_INPUT_NEW_FOCUS = 6
ST_REVIEW_TITLE    = 7
ST_INPUT_NEW_TITLE = 8
ST_FINAL_CONFIRM   = 9

TOOLS_OPTIONS = ["Claude", "Gemini", "Notion"]
FOCUS_OPTIONS = ["AI版本更新", "Vibe coding", "實用功能"]

# ── 狀態讀寫 ──────────────────────────────────────────────────────────────────
def get_st(ctx): return ctx.user_data.get("st", ST_WAIT_CONTENT)
def set_st(ctx, s): ctx.user_data["st"] = s

# ── 外部 API ──────────────────────────────────────────────────────────────────
def is_threads(t): return bool(re.search(r"threads\.(net|com)", t, re.I))
def is_ig(t):      return bool(re.search(r"instagram\.com|ig\.me", t, re.I))

async def fetch_threads(url):
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
            for p in [r'og:description["\'][^>]+content=["\']([^"\']{10,})["\']',
                      r'name=["\']description["\'][^>]+content=["\']([^"\']{10,})["\']']:
                m = re.search(p, r.text)
                if m: return m.group(1)
    except Exception as e:
        logger.error(f"Threads 抓取失敗: {e}")
    return ""

async def call_gemini(content, url, platform):
    prompt = f"""分析以下 {platform} 貼文，只回傳 JSON，不要其他文字：
貼文：{content}
連結：{url}
{{
  "建議主題": "簡短中文標題15字以內",
  "建議工具": ["從 Claude Gemini Notion 選"],
  "建議重點": ["從 AI版本更新 Vibe coding 實用功能 選"],
  "分析說明": "一句話摘要"
}}"""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}",
                json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}}
            )
            raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(re.sub(r"```json|```", "", raw).strip())
    except Exception as e:
        logger.error(f"Gemini 失敗: {e}")
        return {"建議主題": "未能分析", "建議工具": [], "建議重點": [], "分析說明": "請手動填寫"}

async def write_notion(title, url, tools, focus):
    title_obj = [{"type": "text", "text": {"content": title, "link": {"url": url}}}] if url \
                else [{"type": "text", "text": {"content": title}}]
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://api.notion.com/v1/pages",
                headers={"Authorization": f"Bearer {NOTION_TOKEN}",
                         "Content-Type": "application/json",
                         "Notion-Version": "2022-06-28"},
                json={"parent": {"database_id": NOTION_DB_ID},
                      "properties": {
                          "收藏貼文主題": {"title": title_obj},
                          "貼文適用工具": {"multi_select": [{"name": t} for t in tools]},
                          "貼文重點":     {"multi_select": [{"name": f} for f in focus]},
                      }}
            )
            r.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Notion 失敗: {e}")
        return False

def kb(opts, extras=None):
    rows = [[o] for o in opts]
    if extras: rows += [[e] for e in extras]
    return ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True)

# ── 畫面函式（每個畫面獨立，呼叫後立即設定狀態）────────────────────────────────

async def show_tools(update, ctx):
    sel = ctx.user_data.get("sel_tools", [])
    sug = ctx.user_data.get("analysis", {}).get("建議工具", [])
    rem = [t for t in TOOLS_OPTIONS if t not in sel]
    hint = f"（建議：{', '.join(sug)}）" if sug and not sel else ""
    set_st(ctx, ST_SELECT_TOOLS)
    await update.message.reply_text(
        f"🛠 *貼文適用工具* {hint}\n已選：{', '.join(sel) if sel else '（尚未選擇）'}\n\n選完點「✅ 完成」：",
        parse_mode="Markdown",
        reply_markup=kb(rem, extras=["➕ 新增工具", "✅ 完成"])
    )

async def show_focus(update, ctx):
    sel = ctx.user_data.get("sel_focus", [])
    sug = ctx.user_data.get("analysis", {}).get("建議重點", [])
    rem = [f for f in FOCUS_OPTIONS if f not in sel]
    hint = f"（建議：{', '.join(sug)}）" if sug and not sel else ""
    set_st(ctx, ST_SELECT_FOCUS)
    await update.message.reply_text(
        f"🎯 *貼文重點* {hint}\n已選：{', '.join(sel) if sel else '（尚未選擇）'}\n\n選完點「✅ 完成」：",
        parse_mode="Markdown",
        reply_markup=kb(rem, extras=["➕ 新增重點", "✅ 完成"])
    )

async def show_title(update, ctx):
    sug = ctx.user_data.get("analysis", {}).get("建議主題", "")
    ctx.user_data["draft_title"] = sug
    set_st(ctx, ST_REVIEW_TITLE)
    await update.message.reply_text(
        f"📌 *收藏貼文主題*\n\n`{sug}`\n\n請選擇：",
        parse_mode="Markdown",
        reply_markup=kb(["✅ 使用此主題", "✏️ 修改主題"])
    )

async def show_confirm(update, ctx):
    title = ctx.user_data.get("draft_title", "")
    url   = ctx.user_data.get("url", "")
    tools = ctx.user_data.get("sel_tools", [])
    focus = ctx.user_data.get("sel_focus", [])
    url_line = f"\n🔗 連結：{url}" if url else ""
    set_st(ctx, ST_FINAL_CONFIRM)
    await update.message.reply_text(
        f"📋 *最終確認*\n\n🏷 主題：{title}{url_line}\n🛠 工具：{', '.join(tools)}\n🎯 重點：{', '.join(focus)}\n\n確認存入 Notion 嗎？",
        parse_mode="Markdown",
        reply_markup=kb(["✅ 確認存入", "❌ 取消"])
    )

# ── 分析流程 ──────────────────────────────────────────────────────────────────
async def do_analysis(update, ctx):
    await update.message.reply_text("🤖 Gemini 分析中...")
    result = await call_gemini(ctx.user_data["content"], ctx.user_data.get("url",""), ctx.user_data["platform"])
    ctx.user_data["analysis"]  = result
    ctx.user_data["sel_tools"] = []
    ctx.user_data["sel_focus"] = []
    emoji = "🧵" if ctx.user_data["platform"] == "Threads" else "📸"
    await update.message.reply_text(
        f"{emoji} *分析完成！*\n📝 {result['分析說明']}\n\n接下來逐一確認各欄位：",
        parse_mode="Markdown"
    )
    await show_tools(update, ctx)

# ── 主訊息處理器 ──────────────────────────────────────────────────────────────
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    st   = get_st(ctx)

    # 1. 等待貼文
    if st == ST_WAIT_CONTENT:
        if is_threads(text):
            await update.message.reply_text("🔍 正在抓取 Threads 內容...")
            content = await fetch_threads(text)
            if not content:
                await update.message.reply_text("⚠️ 抓取失敗，請直接複製貼文文字給我。")
                return
            ctx.user_data.update({"platform": "Threads", "url": text, "content": content})
            await do_analysis(update, ctx)
        elif is_ig(text):
            await update.message.reply_text("📸 IG 私密帳號無法抓取，請複製貼文文字給我 🙂")
        else:
            ctx.user_data.update({"platform": "Instagram", "content": text, "url": ""})
            set_st(ctx, ST_WAIT_IG_LINK)
            await update.message.reply_text("📎 請提供這篇貼文的 IG 連結：", reply_markup=ReplyKeyboardRemove())

    # 2. 等待 IG 連結
    elif st == ST_WAIT_IG_LINK:
        ctx.user_data["url"] = text
        await do_analysis(update, ctx)

    # 3. 選工具
    elif st == ST_SELECT_TOOLS:
        if text == "✅ 完成":
            if not ctx.user_data.get("sel_tools"):
                await update.message.reply_text("⚠️ 請至少選一個工具！")
                await show_tools(update, ctx)
            else:
                await show_focus(update, ctx)
        elif text == "➕ 新增工具":
            set_st(ctx, ST_INPUT_NEW_TOOL)
            await update.message.reply_text("✏️ 請輸入新工具名稱：", reply_markup=ReplyKeyboardRemove())
        else:
            if text in TOOLS_OPTIONS and text not in ctx.user_data.get("sel_tools", []):
                ctx.user_data.setdefault("sel_tools", []).append(text)
            await show_tools(update, ctx)

    # 4. 輸入新工具名稱
    elif st == ST_INPUT_NEW_TOOL:
        if text not in TOOLS_OPTIONS: TOOLS_OPTIONS.append(text)
        ctx.user_data.setdefault("sel_tools", [])
        if text not in ctx.user_data["sel_tools"]: ctx.user_data["sel_tools"].append(text)
        await update.message.reply_text(f"✅ 已新增工具「{text}」")
        await show_tools(update, ctx)

    # 5. 選重點
    elif st == ST_SELECT_FOCUS:
        if text == "✅ 完成":
            if not ctx.user_data.get("sel_focus"):
                await update.message.reply_text("⚠️ 請至少選一個重點！")
                await show_focus(update, ctx)
            else:
                await show_title(update, ctx)
        elif text == "➕ 新增重點":
            set_st(ctx, ST_INPUT_NEW_FOCUS)
            await update.message.reply_text("✏️ 請輸入新重點名稱：", reply_markup=ReplyKeyboardRemove())
        else:
            if text in FOCUS_OPTIONS and text not in ctx.user_data.get("sel_focus", []):
                ctx.user_data.setdefault("sel_focus", []).append(text)
            await show_focus(update, ctx)

    # 6. 輸入新重點名稱
    elif st == ST_INPUT_NEW_FOCUS:
        if text not in FOCUS_OPTIONS: FOCUS_OPTIONS.append(text)
        ctx.user_data.setdefault("sel_focus", [])
        if text not in ctx.user_data["sel_focus"]: ctx.user_data["sel_focus"].append(text)
        await update.message.reply_text(f"✅ 已新增重點「{text}」")
        await show_focus(update, ctx)

    # 7. 確認主題
    elif st == ST_REVIEW_TITLE:
        if text == "✅ 使用此主題":
            await show_confirm(update, ctx)
        elif text == "✏️ 修改主題":
            set_st(ctx, ST_INPUT_NEW_TITLE)
            await update.message.reply_text("✏️ 請輸入新的主題名稱：", reply_markup=ReplyKeyboardRemove())
        else:
            await show_title(update, ctx)

    # 8. 輸入新主題名稱
    elif st == ST_INPUT_NEW_TITLE:
        ctx.user_data["draft_title"] = text
        await update.message.reply_text(f"✅ 已更新主題：`{text}`", parse_mode="Markdown")
        await show_confirm(update, ctx)   # ← 直接呼叫，不經過其他函式

    # 9. 最終確認
    elif st == ST_FINAL_CONFIRM:
        if text == "✅ 確認存入":
            await update.message.reply_text("⏳ 寫入 Notion 中...")
            ok = await write_notion(
                ctx.user_data.get("draft_title", ""),
                ctx.user_data.get("url", ""),
                ctx.user_data.get("sel_tools", []),
                ctx.user_data.get("sel_focus", [])
            )
            if ok:
                await update.message.reply_text("🎉 成功存入 Notion！\n\n下一篇貼文傳過來就好 👋", reply_markup=ReplyKeyboardRemove())
            else:
                await update.message.reply_text("❌ Notion 寫入失敗，請確認 NOTION_TOKEN 是否正確。", reply_markup=ReplyKeyboardRemove())
            ctx.user_data.clear()
            set_st(ctx, ST_WAIT_CONTENT)
        elif text == "❌ 取消":
            ctx.user_data.clear()
            set_st(ctx, ST_WAIT_CONTENT)
            await update.message.reply_text("已取消。有需要再傳貼文給我 🙂", reply_markup=ReplyKeyboardRemove())
        else:
            await show_confirm(update, ctx)

    # 未知狀態
    else:
        ctx.user_data.clear()
        set_st(ctx, ST_WAIT_CONTENT)
        await update.message.reply_text("出了點問題，已重置。請重新傳貼文給我：", reply_markup=ReplyKeyboardRemove())

# ── 指令 ──────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    set_st(ctx, ST_WAIT_CONTENT)
    await update.message.reply_text(
        "👋 嗨！我是你的貼文收藏助手。\n\n請傳給我：\n• Threads 連結（自動抓內容）\n• IG 貼文文字（複製貼上）",
        reply_markup=ReplyKeyboardRemove()
    )

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    set_st(ctx, ST_WAIT_CONTENT)
    await update.message.reply_text("已取消，重新開始。", reply_markup=ReplyKeyboardRemove())

# ── 主程式 ────────────────────────────────────────────────────────────────────
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    logger.info("✅ Bot v5 啟動中...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
