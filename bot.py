"""
社群貼文收藏 Bot v12
- 完全移除 Gemini，不再需要 GEMINI_API_KEY
- 傳任何連結 → 直接進入選分類流程
- YouTube 仍用 API 抓取標題（供你參考命名）
- 純狀態機
"""

import os, json, logging, re
import httpx
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
NOTION_TOKEN    = os.environ["NOTION_TOKEN"]
YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
NOTION_DB_ID    = "336a2a4fd11380419b1ae2907a8ba216"

ST_WAIT_CONTENT    = 1
ST_SELECT_TOOLS    = 2
ST_INPUT_NEW_TOOL  = 3
ST_SELECT_FOCUS    = 4
ST_INPUT_NEW_FOCUS = 5
ST_REVIEW_TITLE    = 6
ST_INPUT_NEW_TITLE = 7
ST_FINAL_CONFIRM   = 8

DEFAULT_TOOLS = ["Claude", "Gemini", "Notion"]
DEFAULT_FOCUS = ["AI版本更新", "Vibe coding", "實用功能", "Computer Use",
                 "AI基礎教學", "Claude + Gemini協作", "Claude code教學", "免費課程", "Gemma4"]

GLOBAL_TOOLS = list(DEFAULT_TOOLS)
GLOBAL_FOCUS = list(DEFAULT_FOCUS)

async def load_options_from_notion():
    global GLOBAL_TOOLS, GLOBAL_FOCUS
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://api.notion.com/v1/databases/" + NOTION_DB_ID,
                headers={"Authorization": "Bearer " + NOTION_TOKEN, "Notion-Version": "2022-06-28"}
            )
            r.raise_for_status()
            props = r.json().get("properties", {})
            tools = [o["name"] for o in props.get("貼文適用工具", {}).get("multi_select", {}).get("options", [])]
            focus = [o["name"] for o in props.get("貼文重點", {}).get("multi_select", {}).get("options", [])]
            if tools: GLOBAL_TOOLS = tools
            if focus: GLOBAL_FOCUS = focus
            logger.info("Notion 選項載入完成")
    except Exception as e:
        logger.error("載入 Notion 選項失敗: " + str(e))

def get_st(ctx):    return ctx.user_data.get("st", ST_WAIT_CONTENT)
def set_st(ctx, s): ctx.user_data["st"] = s

def get_tools(ctx):
    base = list(GLOBAL_TOOLS)
    for t in ctx.user_data.get("extra_tools", []):
        if t not in base: base.append(t)
    return base

def get_focus(ctx):
    base = list(GLOBAL_FOCUS)
    for f in ctx.user_data.get("extra_focus", []):
        if f not in base: base.append(f)
    return base

def is_url(t):     return bool(re.search(r"https?://", t, re.I))
def is_youtube(t): return bool(re.search(r"youtube\.com/watch|youtu\.be/", t, re.I))

def extract_yt_id(url):
    m = re.search(r"youtu\.be/([A-Za-z0-9_-]+)", url)
    if m: return m.group(1)
    m = re.search(r"youtube\.com/watch\?.*v=([A-Za-z0-9_-]+)", url)
    if m: return m.group(1)
    return None

async def fetch_youtube_title(url):
    """抓 YouTube 標題，給使用者參考命名用"""
    vid = extract_yt_id(url)
    if not vid: return ""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://www.googleapis.com/youtube/v3/videos?part=snippet&id=" + vid + "&key=" + YOUTUBE_API_KEY
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items: return ""
            s = items[0]["snippet"]
            return s.get("title", "") + "（" + s.get("channelTitle", "") + "）"
    except Exception as e:
        logger.error("YouTube 失敗: " + str(e))
    return ""

