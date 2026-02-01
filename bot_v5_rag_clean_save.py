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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

# 初始化
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

if LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET:
    line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
    handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)
DATA_FILE = 'nihs_knowledge_full.json'

# ==========================================
# 🧠 AI 大腦 (Gemini 2.0 Flash - 親切排版版)
# ==========================================
class FullContextBrain:
    def __init__(self, json_path):
        self.knowledge_text = ""
        self.load_data(json_path)

    def load_data(self, path):
        """ 讀取 JSON 並保留詳細資訊 """
        if not os.path.exists(path):
            self.knowledge_text = "目前系統資料庫遺失 >_<"
            return
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            print(f"📂 [系統] 正在載入 {len(data)} 筆資料...")
            
            text_parts = []
            for item in data:
                # 欄位讀取
                date = item.get('date', '無日期')
                unit = item.get('unit', '無單位')
                title = item.get('title', '無標題')
                content = item.get('content', '無內容')
                url = item.get('url', '無連結')
                
                # 附件處理
                attachments = item.get('attachments', [])
                attach_str = "無"
                if isinstance(attachments, list) and len(attachments) > 0:
                    names = []
                    for a in attachments:
                        if isinstance(a, dict):
                            names.append(a.get('name', '附件'))
                        else:
                            names.append(str(a))
                    attach_str = ", ".join(names)

                # 組合資料塊
                part = f"""
【日期】：{date}
【單位】：{unit}
【標題】：{title}
【網址】：{url}
【附件】：{attach_str}
【內容】：{content}
--------------------------------"""
                text_parts.append(part)
            
            self.knowledge_text = "\n".join(text_parts)
            print(f"✅ [系統] 資料載入完成！")
            
        except Exception as e:
            print(f"❌ 讀取資料失敗: {e}")
            self.knowledge_text = "資料讀取發生錯誤。"

    def ask(self, user_query):
        """ 注入『親切+Emoji』的 Prompt """
        if not self.knowledge_text:
            return "系統現在有點累，讀不到資料庫 >_< 請稍後再試！"

        # ✨ 這是讓回應變可愛的關鍵 Prompt ✨
        prompt = f"""
        角色設定：你是內湖高工的 AI 虛擬小志工，名叫「內工小幫手」。
        個性：熱情、有禮貌、喜歡用 Emoji 讓對話更生動，但回答問題時邏輯清晰。

        任務：請閱讀下方的【校園知識庫】，回答家長或同學的【問題】。

        【回覆風格與排版要求】：
        1. 🎨 **排版要舒服**：
           - 請多用「條列式」列出重點，不要給一大塊密密麻麻的文字。
           - 善用空行來區隔不同段落。
        
        2. 😊 **語氣要軟性**：
           - 不要太像機器人，可以使用「您好呀～」、「這邊幫您找到...」、「請參考以下資訊」等親切用語。
        
        3. ✨ **適度使用 Emoji**：
           - 在標題、關鍵字、日期或連結旁加入對應符號。
           - 例如：📅 日期, 🔗 連結, 🏫 學校, 💡 提醒, 🏆 榮譽, 📢 公告。

        4. 🔗 **連結與附件 (非常重要)**：
           - 如果資料有網址 (URL)，請務必換行獨立列出，並加上「👉 點擊查看公告」之類的引導。
           - 如果有附件，請加上 📎 符號提醒。

        5. 🚫 **誠實至上**：
           - 如果資料庫真的找不到答案，請用遺憾但禮貌的語氣說「不好意思，目前的公告裡沒看到相關資訊耶 >_<」，並建議直接詢問處室。

        【校園知識庫內容】：
        {self.knowledge_text}

        【使用者問題】：
        {user_query}
        """

        try:
            # 使用 Gemini 2.0 Flash
            model = genai.GenerativeModel('gemini-2.0-flash')
            
            # 設定稍微高一點的 temperature 讓語氣更活潑 (0.7 ~ 0.8)
            generation_config = genai.types.GenerationConfig(
                temperature=0.75
            )
            
            response = model.generate_content(prompt, generation_config=generation_config)
            return response.text
        except Exception as e:
            print(f"❌ API Error: {e}")
            return "AI 大腦現在有點打結 (連線忙碌中)，請再問我一次試試看！🙏"

# 賴皮啟動
brain = None
def get_brain():
    global brain
    if brain is None:
        brain = FullContextBrain(DATA_FILE)
    return brain

# ==========================================
# 🌐 路由區
# ==========================================
@app.route("/", methods=['GET'])
def home():
    return "Hello! NIHS Bot V9 (Emoji Edition) is ready! ✨", 200

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
    msg = event.message.text.strip()
    print(f"🗣️ 家長問: {msg}")

    try:
        current_brain = get_brain()
        reply_text = current_brain.ask(msg)
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
        print("✅ 已回覆")

    except Exception as e:
        print(f"❌ 錯誤: {e}")

if __name__ == "__main__":
    app.run(port=5000)
