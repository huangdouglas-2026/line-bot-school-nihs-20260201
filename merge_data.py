import json
import os
import datetime

# 定義檔案路徑
FILES = {
    'static': 'nihs_static_data_v43.json',
    'dynamic': 'nihs_final_v40.json', # 這是動態爬蟲剛抓下來的"當日增量"
    'calendar': 'nihs_calendar.json',
    'faq': 'nihs_faq.json',
    'master': 'nihs_knowledge_full.json' # 這是我們的主資料庫 (含 AI 標籤)
}

def load_json(filepath):
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return [] # 若檔案不存在回傳空陣列

def merge_data():
    print("🔄 啟動智慧合併 (Smart Merge)...")

    # 1. 讀取主資料庫 (Master DB) - 這是我們的「資產」，裡面有珍貴的 AI 標籤
    master_data = load_json(FILES['master'])
    print(f"   📖 主資料庫現有: {len(master_data)} 筆")

    # 建立一個用 URL 或 Title 當 Key 的字典，方便快速比對
    # 邏輯：key = url (若無 url 則用 title)
    master_map = {item.get('url', item.get('title')): item for item in master_data}

    # 2. 讀取新資料 (New Inputs)
    new_data_sources = [
        load_json(FILES['static']),
        load_json(FILES['dynamic']),
        load_json(FILES['calendar'])
        # FAQ 結構不同，通常不直接 merge 進 list，而是獨立讀取，這裡視您的架構而定
        # 如果您的 bot 是分開讀 FAQ 的，這裡就不用 merge FAQ
    ]

    updates_count = 0
    new_entry_count = 0

    for source in new_data_sources:
        if not isinstance(source, list): continue # 防呆

        for new_item in source:
            key = new_item.get('url', new_item.get('title'))
            
            if key in master_map:
                # 狀況 A：資料已存在 -> 更新內容，但保留 AI 標籤
                existing_item = master_map[key]
                
                # 保留珍貴的 AI 欄位 (tags, summary, content_enriched)
                if 'tags' in existing_item: new_item['tags'] = existing_item['tags']
                if 'summary' in existing_item: new_item['summary'] = existing_item['summary']
                if 'content_enriched' in existing_item: 
                    # 這裡有個策略：如果原文變了，enriched 其實要重做。
                    # 但通常公告不會改原文。我們先假設保留。
                    new_item['content_enriched'] = existing_item['content_enriched']
                
                # 更新 master_map (這樣新的內容會蓋過舊的，但標籤被我們上面那幾行救回來了)
                master_map[key] = new_item
                updates_count += 1
            else:
                # 狀況 B：新資料 -> 直接加入
                master_map[key] = new_item
                new_entry_count += 1

    # 3. 轉回 List 並存檔
    final_list = list(master_map.values())
    
    # 根據日期排序 (新的在上面)
    # 嘗試解析日期，若無日期則排在最後
    def sort_key(x):
        d = x.get('date', '1900/01/01')
        return d if d else '1900/01/01'

    final_list.sort(key=sort_key, reverse=True)

    with open(FILES['master'], 'w', encoding='utf-8') as f:
        json.dump(final_list, f, ensure_ascii=False, indent=4)

    print(f"✅ 合併完成！")
    print(f"   ➕ 新增資料: {new_entry_count} 筆")
    print(f"   🔄 更新資料: {updates_count} 筆 (保留 AI 標籤)")
    print(f"   📊 目前總數: {len(final_list)} 筆")

if __name__ == "__main__":
    merge_data()
