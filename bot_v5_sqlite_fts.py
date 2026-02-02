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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ==========================================
# 🧠 SQLite 大腦 (含 Debug 檢視器 + 行事曆優化)
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
                        
                        # 1. FAQ
                        if filename == 'nihs_faq.json':
                            self.faq_data = data
                            t = data.get('traffic', {})
                            content = f"地址:{t.get('address')} 捷運:{t.get('mrt')} 公車:{t.get('bus')}"
                            self.cursor.execute("INSERT INTO knowledge (title, content, category, date, unit, url, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                              ("學校交通資訊", content, "交通", "置頂", "總務處", "https://www.nihs.tp.edu.tw", "無"))
                            for c in data.get('contacts', []):
                                self.cursor.execute("INSERT INTO knowledge (title, content, category, date, unit, url, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                              (f"聯絡電話 {c.get('title')}", f"電話:{c.get('phone')}", "電話", "置頂", "學校總機", "無", "無"))
                            count += 10

                        # 2. 行事曆 (nihs_calendar.json)
                        elif isinstance(data, list) and filename == 'nihs_calendar.json':
                            for item in data:
                                if 'event' in item:
                                    # 假設 JSON 裡沒有 url 欄位，我們手動補上學校行事曆網址
                                    calendar_url = item.get('url', 'https://www.nihs.tp.edu.tw/nss/p/calendar')
                                    self.cursor.execute("INSERT INTO knowledge (title, content, category, date, unit, url, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                                      (f"行事曆: {item.get('event')}", item.get('event'), "行事曆", item.get('date'), item.get('category', '教務處'), calendar_url, "無"))
                                    count += 1

                        # 3. 公告 (nihs_knowledge_full.json) - 校長資料應該在這裡
                        elif isinstance(data, list) and filename == 'nihs_knowledge_full.json':
                            for item in data:
                                title = item.get('title', '')
                                # 確保將 List 或 Dict 類型的 content 轉為字串
                                content_raw = item.get('content', '')
                                if isinstance(content_raw, list):
                                    content = " ".join(content_raw)
                                else:
                                    content = str(content_raw)
                                
                                unit = item.get('unit', '校務行政')
                                date = item.get('date', '')
                                url = item.get('url', 'https://www.nihs.tp.edu.tw') # 若無網址則給首頁
                                
                                atts = item.get('attachments', [])
                                att_str = ""
                                if atts:
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
        if any(k in q for k in ['交通', '地址', '在哪', '捷運', '公車', '怎麼去']):
            t = self.faq_data.get('traffic', {})
            return (
                "🏫 **內湖高工交通資訊**\n\n"
                f"📍 **地址**：{t.get('address', '無資料')}\n"
                f"🚇 **捷運**：{t.get('mrt', '無資料')}\n"
                f"🚌 **公車**：\n{t.get('bus', '無資料')}\n\n"
                "🌐 學校首頁：https://www.nihs.tp.edu.tw"
            )
        if any(k in q for k in ['電話', '分機', '聯絡', '總機']):
            msg = "📞 **內湖高工常用電話**\n"
            for c in self.faq_data.get('contacts', []):
                msg += f"\n🔸 {c.get('title')}: {c.get('phone')}"
            return msg
        return None

    # 👉 行事曆查詢 (全量抓取 + 單一來源網址)
    def get_calendar(self, user_query):
        try:
            now = datetime.now()
            target_year = now.year
            target_month = now.month

            # 解析月份
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

            query_date_str = f"{target_year}/{target_month:02d}%"
            
            # 抓取資料
            sql = "SELECT date, unit, title, url, content FROM knowledge WHERE category='行事曆' AND date LIKE ? ORDER BY date ASC"
            self.cursor.execute(sql, (query_date_str,))
            rows = self.cursor.fetchall()

            if not rows: return None, target_month, ""

            # 組合資料 (不重複列出網址)
            formatted_data = ""
            source_url = "https://www.nihs.tp.edu.tw/nss/p/calendar" # 預設來源
            
            for r in rows:
                # r[3] 是 url，如果該活動有特殊網址，我們更新 source_url (或保留最後一個)
                if r[3] and r[3] != '無' and 'calendar' in r[3]:
                    source_url = r[3]

                formatted_data += f"{r[0]} | {r[4]} (單位:{r[1]})\n"

            return formatted_data, target_month, source_url

        except Exception as e:
            print(f"❌ 行事曆查詢錯誤: {e}")
            return None, 0, ""

    # 👉 一般 SQL 模糊檢索
    def search_db(self, query, top_n=5):
        try:
            keywords = [k for k in query.split() if len(k) > 1]
            if not keywords: keywords = [query]
            keyword = keywords[0]
            
            sql = f"SELECT date, unit, title, url, attachments, content FROM knowledge WHERE title LIKE ? OR content LIKE ? ORDER BY date DESC LIMIT {top_n}"
            self.cursor.execute(sql, (f'%{keyword}%', f'%{keyword}%'))
            rows = self.cursor.fetchall()

            formatted_results = ""
            for i, r in enumerate(rows):
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
        direct = self.check_rules(user_query)
        if direct: return direct

        # --- 行事曆邏輯 ---
        if "行事曆" in user_query:
            cal_data, month, source_url = self.get_calendar(user_query)
            
            if cal_data:
                retrieved_data = cal_data
                
                # 這裡設定 Gemini 的指令
                system_instruction = f"""
你現在是內湖高工的行事曆秘書。使用者想查詢 {month} 月份的行事曆。
我會提供該月份的「所有原始活動資料」，請你發揮判斷力，將這些活動區分為兩個區塊呈現：

【區塊一：🏠 家長與學生重要日程】
* 判斷標準：考試 (段考、模擬考)、放假 (補假、寒暑假)、註冊、繳費、全校性典禮、社團活動、競賽、升學相關。
* 請依日期排序。

【區塊二：🏫 學校行政與教師事務】
* 判斷標準：各類會議、設備檢查、作業抽查、教師研習。
* 若該區塊無活動，請標註「無」。

【結尾要求】：
* 請在回覆的**最末端**，獨立一行列出原始參考資料來源。
* 格式： 🌐 資料來源：[114學年度第2學期行事曆]({source_url})
"""
                user_query = f"請幫我整理 {month} 月份的行事曆，請依照上述規則分類。\n\n【原始資料】：\n{cal_data}"
            else:
                return f"🔍 查詢不到 {datetime.now().year}年 相關月份的行事曆資訊。"

        # --- 一般搜尋邏輯 (校長問題會跑這) ---
        else:
            retrieved_data = self.search_db(user_query, top_n=5)
            system_instruction = "你是一個親切的內湖高工校園小幫手。請根據檢索資料回答問題，務必附上網址與附件連結。"
            
            if not retrieved_data:
                return "您的問題很好！目前公告中暫時找不到相關資訊。建議您聯繫學校 (02-26574874)，我們會記錄並更新。"

        # --- 呼叫 Gemini ---
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

brain = SQLiteBrain()

# ==========================================
# 🕵️‍♂️ Debug 檢視器 (新增功能)
# ==========================================
@app.route("/debug", methods=['GET'])
def debug_page():
    try:
        # 1. 查詢資料總筆數
        brain.cursor.execute("SELECT category, COUNT(*) FROM knowledge GROUP BY category")
        stats = brain.cursor.fetchall()
        
        # 2. 查詢「校長」相關資料
        brain.cursor.execute("SELECT id, title, date, unit FROM knowledge WHERE title LIKE '%校長%' OR content LIKE '%校長%'")
        principal_rows = brain.cursor.fetchall()

        # 3. 查詢最近 5 筆行事曆
        brain.cursor.execute("SELECT id, date, content, url FROM knowledge WHERE category='行事曆' ORDER BY date ASC LIMIT 5")
        calendar_rows = brain.cursor.fetchall()

        # 產生 HTML
        html = "<h1>🕵️‍♂️ 內湖高工 Bot 資料庫診斷</h1>"
        html += "<h3>📊 資料統計</h3><ul>"
        for s in stats:
            html += f"<li>{s[0]}: {s[1]} 筆</li>"
        html += "</ul>"

        html += "<h3>👨‍🏫 搜尋 '校長' 結果 (確認資料是否存在)</h3>"
        if principal_rows:
            html += "<table border='1'><tr><th>ID</th><th>標題</th><th>日期</th><th>單位</th></tr>"
            for r in principal_rows:
                html += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>"
            html += "</table>"
        else:
            html += "<p style='color:red;'>❌ 查無 '校長' 相關資料！請檢查 nihs_knowledge_full.json</p>"

        html += "<h3>📅 行事曆前 5 筆 (確認 URL)</h3>"
        html += "<table border='1'><tr><th>ID</th><th>日期</th><th>活動內容</th><th>URL</th></tr>"
        for r in calendar_rows:
            html += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>"
        html += "</table>"
        
        return html
    except Exception as e:
        return f"<h1>❌ Debug Error</h1><p>{str(e)}</p>"

@app.route("/", methods=['GET'])
def index(): return "Bot Live (Debug Enabled)", 200

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
