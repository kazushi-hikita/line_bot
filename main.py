from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os
import json
import asyncio
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import math
import re

# asyncioループの確保
try:
    asyncio.get_running_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

app = FastAPI()

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

DATA_FILE = "group_data.json"

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def notify_and_reset():
    data = load_data()
    now = datetime.now()
    last_month = now.replace(day=1) - timedelta(days=1)
    last_month_str = f"{last_month.month}月"

    for group_id, info in data.items():
        if "users" not in info:
            continue

        user_list = []
        for user_id, user_info in info["users"].items():
            try:
                if group_id:
                    profile = line_bot_api.get_group_member_profile(group_id, user_id) if user_id != "不明なユーザー" else None
                    user_name = profile.display_name if profile else user_id
                else:
                    user_name = user_id
            except:
                user_name = user_id
            user_list.append((user_name, user_info))
        user_list.sort(key=lambda x: x[0])

        for user_name, user_info in user_list:
            total = user_info.get("total", 0)
            details = user_info.get("details", {})
            detail_lines = [
                f"　- {k}: {v['total']:,} 円（{v['count']} 回）" for k, v in details.items()
            ]
            message = f"【{last_month_str}結果発表】\n{user_name}さんの今月の支出は {total:,} 円です！\n内訳:\n" + "\n".join(detail_lines)
            line_bot_api.push_message(group_id, TextSendMessage(text=message))

        for user_id in info["users"]:
            info["users"][user_id]["total"] = 0
            info["users"][user_id]["details"] = {}
            info["users"][user_id]["history"] = []

    save_data(data)

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        return "Invalid signature", 400
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    lines = text.split("\n")

    user_id = event.source.user_id
    group_id = getattr(event.source, "group_id", None)

    user_name = "不明なユーザー"
    try:
        if group_id:
            profile = line_bot_api.get_group_member_profile(group_id, user_id)
        else:
            profile = line_bot_api.get_profile(user_id)
        user_name = profile.display_name
    except:
        pass

    if len(lines) >= 1:
        first_line = lines[0].strip()
    else:
        first_line = ""

    data = load_data()

    if first_line == "today":
        if group_id and group_id in data and "users" in data[group_id] and user_id in data[group_id]["users"]:
            user_info = data[group_id]["users"][user_id]
            today = datetime.now().date()
            today_total = 0
            today_details = {}

            for record in user_info.get("history", []):
                record_time = record.get("timestamp")
                if record_time:
                    try:
                        rec_date = datetime.fromisoformat(record_time).date()
                    except:
                        continue
                    if rec_date == today:
                        usage = record["usage"]
                        amount = record["amount"]
                        count = record["count"]

                        today_total += amount
                        if usage not in today_details:
                            today_details[usage] = {"total": 0, "count": 0}
                        today_details[usage]["total"] += amount
                        today_details[usage]["count"] += count

            if today_total == 0:
                reply = f"{user_name}さん、今日はまだ支出の記録がありません！"
            else:
                detail_lines = [
                    f"　- {k}: {v['total']:,} 円（{v['count']} 回）" for k, v in today_details.items()
                ]
                reply = f"{user_name}さん、本日の支出合計は {today_total:,} 円です！\n内訳:\n" + "\n".join(detail_lines)
        else:
            reply = f"{user_name}さん、まだ支出の記録がありません、、"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # この後に既存の check や 登録処理などを続けて記述してください
    # 各履歴追加の箇所には以下のように timestamp を追加するのを忘れずに：
    # "timestamp": datetime.now().isoformat()

@app.get("/ping_html", response_class=HTMLResponse)
@app.head("/ping_html", response_class=HTMLResponse)
async def ping_html():
    return "<h1>I'm alive!</h1>"

scheduler = AsyncIOScheduler()

@app.on_event("startup")
async def startup_event():
    scheduler.start()
    scheduler.add_job(notify_and_reset, CronTrigger(day=1, hour=9, minute=0))
