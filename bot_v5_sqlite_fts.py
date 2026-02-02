import os
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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)

# 取得目前程式所在的絕對路徑
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==========================================
# 🧠 SQLite 大腦 (完整欄位 + 家長濾鏡版)
# ==========================================
class SQLiteBrain:
    def __init__(self):
        self.db_path = ':memory:'
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.faq_data = {} 
        self.init_db()
        self.load_data()

    def init_db(self):
        # ⚡ 擴充欄位：增加 unit, url, attachments
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
                
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        
                        # 1. FAQ (交通/電話)
                        if filename == 'nihs_faq.json':
                            self.faq_data = data
                            # 交通
                            t = data.get('traffic', {})
                            content = f"地址:{t.get('address')} 捷運:{t.get('mrt')} 公車:{t.get('bus')}"
                            self.cursor.execute("INSERT INTO knowledge (title, content, category, date, unit, url, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                              ("學校交通資訊", content, "交通", "置頂", "總務處", "https://www.nihs.tp.edu.tw", "無"))
                            # 電話
                            for c in data.get('contacts', []):
                                self.cursor.execute("INSERT INTO knowledge (title, content, category, date, unit, url, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                              (f"聯絡電話 {c.get('title')}", f"電話:{c.get('phone')}", "電話", "置頂", "學校總機", "無", "無"))
                            count += 10

                        # 2. 行事曆
                        elif isinstance(data, list) and filename == 'nihs_calendar.json':
                            for item in data:
                                if 'event' in item:
                                    # 行事曆通常沒有 URL，設為無
                                    self.cursor.execute("INSERT INTO knowledge (title, content, category, date, unit, url, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                                      (f"行事曆: {item.get('event')}", item.get('event'), "行事曆", item.get('date'), item.get('category', '教務處'), "無", "無"))
                                    count += 1

                        # 3. 公告 (nihs_knowledge_full.json)
                        elif isinstance(data, list) and filename == 'nihs_knowledge_full.json':
                            for item in data:
                                title = item.get('title', '')
                                content = str(item.get('content', ''))
                                unit = item.get('unit', '校務行政')
                                date = item.get('date', '')
                                url = item.get('url', '無')
                                
                                # 處理附件 (將 List 轉為字串)
                                atts = item.get('attachments', [])
                                att_str = ""
                                if atts:
                                    # 簡單串接附件網址
                                    att_list = [f"{a.get('title', '附件')}: {a.get('url')}" for a in atts]
                                    att_str = "\n".join(att_list)
                                else:
                                    att_str = "無"

                                self.cursor.execute("INSERT INTO knowledge (title, content, category, date, unit, url, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                                  (title, content, "公告", date, unit, url, att_str))
                                count += 1
            
            self.conn.commit()
            print(f"✅ 資料庫載入成功！共 {count} 筆資料。")

        except Exception as e:
            print(f"❌ 資料載入失敗: {e}")

    # 👉 規則直通車
    def check_rules(self, query):
        q = query.lower()
        # 交通
        if any(k in q for k in ['交通', '地址', '在哪', '捷運', '公車', '怎麼去']):
            t = self.faq_data.get('traffic', {})
            return (
                "🏫 **內湖高工交通資訊**\n\n"
                f"📍 **地址**：{t.get('address', '無資料')}\n"
                f"🚇 **捷運**：{t.get('mrt', '無資料')}\n"
                f"🚌 **公車**：\n{t.get('bus', '無資料')}\n\n"
                "🌐 學校首頁：https://www.nihs.tp.edu.tw"
            )
        # 電話
        if any(k in q for k in ['電話', '分機', '聯絡', '總機']):
            msg = "📞 **內湖高工常用電話**\n"
            for c in self.faq_data.get('contacts', []):
                msg += f"\n🔸 {c.get('title')}: {c.get('phone')}"
            return msg
        # 校長
        if '校長' in q:
            return (
                "👨‍🏫 **校長室資訊**\n"
                "聯絡電話：分機 301\n\n"
                "(備註：若您是詢問現任校長姓名，目前公告資料庫中暫無顯示。)\n"
                "相關公告請參考：https://www.nihs.tp.edu.tw"
            )
        return None

    # 👉 行事曆專用查詢 (家長濾鏡版)
    def get_calendar(self):
        try:
            today = datetime.now().strftime("%Y/%m/%d")
            # 抓取今天之後的活動
            self.cursor.execute("SELECT date, content FROM knowledge WHERE category='行事曆' AND date >= ? ORDER BY date ASC LIMIT 15", (today,))
            rows = self.cursor.fetchall()
            if not rows: return None
            
            # ⚡ 家長濾鏡：剔除「會議」、「檢查」、「研習」、「作業」
            # 保留：考試、放假、典禮、說明會、報名、榜單
            filtered_events = []
            block_keywords = ['會議', '檢查', '研習', '作業檢查', '繳交', '日誌']
            
            for r in rows:
                event_name = r[1]
                # 如果包含封鎖關鍵字，就跳過
                if any(bk in event_name for bk in block_keywords):
                    continue
                filtered_events.append(f"{r[0]} {event_name}")
                if len(filtered_events) >= 6: # 只取前 6 個重點
                    break
            
            if not filtered_events: return None

            msg = "📅 **近期重要行事曆 (家長重點版)**\n"
            for e in filtered_events:
                msg += f"\n🔹 {e}"
            return msg
        except: return None

    # 👉 SQL 模糊檢索 (回傳完整格式)
    def search_db(self, query, top_n=5):
        try:
            keywords = [k for k in query.split() if len(k) > 1]
            if not keywords: keywords = [query]
            keyword = keywords[0]
            
            # ⚡ 選取所有欄位
            sql = f"SELECT date, unit, title, url, attachments, content FROM knowledge WHERE title LIKE ? OR content LIKE ? ORDER BY date DESC LIMIT {top_n}"
            self.cursor.execute(sql, (f'%{keyword}%', f'%{keyword}%'))
            rows = self.cursor.fetchall()

            formatted_results = ""
            for i, r in enumerate(rows):
                # r[0]=date, r[1]=unit, r[2]=title, r[3]=url, r[4]=attachments, r[5]=content
                # 嚴格遵照用戶指定格式
                formatted_results += f"""
【資料來源 {i+1}】
日期：{r[0]}
單位：{r[1]}
標題：{r[2]}
網址：{r[3]}
附件：{r[4]}
內容摘要：{r[5][:200]}...
--------------------------------
"""
            return formatted_results

        except Exception as e:
            print(f"❌ 搜尋錯誤: {e}")
            return ""

    def ask(self, user_query):
        # 1. 直通車
        direct = self.check_rules(user_query)
        if direct: return direct

        # 2. 行事曆直通車
        if "行事曆" in user_query:
            cal = self.get_calendar()
            if cal: return cal

        # 3. 資料庫搜尋
        retrieved_data = self.search_db(user_query, top_n=5)
        
        # 4. 判斷是否有資料
        if not retrieved_data:
            return "您的問題很好！目前公告中暫時找不到相關資訊。建議您聯繫學校 (02-26574874)，我們會記錄並更新。"

        # 5. Gemini 生成 (使用用戶指定 Prompt)
        now = datetime.now()
        
        prompt = f"""
你是一個親切的內湖高工校園小幫手。今天是 {now.year}/{now.month}/{now.day}。
請根據下方的【檢索資料】回答家長的【問題】。

【回答準則】：
1. 語氣要親切、有禮貌（繁體中文）。
2. **務必附上「網址」**：如果資料中有連結，請直接提供給家長點擊。
3. **提及附件**：如果資料有附件（如 PDF、Word），請提醒家長可以點擊連結下載。
4. 如果資料中沒有答案，請誠實說「目前公告中找不到相關資訊」，建議家長直接聯繫學校。
5. 若問到行事曆或日期，請精確回答。

另外，當有提問【學校行事曆】則列出當月份的日期，活動內容。
活動內容，家長不需要知道「課務會議」或「設備檢查」何時舉行，他們只需要知道：什麼時候考試？什麼時候放假？什麼時候該出現？

【檢索資料】：
{retrieved_data}

【家長問題】：{user_query}
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt, generation_config={"temperature": 0.3})
            return response.text
        except:
            return "小幫手連線忙碌中，請稍後再試。"

brain = SQLiteBrain()

@app.route("/", methods=['GET'])
def index(): return "Bot Live (Format Fixed)", 200

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
