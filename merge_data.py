import json
import os

# 📂 設定
FILE_1 = "nihs_final_v40.json"      # 公告資料
FILE_2 = "nihs_static_data_v43.json"    # 靜態資料
OUTPUT_FILE = "nihs_knowledge_full.json" # 合併後的總檔案

def merge():
    print("🔄 開始合併資料...")
    data1 = []
    data2 = []

    if os.path.exists(FILE_1):
        with open(FILE_1, 'r', encoding='utf-8') as f:
            data1 = json.load(f)
        print(f"   📖 載入公告資料: {len(data1)} 筆")

    if os.path.exists(FILE_2):
        with open(FILE_2, 'r', encoding='utf-8') as f:
            data2 = json.load(f)
        print(f"   📖 載入靜態資料: {len(data2)} 筆")

    # 合併
    full_data = data1 + data2
    print(f"   📊 總計: {len(full_data)} 筆")

    # 寫入
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(full_data, f, ensure_ascii=False, indent=4)
    
    print(f"✅ 合併完成！請將 Bot 的讀取檔案改為: {OUTPUT_FILE}")

if __name__ == "__main__":
    merge()