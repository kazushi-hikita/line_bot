from fastapi import FastAPI, Request
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os

app = FastAPI()

# 環境変数からトークンを取得
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

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
    first_line = text.split('\n')[0]
    user_id = event.source.user_id

    try:
        source_type = event.source.type
        if source_type == "group":
            group_id = event.source.group_id
            profile = line_bot_api.get_group_member_profile(group_id, user_id)
        elif source_type == "room":
            room_id = event.source.room_id
            profile = line_bot_api.get_room_member_profile(room_id, user_id)
        else:  # "user" = 個人チャット
            profile = line_bot_api.get_profile(user_id)

        user_name = profile.display_name
    except Exception:
        user_name = "大橋"

    if first_line.isdigit():
        reply_text = f"{user_name}さん、支出金額を{first_line}円で記録したよ！"
    else:
        reply_text = f"{user_name}さん、他所で話してくれや。"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


@app.get("/uptimerobot")
async def root():
    return {"status": "ok"}
