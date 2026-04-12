import discord
import asyncio
import random
import re

# ================= 設定區 =================
# 吹雪的token與染岡的使用者ID
BOT_TOKEN = 'MTQ5MjU0NTkxMjY3MTcwMzE2MQ.G2Q59E.BBPY-4g7eQXrlxMZIuqlvh2xoPsfxzmdxfkq3o'
TARGET_BOT_ID = 1492439449714561044

# 建立 client 物件 (必須放在 event 之前)
intents = discord.Intents.default()
intents.message_content = True  # 必須開啟才能讀取網址內容
client = discord.Client(intents=intents)
# ==========================================

def is_japanese(text):
    # 偵測是否含有平假名 (\u3040-\u309f) 或 片假名 (\u30a0-\u30ff)
    # 這是區分日文與中文最準確的方法
    return re.search(r'[\u3040-\u30ff]', text) is not None

@client.event
async def on_ready():
    print(f'已登入為 {client.user}，開始檢查染岡同學的翻譯狀況...')

@client.event
async def on_message(message):
    # 1. 只處理染岡發出的訊息
    if message.author.id == TARGET_BOT_ID:
        
        # 2. 判斷是否為 fxtwitter 連結且需要翻譯
        if "fxtwitter.com" in message.content and "/zh-TW" in message.content and "?" not in message.content:
            
            # 6. 【優化後的等待邏輯：輪詢檢查】等待原本的預覽跑完，並進行輪詢檢查
            # 最多等待 10 秒，每 2 秒檢查一次預覽卡片是否長出來了
            check_text = ""
            for i in range(5): 
                await asyncio.sleep(2) 
                # 重新從伺服器抓取訊息
                try:
                    updated_msg = await message.channel.fetch_message(message.id)
                    if updated_msg.embeds:
                        check_text = updated_msg.embeds[0].description or ""
                        # 如果卡片裡偵測到日文假名，代表這是日文推文，必須翻譯，直接跳出檢查
                        if is_japanese(check_text):
                            print("偵測到日文假名，判定為日文推文，準備翻譯。")
                            break
                except Exception as e:
                    print(f"檢查卡片時出錯: {e}")
            
            # 3. 檢查內容是否已經包含中文了
            # 如果裡面「有日文假名」，我們就視為「尚未翻譯的日文」，不進入省略邏輯
            if not is_japanese(check_text):
                # 計算訊息中有幾個漢字/中文字
                chinese_chars = re.findall(r'[\u4e00-\u9fff]', check_text)
                
                # 如果中文字數大於 10 個且「完全沒有日文假名」，才認定為推文不需翻譯
                if len(chinese_chars) > 10:
                    print(f"偵測到純中文內容 ({len(chinese_chars)} 字)，省略翻譯。")
                    return        

            # 4. 產生一個隨機數作為亂碼
            random_num = random.randint(100, 9999)
            
            # 5. 在網址最後面加上 ?隨機數
            refreshed_url = message.content.replace("/zh-TW", f"/zh-TW?{random_num}")
            
            # 7. 送出翻譯訊息
            await message.channel.send(f"**真是的～染岡同學想說的是這個吧** ❄️\n{refreshed_url}")

# 啟動機器人
client.run(BOT_TOKEN)
