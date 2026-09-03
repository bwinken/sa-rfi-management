# 部署手冊：docker compose（stateless container + `.env`）

本平台的容器是 **stateless** 的：image 裡沒有任何設定值、金鑰或資料，
所有環境差異都靠**環境變數**注入，所有要留存的東西都寫在**一個 volume**。
部署、升級、搬機器都只有三件東西要管：

| 東西 | 放哪裡 | 進不進 image |
|---|---|---|
| 設定（URL、secret…） | `.env` → compose 代換 → 容器環境變數 | ❌（`.dockerignore` 排除） |
| 狀態（SQLite 檔、附件、離線公鑰） | 具名 volume `sa-rfi-data` → 容器內 `/data` | ❌ |
| 程式 | image `sa-rfi-management:<tag>` | ✅ 只有這個 |

```
.env ──(docker compose 讀取，代換 ${VAR:-預設})──▶ docker-compose.yml environment:
                                                          │
                                                          ▼
                                              容器（只認環境變數，找不到 .env）
                                                          │
                                                 /data ◀──┴── volume sa-rfi-data
```

> 常見誤解：compose 的 `.env` **只用來代換 `${...}`**，不會自動變成容器的環境變數。
> 所以每一個要進容器的變數都要在 `docker-compose.yml` 的 `environment:` 列出來，
> `2_compose_setup.sh` 產生的檔案已把 `.env.example` 裡的變數全部對應好；
> 日後新增設定時，`.env.example` 與腳本的 `render()` 要一起加。

---

## 0. 前置：在 Auth Center 註冊本平台

到 Auth Center 的 Admin UI「應用程式」新增（或手動編輯 `config/apps.yaml`）：

```yaml
apps:
  - app_id: sa_rfi_management
    name: SA RFI 管理平台
    client_secret: <bcrypt hash>            # Admin UI 會自動產生並顯示明文一次
    redirect_uri: https://rfi.corp.example/auth/callback   # 逐字比對，必須與下方 APP_BASE_URL 對應
    app_url: https://rfi.corp.example
    allowed_orgs: ["<SA 部門 org_id>"]      # default_level 只在這裡非空時生效
    default_level: 1                        # 組織內預設 read
    token_expire_hours: 12
```

需要 `write`（新增 / 編輯 RFI）的 SA 另外授權：

```bash
python scripts/manage_permissions.py grant <員工帳號> sa_rfi_management --level 2   # level 3 = admin
```

記下 **明文 client_secret**，下一步要填。

---

## 1. 第一次部署

```bash
git clone <repo> && cd sa-rfi-management
cp /path/to/corp-root-ca.crt certs/   # 內網 proxy 會攔 TLS 的話（見第 3 節）
bash deploy/1_env_setup.sh            # 逐項問你、產生 .env（必填沒填會一直問）
bash deploy/2_compose_setup.sh        # 問這台主機的埠 / 資料位置 / DB / proxy，產生 docker-compose.yml
docker compose build                  # 內網一定要走 proxy，第一支腳本會問
docker compose up -d
docker compose logs -f app            # 看到「SA RFI 平台啟動」即可
```

兩支腳本都可以重複執行，上次的值會當預設，直接 Enter 保留。
不想用互動腳本：`cp deploy/.env.example .env`、`cp deploy/docker-compose.example.yml docker-compose.yml`
後手動改（`.env.example` 是正式環境版範本；根目錄那份是開發用）。

### `docker-compose.yml` 是產生的，不在 git 裡

repo **不附** `docker-compose.yml`——每台主機不一樣的東西（對外埠、資料放 volume 還是
主機目錄、要不要 PostgreSQL、容器連 Auth Center 是否走 proxy、本機 build 還是 registry 的 image）
由 `2_compose_setup.sh` 問完後產生一份**完整、自給自足**的檔案，已在 `.gitignore`，
`git pull` 升級不會衝突。腳本會：

- 偵測到既有 `docker-compose.yml`：本腳本產生的 → 上次選擇當預設；不是的 → 先備份成 `.bak.<時間>` 再寫
- 偵測到同目錄有 `compose.yaml` / `compose.yml`（docker compose 會優先讀它們）或 override 檔 → 警示
- 產生後跑 `docker compose config` 驗證，`.env` 缺必填值會直接指出

