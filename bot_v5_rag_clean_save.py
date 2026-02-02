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

DATA_FILE = 'nihs_knowledge_full.json'
FAQ_FILE = 'nihs_faq.json'
CALENDAR_FILE = 'nihs_calendar.json'

# ==========================================
# 🧠 AI 大腦 (防幻覺、防自作聰明版)
# ==========================================
class FullContextBrain:
    def __init__(self):
        self.ready = False
        self.combined_context = ""
        self.load_all_sources()

    def load_all_sources(self):
        all_text_parts = []
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for item in data:
                        all_text_parts.append(f"【公告/知識】標題:{item.get('title')} 內容:{item.get('content')} 網址:{item.get('url')}")
            
            if os.path.exists(FAQ_FILE):
                with open(FAQ_FILE, 'r', encoding='utf-8') as f:
                    faq = json.load(f)
                    t = faq.get("traffic", {})
                    all_text_parts.append(f"【基礎資訊】地址:{t.get('address')} 捷運:{t.get('mrt')} 公車:{t.get('bus')}")
                    for c in faq.get("contacts", []):
                        all_text_parts.append(f"【聯絡電話】職稱:{c.get('title')} 姓名:{c.get('name')} 電話/分機:{c.get('phone')}")
            
            if os.path.exists(CALENDAR_FILE):
                with open(CALENDAR_FILE, 'r', encoding='utf-8') as f:
                    cal = json.load(f)
                    for ev in cal:
                        all_text_parts.append(f"【行事曆】日期:{ev.get('date')} 活動:{ev.get('event')} 類別:{ev.get('category')}")

            self.combined_context = "\n".join(all_text_parts)
            self.ready = True
            print(f"✅ 資料載入成功。")
        except Exception as e:
            self.ready = False

    def ask(self, user_query):
        if not self.ready:
            return "小幫手正在更新資料庫，請稍後再試一次。"

        now = datetime.now()
        cur_year, cur_month = now.year, now.month

        # 🛡️ 核心 Prompt：增加「防禦性指令」
        prompt = f"""
你是「內湖高工校園小幫手」，一個專門為家長解決校務問題的 AI 助手。
今天是西元 {cur_year} 年 {cur_month} 月 {now.day} 日。

【關鍵原則：禁止幻覺與禁止無關回覆】
1. **嚴格對照**：你的回答必須「完全基於」下方的【校園知識庫】。
2. **禁止補償行為**：如果家長詢問的主題（例如「智慧機器人」）在知識庫中完全沒有相關記載，請「絕對不要」列出最近的公告或任何不相關的內容。
3. **查無資料處理**：若無法從知識庫中找到匹配答案，必須「僅回覆」以下內容，不得自行加料：
   「您的問題很好！目前公告中暫時找不到相關資訊。建議家長您可以先直接聯繫學校詢問。同時，我們也會將您的問題記錄下來，並儘快更新在資料庫中，讓其他家長未來可以參考。謝謝您幫助我們變得更好！」
4. **身份說明**：如果你被問到「你是誰」或「你是智慧機器人嗎」，請回答你是「內湖高工校園小幫手」，目前服務於校園資訊查詢。

【處理邏輯】：
- **通訊與地址優先**：問電話或地址，直接去【聯絡電話】與【基礎資訊】找。
- **行事曆篩選**：問日程，僅列出 {cur_month} 月份活動。
- **日期換算**：民國 114/115 年統一顯示為西元 2025/2026 年。

【校園知識庫內容】：
{self.combined_context}

【家長問題】：
{user_query}

【你的回答】：
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            # 💡 將 Temperature 設為 0，徹底壓制 AI 的「創造力」，讓它只會說實話
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(temperature=0.0)
            )
            return response.text
        except Exception as e:
            return "您的問題很好！不過小幫手現在連線有點忙碌，能請您再試一次嗎？"

# ==========================================
# 🌐 服務啟動
# ==========================================
brain = FullContextBrain()

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
