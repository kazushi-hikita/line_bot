from fastapi import FastAPI, Request
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

# asyncioループの確保
try:
    asyncio.get_running_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

app = FastAPI()

# LINEトークン読み込み
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
    now = datetime.now()
    last_month = now.replace(day=1) - timedelta(days=1)
    last_month_str = f"{last_month.month}月"

    for group_id, info in data.items():
        if "users" not in info:
            continue

        # ユーザー名順にソートして通知
        user_list = []
        for user_id, user_info in info["users"].items():
            try:
                profile = line_bot_api.get_group_member_profile(group_id, user_id)
                user_name = profile.display_name
            except:
                user_name = "不明なユーザー"
            user_list.append((user_name, user_info))
        user_list.sort(key=lambda x: x[0])

        for user_name, user_info in user_list:
            total = user_info.get("total", 0)
            details = user_info.get("details", {})
            detail_lines = [f"　- {k}: {v:,} 円" for k, v in details.items()]
            message = f"【{last_month_str}結果発表】\n{user_name}さんの今月の支出は {total:,} 円です。\n内訳:\n" + "\n".join(detail_lines)
            line_bot_api.push_message(group_id, TextSendMessage(text=message))

        # リセット
        for user_id in info["users"]:
            info["users"][user_id]["total"] = 0
            info["users"][user_id]["details"] = {}

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

    user_id = event.source.user_id
    group_id = getattr(event.source, "group_id", None)

    # ユーザー名取得
    user_name = "不明なユーザー"
    try:
        if group_id:
            profile = line_bot_api.get_group_member_profile(group_id, user_id)
        else:
            profile = line_bot_api.get_profile(user_id)
        user_name = profile.display_name
    except:
        pass

    if len(lines) == 1:
        first_line = lines[0].strip()

        # コマンド判定
        if first_line == "debug":
            if not debug_mode:
                debug_mode = True
                asyncio.create_task(debug_notify())
            reply = f"{user_name}さん、デバッグモード（5分ごと通知）を開始したよ！"

        elif first_line == "check":
            data = load_data()
            if group_id and group_id in data and "users" in data[group_id] and user_id in data[group_id]["users"]:
                user_info = data[group_id]["users"][user_id]
                total = user_info.get("total", 0)
                details = user_info.get("details", {})
                detail_lines = [f"　- {k}: {v:,} 円" for k, v in details.items()]
                reply = f"{user_name}さん、あなたの支出は {total:,} 円です。\n内訳:\n" + "\n".join(detail_lines)
            else:
                reply = f"{user_name}さん、まだ支出の記録がありません。"

        elif first_line == "check_all" and group_id:
            data = load_data()
            if group_id not in data or "users" not in data[group_id] or not data[group_id]["users"]:
                reply = "まだ支出の記録がありません。"
            else:
                users = data[group_id]["users"]
                user_list = []
                for uid, info in users.items():
                    try:
                        profile = line_bot_api.get_group_member_profile(group_id, uid)
                        uname = profile.display_name
                    except:
                        uname = "不明なユーザー"
                    user_list.append((uname, info))
                user_list.sort(key=lambda x: x[0])

                messages = []
                for uname, info in user_list:
                    total = info.get("total", 0)
                    details = info.get("details", {})
                    detail_lines = [f"　- {k}: {v:,} 円" for k, v in details.items()]
                    messages.append(f"{uname} さん: {total:,} 円\n" + "\n".join(detail_lines))

                reply = "【途中結果】\n" + "\n\n".join(messages)

        elif first_line == "help":
            reply = (
                "📘 使用可能なコマンド一覧:\n"
                "・1行目: 支出金額_半角数字のみ（必須）\n"
                "・2行目: 使用用途_自由文字（必須）\n"
                "・3行目: 割り勘人数_半角数字のみ（任意）\n"
                "・check: 自分の途中結果を確認\n"
                "・check_all: グループ全体の途中結果を確認\n"
                "・debug: 5分おきに集計（デバッグ用）"
            )

        else:
            reply = f"{user_name}さん、コマンドが認識できません。help と入力して使い方を確認してください。"

    elif len(lines) >= 2:
        # 3行目は任意
        first_line = lines[0].strip()
        usage = lines[1].strip()
        third_line = lines[2].strip() if len(lines) >= 3 else ""

        # 金額・使用用途必須チェック
        if not first_line.isdigit():
            reply = f"{user_name}さん、1行目は半角数字で金額を入力してください。"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return
        if not usage:
            reply = f"{user_name}さん、2行目は使用用途を必ず入力してください。"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        share_count = int(third_line) if third_line.isdigit() and int(third_line) > 0 else 1

        amount = int(first_line)
        share_amount = math.ceil(amount / share_count)

        data = load_data()
        if group_id not in data:
            data[group_id] = {"users": {}}
        if "users" not in data[group_id]:
            data[group_id]["users"] = {}
        if user_id not in data[group_id]["users"]:
            data[group_id]["users"][user_id] = {"total": 0, "details": {}}

        data[group_id]["users"][user_id]["total"] += share_amount
        details = data[group_id]["users"][user_id]["details"]
        if usage not in details:
            details[usage] = 0
        details[usage] += share_amount

        save_data(data)

        if share_count > 1:
            reply = (
                f"{user_name}さん、{amount:,} 円を {share_count} 人で割り勘し、"
                f"1人あたり {share_amount:,} 円（用途：{usage}）で記録しました。"
            )
        else:
            reply = f"{user_name}さん、支出金額 {share_amount:,} 円（用途：{usage}）で記録したよ！"

    else:
        reply = f"{user_name}さん、割り勘人数をうまく読み取れませんでした。"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

@app.get("/uptimerobot")
async def root():
    return {"status": "ok"}

scheduler = AsyncIOScheduler()
scheduler.add_job(notify_and_reset, CronTrigger(day=1, hour=9, minute=0))

@app.on_event("startup")
async def start_scheduler():
    scheduler.start()
