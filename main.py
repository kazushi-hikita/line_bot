from fastapi import FastAPI, Request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os
import json
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

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
debug_mode = False

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
    for group_id, info in data.items():
        if "users" not in info or not info["users"]:
            continue

        for user_id, amount in info["users"].items():
            if amount > 0:
                try:
                    profile = line_bot_api.get_group_member_profile(group_id, user_id)
                    user_name = profile.display_name
                except:
                    user_name = "不明なユーザー"

                line_bot_api.push_message(
                    group_id,
                    TextSendMessage(text=f"{user_name}さんの今月の支出は {amount} 円です。")
                )

        # 支出リセット（ユーザーごとに）
        for user_id in info["users"]:
            info["users"][user_id] = 0

    save_data(data)

async def debug_notify():
    while debug_mode:
        await asyncio.sleep(300)
        notify_and_reset()

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
    global debug_mode

    text = event.message.text.strip()
    lines = text.split("\n")
    first_line = lines[0]
    second_line = lines[1] if len(lines) > 1 else ""

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

    if first_line == "nito_debug":
        if not debug_mode:
            debug_mode = True
            asyncio.create_task(debug_notify())
        reply = f"{user_name}さん、デバッグモード（5分ごと通知）を開始したよ！"

    elif first_line == "nito_rebuild" and group_id:
        data = load_data()
        if group_id not in data or "users" not in data[group_id] or not data[group_id]["users"]:
            reply = "まだ支出の記録がありません。"
        else:
            users = data[group_id]["users"]
            messages = []
            for user_id, amount in users.items():
                try:
                    profile = line_bot_api.get_group_member_profile(group_id, user_id)
                    user_name = profile.display_name
                except:
                    user_name = "不明なユーザー"
                messages.append(f"{user_name} さん: {amount} 円")
            reply = "【途中結果】\n" + "\n".join(messages)
    
    elif first_line == "途中経過":
        data = load_data()
        if group_id and group_id in data:
            group_info = data[group_id]
            user_spending = group_info.get("users", {}).get(user_id, 0)
            reply = f"{user_name}さん、あなたの支出は {user_spending} 円です。"
        else:
            reply = f"{user_name}さん、まだ支出の記録がありません。"

    # 割り勘処理
    elif first_line.isdigit() and second_line == "割り勘" and group_id:
        total_amount = int(first_line)
        try:
            members = []
            next_page_token = None
            while True:
                response = line_bot_api.get_group_members_ids(group_id, start=next_page_token)
                members.extend(response.member_ids)
                next_page_token = response.next
                if not next_page_token:
                    break

            num_members = len(members)
            if num_members == 0:
                reply = "グループのメンバー数が取得できませんでした。"
            else:
                share = total_amount // num_members

                data = load_data()
                if group_id not in data:
                    data[group_id] = {"users": {}}
                if "users" not in data[group_id]:
                    data[group_id]["users"] = {}

                for member_id in members:
                    if member_id not in data[group_id]["users"]:
                        data[group_id]["users"][member_id] = 0
                    data[group_id]["users"][member_id] += share

                save_data(data)
                reply = (
                    f"{user_name}さん、割り勘で合計 {total_amount} 円を"
                    f"グループメンバー {num_members} 人で分割しました。\n"
                    f"一人当たり {share} 円ずつ加算しました。"
                )
        except Exception as e:
            reply = f"割り勘処理でエラーが発生しました: {str(e)}"

    elif first_line.isdigit():
        amount = int(first_line)
        if group_id:
            data = load_data()
            if group_id not in data:
                data[group_id] = {"users": {}}
            if "users" not in data[group_id]:
                data[group_id]["users"] = {}
            if user_id not in data[group_id]["users"]:
                data[group_id]["users"][user_id] = 0
            data[group_id]["users"][user_id] += amount
            save_data(data)
        reply = f"{user_name}さん、支出金額を{amount}円で記録したよ！"
    else:
        reply = f"{user_name}さん、他所で話してくれや。"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )

@app.get("/uptimerobot")
async def root():
    return {"status": "ok"}

scheduler = AsyncIOScheduler()
scheduler.add_job(notify_and_reset, CronTrigger(day=1, hour=9, minute=0))

@app.on_event("startup")
async def start_scheduler():
    scheduler.start()
