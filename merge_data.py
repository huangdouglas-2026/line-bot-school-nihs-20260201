import json
import os
from datetime import datetime

# 📂 設定檔案路徑
FILE_ANNOUNCEMENT = "nihs_final_v40.json"       # 1. 公告資料 (List)
FILE_STATIC       = "nihs_static_data_v43.json" # 2. 靜態資料 (List)
FILE_FAQ          = "nihs_faq.json"             # 3. AI 提煉題庫 (Dict)
FILE_CALENDAR     = "nihs_calendar.json"        # 4. AI 提煉行事曆 (List)

OUTPUT_FILE = "nihs_knowledge_full.json"        # 合併後的總檔案

def merge():
    print("🔄 開始執行資料大整合...")
    full_data = []

    # --- 1. 處理公告資料 (List) ---
    if os.path.exists(FILE_ANNOUNCEMENT):
        with open(FILE_ANNOUNCEMENT, 'r', encoding='utf-8') as f:
            data = json.load(f)
            full_data.extend(data)
        print(f"   📖 [公告資料] 載入完成: {len(data)} 筆")

    # --- 2. 處理靜態資料 (List) ---
    if os.path.exists(FILE_STATIC):
        with open(FILE_STATIC, 'r', encoding='utf-8') as f:
            data = json.load(f)
            full_data.extend(data)
        print(f"   📖 [靜態資料] 載入完成: {len(data)} 筆")

    # --- 3. 處理行事曆 (List) ---
    # 行事曆也是清單格式，直接併入以利 AI 檢索
    if os.path.exists(FILE_CALENDAR):
        with open(FILE_CALENDAR, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # 將行事曆格式標準化為知識條目，方便 AI 搜尋
            calendar_items = []
            for ev in data:
                calendar_items.append({
                    "category": "學期行事曆",
                    "unit": ev.get("category", "校務"),
                    "date": ev.get("date"),
                    "title": f"行事曆活動: {ev.get('event')}",
                    "content": f"日期: {ev.get('date')}\n活動名稱: {ev.get('event')}\n類別: {ev.get('category')}",
                    "url": "https://www.nihs.tp.edu.tw/nss/p/index"
                })
            full_data.extend(calendar_items)
        print(f"   📖 [行事曆] 載入完成: {len(calendar_items)} 筆活動")

    # --- 4. 處理 FAQ 題庫 (Dict) ---
    # FAQ 是字典格式，我們將其轉化為一條大型知識條目
    if os.path.exists(FILE_FAQ):
        with open(FILE_FAQ, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
            # 轉換為可檢索的文字格式
            traffic = data.get("traffic", {})
            contacts = data.get("contacts", [])
            
            faq_content = "【交通資訊】\n"
            faq_content += f"地址：{traffic.get('address')}\n捷運：{traffic.get('mrt')}\n公公車：{traffic.get('bus')}\n\n"
            faq_content += "【常用聯絡電話/分機】\n"
            for c in contacts:
                faq_content += f"{c.get('title')} {c.get('name')}: {c.get('phone')}\n"

            full_data.append({
                "category": "常見問題題庫",
                "unit": "秘書室/總務處",
                "date": datetime.now().strftime("%Y/%m/%d"),
                "title": "內湖高工常見問題 (交通、地址、各處室電話分機)",
                "content": faq_content,
                "url": "https://www.nihs.tp.edu.tw/nss/p/index"
            })
        print(f"   📖 [FAQ 題庫] 載入並結構化完成")

    # --- 總結與存檔 ---
    print(f"   📊 總計整合資料: {len(full_data)} 筆")

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(full_data, f, ensure_ascii=False, indent=4)
    
    print(f"✅ 合併完成！全知資料庫已更新: {OUTPUT_FILE}")

if __name__ == "__main__":
    merge()
