"""
社群貼文收藏 Bot v3
- 修復：新增選項時對話狀態混亂的問題
- 修復：Threads 連結判斷（支援 threads.com 和 threads.net）
- 改善：所有「輸入新內容」步驟都有獨立對話狀態
"""

import os
import json
import logging
import re
import httpx
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
NOTION_TOKEN   = os.environ["NOTION_TOKEN"]
NOTION_DB_ID   = "336a2a4f-d113-808f-88e9-000b2ae5fba0"

# ── 對話狀態（每步獨立，完全不重疊）─────────────────────────────────────────
(
    WAIT_CONTENT,    # 等待貼文（Threads 連結或 IG 文字）
    WAIT_IG_LINK,    # 等待 IG 連結
    SELECT_TOOLS,    # 從鍵盤選工具
    INPUT_NEW_TOOL,  # 鍵盤消失，等待輸入新工具名稱
    SELECT_FOCUS,    # 從鍵盤選重點
    INPUT_NEW_FOCUS, # 鍵盤消失，等待輸入新重點名稱
    REVIEW_TITLE,    # 確認主題
    INPUT_NEW_TITLE, # 鍵盤消失，等待輸入新主題
    FINAL_CONFIRM,   # 最終確認存入
) = range(9)

# 選項清單（新增時動態擴充）
TOOLS_OPTIONS = ["Claude", "Gemini", "Notion"]
FOCUS_OPTIONS = ["AI版本更新", "Vibe coding", "實用功能"]

# ── 工具函式 ──────────────────────────────────────────────────────────────────

def is_threads_url(text: str) -> bool:
    return bool(re.search(r"threads\.(net|com)", text, re.IGNORECASE))

def is_ig_url(text: str) -> bool:
    return bool(re.search(r"instagram\.com|ig\.me", text, re.IGNORECASE))

async def fetch_threads_content(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; PostCollectorBot/1.0)"}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url, headers=headers)
            text = resp.text
            for pattern in [
                r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']{10,})["\']',
                r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{10,})["\']',
            ]:
                m = re.search(pattern, text)
                if m:
                    return m.group(1)
        return ""
    except Exception as e:
        logger.error(f"抓取 Threads 失敗: {e}")
        return ""

