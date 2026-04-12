# 標準作業流程

1. 在 VS Code：修改 fix_translate.py 的邏輯，或者改 configs.yml 裡的染岡設定。
2. 在 VS Code：按下 Commit 和 Sync Changes（Push），把修改推上 GitHub。
3. VM 裡：一律先輸入 cd Tweetcord（cd ~/Tweetcord）
4. VM 裡：輸入 git pull （origin main） 更新檔案，然後重開吹雪，執行 pkill -f fix_translate.py
nohup python3 fix_translate.py &
