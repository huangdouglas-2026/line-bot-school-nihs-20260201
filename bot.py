# ==========================================
# 內湖高工家長小幫手 (Render 雲端部署版)
# ==========================================
import os
import logging
import asyncio
import nest_asyncio
from flask import Flask, request, abort

# --- LINE SDK v3 ---
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent
)

# --- AI 與 資料庫 ---
from crawl4ai import AsyncWebCrawler
import google.generativeai as genai
import chromadb

# 讓 Async 環境共存
nest_asyncio.apply()
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# ==========================================
# 🔑 設定區 (讀取環境變數，若無則使用預設值)
# ==========================================
# 在 Render 後台設定這些 Key，比寫在程式碼裡更安全
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "您的_GEMINI_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "您的_LINE_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "您的_LINE_CHANNEL_SECRET")

# ==========================================
# 🎯 初始化與爬蟲
# ==========================================
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.0-flash')
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Render 免費版重開機後檔案會消失，所以每次啟動都要重建資料庫
chroma_client = chromadb.Client() # 使用記憶體模式，不用存檔到硬碟
collection = chroma_client.get_or_create_collection(name="school_data_cloud")

TARGET_URLS = [
    "https://www.nihs.tp.edu.tw/nss/p/index",
    "https://www.nihs.tp.edu.tw/nss/s/principal/p/01",
    "https://www.nihs.tp.edu.tw/nss/p/contact",
    "https://www.nihs.tp.edu.tw/nss/p/06",
]

# 黃金小抄 (System Prompt)
school_fact_sheet = """
【學校基本資料 (必讀)】
* 現任校長：林俊岳
* 學校地址：臺北市內湖區內湖路一段520號
* 總機電話：(02) 2657-4874
* 學校網址：https://www.nihs.tp.edu.tw
"""

async def update_knowledge_base():
    print("🚀 [雲端系統] 正在啟動爬蟲 (這可能需要幾十秒)...")
    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun_many(urls=TARGET_URLS)

    for result in results:
        if not result.success: continue
        content = result.markdown
        if not content: continue
        
        chunk_size = 1000
        chunks = [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
        if chunks:
            ids = [f"{result.url}_{i}" for i in range(len(chunks))]
            collection.upsert(
                documents=chunks,
                ids=ids,
                metadatas=[{"source": result.url} for _ in range(len(chunks))]
            )
    print("✅ [完成] 知識庫準備就緒！")

# ==========================================
# 🤖 LINE Webhook
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

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_msg = event.message.text
    try:
        results = collection.query(query_texts=[user_msg], n_results=3)
        context_text = ""
        if results['documents'] and results['documents'][0]:
            for i, doc in enumerate(results['documents'][0]):
                context_text += f"參考資料:\n{doc[:200]}...\n\n"

        if not context_text:
            prompt = f"請參考基本資料回答：{school_fact_sheet}\n問題：{user_msg}"
        else:
            prompt = f"請參考基本資料：{school_fact_sheet}\n爬蟲資料：{context_text}\n回答問題：{user_msg}"

        response = model.generate_content(prompt)
        
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=response.text)]
                )
            )
    except Exception as e:
        print(f"Error: {e}")

# ==========================================
# 🚀 啟動入口 (Gunicorn 會呼叫這裡)
# ==========================================
# 這一行非常重要，這是為了在啟動前先跑一次爬蟲
with app.app_context():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(update_knowledge_base())

if __name__ == "__main__":
    app.run()