async def analyze_with_gemini(content: str, url: str, platform: str) -> dict:
    prompt = f"""你是一個社群貼文分析助手。請分析以下來自 {platform} 的貼文，以 JSON 格式回傳（只回傳 JSON，不要有其他文字或 markdown）：

貼文內容：{content}
貼文連結：{url}

{{
  "建議主題": "一句簡短中文標題（15字以內，不含連結）",
  "建議工具": ["從 Claude、Gemini、Notion 中選相關的，可多選"],
  "建議重點": ["從 AI版本更新、Vibe coding、實用功能 中選相關的，可多選，不符合可建議新選項"],
  "分析說明": "一句話說明這篇貼文在講什麼"
}}"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2}
    }
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(api_url, json=payload)
            resp.raise_for_status()
            raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            raw = re.sub(r"```json|```", "", raw).strip()
            return json.loads(raw)
    except Exception as e:
        logger.error(f"Gemini 分析失敗: {e}")
        return {"建議主題": "未能自動分析", "建議工具": [], "建議重點": [], "分析說明": "請手動填寫"}

async def save_to_notion(title: str, tools: list, focus: list) -> bool:
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    body = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "收藏貼文主題": {"title": [{"type": "text", "text": {"content": title}}]},
            "貼文適用工具": {"multi_select": [{"name": t} for t in tools]},
            "貼文重點":     {"multi_select": [{"name": f} for f in focus]},
        }
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post("https://api.notion.com/v1/pages", headers=headers, json=body)
            resp.raise_for_status()
            return True
    except Exception as e:
        logger.error(f"Notion 寫入失敗: {e}")
        return False

def kb(options: list, extras: list = None) -> ReplyKeyboardMarkup:
    rows = [[o] for o in options]
    if extras:
        rows += [[e] for e in extras]
    return ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True)

# ── 入口 ─────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text(
        "👋 嗨！我是你的貼文收藏助手。\n\n"
        "請傳給我：\n"
        "• Threads 貼文連結（自動抓內容）\n"
        "• IG 貼文文字（直接複製貼上）",
        reply_markup=ReplyKeyboardRemove()
    )
    return WAIT_CONTENT

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("已取消。", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ── Step 1：接收貼文 ─────────────────────────────────────────────────────────

async def step_receive_content(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    ctx.user_data.clear()

    if is_threads_url(text):
        await update.message.reply_text("🔍 正在抓取 Threads 內容，請稍候...")
        content = await fetch_threads_content(text)
        if not content:
            await update.message.reply_text("⚠️ 抓取失敗，請直接把貼文文字複製貼給我。")
            return WAIT_CONTENT
        ctx.user_data.update({"platform": "Threads", "url": text, "content": content})
        return await _run_analysis(update, ctx)

    elif is_ig_url(text):
        await update.message.reply_text(
            "📸 IG 私密帳號無法直接抓取。\n請把貼文文字複製貼給我，再問你連結 🙂"
        )
        return WAIT_CONTENT

    else:
        # 純文字 → IG 貼文
        ctx.user_data.update({"platform": "Instagram", "content": text, "url": ""})
        await update.message.reply_text(
            "📎 請提供這篇貼文的 IG 連結：",
            reply_markup=ReplyKeyboardRemove()
        )
        return WAIT_IG_LINK

async def step_receive_ig_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["url"] = update.message.text.strip()
    return await _run_analysis(update, ctx)

async def _run_analysis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Gemini 分析中...")
    result = await analyze_with_gemini(
        ctx.user_data["content"], ctx.user_data["url"], ctx.user_data["platform"]
    )
    ctx.user_data.update({
        "analysis": result,
        "sel_tools": [],
        "sel_focus": [],
    })
    emoji = "🧵" if ctx.user_data["platform"] == "Threads" else "📸"
    await update.message.reply_text(
        f"{emoji} *分析完成！*\n📝 {result['分析說明']}\n\n接下來逐一確認各欄位：",
        parse_mode="Markdown"
    )
    return await _ask_tools(update, ctx)

# ── Step 2：選工具 ────────────────────────────────────────────────────────────

async def _ask_tools(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sel = ctx.user_data["sel_tools"]
    suggested = ctx.user_data["analysis"].get("建議工具", [])
    remaining = [t for t in TOOLS_OPTIONS if t not in sel]
    hint = f"（建議：{', '.join(suggested)}）" if suggested and not sel else ""

    await update.message.reply_text(
        f"🛠 *貼文適用工具* {hint}\n"
        f"已選：{', '.join(sel) if sel else '（尚未選擇）'}\n\n"
        f"選完請點「✅ 完成」：",
        parse_mode="Markdown",
        reply_markup=kb(remaining, extras=["➕ 新增工具", "✅ 完成"])
    )
    return SELECT_TOOLS

async def step_select_tools(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "✅ 完成":
        if not ctx.user_data["sel_tools"]:
            await update.message.reply_text("⚠️ 請至少選一個工具！")
            return await _ask_tools(update, ctx)
        return await _ask_focus(update, ctx)

    if text == "➕ 新增工具":
        await update.message.reply_text(
            "✏️ 請輸入新工具名稱：",
            reply_markup=ReplyKeyboardRemove()
        )
        return INPUT_NEW_TOOL  # ← 進入完全獨立的狀態

    if text in TOOLS_OPTIONS and text not in ctx.user_data["sel_tools"]:
        ctx.user_data["sel_tools"].append(text)
    return await _ask_tools(update, ctx)

async def step_input_new_tool(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # 這個狀態只會接收「新工具名稱」，絕對不會跟其他流程混淆
    name = update.message.text.strip()
    if name not in TOOLS_OPTIONS:
        TOOLS_OPTIONS.append(name)
    if name not in ctx.user_data["sel_tools"]:
        ctx.user_data["sel_tools"].append(name)
    await update.message.reply_text(f"✅ 已新增工具「{name}」")
    return await _ask_tools(update, ctx)

# ── Step 3：選重點 ────────────────────────────────────────────────────────────

async def _ask_focus(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sel = ctx.user_data["sel_focus"]
    suggested = ctx.user_data["analysis"].get("建議重點", [])
    remaining = [f for f in FOCUS_OPTIONS if f not in sel]
    hint = f"（建議：{', '.join(suggested)}）" if suggested and not sel else ""

    await update.message.reply_text(
        f"🎯 *貼文重點* {hint}\n"
        f"已選：{', '.join(sel) if sel else '（尚未選擇）'}\n\n"
        f"選完請點「✅ 完成」：",
        parse_mode="Markdown",
        reply_markup=kb(remaining, extras=["➕ 新增重點", "✅ 完成"])
    )
    return SELECT_FOCUS

async def step_select_focus(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "✅ 完成":
        if not ctx.user_data["sel_focus"]:
            await update.message.reply_text("⚠️ 請至少選一個重點！")
            return await _ask_focus(update, ctx)
        return await _ask_title(update, ctx)

    if text == "➕ 新增重點":
        await update.message.reply_text(
            "✏️ 請輸入新重點名稱：",
            reply_markup=ReplyKeyboardRemove()
        )
        return INPUT_NEW_FOCUS  # ← 進入完全獨立的狀態

    if text in FOCUS_OPTIONS and text not in ctx.user_data["sel_focus"]:
        ctx.user_data["sel_focus"].append(text)
    return await _ask_focus(update, ctx)

async def step_input_new_focus(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # 這個狀態只會接收「新重點名稱」，絕對不會跟其他流程混淆
    name = update.message.text.strip()
    if name not in FOCUS_OPTIONS:
        FOCUS_OPTIONS.append(name)
    if name not in ctx.user_data["sel_focus"]:
        ctx.user_data["sel_focus"].append(name)
    await update.message.reply_text(f"✅ 已新增重點「{name}」")
    return await _ask_focus(update, ctx)

# ── Step 4：確認主題 ──────────────────────────────────────────────────────────

async def _ask_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    suggested = ctx.user_data["analysis"].get("建議主題", "")
    url = ctx.user_data.get("url", "")
    draft = f"{suggested}｜{url}" if url else suggested
    ctx.user_data["draft_title"] = draft

    await update.message.reply_text(
        f"📌 *收藏貼文主題*\n\n`{draft}`\n\n請選擇：",
        parse_mode="Markdown",
        reply_markup=kb(["✅ 使用此主題", "✏️ 修改主題"])
    )
    return REVIEW_TITLE

async def step_review_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "✅ 使用此主題":
        return await _show_summary(update, ctx)

    if text == "✏️ 修改主題":
        url = ctx.user_data.get("url", "")
        note = f"（連結 {url} 會自動附在後面）" if url else ""
        await update.message.reply_text(
            f"✏️ 請輸入新的主題名稱：\n{note}",
            reply_markup=ReplyKeyboardRemove()
        )
        return INPUT_NEW_TITLE  # ← 進入完全獨立的狀態

    return REVIEW_TITLE

async def step_input_new_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # 這個狀態只會接收「新主題名稱」，絕對不會跟其他流程混淆
    name = update.message.text.strip()
    url = ctx.user_data.get("url", "")
    ctx.user_data["draft_title"] = f"{name}｜{url}" if url else name
    await update.message.reply_text(f"✅ 已更新主題：\n`{ctx.user_data['draft_title']}`", parse_mode="Markdown")
    return await _show_summary(update, ctx)

# ── Step 5：最終確認 ──────────────────────────────────────────────────────────

async def _show_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    title = ctx.user_data["draft_title"]
    tools = ctx.user_data["sel_tools"]
    focus = ctx.user_data["sel_focus"]
    await update.message.reply_text(
        f"📋 *最終確認*\n\n"
        f"🏷 主題：{title}\n"
        f"🛠 工具：{', '.join(tools)}\n"
        f"🎯 重點：{', '.join(focus)}\n\n"
        f"確認存入 Notion 嗎？",
        parse_mode="Markdown",
        reply_markup=kb(["✅ 確認存入", "❌ 取消"])
    )
    return FINAL_CONFIRM

async def step_final_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "❌ 取消":
        ctx.user_data.clear()
        await update.message.reply_text("已取消。有需要再傳貼文給我 🙂", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    if text == "✅ 確認存入":
        await update.message.reply_text("⏳ 寫入 Notion 中...")
        ok = await save_to_notion(
            ctx.user_data["draft_title"],
            ctx.user_data["sel_tools"],
            ctx.user_data["sel_focus"]
        )
        if ok:
            await update.message.reply_text("🎉 成功存入 Notion！\n\n下一篇貼文傳過來就好 👋", reply_markup=ReplyKeyboardRemove())
        else:
            await update.message.reply_text("❌ Notion 寫入失敗，請確認 NOTION_TOKEN 是否正確。", reply_markup=ReplyKeyboardRemove())
        ctx.user_data.clear()
        return ConversationHandler.END

    return FINAL_CONFIRM

# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, step_receive_content),
        ],
        states={
            WAIT_CONTENT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, step_receive_content)],
            WAIT_IG_LINK:    [MessageHandler(filters.TEXT & ~filters.COMMAND, step_receive_ig_link)],
            SELECT_TOOLS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, step_select_tools)],
            INPUT_NEW_TOOL:  [MessageHandler(filters.TEXT & ~filters.COMMAND, step_input_new_tool)],
            SELECT_FOCUS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, step_select_focus)],
            INPUT_NEW_FOCUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_input_new_focus)],
            REVIEW_TITLE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, step_review_title)],
            INPUT_NEW_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_input_new_title)],
            FINAL_CONFIRM:   [MessageHandler(filters.TEXT & ~filters.COMMAND, step_final_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    logger.info("✅ Bot 啟動中...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
