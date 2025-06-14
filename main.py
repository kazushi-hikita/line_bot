import re

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

    if len(lines) >= 1:
        first_line = lines[0].strip()
    else:
        first_line = ""

    if first_line == "catch" and group_id:
        # catchコマンドの処理
        pasted_text = "\n".join(lines[1:]).strip()
        if not pasted_text:
            reply = f"{user_name}さん、catchコマンドの2行目以降にcheck_allの結果をペーストしてください。"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
            return

        # 解析開始
        data = load_data()
        if group_id not in data:
            data[group_id] = {"users": {}}
        if "users" not in data[group_id]:
            data[group_id]["users"] = {}

        # ユーザーデータ抽出 正規表現例（名前＋合計＋内訳）
        # 例）username さん: 12,000 円
        user_blocks = re.split(r'\n(?=[^\s].+ さん: \d[\d,]* 円)', pasted_text)

        total_added = 0
        for block in user_blocks:
            lines_block = block.strip().split("\n")
            if not lines_block:
                continue

            # ヘッダー行例: "username さん: 12,000 円"
            header = lines_block[0].strip()
            m = re.match(r"(.+?) さん: ([\d,]+) 円", header)
            if not m:
                continue
            uname = m.group(1)
            total = int(m.group(2).replace(",", ""))

            # user_id不明なので名前をキーにする（あるいは既存のuser_id検索など）
            uid = uname

            if uid not in data[group_id]["users"]:
                data[group_id]["users"][uid] = {"total": 0, "details": {}}
            data[group_id]["users"][uid]["total"] += total
            total_added += total

            details = data[group_id]["users"][uid]["details"]

            # 内訳行例: "　- 昼ごはん: 5,000 円"
            for detail_line in lines_block[1:]:
                dm = re.match(r"　- (.+?): ([\d,]+) 円", detail_line.strip())
                if dm:
                    usage = dm.group(1)
                    amount = int(dm.group(2).replace(",", ""))
                    if usage not in details:
                        details[usage] = 0
                    details[usage] += amount

        save_data(data)

        reply = f"{user_name}さん、catchコマンドのデータを取り込みました。合計 {total_added:,} 円を現在の記録に追加しています。"

    else:
        # ここに既存の金額入力やコマンド処理を書く...
        # （省略、あなたの既存コードを使ってください）
        reply = f"{user_name}さん、コマンドが認識できません。help と入力して使い方を確認してください。"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