async def write_notion(title, url, tools, focus):
    if url:
        title_obj = [{"type": "text", "text": {"content": title, "link": {"url": url}}}]
    else:
        title_obj = [{"type": "text", "text": {"content": title}}]
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://api.notion.com/v1/pages",
                headers={
                    "Authorization": "Bearer " + NOTION_TOKEN,
                    "Content-Type": "application/json",
                    "Notion-Version": "2022-06-28"
                },
                json={
                    "parent": {"database_id": NOTION_DB_ID},
                    "properties": {
                        "收藏貼文主題": {"title": title_obj},
                        "貼文適用工具": {"multi_select": [{"name": t} for t in tools]},
                        "貼文重點":     {"multi_select": [{"name": f} for f in focus]},
                    }
                }
            )
            r.raise_for_status()
            await load_options_from_notion()
            return True
    except Exception as e:
        logger.error("Notion 失敗: " + str(e))
        return False

def kb(opts, extras=None):
    rows = [[o] for o in opts]
    if extras: rows += [[e] for e in extras]
    return ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True)

async def show_tools(update, ctx):
    sel = ctx.user_data.get("sel_tools", [])
    rem = [t for t in get_tools(ctx) if t not in sel]
    set_st(ctx, ST_SELECT_TOOLS)
    await update.message.reply_text(
        "🛠 貼文適用工具\n已選：" + (", ".join(sel) if sel else "（尚未選擇）") + "\n\n選完點「✅ 完成」：",
        reply_markup=kb(rem, extras=["➕ 新增工具", "✅ 完成"])
    )

async def show_focus(update, ctx):
    sel = ctx.user_data.get("sel_focus", [])
    rem = [f for f in get_focus(ctx) if f not in sel]
    set_st(ctx, ST_SELECT_FOCUS)
    await update.message.reply_text(
        "🎯 貼文重點\n已選：" + (", ".join(sel) if sel else "（尚未選擇）") + "\n\n選完點「✅ 完成」：",
        reply_markup=kb(rem, extras=["➕ 新增重點", "✅ 完成"])
    )

async def show_title(update, ctx):
    hint = ctx.user_data.get("title_hint", "")
    ctx.user_data["draft_title"] = hint
    set_st(ctx, ST_REVIEW_TITLE)
    hint_text = hint if hint else "（請輸入主題）"
    await update.message.reply_text(
        "📌 收藏貼文主題\n\n" + hint_text + "\n\n請選擇：",
        reply_markup=kb(["✅ 使用此主題", "✏️ 修改主題"])
    )

async def show_confirm(update, ctx):
    title = ctx.user_data.get("draft_title", "")
    url   = ctx.user_data.get("url", "")
    tools = ctx.user_data.get("sel_tools", [])
    focus = ctx.user_data.get("sel_focus", [])
    url_line = "\n🔗 連結：" + url if url else ""
    set_st(ctx, ST_FINAL_CONFIRM)
    await update.message.reply_text(
        "📋 最終確認\n\n"
        "🏷 主題：" + title + url_line + "\n"
        "🛠 工具：" + ", ".join(tools) + "\n"
        "🎯 重點：" + ", ".join(focus) + "\n\n"
        "確認存入 Notion 嗎？",
        reply_markup=kb(["✅ 確認存入", "❌ 取消"])
    )