手動編輯過產生的檔案後，重跑腳本會以你的選擇重新產生（手改的部分要自己加回去），
所以偏好「改完就重跑」而不是手改。

驗證：

```bash
curl -s http://localhost:8003/readyz
# {"status":"ok","checks":{"database":"ok","data_dir":"ok"}}
```

瀏覽器開 `APP_BASE_URL` → 應被導到 Auth Center 登入頁 → 登入後回到 RFI 列表。
沒有被導去 Auth Center、直接看到列表，代表 `DEV_AUTH_BYPASS` 沒關，立刻檢查 `.env`。

---

## 2. `.env` 要填什麼

### 正式環境最少要填的 5 個

```env
APP_BASE_URL=https://rfi.corp.example          # 本平台對外網址（OAuth callback 由此組出）
AUTH_CENTER_BASE_URL=https://auth.corp.example  # 尾端不要加 /
CLIENT_SECRET=<Auth Center 給的明文 secret>
COOKIE_SECURE=true                              # 走 HTTPS 一律 true
ASSET_VERSION=<image tag 或 commit sha>         # 讓靜態資源快取跟著版本走
```

### 有合理預設、通常不用動

| 變數 | 預設 | 什麼時候要改 |
|---|---|---|
| `APP_ID` | `sa_rfi_management` | Auth Center 用了別的 app_id |
| `REDIRECT_URI` | `{APP_BASE_URL}/auth/callback` | 幾乎不用；改了 Auth Center 那邊要同步 |
| `JWKS_URL` | `{AUTH_CENTER_BASE_URL}/.well-known/jwks.json` | 容器連不到 Auth Center → 填 `off`，改用離線公鑰（第 5 節） |
| `PUBLIC_KEY_PATH` | `/data/keys/public.pem` | 幾乎不用 |
| `ROOT_PATH` | 由 `APP_BASE_URL` 的路徑推導 | 幾乎不用 |
| `DATABASE_URL` | 空 = SQLite 在 `/data/sa_rfi.db` | 換 PostgreSQL（第 8 節） |
| `MAX_UPLOAD_MB` | `25` | 附件太大被擋 |
| `DECK_TITLE` | `Customer RFI Collection` | 週報標題要改 |
| `DESIGNED_BY` / `EXECUTED_BY` | 空 | 頁尾要署名 |
| `LOG_LEVEL` | `INFO` | 排查問題時改 `DEBUG` |

### 絕對不要在正式環境設的

```env
DEV_AUTH_BYPASS=true    # 略過認證，任何人都是 admin
```

預設是 `false`，不寫就是安全的。只在 Auth Center 還沒接好、想先看畫面時暫時打開。

---

## 3. 內網 build：proxy / 私有 PyPI / 公司 CA（內網必做）

build 要下載套件，內網環境**一定要設 proxy**，否則 `docker compose build` 會卡在 `uv sync`。
這些是 **build args**，寫在 `.env` 裡 `docker compose build` 會自動帶入，不需要改 Dockerfile
（`1_env_setup.sh` 第 6 步會問，`HTTPS_PROXY` 為必填）：

```env
HTTPS_PROXY=http://proxy.corp:3128
HTTP_PROXY=http://proxy.corp:3128
NO_PROXY=localhost,127.0.0.1,.corp
PIP_INDEX_URL=https://nexus.corp/repository/pypi-proxy/simple
PIP_TRUSTED_HOST=nexus.corp
```

公司自簽 / 攔截式 proxy 的 CA 憑證（PEM 格式、副檔名 `.crt`）放到 `certs/`，
build 時會裝進 image 的系統信任清單；**執行階段** App 連 Auth Center 也會信任它。

```bash
cp /path/to/corp-root-ca.crt certs/
docker compose build
```

---

## 4. 執行階段的 proxy（只有容器連 Auth Center 要走 proxy 時）

上面的 `HTTPS_PROXY` **只用在 build**，刻意不帶進執行階段——
否則容器對內網 Auth Center 的請求也會被導去 proxy。
若你的網路拓樸真的需要，重跑 `bash deploy/2_compose_setup.sh`，第 5 步選 `yes`，
然後 `docker compose up -d`。它會把 `.env` 裡的 `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` 帶進容器。
**務必**把 Auth Center 的主機名放進 `NO_PROXY`（除非它就是要走 proxy 才到得了）。

