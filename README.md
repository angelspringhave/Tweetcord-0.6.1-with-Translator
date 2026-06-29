# Tweetcord 0.6.1 with Translator

基於 [Yuuzi261/Tweetcord](https://github.com/Yuuzi261/Tweetcord) v0.6.1，加上自動翻譯修正 bot（`translator_with_monitor.py`）。

Tweetcord 本身已有 fxtwitter 自動翻譯功能，但翻譯結果不穩定——有時原文不動、有時翻成一半日文混中文、有時根本沒翻。此 bot 會即時監控，偵測到翻譯失敗即補發一則強制重整的連結。

---

## 架構

兩支 bot 同時運作：

- **Tweetcord**（染岡）：原版功能，負責抓推文並發 fxtwitter embed
- **Translator bot**（吹雪）：監控染岡的輸出，翻譯失敗時自動補發修正連結，同時監控 Tweetcord Docker 容器日誌，偵測到 auth_token 失效則發 Discord 警報

---

## 翻譯修正邏輯

偵測到以下任一情況時觸發重整：

- 翻譯結果與原文完全相同（無效翻譯）
- 翻譯結果仍包含日文假名
- 翻譯結果完全不含中文
- fxtwitter 根本沒有嘗試翻譯，且內文含有日文或沒有中文

純符號、Emoji、互動統計數字、純圖片推文（無文字內文）一律跳過，不觸發重整。

---

## 設定

### `.env`

```
TRANSLATOR_TOKEN=   # 吹雪的 bot token
ALERT_MENTION_USER_ID=  # auth_token 失效時要 @ 的使用者 ID（可選）
ALERT_CHANNEL_ID=   # 警報要發到哪個頻道 ID（可選）
```

### `configs.yml`

Tweetcord 的設定，`auto_translation` 區塊需開啟：

```yaml
auto_translation:
  enabled: true
  default_language: 'zh-TW'
```

### `translator_with_monitor.py` 設定區

```python
TARGET_BOT_ID = 1492439449714561044  # 染岡的 bot user ID
DOCKER_CONTAINER_NAME = "tweetcord"  # docker ps 查到的容器名稱
```

---

## 啟動

```bash
# 先啟動 Tweetcord（參考原版 README）
docker compose up -d

# 再啟動 translator bot
python translator_with_monitor.py
```

---

## 注意

`.env` 包含 bot token，不要 commit（`.gitignore` 已設定）。

auth_token 監控需要 translator bot 所在的環境有權限執行 `docker logs`。
