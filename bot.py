"""
社群貼文收藏 Bot v10
- 啟動時從 Notion API 動態讀取選項，永遠與 Notion 同步
- 支援 Threads、YouTube（API）、IG（手動）
- 純狀態機，無 Markdown parse_mode
"""

import os, json, logging, re
import httpx
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
NOTION_TOKEN     = os.environ["NOTION_TOKEN"]
YOUTUBE_API_KEY  = os.environ["YOUTUBE_API_KEY"]
NOTION_DB_ID     = "336a2a4fd11380419b1ae2907a8ba216"
NOTION_SOURCE_ID = "336a2a4f-d113-808f-88e9-000b2ae5fba0"

ST_WAIT_CONTENT    = 1
ST_WAIT_IG_LINK    = 2
ST_SELECT_TOOLS    = 3
ST_INPUT_NEW_TOOL  = 4
ST_SELECT_FOCUS    = 5
ST_INPUT_NEW_FOCUS = 6
ST_REVIEW_TITLE    = 7
ST_INPUT_NEW_TITLE = 8
ST_FINAL_CONFIRM   = 9

# 預設選項（啟動時會被 Notion 的真實選項覆蓋）
DEFAULT_TOOLS = ["Claude", "Gemini", "Notion"]
DEFAULT_FOCUS = ["AI版本更新", "Vibe coding", "實用功能", "Computer Use",
                 "AI基礎教學", "Claude + Gemini協作", "Claude code教學", "免費課程", "Gemma4"]

# 全域選項（啟動時從 Notion 讀取）
GLOBAL_TOOLS = list(DEFAULT_TOOLS)
GLOBAL_FOCUS = list(DEFAULT_FOCUS)

# ── 啟動時從 Notion 讀取選項 ───────────────────────────────────────────────────
async def load_options_from_notion():
    global GLOBAL_TOOLS, GLOBAL_FOCUS
    try:
        url = "https://api.notion.com/v1/databases/" + NOTION_DB_ID
        headers = {
            "Authorization": "Bearer " + NOTION_TOKEN,
            "Notion-Version": "2022-06-28"
        }
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
            props = data.get("properties", {})

            tools_prop = props.get("貼文適用工具", {})
            tools = [o["name"] for o in tools_prop.get("multi_select", {}).get("options", [])]
            if tools:
                GLOBAL_TOOLS = tools
                logger.info("從 Notion 載入工具選項：" + str(tools))

            focus_prop = props.get("貼文重點", {})
            focus = [o["name"] for o in focus_prop.get("multi_select", {}).get("options", [])]
            if focus:
                GLOBAL_FOCUS = focus
                logger.info("從 Notion 載入重點選項：" + str(focus))
    except Exception as e:
        logger.error("從 Notion 讀取選項失敗，使用預設值：" + str(e))

def get_st(ctx):   return ctx.user_data.get("st", ST_WAIT_CONTENT)
def set_st(ctx,s): ctx.user_data["st"] = s

def get_tools(ctx):
    # 用全域選項，加上本次對話新增的
    base = list(GLOBAL_TOOLS)
    for t in ctx.user_data.get("extra_tools", []):
        if t not in base:
            base.append(t)
    return base

def get_focus(ctx):
    base = list(GLOBAL_FOCUS)
    for f in ctx.user_data.get("extra_focus", []):
        if f not in base:
            base.append(f)
    return base

def is_threads(t): return bool(re.search(r"threads\.(net|com)", t, re.I))
def is_ig(t):      return bool(re.search(r"instagram\.com|ig\.me", t, re.I))
def is_youtube(t): return bool(re.search(r"youtube\.com/watch|youtu\.be/", t, re.I))

def extract_yt_id(url):
    m = re.search(r"youtu\.be/([A-Za-z0-9_-]+)", url)
    if m: return m.group(1)
    m = re.search(r"youtube\.com/watch\?.*v=([A-Za-z0-9_-]+)", url)
    if m: return m.group(1)
    return None

async def fetch_youtube(url):
    vid = extract_yt_id(url)
    if not vid: return ""
    try:
        api = "https://www.googleapis.com/youtube/v3/videos?part=snippet&id=" + vid + "&key=" + YOUTUBE_API_KEY
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(api)
            r.raise_for_status()
            items = r.json().get("items", [])
            if not items: return ""
            s = items[0]["snippet"]
            return s.get("title","") + "\n頻道：" + s.get("channelTitle","") + "\n" + s.get("description","")[:300]
    except Exception as e:
        logger.error("YouTube 失敗: " + str(e))
    return ""

