import discord
import asyncio
import random
import re
import os
from dotenv import load_dotenv

# ================= 設定區 =================
# 載入 .env 檔案裡的機密資料
load_dotenv()

# 透過 os.getenv 抓取 .env 裡名為 TRANSLATOR_TOKEN 的值
# 吹雪的token與染岡的使用者ID
BOT_TOKEN = os.getenv('TRANSLATOR_TOKEN')
TARGET_BOT_ID = 1492439449714561044

# --- 新增的監控設定 ---
# 替換成你想收到警報的 Discord 頻道 ID (請複製頻道ID，這必須是純數字，不能有引號)
ALERT_CHANNEL_ID = 1489990475242012834 
# 替換成你的染岡 Docker 容器名稱 (可以在 VM 輸入 docker ps 查看 NAMES 那欄)
DOCKER_CONTAINER_NAME = "tweetcord"

# 建立 client 物件 (必須放在 event 之前)
intents = discord.Intents.default()
intents.message_content = True  # 必須開啟才能讀取網址內容
client = discord.Client(intents=intents)

# ==========================================

def check_needs_translation(text):
    """檢查文字是否真的需要翻譯"""
    if not text:
        return False
        
    # 1. 移除推文裡的網址 (包含圖片連結)
    text = re.sub(r'http\S+', '', text)
    
    # 2. 移除 Discord 標記 (像是 @染岡)
    text = re.sub(r'<@\d+>', '', text)
    
    # 3. 核心過濾：移除所有標點符號與 Emoji
    # \w 代表保留各國語言文字與數字，\s 代表保留空格。其餘(包含Emoji)全部殺掉
    clean_text = re.sub(r'[^\w\s]', '', text)
    
    # 4. 去除頭尾多餘的空白
    clean_text = clean_text.strip()
    
    # 判斷：如果清完之後變成空的，或是「只剩下純數字」，就回傳 False (不需要翻譯)
    if not clean_text or clean_text.isnumeric():
        return False
        
    return True

def is_japanese(text):
    # 偵測是否含有平假名 (\u3040-\u309f) 或 片假名 (\u30a0-\u30ff)
    # 這是區分日文與中文最準確的方法
    return re.search(r'[\u3040-\u30ff]', text) is not None

# ================== 吹雪的秘密監視任務 ==================
async def monitor_someoka_logs():
    await client.wait_until_ready()
    channel = client.get_channel(ALERT_CHANNEL_ID)
    
    if not channel:
        print("找不到警報頻道，請確認 ALERT_CHANNEL_ID 是否正確！")
        return

    while not client.is_closed():
        try:
            # 讓吹雪執行指令，抓取染岡 Docker 的最後 30 行日誌
            cmd = f"docker logs --tail 30 {DOCKER_CONTAINER_NAME}"
            
            # 使用異步執行，避免這動作卡住吹雪原本的翻譯工作
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            # Docker 的日誌有時候會跑到 stderr，所以兩個都抓出來看
            logs = stdout.decode('utf-8') + stderr.decode('utf-8')

            # 檢查日誌裡有沒有出現 Token 過期的關鍵字
            if "401" in logs or "Unauthorized" in logs:
                await channel.send("🚨 **警告！** 染岡同學，你的 Twitter Token 好像過期了～")
                print("已發送 auth_token 過期警告！")
                # 為了避免吹雪每 10 分鐘就一直狂發訊息洗版，發送一次後讓他暫停監視 12 小時 (43200秒)
                await asyncio.sleep(43200)
                continue
                
        except Exception as e:
            print(f"吹雪監控 Docker 出錯: {e}")

        # 如果沒事，吹雪就去休息，10 分鐘 (600秒) 後再來偷看一次
        await asyncio.sleep(600)

# ========================================================

@client.event
async def on_ready():
    print(f'已登入為 {client.user}，開始檢查染岡同學的翻譯狀況...')

@client.event
async def on_message(message):
    # 1. 只處理染岡發出的訊息
    if message.author.id == TARGET_BOT_ID:
        
        # 2. 判斷是否為 fxtwitter 連結且需要翻譯
        if "fxtwitter.com" in message.content and "/zh-TW" in message.content and "?" not in message.content:
            
            check_text = ""
            embed_full_text = "" # 用來全面檢查整張卡片是否有「翻譯自」
            
            # 6. 【等待邏輯：輪詢檢查】等待原本的預覽跑完，並進行輪詢檢查
            # 最多等待 10 秒，每 2 秒檢查一次預覽卡片是否長出來了
            for i in range(5): 
                await asyncio.sleep(2) 
                # 重新從伺服器抓取訊息
                try:
                    updated_msg = await message.channel.fetch_message(message.id)
                    if updated_msg.embeds:
                        check_text = updated_msg.embeds[0].description or ""
                        # 把整張卡片的所有資訊(包含底部 Footer)轉成字串，一網打盡
                        embed_full_text = str(updated_msg.embeds[0].to_dict())
                        if check_text:
                            break
                except Exception as e:
                    print(f"檢查卡片時出錯: {e}")
            
            # 裝上過濾器！如果判定不需要翻譯(空字串、全符號)，直接結束
            if not check_needs_translation(check_text):
                print("推文內容為空、僅含網址或無意義符號，省略翻譯。")
                return
            
            # ================== 【乾淨俐落的兩段式過濾】 ==================
            
            # 優先級 1：檢查卡片裡是否有「翻譯自」 (已經翻譯過的)
            if "翻譯自" in embed_full_text:
                if "原文" in check_text:
                    parts = check_text.split("原文")
                    translated_part = parts[0].strip()
                    original_part = parts[1].strip() if len(parts) > 1 else ""
                    
                    if not translated_part:
                        print("翻譯結果為空白，觸發重新整理。")
                    elif translated_part == original_part:
                        print("翻譯結果與原文相同，觸發重新整理。")
                    elif is_japanese(translated_part):
                        print("翻譯結果仍包含日文假名，觸發重新整理。")
                    else:
                        print("偵測到有效的翻譯內容，不需翻譯。")
                        return
                else:
                    print("偵測到「翻譯自」標記且無原文對照，確認為已翻譯內容，不需翻譯。")
                    return
            
            # 優先級 2：沒有「翻譯自」標記，判斷是否包含日文假名 (排除純英文、純中文等非日語內容)
            else:
                if not is_japanese(check_text):
                    print("未偵測到日文假名，非為含漢字的日文推文，不需翻譯。")
                    return
            
            # ==============================================================
            # 走到這裡代表：翻譯失敗(空白/偷懶/沒翻好)，或是全新的日文推文 -> 絕對是需要翻譯的推文！

            # 4. 產生一個隨機數作為亂碼
            random_num = random.randint(100, 9999)
            
            # 5. 在網址最後面加上 ?隨機數
            refreshed_url = message.content.replace("/zh-TW", f"/zh-TW?{random_num}")
            
            # 7. 送出翻譯訊息
            await message.channel.send(f"**真是的～染岡同學想說的是這個吧** ❄️\n{refreshed_url}")

# 啟動機器人
client.run(BOT_TOKEN)