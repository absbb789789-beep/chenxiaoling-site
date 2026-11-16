# 陳小綾房屋網站｜每週免費物件同步

## 免費執行方式
- GitHub Pages：免費發布靜態網站。
- GitHub Actions：每週一 09:00（Asia/Taipei，UTC 01:00）執行。
- Playwright + Chromium：在 Actions runner 中以瀏覽器渲染動態頁面。
- Python：擷取、整理、比對及產生報告。
- Git：保存 JSON、備份、報告及執行紀錄。

## 資料來源
- TBD9：https://store.twhg.com.tw/TBD9
- TB73：https://store.twhg.com.tw/TB73
- TB75：https://store.twhg.com.tw/TB75
- 詳情頁：https://www.twhg.com.tw/buy/<物件編號>

## 輸出
- data/listings.json：目前有效物件。
- backups/listings_YYYY-MM-DD.json：當日執行前的資料備份。
- reports/weekly_report.md：本週異動報告。
- logs/execution_history.jsonl：逐次執行紀錄。

## 手動測試
GitHub → Actions → Weekly Taiwan House Listings Sync → Run workflow。

## 注意
1. GitHub Actions 排程可能因平台負載延後，不能視為精準到分鐘的 SLA。
2. 免費方案仍受 GitHub Actions 使用限制及來源網站存取政策約束。
3. 若來源網站改版、封鎖自動化流量或調整物件頁格式，請檢查 Actions log 並更新解析器。
4. 不會繞過登入、驗證碼、存取限制或其他防護。
5. 不擷取經紀人姓名、電話等個人資料。
6. 只有成功讀取的店面才會用於判斷下架；失敗店面沿用前版資料。
