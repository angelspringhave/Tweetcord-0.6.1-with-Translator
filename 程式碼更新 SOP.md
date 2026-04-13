# 標準作業流程

1. 在 VS Code：直接修改程式碼。
   
2. 在 VS Code：按下 Commit 和 Sync Changes（Push），把修改推上 GitHub。
   
3. VM 裡：一律先輸入 `cd Tweetcord` 或 `cd ~/Tweetcord`。
   
4. VM 裡：輸入 `git pull` 或 `git pull origin main` 更新檔案，然後重開吹雪，執行 `pkill -f translator_with_monitor.py
nohup python3 -u translator_with_monitor.py &`

5. VM 裡：若想看 log，執行 `tail -f nohup.out`。
   
6. 若想在 VM 裡用前景跑：先用 `pkill -f translator_with_monitor.py` 把背景執行停止，再執行 `python3 translator_with_monitor.py`。但須注意一旦關掉 VM 的 SSH 連線視窗， translator_with_monitor.py 也會直接跟著停止。
當確定測試都沒問題後，記得要用 Ctrl + C 停止，並改回 `nohup python3 -u translator_with_monitor.py &` 使其回背景執行。