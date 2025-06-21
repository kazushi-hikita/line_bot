from fastapi import FastAPI, Request
from fastapi import BackgroundTasks
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

# LINEトークン読み込み
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

DATA_FILE = "group_data.json"
debug_mode = False
debug_task = None

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

def clear_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False, indent=2)

async def debug_notify():
    try:
        while True:
            await asyncio.sleep(300)
            notify_and_reset()
    except asyncio.CancelledError:
        print("Debug task cancelled.")

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
    global debug_mode, debug_task

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

    # --- 取り消しコマンド対応 ---
    if first_line == "取り消し" and group_id:
        data = load_data()
        if group_id not in data or "users" not in data[group_id] or user_id not in data[group_id]["users"]:
            reply = f"{user_name}さん、取り消せる記録がありません、、"
        else:
            users = data[group_id]["users"]
            user_data = users[user_id]
            history = user_data.get("history", [])
            if not history:
                reply = f"{user_name}さん、取り消せる記録がありません、、"
            else:
                last = history.pop()
                usage = last["usage"]
                amount = last["amount"]
                count = last["count"]
    
                # 自分の合計・詳細から減算
                user_data["total"] -= amount
                if usage in user_data["details"]:
                    user_data["details"][usage]["total"] -= amount
                    user_data["details"][usage]["count"] -= count
                    if user_data["details"][usage]["count"] <= 0:
                        del user_data["details"][usage]
    
                # --- もし割り勘で複数人に同額加算されていた場合 ---
                shared_users = []
                for uid, udata in users.items():
                    if uid == user_id:
                        continue
                    if "history" in udata and udata["history"]:
                        if udata["history"][-1]["usage"] == usage and udata["history"][-1]["amount"] == amount:
                            # 一致する履歴があれば削除
                            shared_users.append(uid)
                            udata["history"].pop()
                            udata["total"] -= amount
                            if usage in udata["details"]:
                                udata["details"][usage]["total"] -= amount
                                udata["details"][usage]["count"] -= count
                                if udata["details"][usage]["count"] <= 0:
                                    del udata["details"][usage]
    
                save_data(data)
    
                if shared_users:
                    reply = (
                        f"{user_name}さん、割り勘として登録された「{usage}」 {amount:,} 円の記録を "
                        f"{len(shared_users)+1}人分まとめて取り消しました。"
                    )
                else:
                    reply = (
                        f"{user_name}さん、直近の支出「{usage}」 {amount:,} 円の登録を取り消しました。"
                    )
    
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return


    if first_line == "debug":
        notify_and_reset()
        reply = f"{user_name}さん、集計を実施しました！（デバッグモードではありません）"

    elif first_line == "check":
        data = load_data()
        if group_id and group_id in data and "users" in data[group_id] and user_id in data[group_id]["users"]:
            user_info = data[group_id]["users"][user_id]
            total = user_info.get("total", 0)
            details = user_info.get("details", {})
            detail_lines = [
                f"　- {k}: {v['total']:,} 円（{v['count']} 回）" for k, v in details.items()
            ]
            reply = f"{user_name}さん、あなたの支出は {total:,} 円です！\n内訳:\n" + "\n".join(detail_lines)
        else:
            reply = f"{user_name}さん、まだ支出の記録がありません、、"

    elif first_line == "check_all" and group_id:
        data = load_data()
        if group_id not in data or "users" not in data[group_id] or not data[group_id]["users"]:
            reply = "まだ支出の記録がありません、、"
        else:
            users = data[group_id]["users"]
            user_list = []
            for uid, info in users.items():
                try:
                    profile = line_bot_api.get_group_member_profile(group_id, uid) if uid != "不明なユーザー" else None
                    uname = profile.display_name if profile else uid
                except:
                    uname = uid
                user_list.append((uname, info))
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
            reply = f"{user_name}さん、catchコマンドの2行目以降にcheck_allの結果をペーストしてください、、"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        data = load_data()
        if group_id not in data:
            data[group_id] = {"users": {}}
        if "users" not in data[group_id]:
            data[group_id]["users"] = {}

        user_blocks = re.split(r'\n(?=[^\s].+ さん: \d[\d,]* 円)', pasted_text)

        # 表示名→user_id のマッピング構築
        uid_name_map = {}
        try:
            members = data[group_id]["users"].keys()
            for uid in members:
                if uid != "不明なユーザー":
                    profile = line_bot_api.get_group_member_profile(group_id, uid)
                    uname = profile.display_name
                    uid_name_map[uname] = uid
        except:
            pass

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
                data[group_id]["users"][uid] = {"total": 0, "details": {}, "history": []}
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

        reply = f"{user_name}さん、catchコマンドのデータを取り込みました。合計 {total_added:,} 円を現在の記録に加算しました！"
    
    elif len(lines) >= 2:
        usage = lines[0].strip()  # 1行目：品目（用途）
        amount_line_raw = lines[1].strip()  # 2行目：金額
        third_line = lines[2].strip() if len(lines) >= 3 else ""

        # マイナスも許可（--1000形式は特別扱い）
        is_double_minus = amount_line_raw.startswith("--")
        amount_line = amount_line_raw.lstrip("-")
        if not re.match(r"^\d+$", amount_line):
            reply = "1行目に用途、2行目に半角数字で金額を入力してください、、"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return
        if not usage:
            reply = "1行目に用途、2行目に半角数字で金額を入力してください、、"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        amount = -int(amount_line) if amount_line_raw.startswith("-") else int(amount_line)

        data = load_data()
        if group_id not in data:
            data[group_id] = {"users": {}}
        if "users" not in data[group_id]:
            data[group_id]["users"] = {}

        users = data[group_id]["users"]

        if third_line == "割り勘":
            user_ids = list(users.keys())
            num_users = len(user_ids)
            if num_users == 0:
                reply = "このグループにはまだユーザーが登録されていません、、"
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
                return
            share_amount = math.ceil(amount / num_users)

            for uid in user_ids:
                if uid not in users:
                    users[uid] = {"total": 0, "details": {}, "history": []}
                users[uid]["total"] += share_amount

                details = users[uid]["details"]
                if usage not in details:
                    details[usage] = {"total": 0, "count": 0}
                details[usage]["total"] += share_amount

                # マイナス金額は回数に含めない、--は1減らす
                count_change = -1 if is_double_minus else (0 if share_amount < 0 else 1)
                details[usage]["count"] += count_change

                users[uid]["history"].append({
                    "usage": usage,
                    "amount": share_amount,
                    "count": count_change
                })

            save_data(data)

            reply = (
                f"{user_name}さん、「{usage}」で {amount:,} 円を "
                f"{num_users} 人で割り勘し、1人あたり {share_amount:,} 円で記録しました！"
            )

        else:
            share_count = int(third_line) if third_line.isdigit() and int(third_line) > 0 else 1
            share_amount = math.ceil(amount / share_count)

            if user_id not in users:
                users[user_id] = {"total": 0, "details": {}, "history": []}

            user_data = users[user_id]
            user_data["total"] += share_amount

            details = user_data["details"]
            if usage not in details:
                details[usage] = {"total": 0, "count": 0}
            details[usage]["total"] += share_amount

            # マイナス金額はカウントに含めない、--は-1
            count_change = -1 if is_double_minus else (0 if share_amount < 0 else 1)
            details[usage]["count"] += count_change

            user_data["history"].append({
                "usage": usage,
                "amount": share_amount,
                "count": count_change
            })

            save_data(data)

            if share_count > 1:
                reply = (
                    f"{user_name}さん、「{usage}」で {amount:,} 円を {share_count} 人で割り勘し、"
                    f"1人あたり {share_amount:,} 円で記録しました！"
                )
            else:
                reply = f"{user_name}さん、「{usage}」の支出金額 {share_amount:,} 円で記録しました！"

    elif first_line == "help":
        reply = (
            "📘 【記載方法】\n"
            "・1行目: 使用用途_自由文字（必須）\n"
            "・2行目: 支出金額_半角数字（必須）\n"
            "・3行目: 「割り勘」と入力で当月入力履歴のある人に振り分け（任意）\n"
            "📘 【コマンド一覧】\n"
            "・支出金額「-」付与: 減算\n"
            "・支出金額「--」付与: 減算かつ加算回数を一つデクリメント\n"
            "・取り消し: 1件前の登録を取り消す\n"
            "・check: 自分の途中結果を確認\n"
            "・check_all: グループ全体の途中結果を確認\n"
            "・debug: 結果発表のデバッグ(全記載内容のクリア)\n"
            "・catch: バックアップの取得デバッグ"
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