---

## 5. 容器連不到 Auth Center 時：離線驗章公鑰

App 驗 JWT 需要 Auth Center 的公鑰。預設透過 JWKS 端點自動取得，
若容器出不去（或你不想有這條依賴），把公鑰放進 volume：

```bash
# 從 Auth Center 主機拿 keys/public.pem，放到 volume 的 keys/ 底下
docker run --rm -v sa-rfi-management_sa-rfi-data:/data -v "$PWD":/src alpine \
    sh -c 'mkdir -p /data/keys && cp /src/public.pem /data/keys/public.pem && chown -R 10001:10001 /data/keys'
```

然後 `.env` 設 `JWKS_URL=off`，`docker compose up -d` 重啟即可。
（不設 `off` 也能用：JWKS 取不到會自動退回本地公鑰，只是每次登入會多一次失敗的連線嘗試。）

> volume 名稱：`docker volume ls | grep sa-rfi`。compose 會加上專案名前綴，
> 預設是目錄名 `sa-rfi-management_sa-rfi-data`。

---

## 6. 升級

程式在 image、資料在 volume，升級就是換 image：

```bash
git pull
$EDITOR .env               # 更新 ASSET_VERSION
docker compose build
docker compose up -d       # 只重建 app 容器，volume 原封不動
docker compose logs -f app
```

資料表結構由 App 啟動時自動建立 / 補齊（`create_all`），不需要手動跑 migration。
升級前建議先備份（第 7 節），花不到一秒。

---

## 7. 備份與還原

備份腳本在 image 裡，讓它掛上同一個 volume 就能跑，**服務不用停**
（SQLite 走 online backup API 取一致性快照）：

```bash
mkdir -p /srv/sa-rfi-backups
docker run --rm \
    -v sa-rfi-management_sa-rfi-data:/data \
    -v /srv/sa-rfi-backups:/backups \
    sa-rfi-management:latest python scripts/backup.py /backups
# 產出：/srv/sa-rfi-backups/sa_rfi_<時間>.db 與 uploads_<時間>.tar.gz
```

排程（主機 cron，每日 02:00）：

```cron
0 2 * * * docker run --rm -v sa-rfi-management_sa-rfi-data:/data -v /srv/sa-rfi-backups:/backups sa-rfi-management:latest python scripts/backup.py /backups
```

還原（會蓋掉現有資料，先停服務）：

```bash
docker compose down
docker run --rm -v sa-rfi-management_sa-rfi-data:/data -v /srv/sa-rfi-backups:/b alpine sh -c '
    cp /b/sa_rfi_<時間>.db /data/sa_rfi.db &&
    rm -rf /data/uploads && tar xzf /b/uploads_<時間>.tar.gz -C /data &&
    chown -R 10001:10001 /data'
docker compose up -d
```

---

## 8. 換成 PostgreSQL（需要 HA 或多副本時才需要）

先用 SQLite 是預期路徑；以十幾個 SA、每週幾十筆的量，單副本綽綽有餘。
真的要換時只動 `DATABASE_URL` 一行，附件不用搬（一直都在 volume 的 `uploads/`）：

```bash
docker compose down
# 取出 SQLite 檔
docker run --rm -v sa-rfi-management_sa-rfi-data:/d -v "$PWD":/out alpine cp /d/sa_rfi.db /out/sa_rfi.db
# 重跑兩支腳本：1_env_setup 第 5 步設 POSTGRES_PASSWORD、2_compose_setup 第 4 步選 PostgreSQL
bash deploy/1_env_setup.sh && bash deploy/2_compose_setup.sh
docker compose up -d db          # 先只起資料庫
# 搬資料（保留 id，修改紀錄與附件關聯不會斷）
uv run python scripts/migrate_sqlite_to_postgres.py --source ./sa_rfi.db \
    --target postgresql://sarfi:<密碼>@localhost:5432/sa_rfi
docker compose up -d             # 之後就是一般的 up
```

多副本另外要注意：附件目錄必須是共用儲存（NFS 等），`ASSET_VERSION` 必須固定。

---

## 9. 反向代理

