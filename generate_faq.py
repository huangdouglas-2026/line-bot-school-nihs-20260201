import os
import json
import google.generativeai as genai

# ==========================================
# 🔑 設定區
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

INPUT_FILE = 'nihs_knowledge_full.json'
OUTPUT_FILE = 'nihs_faq.json'

# ==========================================
# 🛡️ 保底資料庫 (Hardcoded Fallback)
# 當 AI 爬不到時，就用這些資料補位
# ==========================================
FALLBACK_DATA = {
    "traffic": {
        "address": "114064 臺北市內湖區內湖路一段520號",
        "mrt": "捷運文湖線-港墘站 (2號出口步行約3分鐘)",
        "bus": "內捷運港墘站：21、28、110、222、247、267、268、286、287、620、646、677、紅2、 藍7、藍26、棕16。港墘派出所站：0東、202、551、646、652、紅3。西湖圖書館站：214、278、552、553、1801、小2、 藍20"
    },
    "contacts": [
        { "category": "校級", "title": "學校總機", "name": "", "phone": "(02)2657-4874" },
        { "category": "校級", "title": "校安專線", "name": "", "phone": "(02)2798-9025" },
    #    { "category": "校級", "title": "傳真", "name": "教務處", "phone": "(02)2797-2384" },
        # 以下為預設分機 (若 AI 抓不到更新的，就用這些)
        { "category": "處室", "title": "校長室", "name": "", "phone": "分機 301" },
        { "category": "處室", "title": "秘書", "name": "", "phone": "分機 302" },
        { "category": "處室", "title": "教務主任", "name": "", "phone": "分機 311" },
        { "category": "處室", "title": "學務主任", "name": "", "phone": "分機 201" },
        { "category": "處室", "title": "總務主任", "name": "", "phone": "分機 121" },
        { "category": "處室", "title": "實習主任", "name": "", "phone": "分機 321" },
        { "category": "處室", "title": "輔導主任", "name": "", "phone": "分機 401" },
        { "category": "處室", "title": "圖書館主任", "name": "", "phone": "分機 271" },
    #    { "category": "處室", "title": "教官室", "name": "主任教官", "phone": "分機 309" }
    ]
}

def load_and_filter_data():
    if not os.path.exists(INPUT_FILE):
        return "", ""

    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    traffic_context = []
    contact_context = []
    
    # 關鍵字篩選
    kw_traffic = ["地址", "捷運", "公車", "路線", "交通"]
    kw_contact = ["電話", "分機", "總機", "主任", "組長", "校長"]

    for item in data:
        full_text = f"{item.get('title', '')}\n{item.get('content', '')}"
        
        if any(k in full_text for k in kw_traffic):
            traffic_context.append(full_text[:1000])
        if any(k in full_text for k in kw_contact):
            contact_context.append(full_text[:3000]) # 抓長一點避免漏掉名單

    return "\n".join(traffic_context), "\n".join(contact_context)

def merge_data(ai_data):
    """ 
    智慧合併：
    1. 優先使用 AI 抓到的資料 (因為可能是最新的)。
    2. 如果 AI 回傳 "查無資料" 或空值，就用 FALLBACK_DATA 覆蓋。
    """
    if not ai_data:
        return FALLBACK_DATA

    final_data = {"traffic": {}, "contacts": []}

    # --- 處理交通資訊 ---
    ai_traffic = ai_data.get("traffic", {})
    fb_traffic = FALLBACK_DATA["traffic"]
    
    for key in ["address", "mrt", "bus"]:
        val = ai_traffic.get(key, "")
        # 如果 AI 沒抓到，或者 AI 說 "查無資料"，就用保底的
        if not val or "查無" in val or len(val) < 5:
            final_data["traffic"][key] = fb_traffic[key]
        else:
            final_data["traffic"][key] = val

    # --- 處理通訊錄 ---
    ai_contacts = ai_data.get("contacts", [])
    fb_contacts = FALLBACK_DATA["contacts"]
    
    # 將 AI 抓到的聯絡人轉成字典方便查找
    ai_dict = {c.get("title", ""): c for c in ai_contacts}
    
    # 1. 先放入保底名單 (作為基礎)
    merged_contacts = []
    for fb_item in fb_contacts:
        title = fb_item["title"]
        # 如果 AI 也有抓到這個職稱，且內容不是"查無資料"，就用 AI 的 (可能有新名字)
        if title in ai_dict:
            ai_item = ai_dict[title]
            if ai_item.get("phone") and "查無" not in ai_item["phone"]:
                merged_contacts.append(ai_item)
            else:
                merged_contacts.append(fb_item) # AI 抓失敗，用保底
        else:
            merged_contacts.append(fb_item) # AI 沒抓到，用保底

    # 2. 加入 AI 抓到但不在保底名單內的新職稱 (例如：衛生組長)
    fb_titles = [c["title"] for c in fb_contacts]
    for c in ai_contacts:
        if c.get("title") not in fb_titles and "查無" not in c.get("phone", ""):
            merged_contacts.append(c)

    final_data["contacts"] = merged_contacts
    return final_data

def generate_faq_json(t_text, c_text):
    print("🧠 AI 正在分析資料...")
    
    # 如果完全沒爬到資料，直接回傳保底
    if not t_text and not c_text:
        print("⚠️ 爬蟲資料不足，直接使用保底資料庫。")
        return FALLBACK_DATA

    prompt = f"""
    請根據資料提取資訊並輸出 JSON。若找不到資料，對應欄位填寫 "null"。
    
    【格式要求】：
    {{
        "traffic": {{ "address": "...", "mrt": "...", "bus": "..." }},
        "contacts": [
            {{ "category": "處室", "title": "職稱", "name": "姓名", "phone": "分機" }}
        ]
    }}
    
    【資料】：
    {t_text[:10000]}
    {c_text[:20000]}
    """
    
    try:
        model = genai.GenerativeModel('gemini-2.0-flash')
        response = model.generate_content(prompt)
        json_str = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(json_str)
    except:
        return None

if __name__ == "__main__":
    t_text, c_text = load_and_filter_data()
    
    # 1. 嘗試用 AI 生成
    ai_result = generate_faq_json(t_text, c_text)
    
    # 2. 進行智慧合併 (AI + 保底)
    final_output = merge_data(ai_result)
    
    # 3. 存檔
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(final_output, f, ensure_ascii=False, indent=4)
        
    print(f"✅ 題庫建立成功 (混合模式)！已儲存至: {OUTPUT_FILE}")
    print("👉 交通與總機等核心資料已強制寫入，不會再有『查無資料』的情況。")