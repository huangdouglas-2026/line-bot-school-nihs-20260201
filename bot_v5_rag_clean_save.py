import os
import json
import google.generativeai as genai
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

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

# 📂 完整資料來源路徑
DATA_FILE = 'nihs_knowledge_full.json'
FAQ_FILE = 'nihs_faq.json'
CALENDAR_FILE = 'nihs_calendar.json'

# ==========================================
# 🧠 AI 大腦 (全量檢索 + 美式積極服務)
# ==========================================
class FullContextBrain:
    def __init__(self):
        self.ready = False
        self.combined_context = ""
        self.load_all_sources()

    def load_all_sources(self):
        """ 同時讀取三個檔案，確保資料不縮水 """
        all_text_parts = []
        try:
            # 1. 載入全知資料庫 (主要公告與內容)
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for item in data:
                        all_text_parts.append(f"【公告/知識】標題:{item.get('title')} 內容:{item.get('content')} 網址:{item.get('url')}")
            
            # 2. 載入 FAQ (地址、交通、電話)
            if os.path.exists(FAQ_FILE):
                with open(FAQ_FILE, 'r', encoding='utf-8') as f:
                    faq = json.load(f)
                    t = faq.get("traffic", {})
                    all_text_parts.append(f"【基礎資訊】地址:{t.get('address')} 捷運:{t.get('mrt')} 公車:{t.get('bus')}")
                    for c in faq.get("contacts", []):
                        all_text_parts.append(f"【聯絡電話】{c.get('title')}({c.get('name')}):{c.get('phone')}")
            
            # 3. 載入行事曆 (日程活動)
            if os.path.exists(CALENDAR_FILE):
                with open(CALENDAR_FILE, 'r', encoding='utf-8') as f:
                    cal = json.load(f)
                    for ev in cal:
                        all_text_parts.append(f"【行事曆】日期:{ev.get('date')} 活動:{ev.get('event')} 類別:{ev.get('category')}")

            self.combined_context = "\n".join(all_text_parts)
            self.ready = True
            print(f"✅ 資料載入成功，總知識量：{len(all_text_parts)} 條")
        except Exception as e:
            print(f"❌ 資料載入失敗: {e}")
            self.ready = False

    def ask(self, user_query):
        if not self.ready:
            return "系統正在更新資料庫，請稍後再試一次唷！"

        # 構建注入所有來源的 Prompt
        prompt = f"""
你是一個親切且積極的內湖高工校園小幫手。
請根據下方的【全量校園知識庫】回答家長的【問題】。

【回答準則】：
1. 語氣：親切、專業、展現熱誠。
2. **美式服務風格（針對查無資料時）**：
   「您的問題很好！目前公告中暫時找不到相關資訊。建議家長您可以先直接聯繫學校詢問。同時，我們也會將您的問題記錄下來，並儘快更新在資料庫中，讓其他家長未來也可以參考。謝謝您幫助我們變得更好！」
3. **資訊完整性**：
   - 務必提及資料中的具體日期、分機、網址。
   - 附件提醒：若資料有附件，提醒家長可點擊連結查看。
4. **來源呈現**：
   - 回答結束後，若有參考網址，請統一標註一次「👉 參考來源：[URL]」。

【全量校園知識庫內容】：
{self.combined_context}

【家長問題】：
{user_query}

【你的回答】：
"""
        try:
            model = genai.GenerativeModel(MODEL_NAME)
            # 針對長內容調整設定
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            print(f"❌ AI 生成錯誤: {e}")
            return "您的問題很好！但小幫手連線出了點小狀況，能請您再試一次嗎？感謝您的包容！"

# 初始化
brain = FullContextBrain()

# ==========================================
# 🌐 路由區 (由 Render/地端接收訊息)
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
