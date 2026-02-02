# ... (前面的程式碼保持不變) ...

    # 👉 行事曆專用查詢 (修正版：全量抓取 + AI 分類)
    def get_calendar(self, user_query):
        try:
            now = datetime.now()
            target_year = now.year
            target_month = now.month

            # 1. 解析月份 (支援 "3月", "三月", "下個月")
            match = re.search(r'(\d+|[一二三四五六七八九十]+)月', user_query)
            if match:
                raw_month = match.group(1)
                cn_map = {'一':1, '二':2, '三':3, '四':4, '五':5, '六':6, '七':7, '八':8, '九':9, '十':10, '十一':11, '十二':12}
                if raw_month.isdigit():
                    target_month = int(raw_month)
                elif raw_month in cn_map:
                    target_month = cn_map[raw_month]
            elif "下個月" in user_query:
                target_month += 1
                if target_month > 12:
                    target_month = 1
                    target_year += 1

            # 2. SQL 查詢該月份所有活動 (不做任何 Python 過濾)
            query_date_str = f"{target_year}/{target_month:02d}%"
            
            # 抓取 date, unit, title, url, content (content 是活動名稱)
            sql = "SELECT date, unit, title, url, content FROM knowledge WHERE category='行事曆' AND date LIKE ? ORDER BY date ASC"
            self.cursor.execute(sql, (query_date_str,))
            rows = self.cursor.fetchall()

            if not rows: return None, target_month

            # 3. 格式化原始資料給 AI
            formatted_data = ""
            for r in rows:
                # r[0]=date, r[1]=unit, r[2]=title, r[3]=url, r[4]=content
                # 若資料庫中 url 為 '無'，則留空讓 Prompt 處理
                link = r[3] if r[3] and r[3] != '無' else 'https://www.nihs.tp.edu.tw/nss/p/calendar'
                
                formatted_data += f"""
日期：{r[0]}
活動：{r[4]}
單位：{r[1]}
連結：{link}
---
"""
            return formatted_data, target_month

        except Exception as e:
            print(f"❌ 行事曆查詢錯誤: {e}")
            return None, 0

    def ask(self, user_query):
        # 1. 直通車 (交通/電話)
        direct = self.check_rules(user_query)
        if direct: return direct

        # 2. 行事曆查詢 (交給 AI 分類)
        if "行事曆" in user_query:
            cal_data, month = self.get_calendar(user_query)
            
            if cal_data:
                # 這裡不需要做搜尋，直接把撈到的全量資料丟給 Gemini
                retrieved_data = cal_data
                
                # 🛠️ 關鍵 Prompt：指示 Gemini 進行分類
                system_instruction = f"""
你現在是內湖高工的行事曆秘書。使用者想查詢 {month} 月份的行事曆。
我會提供該月份的「所有原始活動資料」，請你發揮判斷力，將這些活動區分為兩個區塊呈現：

【區塊一：🏠 家長與學生重要日程】
* 判斷標準：考試 (段考、模擬考)、放假 (補假、寒暑假)、註冊、繳費、全校性典禮、社團活動、競賽、升學相關。
* **這是家長最關心的部分，請放在最前面。**

【區塊二：🏫 學校行政與教師事務】
* 判斷標準：各類會議 (課務會議、校務會議)、設備檢查、作業抽查、教師研習、各處室填報作業。
* 這是學校內部的行政流程，家長通常不需要參與。

【格式要求】：
1.  請務必保留原始連結 (URL)，讓使用者可以點擊。
2.  依照日期排序。
3.  如果該區塊沒有活動，請標註「本月無相關活動」。
"""
                # 修改 user_query 讓 AI 知道只要處理這些資料
                user_query = f"請幫我整理 {month} 月份的行事曆，請依照上述規則分類。\n\n【原始資料】：\n{cal_data}"
            else:
                return f"🔍 查詢不到 {datetime.now().year}年 相關月份的行事曆資訊。"

        else:
            # 3. 一般資料庫搜尋 (公告、規則等)
            retrieved_data = self.search_db(user_query, top_n=5)
            system_instruction = "你是一個親切的內湖高工校園小幫手。請根據檢索資料回答問題，務必附上網址與附件連結。"
            if not retrieved_data:
                 return "您的問題很好！目前公告中暫時找不到相關資訊。建議您聯繫學校 (02-26574874)，我們會記錄並更新。"

        # 4. 呼叫 Gemini
        now = datetime.now()
        prompt = f"""
{system_instruction}
今天是 {now.year}/{now.month}/{now.day}。

【檢索/原始資料】：
{retrieved_data}

【使用者問題】：{user_query}
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt, generation_config={"temperature": 0.3})
            return response.text
        except:
            return "小幫手連線忙碌中，請稍後再試。"