容器已用 `--proxy-headers --forwarded-allow-ips '*'` 啟動，會信任前面代理送來的
`X-Forwarded-Proto / Host`。代理端要做的：

- 把 `X-Forwarded-Proto: https` 傳進來（否則 OAuth callback 會組成 http）
- 子路徑部署（如 `https://portal.corp/sa-rfi/`）：`APP_BASE_URL` 填完整含路徑，
  `ROOT_PATH` 會自動推導；代理**不要**剝掉路徑前綴
- 附件上傳上限：代理的 body size 要 ≥ `MAX_UPLOAD_MB`（nginx：`client_max_body_size 30m`）

Nginx 最小範例：

```nginx
location / {
    proxy_pass         http://127.0.0.1:8003;
    proxy_set_header   Host              $host;
    proxy_set_header   X-Forwarded-Proto $scheme;
    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
    client_max_body_size 30m;
}
```

---

## 10. 故障排除

| 現象 | 原因 | 處理 |
|---|---|---|
| 容器啟動就退出，log 有「DATA_DIR 不可寫」 | volume 權限不對 | 容器以 UID 10001 執行：`docker run --rm -v <volume>:/data alpine chown -R 10001:10001 /data` |
| `/readyz` 回 503 | 看回應裡的 `checks` 哪一項 `fail` | `database`：DATABASE_URL 錯或 PG 沒起來；`data_dir`：同上一列 |
| 點登入被 Auth Center 顯示「Redirect URI 不匹配」 | `APP_BASE_URL` 與 Auth Center 註冊的 `redirect_uri` 不一致 | 兩邊逐字對齊（含 http/https、尾端路徑） |
| 登入後又被踢回登入頁，log 有 `InvalidIssuerError` | `AUTH_CENTER_BASE_URL` 與 Auth Center 自己設定的 base URL 不同 | 兩邊填同一個值（App 會自動去掉尾端 `/`） |
| log 有 `InvalidAudienceError` | `APP_ID` 與 Auth Center 註冊的 `app_id` 不同 | 對齊 |
| log 有「無可用驗章公鑰」 | 容器連不到 JWKS 且沒有離線公鑰 | 第 5 節 |
| 登入成功但每個操作都 403 | Auth Center 那邊只給 level 1（read） | `manage_permissions.py grant ... --level 2` |
| 「Token 交換失敗：invalid_client」 | `CLIENT_SECRET` 錯 | 到 Auth Center 重新產生並更新 `.env` |
| 「Token 交換失敗：staff_not_found」 | 員工不在 Auth Center 的 MSSQL 主檔 | Auth Center 端處理 |
| 沒被導去 Auth Center、直接進列表 | `DEV_AUTH_BYPASS=true` | 改 `false`，`docker compose up -d` |
| icon 全變成英文字（`expand_more`） | 使用者瀏覽器連不到 `fonts.googleapis.com` | 前端仍靠 Google Fonts CDN；純內網需把字型改成本地檔（待辦） |
| Cookie 一直掉、登入後回首頁又要登入 | `COOKIE_SECURE=true` 但實際走 http | 走 HTTPS，或測試期先設 `false` |

看 log：`docker compose logs -f app`。想看更細：`.env` 設 `LOG_LEVEL=DEBUG` 後 `up -d`。

---

## 檔案索引

| 檔案 | 用途 |
|---|---|
| `docker-compose.yml`（根目錄，**不進 git**） | 由 `2_compose_setup.sh` 產生的完整 compose 設定 |
| `deploy/1_env_setup.sh` | 互動式逐項產生 `.env` |
| `deploy/2_compose_setup.sh` | 互動式產生這台主機的 `docker-compose.yml` |
| `deploy/docker-compose.example.yml` | 腳本以預設選項產生的範例，手動部署可複製到根目錄 |
| `deploy/_lib.sh` | 上面兩支共用的互動函式 |
| `deploy/.env.example` | 正式環境版 `.env` 範本 |
| `deploy/kubernetes.yaml` | K8s 範例（PVC、Secret、probes、fsGroup） |
| `.env.example`（根目錄） | 所有變數與說明 |
| `certs/` | 公司 CA（build 時裝進 image） |
| `scripts/backup.py` | 線上備份 |
| `scripts/migrate_sqlite_to_postgres.py` | SQLite → PostgreSQL 搬移 |