async def start_flow(update, ctx, url, title_hint=""):
    ctx.user_data["url"]        = url
    ctx.user_data["title_hint"] = title_hint
    ctx.user_data["sel_tools"]  = []
    ctx.user_data["sel_focus"]  = []
    await show_tools(update, ctx)

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    st   = get_st(ctx)

    if st == ST_WAIT_CONTENT:
        if is_youtube(text):
            await update.message.reply_text("▶️ 正在抓取 YouTube 標題...")
            title = await fetch_youtube_title(text)
            if title:
                await update.message.reply_text("找到影片：" + title + "\n\n接下來選擇分類：")
            else:
                await update.message.reply_text("✅ 連結已記住！接下來選擇分類：")
            await start_flow(update, ctx, text, title)
        elif is_url(text):
            ctx.user_data.clear()
            await update.message.reply_text("✅ 連結已記住！接下來選擇分類：")
            await start_flow(update, ctx, text, "")
        else:
            await update.message.reply_text("請傳給我一個連結（Threads / IG / YouTube 等），我會幫你存入 Notion 🙂")

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
            if text in get_tools(ctx) and text not in ctx.user_data.get("sel_tools", []):
                ctx.user_data.setdefault("sel_tools", []).append(text)
            await show_tools(update, ctx)

    elif st == ST_INPUT_NEW_TOOL:
        ctx.user_data.setdefault("extra_tools", [])
        if text not in get_tools(ctx):
            ctx.user_data["extra_tools"].append(text)
        ctx.user_data.setdefault("sel_tools", [])
        if text not in ctx.user_data["sel_tools"]:
            ctx.user_data["sel_tools"].append(text)
        await update.message.reply_text("✅ 已新增工具「" + text + "」")
        await show_tools(update, ctx)

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
            if text in get_focus(ctx) and text not in ctx.user_data.get("sel_focus", []):
                ctx.user_data.setdefault("sel_focus", []).append(text)
            await show_focus(update, ctx)

    elif st == ST_INPUT_NEW_FOCUS:
        ctx.user_data.setdefault("extra_focus", [])
        if text not in get_focus(ctx):
            ctx.user_data["extra_focus"].append(text)
        ctx.user_data.setdefault("sel_focus", [])
        if text not in ctx.user_data["sel_focus"]:
            ctx.user_data["sel_focus"].append(text)
        await update.message.reply_text("✅ 已新增重點「" + text + "」")
        await show_focus(update, ctx)

    elif st == ST_REVIEW_TITLE:
        if text == "✅ 使用此主題":
            if not ctx.user_data.get("draft_title"):
                await update.message.reply_text("⚠️ 主題不能為空，請點「✏️ 修改主題」輸入名稱。")
                await show_title(update, ctx)
            else:
                await show_confirm(update, ctx)
        elif text == "✏️ 修改主題":
            set_st(ctx, ST_INPUT_NEW_TITLE)
            await update.message.reply_text("✏️ 請輸入主題名稱：", reply_markup=ReplyKeyboardRemove())
        else:
            await show_title(update, ctx)

    elif st == ST_INPUT_NEW_TITLE:
        ctx.user_data["draft_title"] = text
        await update.message.reply_text("✅ 已設定主題：" + text)
        await show_confirm(update, ctx)

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
                await update.message.reply_text("🎉 成功存入 Notion！\n\n下一篇傳過來就好 👋", reply_markup=ReplyKeyboardRemove())
            else:
                await update.message.reply_text("❌ Notion 寫入失敗，請確認 NOTION_TOKEN 是否正確。", reply_markup=ReplyKeyboardRemove())
            ctx.user_data.clear()
            set_st(ctx, ST_WAIT_CONTENT)
        elif text == "❌ 取消":
            ctx.user_data.clear()
            set_st(ctx, ST_WAIT_CONTENT)
            await update.message.reply_text("已取消。有需要再傳連結給我 🙂", reply_markup=ReplyKeyboardRemove())
        else:
            await show_confirm(update, ctx)

    else:
        ctx.user_data.clear()
        set_st(ctx, ST_WAIT_CONTENT)
        await update.message.reply_text("出了點問題，已重置。請重新傳連結給我：", reply_markup=ReplyKeyboardRemove())

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    set_st(ctx, ST_WAIT_CONTENT)
    await load_options_from_notion()
    await update.message.reply_text(
        "👋 嗨！我是你的內容收藏助手。\n\n"
        "傳給我任何連結：\n"
        "• Threads / IG / 其他連結 → 直接選分類\n"
        "• YouTube 連結 → 自動抓標題再選分類\n\n"
        "傳過來就開始！",
        reply_markup=ReplyKeyboardRemove()
    )

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    set_st(ctx, ST_WAIT_CONTENT)
    await update.message.reply_text("已取消，重新開始。", reply_markup=ReplyKeyboardRemove())

async def post_init(app):
    await load_options_from_notion()

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    logger.info("Bot v12 啟動中...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
