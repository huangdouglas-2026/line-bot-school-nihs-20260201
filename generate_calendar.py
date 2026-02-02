import os
import json
import requests
import pdfplumber
import google.generativeai as genai
import re
from datetime import datetime

# ==========================================
# 🔑 設定區
# ==========================================
# 統一使用 2.0-flash 確保邏輯與年份判斷最準確
MODEL_NAME = 'gemini-2.0-flash' 

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

INPUT_FILE = 'nihs_knowledge_full.json'
OUTPUT_FILE = 'nihs_calendar.json'
TEMP_PDF = 'temp_calendar.pdf'

def find_official_calendar():
    """ 
    邏輯優化：精準鎖定標題符合「XX學年度第X學期行事曆」的 PDF
    """
    if not os.path.exists(INPUT_FILE):
        print("❌ 找不到資料庫檔案")
        return None, None, None

    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 用正則表達式匹配：XX學年度(第X學期)行事曆
    pattern = re.compile(r"(\d{3})學年度(第[一二12]學期)?行事曆")

    candidates = []
    for item in data:
        title = item.get('title', '')
        match = pattern.search(title)
        
        if match and item.get('attachments'):
            for att in item['attachments']:
                url = att.get('url', '')
                if url.lower().endswith('.pdf'):
                    candidates.append({
                        "weight": int(match.group(1)),
                        "date": item.get('date', '1900/01/01'),
                        "title": title,
                        "url": url
                    })
    
    if not candidates:
        print("⚠️ 找不到符合格式的行事曆 PDF")
        return None, None, None

    candidates.sort(key=lambda x: (x['weight'], x['date']), reverse=True)
    latest = candidates[0]
    
    print(f"✅ 成功鎖定正式行事曆：{latest['title']}")
    return latest['url'], latest['title'], latest['date']

def download_pdf(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        # verify=False 處理學校網站可能的 SSL 問題
        response = requests.get(url, headers=headers, stream=True, timeout=15, verify=False)
        if response.status_code == 200:
            with open(TEMP_PDF, 'wb') as f:
                f.write(response.content)
            return True
    except Exception as e:
        print(f"❌ 下載錯誤: {e}")
    return False

def extract_text_from_pdf():
    full_text = ""
    try:
        with pdfplumber.open(TEMP_PDF) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row in table:
                            clean_row = [str(cell).strip().replace('\n', '') for cell in row if cell]
                            full_text += " | ".join(clean_row) + "\n"
                full_text += page.extract_text() or ""
        return full_text
    except Exception as e:
        print(f"❌ PDF 解析失敗: {e}")
        return ""

def generate_calendar_json(pdf_text, doc_title, doc_date):
    print(f"🧠 使用 {MODEL_NAME} 解析行事曆 (年份校正模式)...")
    
    academic_year_match = re.search(r'(\d{3})', doc_title)
    academic_year = int(academic_year_match.group(1)) if academic_year_match else 114
    
    is_second_semester = "二" in doc_title or "2" in doc_title
    base_year_start = academic_year + 1911
    
    if is_second_semester:
        target_year = base_year_start + 1
        year_instruction = f"此為第2學期，所有月份（2月至7月）的年份皆為 {target_year} 年。"
        year_limit = f"嚴禁出現 {target_year + 1} 年 (如 2027)。"
    else:
        target_year = base_year_start
        year_instruction = f"此為第1學期，8月至12月為 {target_year} 年，隔年1月為 {target_year + 1} 年。"
        year_limit = f"嚴禁在 12 月之前出現 {target_year + 1} 年。"

    prompt = f"""
    你是校務資料處理專家。請根據下方行事曆 PDF 內容，整理出完整的活動清單。

    【背景資訊】:
    - 文件標題: "{doc_title}"
    - 年份判定邏輯: {year_instruction}
    - 限制: {year_limit}

    【任務】:
    1. 將所有活動轉為標準 JSON 格式。
    2. 日期格式必須為: "YYYY/MM/DD"。
    3. 如果活動有多個日期(如 6/29-6/30)，請拆分為兩筆或使用該範圍的第一天。
    4. **重要**：如果內容很多，請精簡描述活動名稱，確保 JSON 結構完整。

    【輸出格式】:
    [
      {{ "date": "YYYY/MM/DD", "event": "活動名稱", "category": "分類" }}
    ]

    【PDF 內容】:
    {pdf_text[:35000]}
    """

    # 🛠️ 關鍵設定優化
    generation_config = genai.types.GenerationConfig(
        response_mime_type="application/json",
        max_output_tokens=8192, # 確保空間足夠
        temperature=0
    )

    try:
        model = genai.GenerativeModel(MODEL_NAME)
        response = model.generate_content(prompt, generation_config=generation_config)
        
        # 取得原始文字
        res_text = response.text.strip()
        
        # 🛠️ 修復機制：檢查 JSON 是否被截斷 (漏掉結尾的 ])
        if not res_text.endswith(']'):
            print("⚠️ 偵測到 JSON 截斷，嘗試自動修復結尾...")
            # 找到最後一個完整的物件結束位置
            last_obj_end = res_text.rfind('}')
            if last_obj_end != -1:
                res_text = res_text[:last_obj_end+1] + ']'
        
        return json.loads(res_text)

    except json.JSONDecodeError as je:
        print(f"❌ JSON 解析失敗: {je}")
        # 除錯用：印出出錯位置附近的文字
        start_pos = max(0, je.pos - 50)
        end_pos = min(len(response.text), je.pos + 50)
        print(f"🔍 錯誤附近文字: ...{response.text[start_pos:end_pos]}...")
        return []
    except Exception as e:
        print(f"❌ AI 解析出錯: {e}")
        return []

if __name__ == "__main__":
    pdf_url, title, date_str = find_official_calendar()
    
    if pdf_url:
        if download_pdf(pdf_url):
            raw_text = extract_text_from_pdf()
            if raw_text:
                events = generate_calendar_json(raw_text, title, date_str)
                if events:
                    # 排序確保 JSON 產出按日期排列
                    events.sort(key=lambda x: x['date'])
                    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                        json.dump(events, f, ensure_ascii=False, indent=4)
                    print(f"✅ 成功生成行事曆資料庫 ({len(events)} 筆活動)")
            
            if os.path.exists(TEMP_PDF):
                os.remove(TEMP_PDF)