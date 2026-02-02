import os
# ⚡ 移除所有重型運算環境變數，回歸純淨
import json
import sqlite3
import google.generativeai as genai
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from datetime import datetime

# ==========================================
# 🔑 設定區
# ==========================================
MODEL_NAME = 'gemini-2.0-flash'
# 注意：這裡不需要 Embedding Model 了，省下大量 API 呼叫

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)

# ==========================================
# 🧠 SQLite FTS 大腦 (關鍵字精準檢索)
# ==========================================
class SQLiteBrain:
    def __init__(self):
        self.db_path = ':memory:' # 使用記憶體資料庫，速度最快且 Render 重啟後自動重置
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.faq_data = {} # 直通車用
        self.init_db()
        self.load_data()

    def init_db(self):
        """ 初始化 FTS5 全文檢索表 """
        # 建立虛擬表，支援全文檢索
        self.cursor.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge USING fts5(
                title, 
                content, 
                category, 
                date,
                tokenize="trigram" 
            )
        ''')
        # tokenize="trigram" 對中文搜尋支援度較好 (若無支援可改用 unicode61)
        self.conn.commit()

    def load_data(self):
        """ 讀取 JSON 並寫入 SQLite """
        files = ['nihs_knowledge_full.json', 'nihs_faq.json', 'nihs_calendar.json']
        count = 0
        try:
            for file in files:
                if os.path.exists(file):
                    with open(file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        
                        # 1. FAQ 處理 (同時存入規則庫與資料庫)
                        if file == 'nihs_faq.json':
                            self.faq_data = data
                            # 寫入資料庫讓 FTS 也能搜到
                            t = data.get('traffic', {})
                            traffic_content = f"地址:{t.get('address')} 捷運:{t.get('mrt')} 公車:{t.get('bus')}"
                            self.cursor.execute("INSERT INTO knowledge (title, content, category, date) VALUES (?, ?, ?, ?)", 
                                              ("學校交通資訊", traffic_content, "交通", "置頂"))
                            
                            for c in data.get('contacts', []):
                                self.cursor.execute("INSERT INTO knowledge (title, content, category, date) VALUES (?, ?, ?, ?)", 
                                              (f"聯絡電話 {c.get('title')}", f"電話:{c.get('phone')}", "電話", "置頂"))
                            count += 1 + len(data.get('contacts', []))

                        # 2. 行事曆
                        elif isinstance(data, list):
                            for item in data:
                                if 'event' in item:
                                    # 組合標題與內容
                                    title = f"行事曆: {item.get('event')}"
                                    content = f"日期: {item.get('date')} 活動: {item.get('event')}"
                                    self.cursor.execute("INSERT INTO knowledge (title, content, category, date) VALUES (?, ?, ?, ?)", 
                                                      (title, content, "行事曆", item.get('date')))
                                    count += 1
                                else: # 公告
                                    # 處理公告
                                    title = item.get('title', '')
                                    content = str(item.get('content', '')) # 不截斷，讓 FTS 全文索引
                                    unit = item.get('unit', '')
                                    date = item.get('date', '')
                                    self.cursor.execute("INSERT INTO knowledge (title, content, category, date) VALUES (?, ?, ?, ?)", 
                                                      (title, content, unit, date))
                                    count += 1
            
            self.conn.commit()
            print(f"✅ SQLite 大腦啟動完畢！已索引 {count} 筆資料。")

        except Exception as e:
            print(f"❌ 資料庫初始化失敗: {e}")

    # 👉 規則直通車 (優先攔截)
    def check_rules(self, query):
        q = query.lower()
        if any(k in q for k in ['交通', '地址', '在哪', '捷運', '公車', '怎麼去']):
            t = self.faq_data.get('traffic', {})
            return (
                "🏫 **內湖高工交通資訊**\n\n"
                f"📍 **地址**：{t.get('address', '無資料')}\n"
                f"🚇 **捷運**：{t.get('mrt', '無資料')}\n"
                f"🚌 **公車**：\n{t.get('bus', '無資料')}"
            )
        if any(k in q for k in ['電話', '分機', '聯絡', '總機']):
            msg = "📞 **內湖高工常用電話**\n"
            for c in self.faq_data.get('contacts', []):
                msg += f"\n🔸 {c.get('title')}: {c.get('phone')}"
            return msg
        return None

    # 👉 SQLite 全文檢索
    def search_db(self, query, top_n=5):
        try:
            # 簡單斷詞：把使用者問題切成關鍵字 (簡單以空白或字元切分)
            # 例如 "校長候選人" -> "校長" AND "候選人" (這裡做個簡單處理，將連續字串視為整體查詢)
            
            # FTS5 查詢語法：簡單關鍵字匹配
            # 將輸入的特殊符號去除，避免 SQL Injection 風險
            clean_query = "".join([c for c in query if c.isalnum() or c in [' ', '?']])
            
            # 使用 SQLite FTS 查詢
            # 這裡使用簡單的 "包含" 邏輯。若要更強，可把 query 拆成字元加空格 (e.g. "校 長")
            sql_query = f'SELECT title, content, date FROM knowledge WHERE knowledge MATCH "{clean_query}" ORDER BY rank LIMIT {top_n}'
            
            self.cursor.execute(sql_query)
            rows = self.cursor.fetchall()
            
            # 如果完全匹配找不到，嘗試「模糊拆字」搜尋 (Fallback)
            if not rows and len(clean_query) > 1:
                # 將 "校長" 拆成 "校 OR 長"
                fuzzy_query = " OR ".join(list(clean_query))
                sql_query = f'SELECT title, content, date FROM knowledge WHERE knowledge MATCH "{fuzzy_query}" ORDER BY rank LIMIT {top_n}'
                self.cursor.execute(sql_query)
                rows = self.cursor.fetchall()

            results = []
            for r in rows:
                results.append(f"【{r[2]}】{r[0]}: {r[1][:150]}...") # 限制丟給 AI 的長度
            
            # Debug Log
            print(f"🔍 FTS 搜尋 '{clean_query}' -> 找到 {len(rows)} 筆")
            return results

        except Exception as e:
            print(f"❌ 搜尋錯誤: {e}")
            return []

    def ask(self, user_query):
        # 1. 直通車
        direct = self.check_rules(user_query)
        if direct: return direct

        # 2. SQLite 檢索
        docs = self.search_db(user_query, top_n=5)
        
        # 3. 判斷是否有資料
        if not docs:
            return "您的問題很好！目前公告中暫時找不到相關資訊。建議您聯繫學校，我們會記錄並更新。"

        # 4. 組合 Prompt 給 Gemini
        context = "\n".join(docs)
        now = datetime.now()
        
        prompt = f"""
你是「內湖高工校園小幫手」。今天是 {now.year}/{now.month}/{now.day}。
請根據【參考資料】回答問題。

【策略】：
1. **事實陳述**：參考資料有的才說，沒有的不要編。
2. **語氣**：親切、條列式。
3. **日期**：將參考資料中的日期與今天對比，標註是否為「過去活動」或「即將到來」。

【參考資料】：
{context}

【問題】：{user_query}
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt, generation_config={"temperature": 0.3})
            return response.text
        except:
            return "小幫手連線忙碌中，請稍後再試。"

brain = SQLiteBrain()

@app.route("/", methods=['GET'])
def index(): return "Bot Live (SQLite)", 200

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except: abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    reply = brain.ask(event.message.text.strip())
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=10000)
