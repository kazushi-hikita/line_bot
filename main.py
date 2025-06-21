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

            uid = uid_name_map.get(uname, uname)

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

        reply = f"{user_name}さん、catchコマンドのデータを取り込みました。合計 {total_added:,} 円を現在の記録に加算しました！"
