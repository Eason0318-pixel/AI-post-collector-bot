"""
社群貼文收藏 Bot v4
- 改用純狀態機，完全移除 ConversationHandler
- 根本解決對話狀態混亂問題
"""

import os
import json
import logging
import re
import httpx
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
NOTION_TOKEN   = os.environ["NOTION_TOKEN"]
NOTION_DB_ID   = "336a2a4fd11380419b1ae2907a8ba216"

# 狀態名稱常數
S_WAIT_CONTENT    = "wait_content"
S_WAIT_IG_LINK    = "wait_ig_link"
S_SELECT_TOOLS    = "select_tools"
S_INPUT_NEW_TOOL  = "input_new_tool"
S_SELECT_FOCUS    = "select_focus"
S_INPUT_NEW_FOCUS = "input_new_focus"
S_REVIEW_TITLE    = "review_title"
S_INPUT_NEW_TITLE = "input_new_title"
S_FINAL_CONFIRM   = "final_confirm"

TOOLS_OPTIONS = ["Claude", "Gemini", "Notion"]
FOCUS_OPTIONS = ["AI版本更新", "Vibe coding", "實用功能"]

# ── 狀態管理（每個用戶獨立）────────────────────────────────────────────────────

def get_state(ctx) -> str:
    return ctx.user_data.get("_state", S_WAIT_CONTENT)

def set_state(ctx, state: str):
    ctx.user_data["_state"] = state

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
            for pattern in [
                r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']{10,})["\']',
                r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{10,})["\']',
            ]:
                m = re.search(pattern, resp.text)
                if m:
                    return m.group(1)
        return ""
    except Exception as e:
        logger.error(f"抓取 Threads 失敗: {e}")
        return ""

