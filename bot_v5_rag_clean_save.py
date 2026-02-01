import os
import json
import numpy as np
import faiss
import pickle
import google.generativeai as genai
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# ==========================================
# 🔑 金鑰設定 (從環境變數讀取)
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET")

# 初始化 Google Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# 初始化 LINE Bot
if LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET:
    line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
    handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)

# 檔案路徑設定 (必須與您上傳的檔名一致)
RAW_DATA_FILE = 'nihs_knowledge_full.json'
INDEX_FILE = 'nihs_faiss.index'  # 向量索引檔
PKL_FILE = 'nihs_chunks.pkl'     # 文字內容檔

# ==========================================
# 🧠 AI 大腦核心 (讀取檔案優先版)
# ==========================================
class CloudSchoolBrain:
    def __init__(self, json_path):
        self.ready = False
        self.index = None
        self.chunks = []
        self.json_path = json_path
        
        # 🟢 關鍵邏輯：優先讀取現成的索引檔
        if os.path.exists(INDEX_FILE) and os.path.exists(PKL_FILE):
            print("📂 [系統] 發現雲端大腦檔案，正在載入...")
            self.load_brain()
        else:
            print("🐢 [系統] 警告：找不到索引檔，將嘗試 API 重建 (可能導致記憶體不足)...")
            self.build_brain()

    def load_brain(self):
        """ 從硬碟讀取大腦 (快速啟動) """
        try:
            self.index = faiss.read_index(INDEX_FILE)
            with open(PKL_FILE, "rb") as f:
                self.chunks = pickle.load(f)
            self.ready = True
            print(f"✅ [系統] 大腦載入成功！索引大小: {self.index.ntotal}")
        except Exception as e:
            print(f"❌ 讀取存檔失敗: {e}")

    def get_embedding(self, text):
        try:
            # 使用最新 text-embedding-004
            result = genai.embed_content(
                model="models/text-embedding-004",
                content=text,
                task_type="retrieval_query"
            )
            return result['embedding']
        except Exception as e:
            print(f"❌ Embedding Error: {e}")
            return [0] * 768

    def build_brain(self):
        """ 備用方案：現場建立索引 (盡量避免在雲端執行此段) """
        try:
            if not os.path.exists(self.json_path):
                return
            with open(self.json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.chunks = []
            vectors = []
            for item in data:
                text_content = f"標題：{item.get('title', '')}\n內文：{item.get('content', '')}"
                self.chunks.append(text_content)
                vec = self.get_embedding(text_content)
                vectors.append(vec)

            embedding_matrix = np.array(vectors).astype('float32')
            dimension = 768 
            self.index = faiss.IndexFlatL2(dimension)
            self.index.add(embedding_matrix)
            self.ready = True
            print(f"✅ [雲端大腦] 重建完成！")
        except Exception as e:
            print(f"❌ 重建失敗: {e}")

    def search(self, query, top_k=3):
        if not self.ready:
            return []
        query_vec = self.get_embedding(query)
        query_vec_np = np.array([query_vec]).astype('float32')
        distances, indices = self.index.search(query_vec_np, top_k)
        results = []
        for i in range(top_k):
            idx = indices[0][i]
            if idx != -1 and idx < len(self.chunks):
                results.append(self.chunks[idx])
        return results

    def ask_gemini(self, query, context_list):
        context_text = "\n\n".join(context_list)
        prompt = f"""
        你是內湖高工的親切校園助手。請根據參考資料回答問題。
        若資料不足，請禮貌告知查無資訊。
        
        【參考資料】：
        {context_text}
        
        【問題】：{query}
        """
        model = genai.GenerativeModel('gemini-1.5-pro') 
        response = model.generate_content(prompt)
        return response.text

# ==========================================
# 🚀 啟動機制
# ==========================================
brain = None

def get_brain():
    global brain
    if brain is None:
        print("🐢 [系統] 啟動大腦引擎中...")
        brain = CloudSchoolBrain(RAW_DATA_FILE)
    return brain

# ==========================================
# 🌐 Flask 路由
# ==========================================
@app.route("/", methods=['GET'])
def home():
    # 這是給 UptimeRobot 的心跳回應
    return "Hello, NIHS Bot is alive! (Brain Loaded)", 200

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
    print(f"👉 [Debug] 收到訊息: {user_msg}")
    
    try:
        current_brain = get_brain()
        
        if not current_brain or not current_brain.ready:
            reply_text = "系統正在啟動中，請稍後再試..."
        else:
            relevant_docs = current_brain.search(user_msg, top_k=3)
            reply_text = current_brain.ask_gemini(user_msg, relevant_docs)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )
    except Exception as e:
        print(f"❌ [Error] {e}")

if __name__ == "__main__":
    app.run(port=5000)

# 這是給 UptimeRobot 檢查用的 "心跳" 路徑
@app.route("/")
def home():
    return "Hello, NIHS Bot is alive!", 200


