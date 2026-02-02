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
# 🧠 混合大腦 (Agentic RAG + Unified Search)
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
        """ 載入所有 JSON 資料並分類存入 SQLite """
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
            print(f"✅ 資料庫載入成功！共 {count} 筆公告。")
        except Exception as e:
            print(f"❌ 載入失敗: {e}")

    def generate_keywords(self, query):
        """ 使用 AI 擴展搜尋詞，確保命中資料庫內容 """
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            prompt = f"使用者的問題是：『{query}』。請回傳 3-5 個相關的搜尋關鍵字用來查學校公告與行事曆。格式：['詞1', '詞2']"
            response = model.generate_content(prompt, generation_config={"temperature": 0.1})
            keywords = eval(response.text.strip().replace("```python", "").replace("```", ""))
            return keywords if isinstance(keywords, list) else [query]
        except:
            return [query]

    def search_db(self, keywords, top_n=10):
        """ 統一檢索：同時搜尋公告與行事曆細項 """
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
            res += f"【資料 {i+1}】日期:{r[0]} | 單位:{r[1]} | 標題:{r[2]} | 網址:{r[3]} | 內容:{r[4]}\n---\n"
        return res

    def get_monthly_calendar(self, query):
        """ 專門抓取一整個月的資料供 AI 分類使用 """
        now = datetime.now()
        month_match = re.search(r'(\d+|[一二三四五六七八九十]+)月', query)
        target_month = int(month_match.group(1)) if month_match and month_match.group(1).isdigit() else now.month
        query_date = f"{now.year}/{target_month:02d}%"
        
        self.cursor.execute("SELECT date, content FROM knowledge WHERE category='行事曆' AND date LIKE ? ORDER BY date ASC", (query_date,))
        rows = self.cursor.fetchall()
        
        # 同時抓取行事曆 PDF 的真實連結
        self.cursor.execute("SELECT url FROM knowledge WHERE title LIKE '%114%行事曆%' LIMIT 1")
        url_row = self.cursor.fetchone()
        source_url = url_row[0] if url_row else "https://www.nihs.tp.edu.tw/nss/p/calendar"
        
        data_str = "\n".join([f"{r[0]} | {r[1]}" for r in rows])
        return data_str, target_month, source_url

    def ask(self, user_query):
        # 1. 基礎規則 (交通、電話)
        q = user_query.lower()
        if any(k in q for k in ['交通', '地址', '在哪', '捷運', '公車']):
            t = self.faq_data.get('traffic', {})
            return f"🏫 **交通資訊**\n地址：{t.get('address')}\n捷運：{t.get('mrt')}\n公車：{t.get('bus')}"
        if any(k in q for k in ['電話', '分機', '聯絡']):
            return "📞 **常用電話**\n" + "\n".join([f"🔸 {c.get('title')}: {c.get('phone')}" for c in self.faq_data.get('contacts', [])])

        # 2. AI 聯想關鍵字與檢索
        keywords = self.generate_keywords(user_query)
        retrieved_data = self.search_db(keywords)

        # 3. 針對日期/開學問題，強制補充當月行事曆背景
        is_calendar_query = any(k in user_query for k in ['行事曆', '何時', '開學', '日期', '放假', '考試'])
        calendar_bg = ""
        source_url = ""
        if is_calendar_query:
            calendar_bg, month, source_url = self.get_monthly_calendar(user_query)
            retrieved_data = f"【重點行事曆背景】:\n{calendar_bg}\n\n" + retrieved_data

        # 4. 生成 Prompt
        prompt = f"""
你現在是內湖高工的 AI 秘書。使用者詢問：『{user_query}』。
今天是 {datetime.now().strftime("%Y/%m/%d")}。

【檢索資料內容】：
{retrieved_data}

【回答準則】：
1. **行事曆分類**：若詢問行事曆，請區分「🏠 家長學生重要日程」與「🏫 學校行政事務」。
2. **事實優先**：資料中若有提到的日期（如：2/23開學），必須精確回答，不可說找不到。
3. **網址附件**：若資料有網址或連結，請務必附上。
4. **結尾**：若使用了行事曆資料，請附上：🌐 資料來源：[114學年度第2學期行事曆]({source_url if source_url else 'https://www.nihs.tp.edu.tw'})
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt, generation_config={"temperature": 0.2})
            return response.text
        except:
            return "小幫手忙碌中，請稍後再試。"

brain = UnifiedBrain()

@app.route("/debug")
def debug():
    brain.cursor.execute("SELECT category, COUNT(*) FROM knowledge GROUP BY category")
    return f"Status: {brain.cursor.fetchall()}"

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