async def fetch_threads(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as c:
            r = await c.get(url, headers=headers)
            patterns = [
                r'"og:description"[^>]*content="([^"]{10,})"',
                r'content="([^"]{10,})"[^>]*property="og:description"',
                r'<meta[^>]*name="description"[^>]*content="([^"]{10,})"',
                r'"description":"([^"]{10,})"',
            ]
            for p in patterns:
                m = re.search(p, r.text)
                if m:
                    result = m.group(1).strip()
                    if len(result) > 30:
                        return result
    except Exception as e:
        logger.error("Threads 失敗: " + str(e))
    return ""

async def call_gemini(content, url, platform):
    tools_list = ", ".join(GLOBAL_TOOLS)
    focus_list = ", ".join(GLOBAL_FOCUS)
    prompt = (
        "分析以下 " + platform + " 內容，只回傳 JSON 不要其他文字：\n"
        "內容：" + content + "\n"
        "連結：" + url + "\n"
        '{"建議主題":"簡短中文標題15字以內",'
        '"建議工具":["從 ' + tools_list + ' 選相關的"],'
        '"建議重點":["從 ' + focus_list + ' 選相關的"],'
        '"分析說明":"一句話摘要"}'
    )
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=" + GEMINI_API_KEY,
                json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2}}
            )
            raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            raw = re.sub(r"```json|```", "", raw).strip()
            return json.loads(raw)
    except Exception as e:
        logger.error("Gemini 失敗: " + str(e))
        return {"建議主題": "未能分析", "建議工具": [], "建議重點": [], "分析說明": "請手動填寫"}

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
            # 寫入成功後重新讀取選項（同步新增的選項到 Notion）
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
    all_tools = get_tools(ctx)
    sel = ctx.user_data.get("sel_tools", [])
    sug = ctx.user_data.get("analysis", {}).get("建議工具", [])
    rem = [t for t in all_tools if t not in sel]
    hint = "（建議：" + ", ".join(sug) + "）" if sug and not sel else ""
    set_st(ctx, ST_SELECT_TOOLS)
    await update.message.reply_text(
        "🛠 貼文適用工具 " + hint + "\n已選：" + (", ".join(sel) if sel else "（尚未選擇）") + "\n\n選完點「✅ 完成」：",
        reply_markup=kb(rem, extras=["➕ 新增工具", "✅ 完成"])
    )

async def show_focus(update, ctx):
    all_focus = get_focus(ctx)
    sel = ctx.user_data.get("sel_focus", [])
    sug = ctx.user_data.get("analysis", {}).get("建議重點", [])
    rem = [f for f in all_focus if f not in sel]
    hint = "（建議：" + ", ".join(sug) + "）" if sug and not sel else ""
    set_st(ctx, ST_SELECT_FOCUS)
    await update.message.reply_text(
        "🎯 貼文重點 " + hint + "\n已選：" + (", ".join(sel) if sel else "（尚未選擇）") + "\n\n選完點「✅ 完成」：",
        reply_markup=kb(rem, extras=["➕ 新增重點", "✅ 完成"])
    )

async def show_title(update, ctx):
    sug = ctx.user_data.get("analysis", {}).get("建議主題", "")
    ctx.user_data["draft_title"] = sug
    set_st(ctx, ST_REVIEW_TITLE)
    await update.message.reply_text(
        "📌 收藏貼文主題\n\n" + sug + "\n\n請選擇：",
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

async def do_analysis(update, ctx):
    await update.message.reply_text("🤖 Gemini 分析中...")
    result = await call_gemini(
        ctx.user_data["content"],
        ctx.user_data.get("url", ""),
        ctx.user_data["platform"]
    )
    ctx.user_data["analysis"]  = result
    ctx.user_data["sel_tools"] = []
    ctx.user_data["sel_focus"] = []
    platform = ctx.user_data["platform"]
    if platform == "Threads":   emoji = "🧵"
    elif platform == "YouTube": emoji = "▶️"
    else:                       emoji = "📸"
    await update.message.reply_text(
        emoji + " 分析完成！\n📝 " + result["分析說明"] + "\n\n接下來逐一確認各欄位："
    )
    await show_tools(update, ctx)

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    st   = get_st(ctx)

    if st == ST_WAIT_CONTENT:
        if is_youtube(text):
            await update.message.reply_text("▶️ 正在透過 YouTube API 抓取影片資訊...")
            content = await fetch_youtube(text)
            if not content:
                await update.message.reply_text("⚠️ 抓取失敗，請確認連結是否正確，或直接把影片標題貼給我。")
                return
            ctx.user_data.update({"platform": "YouTube", "url": text, "content": content})
            await do_analysis(update, ctx)
        elif is_threads(text):
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

    elif st == ST_WAIT_IG_LINK:
        ctx.user_data["url"] = text
        await do_analysis(update, ctx)

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
            await show_confirm(update, ctx)
        elif text == "✏️ 修改主題":
            set_st(ctx, ST_INPUT_NEW_TITLE)
            await update.message.reply_text("✏️ 請輸入新的主題名稱：", reply_markup=ReplyKeyboardRemove())
        else:
            await show_title(update, ctx)

    elif st == ST_INPUT_NEW_TITLE:
        ctx.user_data["draft_title"] = text
        await update.message.reply_text("✅ 已更新主題：" + text)
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
            await update.message.reply_text("已取消。有需要再傳貼文給我 🙂", reply_markup=ReplyKeyboardRemove())
        else:
            await show_confirm(update, ctx)

    else:
        ctx.user_data.clear()
        set_st(ctx, ST_WAIT_CONTENT)
        await update.message.reply_text("出了點問題，已重置。請重新傳內容給我：", reply_markup=ReplyKeyboardRemove())

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    set_st(ctx, ST_WAIT_CONTENT)
    await load_options_from_notion()
    await update.message.reply_text(
        "👋 嗨！我是你的內容收藏助手。\n\n請傳給我：\n"
        "• Threads 連結（自動抓內容）\n"
        "• YouTube 連結（自動抓內容）\n"
        "• IG 貼文文字（複製貼上）",
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
    logger.info("Bot v10 啟動中...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
