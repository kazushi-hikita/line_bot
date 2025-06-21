from fastapi import FastAPI, Request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, MemberJoinedEvent
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

# LINEトークン読み込み
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

DATA_FILE = "group_data.json"
NICKNAME_FILE = "nicknames.json"

# ニックネーム管理
def load_nicknames():
    if not os.path.exists(NICKNAME_FILE):
        return {}
    with open(NICKNAME_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_nicknames(data):
    with open(NICKNAME_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

nicknames = load_nicknames()
waiting_for_nickname = set()  # ニックネーム入力待ちのuser_id集合

# 支出データ管理
def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# 毎月1日に集計して通知しリセット
def notify_and_reset():
    data = load_data()
    now = datetime.now()
    last_month = now.replace(day=1) - timedelta(days=1)
    last_month_str = f"{last_month.month}月"

    for group_id, info in data.items():
        if "users" not in info:
            continue

        user_list = []
        for user_key, user_info in info["users"].items():
            # user_key はニックネームかuser_id
            user_list.append((user_key, user_info))
        user_list.sort(key=lambda x: x[0])

        for user_name, user_info in user_list:
            total = user_info.get("total", 0)
            details = user_info.get("details", {})
            detail_lines = [
                f"　- {k}: {v['total']:,} 円（{v['count']} 回）" for k, v in details.items()
            ]
            message = f"【{last_month_str}結果発表】\n{user_name}さんの今月の支出は {total:,} 円です！\n内訳:\n" + "\n".join(detail_lines)
            line_bot_api.push_message(group_id, TextSendMessage(text=message))

        # リセット
        for user_key in info["users"]:
            info["users"][user_key]["total"] = 0
            info["users"][user_key]["details"] = {}

    save_data(data)

def clear_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False, indent=2)

@app.post("/callback")
async def callback(request: Request):
    signature = request.headers.get("X-Line-Signature")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        return "Invalid signature", 400
    return "OK"

# グループ参加時にニックネーム入力を促す
@handler.add(MemberJoinedEvent)
def handle_member_join(event):
    group_id = event.source.group_id
    for member in event.joined.members:
        user_id = member.user_id
        if user_id not in nicknames:
            waiting_for_nickname.add(user_id)
            line_bot_api.push_message(
                group_id,
                TextSendMessage(text=f"{user_id}さん、ニックネームを教えてください！このチャットで返信してください。")
            )

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    lines = text.split("\n")

    user_id = event.source.user_id
    group_id = getattr(event.source, "group_id", None)

    # ニックネーム未登録ならニックネームとして登録
    if user_id in waiting_for_nickname:
        # ニックネーム重複チェック（シンプル）
        if text in nicknames.values():
            reply = f"そのニックネームはすでに使われています。別のニックネームを教えてください。"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        nicknames[user_id] = text
        save_nicknames(nicknames)
        waiting_for_nickname.remove(user_id)
        reply = f"ニックネーム「{text}」を登録しました！ありがとうございます。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # user_idの代わりにニックネームをキーとして使用
    user_key = nicknames.get(user_id, user_id)

    if len(lines) >= 1:
        first_line = lines[0].strip()
    else:
        first_line = ""

    if first_line == "debug":
        notify_and_reset()
        reply = f"{user_key}さん、集計を実施しました！（デバッグモードはありません）"

    elif first_line == "check":
        data = load_data()
        if group_id and group_id in data and "users" in data[group_id] and user_key in data[group_id]["users"]:
            user_info = data[group_id]["users"][user_key]
            total = user_info.get("total", 0)
            details = user_info.get("details", {})
            detail_lines = [
                f"　- {k}: {v['total']:,} 円（{v['count']} 回）" for k, v in details.items()
            ]
            reply = f"{user_key}さん、あなたの支出は {total:,} 円です！\n内訳:\n" + "\n".join(detail_lines)
        else:
            reply = f"{user_key}さん、まだ支出の記録がありません、、"

    elif first_line == "check_all" and group_id:
        data = load_data()
        if group_id not in data or "users" not in data[group_id] or not data[group_id]["users"]:
            reply = "まだ支出の記録がありません、、"
        else:
            users = data[group_id]["users"]
            user_list = []
            for key, info in users.items():
                user_list.append((key, info))
            user_list.sort(key=lambda x: x[0])

            messages = []
            for uname, info in user_list:
                total = info.get("total", 0)
                details = info.get("details", {})
                detail_lines = [
                    f"　- {k}: {v['total']:,} 円（{v['count']} 回）" for k, v in details.items()
                ]
                messages.append(f"{uname} さん: {total:,} 円\n" + "\n".join(detail_lines))

            reply = "【途中結果】\n" + "\n\n".join(messages)

    elif first_line == "catch" and group_id:
        pasted_text = "\n".join(lines[1:]).strip()
        if not pasted_text:
            reply = f"{user_key}さん、catchコマンドの2行目以降にcheck_allの結果をペーストしてください、、"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        data = load_data()
        if group_id not in data:
            data[group_id] = {"users": {}}
        if "users" not in data[group_id]:
            data[group_id]["users"] = {}

        user_blocks = re.split(r'\n(?=[^\s].+ さん: \d[\d,]* 円)', pasted_text)

        # 表示名→user_key のマッピング構築
        uid_name_map = {}
        for key in data[group_id]["users"].keys():
            uid_name_map[key] = key

        total_added = 0
        for block in user_blocks:
            lines_block = block.strip().split("\n")
            if not lines_block:
                continue

            header = lines_block[0].strip()
            m = re.match(r"(.+?) さん: ([\d,]+) 円", header)
            if not m:
                continue
            uname = m.group(1)
            total = int(m.group(2).replace(",", ""))

            uid = uid_name_map.get(uname, "不明なユーザー")

            if uid not in data[group_id]["users"]:
                data[group_id]["users"][uid] = {"total": 0, "details": {}}
            data[group_id]["users"][uid]["total"] += total
            total_added += total

            details = data[group_id]["users"][uid]["details"]

            for detail_line in lines_block[1:]:
                dm = re.match(r"[-ー・\s]*\s*(.+?):\s*([\d,]+)\s*円", detail_line.strip())
                if dm:
                    usage = dm.group(1)
                    amount = int(dm.group(2).replace(",", ""))
                    if usage not in details:
                        details[usage] = {"total": 0, "count": 0}
                    details[usage]["total"] += amount
                    details[usage]["count"] += 1

        save_data(data)

        reply = f"{user_key}さん、catchコマンドのデータを取り込みました。合計 {total_added:,} 円を現在の記録に加算しました！"

    elif len(lines) >= 2:
        usage = lines[0].strip()  # 1行目：品目（用途）
        amount_line = lines[1].strip()  # 2行目：金額
        third_line = lines[2].strip() if len(lines) >= 3 else ""

        if not amount_line.isdigit():
            reply = f"{user_key}さん、2行目は半角数字で金額を入力してください、、"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return
        if not usage:
            reply = f"{user_key}さん、1行目は使用用途を必ず入力してください、、"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        share_count = int(third_line) if third_line.isdigit() and int(third_line) > 0 else 1

        amount = int(amount_line)
        share_amount = math.ceil(amount / share_count)

        data = load_data()
        if group_id not in data:
            data[group_id] = {"users": {}}
        if "users" not in data[group_id]:
            data[group_id]["users"] = {}
        if user_key not in data[group_id]["users"]:
            data[group_id]["users"][user_key] = {"total": 0, "details": {}}

        user_data = data[group_id]["users"][user_key]
        user_data["total"] += share_amount

        details = user_data["details"]
        if usage not in details:
            details[usage] = {"total": 0, "count": 0}
        details[usage]["total"] += share_amount
        details[usage]["count"] += 1

        save_data(data)

        if share_count > 1:
            reply = (
                f"{user_key}さん、「{usage}」で {amount:,} 円を {share_count} 人で割り勘し、"
                f"1人あたり {share_amount:,} 円で記録しました！"
            )
        else:
            reply = f"{user_key}さん、「{usage}」の支出金額 {share_amount:,} 円で記録しました！"

    elif first_line == "help":
        reply = (
            "📘 【記載方法】\n"
            "・1行目: 使用用途_自由文字（必須）\n"
            "・2行目: 支出金額_半角数字のみ（必須）\n"
            "・3行目: 割り勘人数_半角数字のみ（任意）\n"
            "📘 【コマンド一覧】\n"
            "・check: 自分の途中結果を確認\n"
            "・check_all: グループ全体の途中結果を確認\n"
            "・debug: 集計実行\n"
            "※グループ参加時にニックネームを聞かれます。"
        )
    else:
        return

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

@app.get("/uptimerobot")
async def root():
    return {"status": "ok"}

scheduler = AsyncIOScheduler()

@app.on_event("startup")
async def startup_event():
    scheduler.start()
    scheduler.add_job(notify_and_reset, CronTrigger(day=1, hour=9, minute=0))
