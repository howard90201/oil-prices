"""一次性檢查：GitHub 的伺服器能不能讀到 Yahoo 汽車和政府開放資料。結果寫進 probe/ 給人看。"""
import os, urllib.request, json, ssl
os.makedirs('probe', exist_ok=True)
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36', 'Accept-Language': 'zh-TW,zh;q=0.9'}
T = {
 'yahoo_robots': 'https://autos.yahoo.com.tw/robots.txt',
 'yahoo_home': 'https://autos.yahoo.com.tw/new-cars',
 'yahoo_sitemap': 'https://autos.yahoo.com.tw/sitemap.xml',
 'yahoo_trim': 'https://autos.yahoo.com.tw/new-cars/trim/toyota-crown-sport-2027-hev/spec',
 'yahoo_make': 'https://autos.yahoo.com.tw/new-cars/make/toyota',
 'yahoo_bikes': 'https://autos.yahoo.com.tw/new-bikes',
 'gov_6032': 'https://data.gov.tw/api/v2/rest/dataset/6032',
 'gov_34934': 'https://data.gov.tw/api/v2/rest/dataset/34934',
 'moenv_swagger': 'https://data.moenv.gov.tw/swagger/',
 'moenv_nos20': 'https://data.moenv.gov.tw/api/v2/nos_p_20?format=json&limit=3',
}
out = {}
for k, u in T.items():
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=30)
        b = r.read()
        open(f'probe/{k}.txt', 'wb').write(b[:400000])
        out[k] = {'status': r.status, 'bytes': len(b), 'final_url': r.geturl()}
    except Exception as e:
        out[k] = {'error': repr(e)[:300]}
json.dump(out, open('probe/summary.json', 'w'), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
