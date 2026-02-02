# ... (前面的 SQLiteBrain 定義保持不變) ...

    def ask(self, user_query):
        # 1. 基礎規則 (交通、電話) - 優先權最高
        q = user_query.lower()
        direct = self.check_rules(user_query)
        if direct: return direct

        # 2. AI 聯想關鍵字：將「合作社有賣泡麵嗎」擴展為搜尋詞
        # 我們直接在 search_db 之前執行聯想
        ai_keywords = self.generate_keywords(user_query)
        
        # 3. 執行檢索 (包含公告、行事曆、靜態資訊)
        retrieved_data = self.search_db(ai_keywords, top_n=10)

        # 4. 針對「日期/開學/行事曆」問題的自動補全
        # 只要問題有日期相關，就強迫補充當月背景，避免它找不到
        if any(k in user_query for k in ['何時', '日期', '幾號', '開學', '考試', '放假', '行事曆']):
             cal_extra, month, source_url = self.get_monthly_calendar(user_query)
             if cal_extra:
                 retrieved_data = f"【重點行事曆背景資料】:\n{cal_extra}\n(參考網址: {source_url})\n---\n" + retrieved_data
        else:
             source_url = "https://www.nihs.tp.edu.tw"

        # 5. 生成最終回答 (嚴格要求型態)
        now = datetime.now()
        prompt = f"""
你是一個親切的內湖高工校園小幫手。
今天是 {now.year}/{now.month}/{now.day}。
請根據下方的【檢索資料】回答家長的【問題】：『{user_query}』

【回答準則】：
1. 語氣要親切、有禮貌（繁體中文）。
2. **務必附上「網址」**：如果資料中有連結，請直接提供給家長點擊。
3. **提及附件**：如果資料有附件（如 PDF、Word），請提醒家長可以點擊連結下載。
4. 如果資料中沒有答案，請誠實說「目前公告中找不到相關資訊」，建議家長直接聯繫學校。
5. **精確日期**：若問到行事曆或日期，請從檢索資料中精確提取回答。
6. **關聯思考**：如果問題提到特定物品（如泡麵）或人名（如校長），請仔細閱讀檢索資料中的每一項內容（包含內容摘要），只要有相關資訊就必須整理在回答中。

【檢索資料】：
{retrieved_data}

【家長問題】：{user_query}
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            # 降低 temperature 確保穩定性
            response = model.generate_content(prompt, generation_config={"temperature": 0.1})
            return response.text
        except:
            return "小幫手忙碌中，請稍後再試。"
