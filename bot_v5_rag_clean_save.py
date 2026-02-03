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
# 🧠 高度類人化 AI 大腦 (Human-Like Brain)
# ==========================================
class HumanLikeBrain:
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
        """ 載入並索引所有校園資料 """
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
            print(f"✅ 大腦載入完畢，共 {count} 筆記憶。")
        except Exception as e:
            print(f"❌ 載入失敗: {e}")

    # 🔥 策略二：意圖擴展 (Query Expansion)
    def generate_search_strategy(self, user_query):
        """
        讓 AI 擔任「翻譯官」，把使用者的口語（如：那個補助）
        翻譯成資料庫懂的語言（如：['學費補助', '清寒', '申請']）。
        """
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            prompt = f"""
            角色：你是一個精通校務資料庫的檢索專家。
            任務：將使用者的口語問題轉換為 3-5 個精確的「搜尋關鍵字」。
            思考邏輯：
            1. 如果問「泡麵/吃的」，關鍵字應包含 ['員生社', '熱食', '販售']。
            2. 如果問「校長」，關鍵字應包含 ['校長室', '林俊岳', '職掌']。
            3. 如果問「開學」，關鍵字應包含 ['行事曆', '開學', '註冊']。
            
            使用者問題：『{user_query}』
            
            請直接回傳 Python List 格式，例如：['詞1', '詞2', '詞3']
            """
            response = model.generate_content(prompt, generation_config={"temperature": 0.1})
            text = response.text.strip().replace("```python", "").replace("```", "")
            keywords = eval(text)
            return keywords if isinstance(keywords, list) else [user_query]
        except:
            # 如果 AI 思考失敗，回退到原始問題
            return [user_query]

    def search_db(self, keywords, top_n=10):
        """ 執行多維度模糊搜尋 """
        conditions = []
        params = []
        for k in keywords:
            # 同時搜標題、內容、類別
            conditions.append("(title LIKE ? OR content LIKE ? OR category LIKE ?)")
            params.extend([f'%{k}%', f'%{k}%', f'%{k}%'])
        
        where_clause = " OR ".join(conditions)
        sql = f"SELECT date, unit, title, url, content FROM knowledge WHERE {where_clause} ORDER BY date DESC LIMIT {top_n}"
        self.cursor.execute(sql, tuple(params))
        rows = self.cursor.fetchall()
        
        res = ""
        for i, r in enumerate(rows):
            res += f"【資料來源 {i+1}】\n日期：{r[0]}\n單位：{r[1]}\n標題：{r[2]}\n網址：{r[3]}\n內容摘要：{r[4][:200]}...\n---\n"
        return res

    def get_monthly_calendar(self, query):
        """ 針對日期問題，強制拉取行事曆背景 """
        now = datetime.now()
        # 簡單的正則表達式抓月份
        month_match = re.search(r'(\d+|[一二三四五六七八九十]+)月', query)
        target_month = int(month_match.group(1)) if month_match and month_match.group(1).isdigit() else now.month
        query_date = f"{now.year}/{target_month:02d}%"
        
        self.cursor.execute("SELECT date, content FROM knowledge WHERE category='行事曆' AND date LIKE ? ORDER BY date ASC", (query_date,))
        rows = self.cursor.fetchall()
        
        # 抓 PDF 原始連結
        self.cursor.execute("SELECT url FROM knowledge WHERE title LIKE '%114%行事曆%' LIMIT 1")
        url_row = self.cursor.fetchone()
        source_url = url_row[0] if url_row else "https://www.nihs.tp.edu.tw/nss/p/calendar"
        
        data_str = "\n".join([f"{r[0]} | {r[1]}" for r in rows])
        return data_str, target_month, source_url

    # 🔥 策略三：人設生成 (Human-Like Generation)
    def ask(self, user_query):
        # 1. 基礎規則直通車 (處理絕對標準答案)
        q = user_query.lower()
        if any(k in q for k in ['交通', '地址', '捷運', '公車']):
             t = self.faq_data.get('traffic', {})
             return f"🏫 **內湖高工交通資訊**\n📍 地址：{t.get('address')}\n🚇 捷運：{t.get('mrt')}\n🚌 公車：{t.get('bus')}"
        if any(k in q for k in ['電話', '分機', '聯絡']):
             return "📞 **常用電話表**\n" + "\n".join([f"🔸 {c.get('title')}: {c.get('phone')}" for c in self.faq_data.get('contacts', [])])

        # 2. 啟動「意圖擴展」思考
        keywords = self.generate_search_strategy(user_query)
        
        # 3. 執行檢索
        retrieved_data = self.search_db(keywords)

        # 4. 背景注入 (Context Injection)
        # 如果發現問題跟時間有關，自動把行事曆塞進去
        source_url_ref = "https://www.nihs.tp.edu.tw"
        if any(k in user_query for k in ['行事曆', '何時', '幾號', '開學', '放假', '段考', '考試']):
            cal_bg, month, s_url = self.get_monthly_calendar(user_query)
            source_url_ref = s_url
            if cal_bg:
                retrieved_data = f"【參考背景：{month}月行事曆】:\n{cal_bg}\n\n" + retrieved_data

        # 5. 最終生成 (Persona Prompt)
        now = datetime.now()
        prompt = f"""
SYSTEM: 你現在是內湖高工的「AI 校務秘書」。
你的語氣：親切、專業、有禮貌，像是一位有經驗的老師。

【任務目標】：
請根據下方的【檢索資料】回答家長或學生的【問題】。

【回答邏輯檢查】：
1. **意圖識別**：
   - 如果是家長問（如學費、接送），請用讓家長放心的口吻。
   - 如果是學生問（如社團、補考），請用鼓勵且明確的口吻。
   
2. **精確性**：
   - 若資料中有明確日期（如開學日、截止日），請清楚列出。
   - 若資料庫中真的找不到（如泡麵具體口味），請誠實說：「目前公告中未詳列細節，建議直接詢問員生社」，不要瞎掰。

3. **引用規範**：
   - 請在回答的最後，加上「💡 參考來源」並附上網址。

【當下時間】：{now.strftime("%Y/%m/%d")}
【使用者問題】：{user_query}

【檢索資料庫內容】：
{retrieved_data}
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            # 提高一點 temperature 讓語氣稍微自然一點，不要太死板
            response = model.generate_content(prompt, generation_config={"temperature": 0.3})
            return response.text
        except:
            return "校務小幫手目前線路忙碌，請稍後再試。"

# ==========================================
# 🌐 Flask 路由
# ==========================================
brain = HumanLikeBrain()

@app.route("/debug")
def debug():
    # Debug 頁面讓我們可以看到 AI 把使用者的問題「翻譯」成了什麼關鍵字
    test_q = "校長叫什麼"
    keywords = brain.generate_search_strategy(test_q)
    return f"<h1>🧠 AI Brain Debug</h1><p>測試問題：{test_q}</p><p>AI 聯想關鍵字：{keywords}</p>"

@app.route("/", methods=['GET'])
def index(): return "Neihu High School Bot (Human-Like Version Active)", 200

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
