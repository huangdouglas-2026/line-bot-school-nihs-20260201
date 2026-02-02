import os
import json
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

# 📂 資料路徑
DATA_FILE = 'nihs_knowledge_full.json'
FAQ_FILE = 'nihs_faq.json'
CALENDAR_FILE = 'nihs_calendar.json'

# ==========================================
# 🧠 AI 大腦 (西元年優化 + 月份智慧過濾)
# ==========================================
class FullContextBrain:
    def __init__(self):
        self.ready = False
        self.combined_context = ""
        self.load_all_sources()

    def load_all_sources(self):
        """ 同時載入所有資料並預處理 """
        all_text_parts = []
        try:
            # 1. 公告與知識庫
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for item in data:
                        all_text_parts.append(f"【公告】標題:{item.get('title')} 內容:{item.get('content')} 網址:{item.get('url')}")
            
            # 2. 交通與通訊
            if os.path.exists(FAQ_FILE):
                with open(FAQ_FILE, 'r', encoding='utf-8') as f:
                    faq = json.load(f)
                    t = faq.get("traffic", {})
                    all_text_parts.append(f"【交通】地址:{t.get('address')} 捷運:{t.get('mrt')} 公車:{t.get('bus')}")
                    for c in faq.get("contacts", []):
                        all_text_parts.append(f"【通訊】{c.get('title')}({c.get('name')}):{c.get('phone')}")
            
            # 3. 行事曆
            if os.path.exists(CALENDAR_FILE):
                with open(CALENDAR_FILE, 'r', encoding='utf-8') as f:
                    cal = json.load(f)
                    for ev in cal:
                        all_text_parts.append(f"【行事曆】日期:{ev.get('date')} 活動:{ev.get('event')}")

            self.combined_context = "\n".join(all_text_parts)
            self.ready = True
        except Exception as e:
            print(f"❌ 載入失敗: {e}")
            self.ready = False

    def ask(self, user_query):
        if not self.ready:
            return "小幫手正在更新腦袋資料，請稍等一下再問我喔！"

        # 🕒 獲取目前時間
        now = datetime.now()
        cur_year, cur_month = now.year, now.month

        # 🧠 加強版 Prompt
        prompt = f"""
你是一個親切且積極的內湖高工校園小幫手。
今天是 {cur_year} 年 {cur_month} 月 {now.day} 日。

【關鍵指令】：
1. **西元年格式**：資料庫中若出現「民國115年」或「114年」，請一律換算並以「西元年」呈現 (例如 2026年、2025年)。
2. **月份智慧過濾**：
   - 當家長詢問「學校行事曆」或詢問日程但未指定月份時，請「僅顯示 {cur_month} 月份」的活動。
   - 並在結尾親切提醒：『其餘月份的活動，歡迎參考下方來源網址，或輸入具體月份（如：3月行事曆）讓我幫您查詢唷！』
3. **美式服務風格**：
   查無資訊時請說：「您的問題很好！目前公告中暫時找不到相關資訊。建議家長您可以先直接聯繫學校詢問。同時，我們也會將您的問題記錄下來，並儘快更新在資料庫中，讓其他家長未來可以參考。謝謝您幫助我們變得更好！」
4. **格式與網址**：
   - 使用條列式、適度 Emoji。
   - 網址請在回答結束後標註一次「👉 來源網址：[URL]」。

【校園知識庫內容】：
{self.combined_context}

【家長問題】：
{user_query}

【你的回答】：
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            response = model.generate_content(prompt)
            return response.text
        except:
            return "您的問題很好！不過小幫手現在連線有點忙碌，能請您稍後再試一次嗎？感謝您的包容！"

# 初始化
brain = FullContextBrain()

# ==========================================
# 🌐 路由與 LINE 處理
# ==========================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text.strip()
    reply = brain.ask(user_msg)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run(port=5000)
