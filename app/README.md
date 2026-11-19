# 車帳本 網頁版

網址（開啟 GitHub Pages 後）：https://howard90201.github.io/oil-prices/app/

- 和 Claude 上的車帳本是同一份程式，多了 Email 登入和雲端同步（Supabase）。
- 油價直接讀這個 repo 的 `data/oil_prices.csv`，車款讀 `vehicles/data/vehicles.json`，所以每天、每月更新後網頁版馬上就是新的。
- `supabase.js` 是 supabase-js 2.119.1（MIT 授權，見 supabase.LICENSE），放在這裡就不用依賴外部 CDN。
- 頁面裡的 Supabase「publishable key」本來就是公開用的；資料靠資料庫的權限規則保護（每個人只看得到自己的車）。
