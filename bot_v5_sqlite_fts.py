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
# 🧠 SQLite 大腦 (智慧關鍵字拆解版)
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
                                    self.cursor.execute("INSERT INTO knowledge (title, content, category, date, unit, url, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                                      (f"行事曆: {item.get('event')}", item.get('event'), "行事曆", item.get('date'), item.get('category', '教務處'), "無", "無"))
                                    count += 1

                        # 3. 完整公告 (nihs_knowledge_full.json) - 重點在這裡
                        elif isinstance(data, list) and filename == 'nihs_knowledge_full.json':
                            for item in data:
                                title = item.get('title', '')
                                
                                # 🔥 確保內容被轉成純文字，增加搜尋命中率
                                content_raw = item.get('content', '')
                                if isinstance(content_raw, list):
                                    content = " ".join([str(x) for x in content_raw])
                                else:
                                    content = str(content_raw)
                                
                                # 移除多餘的空白，讓 "校　長" 變成 "校長" (雖然原始資料保留，但可增加一欄 clean_content 做搜尋優化，這裡先簡單處理)
                                # content = content.replace("　", "") 
                                
                                category = item.get('category', '公告')
                                unit = item.get('unit', '校務行政')
                                date = item.get('date', '')
                                url = item.get('url', 'https://www.nihs.tp.edu.tw')
                                
                                atts = item.get('attachments', [])
                                att_str = "\n".join([f"{a.get('title', '附件')}: {a.get('url')}" for a in atts]) if atts else "無"

                                self.cursor.execute("INSERT INTO knowledge (title, content, category, date, unit, url, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                                  (title, content, category, date, unit, url, att_str))
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

    # 👉 行事曆查詢
    def get_calendar(self, user_query):
        try:
            now = datetime.now()
            target_year = now.year
            target_month = now.month

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
            sql = "SELECT date, unit, title, url, content FROM knowledge WHERE category='行事曆' AND date LIKE ? ORDER BY date ASC"
            self.cursor.execute(sql, (query_date_str,))
            rows = self.cursor.fetchall()

            if not rows: return None, target_month, ""

            # 嘗試抓取真實行事曆連結
            calendar_source_url = "https://www.nihs.tp.edu.tw/nss/p/calendar"
            try:
                self.cursor.execute("SELECT url FROM knowledge WHERE title LIKE '%114%行事曆%' AND (category='公告' OR category='校園靜態資訊') LIMIT 1")
                url_row = self.cursor.fetchone()
                if url_row and url_row[0] != '無':
                    calendar_source_url = url_row[0]
            except: pass

            formatted_data = ""
            for r in rows:
                formatted_data += f"\n日期：{r[0]}\n活動：{r[4]}\n單位：{r[1]}\n---\n"
            return formatted_data, target_month, calendar_source_url

        except Exception as e:
            return None, 0, ""

    # 👉 🔥 核心修正：智慧多重搜尋 (Smart Search)
    def search_db(self, query, top_n=10): # 增加 top_n 讓 Gemini 讀多一點資料
        try:
            # 1. 關鍵字拆解：將 "校長姓名" 拆為 ["校長", "姓名"]
            # 簡單邏輯：如果是長句，每兩個字切一刀；或直接取關鍵名詞
            # 這裡使用簡單的「單字 + 雙字」拆解策略
            
            keywords = []
            
            # 如果使用者輸入很短 (如 "校長")，直接搜
            if len(query) <= 2:
                keywords.append(query)
            else:
                # 簡單分詞：取前兩個字 (如 "校長")，取後兩個字 (如 "泡麵")
                # 這能有效解決 "校長姓名" 這種複合詞
                keywords.append(query) # 原句
                keywords.append(query[:2]) # 前兩字
                if len(query) > 2:
                    keywords.append(query[-2:]) # 後兩字

            # 去除重複並過濾過短的
            keywords = list(set([k for k in keywords if len(k) >= 2]))
            
            if not keywords: keywords = [query]

            print(f"🔍 搜尋關鍵字: {keywords}") # Debug 用

            # 2. 動態 SQL 生成：使用 OR 邏輯
            # WHERE (title LIKE %k1% OR content LIKE %k1%) OR (title LIKE %k2% OR content LIKE %k2%)
            
            conditions = []
            params = []
            for k in keywords:
                conditions.append("(title LIKE ? OR content LIKE ?)")
                params.extend([f'%{k}%', f'%{k}%'])
            
            where_clause = " OR ".join(conditions)
            
            sql = f"""
                SELECT date, unit, title, url, attachments, content 
                FROM knowledge 
                WHERE {where_clause} 
                ORDER BY date DESC 
                LIMIT {top_n}
            """
            
            self.cursor.execute(sql, tuple(params))
            rows = self.cursor.fetchall()

            if not rows: return ""

            formatted_results = ""
            for i, r in enumerate(rows):
                formatted_results += f"""
【資料來源 {i+1}】
日期：{r[0]}
單位：{r[1]}
標題：{r[2]}
網址：{r[3]}
附件：{r[4]}
內容摘要：{r[5][:500]}... 
--------------------------------
"""
            return formatted_results

        except Exception as e:
            print(f"❌ 搜尋錯誤: {e}")
            return ""

    def ask(self, user_query):
        direct = self.check_rules(user_query)
        if direct: return direct

        # 行事曆
        if "行事曆" in user_query:
            cal_data, month, source_url = self.get_calendar(user_query)
            if cal_data:
                retrieved_data = cal_data
                system_instruction = f"""
你現在是內湖高工的行事曆秘書。使用者想查詢 {month} 月份的行事曆。
請根據原始資料，區分【🏠 家長與學生重要日程】與【🏫 學校行政與教師事務】。
請在回覆最末端列出：🌐 資料來源：[114學年度第2學期行事曆]({source_url})
"""
                user_query = f"請幫我整理 {month} 月份的行事曆。\n\n【原始資料】：\n{cal_data}"
            else:
                return f"🔍 查詢不到 {datetime.now().year}年 相關月份的行事曆資訊。"

        # 一般搜尋 (校長、合作社)
        else:
            retrieved_data = self.search_db(user_query, top_n=8) # 抓多一點給 AI 判斷
            
            # 🔥 Prompt 優化：讓 Gemini 知道它的任務是「閱讀理解」
            system_instruction = """
你是一個聰明的內湖高工校園小幫手。
使用者的問題可能無法直接從關鍵字找到答案，你需要「閱讀」下方的檢索資料來推理。

【特殊任務】：
1. **校長資訊**：若檢索資料中有提到「校長室」、「業務職掌」或「林俊岳」，請整理出校長姓名與聯絡方式。
2. **合作社/泡麵**：若檢索資料中有提到「員生社」、「販賣」、「食品」或「熱食部」，請查看內容是否有提到相關商品。若資料中完全沒提到販賣項目，請回答「資料庫中尚無合作社詳細販售清單」。
3. **網址**：請務必附上該筆資料的網址。
"""
            if not retrieved_data:
                return "您的問題很好！目前公告資料庫中暫時找不到相關資訊。建議您聯繫學校 (02-26574874)，我們會記錄並更新。"

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

# ... (Debug 頁面與路由維持不變) ...
@app.route("/debug", methods=['GET'])
def debug_page():
    try:
        brain.cursor.execute("SELECT category, COUNT(*) FROM knowledge GROUP BY category")
        stats = brain.cursor.fetchall()
        brain.cursor.execute("SELECT id, title, content FROM knowledge WHERE content LIKE '%校長%' LIMIT 5")
        principal_rows = brain.cursor.fetchall()
        
        html = "<h1>🕵️‍♂️ 資料庫診斷</h1>"
        html += "<h3>📊 分類統計</h3><ul>"
        for s in stats: html += f"<li>{s[0]}: {s[1]} 筆</li>"
        html += "</ul>"
        html += "<h3>👨‍🏫 校長資料檢查</h3>"
        for r in principal_rows: html += f"<p>ID:{r[0]} | {r[1]}...</p>"
        return html
    except Exception as e: return str(e)

@app.route("/", methods=['GET'])
def index(): return "Bot Live (Smart Search)", 200

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
