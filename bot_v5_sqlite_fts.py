import os
import re
import json
import sqlite3
import google.generativeai as genai
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime

# ==========================================
# 🔑 核心設定
# ==========================================
MODEL_NAME = 'gemini-2.0-flash'

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==========================================
# 🧠 統一檢索大腦 (智慧聯想 + 背景補充版)
# ==========================================
class UnifiedBrain:
    def __init__(self):
        self.db_path = ':memory:'
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.faq_data = {} 
        self.init_db()
        self.load_data()

    def init_db(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT, 
                content TEXT, 
                category TEXT, 
                date TEXT,
                unit TEXT,
                url TEXT,
                attachments TEXT
            )
        ''')
        self.conn.commit()

    def load_data(self):
        files = ['nihs_knowledge_full.json', 'nihs_faq.json', 'nihs_calendar.json']
        count = 0
        try:
            for filename in files:
                file_path = os.path.join(BASE_DIR, filename)
                if not os.path.exists(file_path): continue
                
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    
                    if filename == 'nihs_faq.json':
                        self.faq_data = data
                        t = data.get('traffic', {})
                        self.cursor.execute("INSERT INTO knowledge (title, content, category, date, unit, url, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                          ("學校交通資訊", f"地址:{t.get('address')} 捷運:{t.get('mrt')} 公車:{t.get('bus')}", "交通", "置頂", "總務處", "https://www.nihs.tp.edu.tw", "無"))
                        for c in data.get('contacts', []):
                            self.cursor.execute("INSERT INTO knowledge (title, content, category, date, unit, url, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                          (f"聯絡電話 {c.get('title')}", f"電話:{c.get('phone')}", "電話", "置頂", "學校總機", "無", "無"))

                    elif filename == 'nihs_calendar.json':
                        for item in data:
                            if 'event' in item:
                                self.cursor.execute("INSERT INTO knowledge (title, content, category, date, unit, url, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                                  (f"行事曆活動", item.get('event'), "行事曆", item.get('date'), "教務處", "https://www.nihs.tp.edu.tw/nss/p/calendar", "無"))
                                count += 1

                    elif filename == 'nihs_knowledge_full.json':
                        for item in data:
                            title = item.get('title', '')
                            content_raw = item.get('content', '')
                            content = " ".join(content_raw) if isinstance(content_raw, list) else str(content_raw)
                            category = item.get('category', '公告')
                            unit = item.get('unit', '校務行政')
                            date = item.get('date', '')
                            url = item.get('url', 'https://www.nihs.tp.edu.tw')
                            atts = item.get('attachments', [])
                            att_str = "\n".join([f"{a.get('title')}: {a.get('url')}" for a in atts]) if atts else "無"
                            self.cursor.execute("INSERT INTO knowledge (title, content, category, date, unit, url, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                              (title, content, category, date, unit, url, att_str))
                            count += 1
            self.conn.commit()
        except Exception as e:
            print(f"❌ 載入失敗: {e}")

    def generate_keywords(self, query):
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            prompt = f"使用者問題：『{query}』。請回傳 3-5 個相關搜尋關鍵字，用於查詢學校公告。格式：['詞1', '詞2']"
            response = model.generate_content(prompt, generation_config={"temperature": 0.1})
            text = response.text.strip().replace("```python", "").replace("```", "")
            keywords = eval(text)
            return keywords if isinstance(keywords, list) else [query]
        except:
            return [query]

    def search_db(self, keywords, top_n=10):
        conditions = []
        params = []
        for k in keywords:
            conditions.append("(title LIKE ? OR content LIKE ? OR category LIKE ?)")
            params.extend([f'%{k}%', f'%{k}%', f'%{k}%'])
        
        where_clause = " OR ".join(conditions)
        sql = f"SELECT date, unit, title, url, content FROM knowledge WHERE {where_clause} ORDER BY (category='行事曆') DESC, date DESC LIMIT {top_n}"
        self.cursor.execute(sql, tuple(params))
        rows = self.cursor.fetchall()
        
        res = ""
        for i, r in enumerate(rows):
            res += f"【來源 {i+1}】日期:{r[0]} | 單位:{r[1]} | 標題:{r[2]} | 網址:{r[3]} | 內容:{r[4]}\n---\n"
        return res

    def get_monthly_calendar(self, query):
        now = datetime.now()
        month_match = re.search(r'(\d+|[一二三四五六七八九十]+)月', query)
        target_month = int(month_match.group(1)) if month_match and month_match.group(1).isdigit() else now.month
        query_date = f"{now.year}/{target_month:02d}%"
        
        self.cursor.execute("SELECT date, content FROM knowledge WHERE category='行事曆' AND date LIKE ? ORDER BY date ASC", (query_date,))
        rows = self.cursor.fetchall()
        
        self.cursor.execute("SELECT url FROM knowledge WHERE title LIKE '%114%行事曆%' LIMIT 1")
        url_row = self.cursor.fetchone()
        source_url = url_row[0] if url_row else "https://www.nihs.tp.edu.tw/nss/p/calendar"
        
        data_str = "\n".join([f"{r[0]} | {r[1]}" for r in rows])
        return data_str, target_month, source_url

    def ask(self, user_query):
        # 1. 基本規則直通車
        q = user_query.lower()
        if any(k in q for k in ['交通', '地址', '在哪', '捷運', '公車']):
            t = self.faq_data.get('traffic', {})
            return f"🏫 **交通資訊**\n地址：{t.get('address')}\n捷運：{t.get('mrt')}\n公車：{t.get('bus')}"
        if any(k in q for k in ['電話', '分機', '聯絡']):
            return "📞 **常用電話**\n" + "\n".join([f"🔸 {c.get('title')}: {c.get('phone')}" for c in self.faq_data.get('contacts', [])])

        # 2. 搜尋與背景補充
        keywords = self.generate_keywords(user_query)
        retrieved_data = self.search_db(keywords)

        # 針對日期問題補充行事曆
        source_url = "https://www.nihs.tp.edu.tw"
        if any(k in user_query for k in ['行事曆', '何時', '開學', '日期', '放假', '考試']):
            cal_extra, month, s_url = self.get_monthly_calendar(user_query)
            source_url = s_url
            if cal_extra:
                retrieved_data = f"【本月重點行事曆背景】:\n{cal_extra}\n---\n" + retrieved_data

        # 3. 生成 Prompt
        prompt = f"""
你是一個親切的內湖高工校園小幫手。今天是 {datetime.now().strftime("%Y/%m/%d")}。
請根據下方的【檢索資料】回答家長的【問題】：『{user_query}』

【回答準則】：
1. 語氣要親切、有禮貌（繁體中文）。
2. **務必附上「網址」**：如果資料中有連結，請直接提供給家長。
3. 如果資料中沒有答案，請誠實說「目前公告中找不到相關資訊」。
4. 若問到行事曆或日期（如開學），請精確從檢索資料中提取回答。
5. 行事曆請區分「🏠 家長與學生重要日程」與「🏫 學校行政事務」。

【檢索資料】：
{retrieved_data}

🌐 資料來源：[內湖高工官方網站]({source_url})
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt, generation_config={"temperature": 0.2})
            return response.text
        except:
            return "小幫手忙碌中，請稍後再試。"

# ==========================================
# 🌐 Flask 路由設定
# ==========================================
brain = UnifiedBrain()

@app.route("/debug")
def debug():
    brain.cursor.execute("SELECT category, COUNT(*) FROM knowledge GROUP BY category")
    return f"DB Stats: {brain.cursor.fetchall()}"

@app.route("/", methods=['GET'])
def index(): return "Live", 200

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    reply = brain.ask(event.message.text.strip())
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=10000)