async def analyze_with_gemini(content: str, url: str, platform: str) -> dict:
    prompt = f"""你是社群貼文分析助手。分析以下 {platform} 貼文，只回傳 JSON，不要有其他文字：

貼文內容：{content}
貼文連結：{url}

{{
  "建議主題": "一句簡短中文標題（15字以內，不含連結）",
  "建議工具": ["從 Claude、Gemini、Notion 中選相關的"],
  "建議重點": ["從 AI版本更新、Vibe coding、實用功能 中選相關的"],
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
        logger.error(f"Gemini 失敗: {e}")
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
        logger.error(f"Notion 失敗: {e}")
        return False

def kb(options: list, extras: list = None) -> ReplyKeyboardMarkup:
    rows = [[o] for o in options]
    if extras:
        rows += [[e] for e in extras]
    return ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True)

# ── 各步驟的回應函式 ───────────────────────────────────────────────────────────

async def send_ask_tools(update, ctx):
    sel = ctx.user_data.get("sel_tools", [])
    suggested = ctx.user_data.get("analysis", {}).get("建議工具", [])
    remaining = [t for t in TOOLS_OPTIONS if t not in sel]
    hint = f"（建議：{', '.join(suggested)}）" if suggested and not sel else ""
    set_state(ctx, S_SELECT_TOOLS)
    await update.message.reply_text(
        f"🛠 *貼文適用工具* {hint}\n"
        f"已選：{', '.join(sel) if sel else '（尚未選擇）'}\n\n"
        f"選完請點「✅ 完成」：",
        parse_mode="Markdown",
        reply_markup=kb(remaining, extras=["➕ 新增工具", "✅ 完成"])
    )

async def send_ask_focus(update, ctx):
    sel = ctx.user_data.get("sel_focus", [])
    suggested = ctx.user_data.get("analysis", {}).get("建議重點", [])
    remaining = [f for f in FOCUS_OPTIONS if f not in sel]
    hint = f"（建議：{', '.join(suggested)}）" if suggested and not sel else ""
    set_state(ctx, S_SELECT_FOCUS)
    await update.message.reply_text(
        f"🎯 *貼文重點* {hint}\n"
        f"已選：{', '.join(sel) if sel else '（尚未選擇）'}\n\n"
        f"選完請點「✅ 完成」：",
        parse_mode="Markdown",
        reply_markup=kb(remaining, extras=["➕ 新增重點", "✅ 完成"])
    )

async def send_ask_title(update, ctx):
    suggested = ctx.user_data.get("analysis", {}).get("建議主題", "")
    url = ctx.user_data.get("url", "")
    draft = f"{suggested}｜{url}" if url else suggested
    ctx.user_data["draft_title"] = draft
    set_state(ctx, S_REVIEW_TITLE)
    await update.message.reply_text(
        f"📌 *收藏貼文主題*\n\n`{draft}`\n\n請選擇：",
        parse_mode="Markdown",
        reply_markup=kb(["✅ 使用此主題", "✏️ 修改主題"])
    )

async def send_summary(update, ctx):
    title = ctx.user_data.get("draft_title", "")
    tools = ctx.user_data.get("sel_tools", [])
    focus = ctx.user_data.get("sel_focus", [])
    set_state(ctx, S_FINAL_CONFIRM)
    await update.message.reply_text(
        f"📋 *最終確認*\n\n"
        f"🏷 主題：{title}\n"
        f"🛠 工具：{', '.join(tools)}\n"
        f"🎯 重點：{', '.join(focus)}\n\n"
        f"確認存入 Notion 嗎？",
        parse_mode="Markdown",
        reply_markup=kb(["✅ 確認存入", "❌ 取消"])
    )

async def run_analysis(update, ctx):
    await update.message.reply_text("🤖 Gemini 分析中...")
    result = await analyze_with_gemini(
        ctx.user_data["content"],
        ctx.user_data.get("url", ""),
        ctx.user_data["platform"]
    )
    ctx.user_data["analysis"] = result
    ctx.user_data["sel_tools"] = []
    ctx.user_data["sel_focus"] = []
    emoji = "🧵" if ctx.user_data["platform"] == "Threads" else "📸"
    await update.message.reply_text(
        f"{emoji} *分析完成！*\n📝 {result['分析說明']}\n\n接下來逐一確認各欄位：",
        parse_mode="Markdown"
    )
    await send_ask_tools(update, ctx)

# ── 主要訊息處理器（純狀態機）────────────────────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    state = get_state(ctx)

    # ── 等待貼文內容 ──────────────────────────────────────────────────────────
    if state == S_WAIT_CONTENT:
        if is_threads_url(text):
            await update.message.reply_text("🔍 正在抓取 Threads 內容，請稍候...")
            content = await fetch_threads_content(text)
            if not content:
                await update.message.reply_text("⚠️ 抓取失敗，請直接把貼文文字複製貼給我。")
                return
            ctx.user_data.update({"platform": "Threads", "url": text, "content": content})
            await run_analysis(update, ctx)
        elif is_ig_url(text):
            await update.message.reply_text(
                "📸 IG 私密帳號無法直接抓取。\n請把貼文文字複製貼給我，再問你連結 🙂"
            )
        else:
            ctx.user_data.update({"platform": "Instagram", "content": text, "url": ""})
            set_state(ctx, S_WAIT_IG_LINK)
            await update.message.reply_text(
                "📎 請提供這篇貼文的 IG 連結：",
                reply_markup=ReplyKeyboardRemove()
            )

    # ── 等待 IG 連結 ───────────────────────────────────────────────────────────
    elif state == S_WAIT_IG_LINK:
        ctx.user_data["url"] = text
        await run_analysis(update, ctx)

    # ── 選擇工具 ───────────────────────────────────────────────────────────────
    elif state == S_SELECT_TOOLS:
        if text == "✅ 完成":
            if not ctx.user_data.get("sel_tools"):
                await update.message.reply_text("⚠️ 請至少選一個工具！")
                await send_ask_tools(update, ctx)
            else:
                await send_ask_focus(update, ctx)
        elif text == "➕ 新增工具":
            set_state(ctx, S_INPUT_NEW_TOOL)
            await update.message.reply_text(
                "✏️ 請輸入新工具名稱：",
                reply_markup=ReplyKeyboardRemove()
            )
        elif text in TOOLS_OPTIONS:
            if text not in ctx.user_data.get("sel_tools", []):
                ctx.user_data.setdefault("sel_tools", []).append(text)
            await send_ask_tools(update, ctx)
        else:
            await send_ask_tools(update, ctx)

    # ── 輸入新工具名稱 ─────────────────────────────────────────────────────────
    elif state == S_INPUT_NEW_TOOL:
        if text not in TOOLS_OPTIONS:
            TOOLS_OPTIONS.append(text)
        ctx.user_data.setdefault("sel_tools", [])
        if text not in ctx.user_data["sel_tools"]:
            ctx.user_data["sel_tools"].append(text)
        await update.message.reply_text(f"✅ 已新增工具「{text}」")
        await send_ask_tools(update, ctx)

    # ── 選擇重點 ───────────────────────────────────────────────────────────────
    elif state == S_SELECT_FOCUS:
        if text == "✅ 完成":
            if not ctx.user_data.get("sel_focus"):
                await update.message.reply_text("⚠️ 請至少選一個重點！")
                await send_ask_focus(update, ctx)
            else:
                await send_ask_title(update, ctx)
        elif text == "➕ 新增重點":
            set_state(ctx, S_INPUT_NEW_FOCUS)
            await update.message.reply_text(
                "✏️ 請輸入新重點名稱：",
                reply_markup=ReplyKeyboardRemove()
            )
        elif text in FOCUS_OPTIONS:
            if text not in ctx.user_data.get("sel_focus", []):
                ctx.user_data.setdefault("sel_focus", []).append(text)
            await send_ask_focus(update, ctx)
        else:
            await send_ask_focus(update, ctx)

    # ── 輸入新重點名稱 ─────────────────────────────────────────────────────────
    elif state == S_INPUT_NEW_FOCUS:
        if text not in FOCUS_OPTIONS:
            FOCUS_OPTIONS.append(text)
        ctx.user_data.setdefault("sel_focus", [])
        if text not in ctx.user_data["sel_focus"]:
            ctx.user_data["sel_focus"].append(text)
        await update.message.reply_text(f"✅ 已新增重點「{text}」")
        await send_ask_focus(update, ctx)

    # ── 確認主題 ───────────────────────────────────────────────────────────────
    elif state == S_REVIEW_TITLE:
        if text == "✅ 使用此主題":
            await send_summary(update, ctx)
        elif text == "✏️ 修改主題":
            url = ctx.user_data.get("url", "")
            note = f"\n（連結 {url} 會自動附在後面）" if url else ""
            set_state(ctx, S_INPUT_NEW_TITLE)
            await update.message.reply_text(
                f"✏️ 請輸入新的主題名稱：{note}",
                reply_markup=ReplyKeyboardRemove()
            )
        else:
            await send_ask_title(update, ctx)

    # ── 輸入新主題 ─────────────────────────────────────────────────────────────
    elif state == S_INPUT_NEW_TITLE:
        url = ctx.user_data.get("url", "")
        ctx.user_data["draft_title"] = f"{text}｜{url}" if url else text
        await update.message.reply_text(
            f"✅ 已更新主題：\n`{ctx.user_data['draft_title']}`",
            parse_mode="Markdown"
        )
        await send_summary(update, ctx)

    # ── 最終確認 ───────────────────────────────────────────────────────────────
    elif state == S_FINAL_CONFIRM:
        if text == "❌ 取消":
            ctx.user_data.clear()
            set_state(ctx, S_WAIT_CONTENT)
            await update.message.reply_text(
                "已取消。有需要再傳貼文給我 🙂",
                reply_markup=ReplyKeyboardRemove()
            )
        elif text == "✅ 確認存入":
            await update.message.reply_text("⏳ 寫入 Notion 中...")
            ok = await save_to_notion(
                ctx.user_data.get("draft_title", ""),
                ctx.user_data.get("sel_tools", []),
                ctx.user_data.get("sel_focus", [])
            )
            if ok:
                await update.message.reply_text(
                    "🎉 成功存入 Notion！\n\n下一篇貼文傳過來就好 👋",
                    reply_markup=ReplyKeyboardRemove()
                )
            else:
                await update.message.reply_text(
                    "❌ Notion 寫入失敗，請確認 NOTION_TOKEN 是否正確。",
                    reply_markup=ReplyKeyboardRemove()
                )
            ctx.user_data.clear()
            set_state(ctx, S_WAIT_CONTENT)
        else:
            await send_summary(update, ctx)

    else:
        # 未知狀態，重置
        ctx.user_data.clear()
        set_state(ctx, S_WAIT_CONTENT)
        await update.message.reply_text(
            "出了點問題，已重置。請重新傳貼文給我：",
            reply_markup=ReplyKeyboardRemove()
        )

# ── /start 和 /cancel ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    set_state(ctx, S_WAIT_CONTENT)
    await update.message.reply_text(
        "👋 嗨！我是你的貼文收藏助手。\n\n"
        "請傳給我：\n"
        "• Threads 貼文連結（自動抓內容）\n"
        "• IG 貼文文字（直接複製貼上）",
        reply_markup=ReplyKeyboardRemove()
    )

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    set_state(ctx, S_WAIT_CONTENT)
    await update.message.reply_text("已取消，重新開始。", reply_markup=ReplyKeyboardRemove())

# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("✅ Bot 啟動中（v4 純狀態機）...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
