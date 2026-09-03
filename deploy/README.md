# 部署手冊：docker compose（stateless container + `.env`）

一支腳本問完所有設定，產生 `.env` 與 `docker-compose.yml`，然後 `build` + `up`：

```bash
git clone <repo> && cd sa-rfi-management
cp /path/to/corp-root-ca.crt certs/   # proxy 會攔 TLS、或 PyPI 鏡像用公司 CA 的話（見第 3 節）
bash deploy/setup.sh                  # 逐項問你（見第 2 節）
docker compose build                  # ⚠ 內網一定要 proxy + trusted host，見第 3 節
docker compose up -d
docker compose logs -f app            # 看到「SA RFI 平台啟動」即可
curl -s http://127.0.0.1:8003/readyz  # {"status":"ok",...}
```

## 目錄

0. [前置：在 Auth Center 註冊](#0-前置在-auth-center-註冊本平台)
1. [這個平台怎麼部署：stateless + `.env`](#1-stateless-container--env)
2. [`deploy/setup.sh` 會問什麼](#2-deploysetupsh-會問什麼)
3. [**`docker compose build` 注意：proxy 與 trusted host**](#3-docker-compose-build-注意proxy-與-trusted-host)
4. [執行階段的 proxy](#4-執行階段的-proxy)
5. [離線驗章公鑰](#5-容器連不到-auth-center-時離線驗章公鑰)
6. [升級](#6-升級)
7. [備份與還原](#7-備份與還原)
8. [換成 PostgreSQL](#8-換成-postgresql)
9. [反向代理](#9-反向代理)
10. [故障排除](#10-故障排除)

---

## 0. 前置：在 Auth Center 註冊本平台

到 Auth Center 的 Admin UI「應用程式」新增（或手動編輯 `config/apps.yaml`）：

```yaml
apps:
  - app_id: sa_rfi_management
    name: SA RFI 管理平台
    client_secret: <bcrypt hash>            # Admin UI 會自動產生並顯示明文一次
    redirect_uri: https://rfi.corp.example/auth/callback   # 逐字比對，必須與 APP_BASE_URL 對應
    app_url: https://rfi.corp.example
    allowed_orgs: ["<SA 部門 org_id>"]      # default_level 只在這裡非空時生效
    default_level: 1                        # 組織內預設 read
    token_expire_hours: 12
```

需要 `write`（新增 / 編輯 RFI）的 SA 另外授權：

```bash
python scripts/manage_permissions.py grant <員工帳號> sa_rfi_management --level 2   # level 3 = admin
```

記下 **明文 client_secret**，跑 `setup.sh` 時要填。

---

## 1. Stateless container + `.env`

容器裡沒有任何設定值、金鑰或資料。部署、升級、搬機器只有三件東西要管：

| 東西 | 放哪裡 | 進不進 image |
|---|---|---|
| 設定（URL、secret、proxy…） | `.env` → compose 代換 → 容器環境變數 | ❌（`.dockerignore` 排除） |
| 狀態（SQLite 檔、附件、離線公鑰） | volume 或主機目錄 → 容器內 `/data` | ❌ |
| 程式 | image `sa-rfi-management:<tag>` | ✅ 只有這個 |

```
.env ──(docker compose 代換 ${VAR:-預設})──▶ docker-compose.yml ──▶ 容器（只認環境變數，找不到 .env）
                                                                          │
                                                                    /data ◀┴── volume sa-rfi-data
```

> compose 的 `.env` **只用來代換 `${...}`**，不會自動變成容器的環境變數，所以每個要進容器的變數
> 都得列在 `docker-compose.yml` 的 `environment:`。`setup.sh` 產生的檔案已全部對應好；
> 日後新增設定時，`.env.example` 與 `setup.sh` 的 `render()` 要一起加。

**`.env` 與 `docker-compose.yml` 都不在 git 裡**（`.gitignore`），由 `setup.sh` 產生；
`git pull` 升級不會衝突。想手動寫的話，範本在 `deploy/.env.example` 與 `deploy/docker-compose.example.yml`。

---

## 2. `deploy/setup.sh` 會問什麼

```bash
bash deploy/setup.sh                 # 全部：A（.env）→ B（docker-compose.yml）
bash deploy/setup.sh --env-only      # 只重做 .env：改網址、換 secret、改 proxy
bash deploy/setup.sh --compose-only  # 只重做 docker-compose.yml：改埠、資料位置、DB
```

直接 Enter 採用方括號裡的預設；必填沒填會一直問。**重跑時上次的值就是預設**，一路 Enter 即保留
（secret 也是，輸入不回顯）。Ctrl-C 隨時中止，不會寫任何東西。

### A. `.env`

| 步驟 | 問什麼 | 備註 |
|---|---|---|
| A1 必填 | `APP_BASE_URL`、`AUTH_CENTER_BASE_URL`、`APP_ID`、`CLIENT_SECRET`、`COOKIE_SECURE`、`ASSET_VERSION` | URL 會去尾端 `/`；`COOKIE_SECURE` 依 https 自動預設；`ASSET_VERSION` 預設 commit sha |
| A2 認證進階 | `REDIRECT_URI`、`JWKS_URL`、`PUBLIC_KEY_PATH` | 預設跳過；離線環境才需要（第 5 節） |
| A3 應用設定 | 上傳上限、投影片標題、頁尾署名、log 等級 | 預設跳過 |
| A4 開發模式 | `DEV_AUTH_BYPASS` | 選 `true` 會再確認一次；正式環境務必 `false` |
| A5 資料庫 | 要不要 PostgreSQL → 帳號、DB 名、密碼 | 預設 SQLite |
| **A6 build** | **proxy、`NO_PROXY`、PyPI 鏡像、trusted host** | **內網必填，見第 3 節** |

產出 `.env`（權限 600）。

### B. `docker-compose.yml`

| 步驟 | 問什麼 | 選項 |
|---|---|---|
| B1 對外埠 | 埠號、開給誰連 | `0.0.0.0` 直連 / `127.0.0.1` 只給本機反向代理 |
| B2 image 來源 | 這台 build / 從 registry 拉 | registry 需給完整 image 名 |
| B3 資料放哪 | 容器 `/data` 對應到 | 具名 volume / 主機目錄（會幫你 `mkdir` + `chown 10001`） |
| B4 資料庫 | SQLite / PostgreSQL | 選 PostgreSQL 會在同一份 compose 多起 `db` 服務；預設跟著 A5 |
| B5 執行階段 proxy | 容器連 Auth Center 要不要走 proxy | 預設不用（第 4 節） |

產生一份**完整、自給自足**的 `docker-compose.yml`（含 healthcheck、日誌輪替 10MB×5）。
偵測到既有檔案才警示：本腳本產生的 → 沿用上次選擇；不是的 → 先備份成 `.bak.<時間>`。
同目錄有 `compose.yaml`（docker compose 會優先讀它）或 override 檔也會警示。

### 結尾

- 列出 `.env`（secret 遮罩）與 compose 的選擇
- **列出 `docker compose build` 會帶入的 proxy / PyPI / trusted host / CA**，一眼確認
- 跑 `docker compose config` 驗證；`.env` 缺必填會直接指出是哪個
- 印出接下來的指令與要去 Auth Center 核對的 `redirect_uri`

---

## 3. `docker compose build` 注意：proxy 與 trusted host

build 要下載 Python 套件（`pip install uv` → `uv sync`）。內網環境**兩件事都要設**，否則卡在下載：

| 要設什麼 | `.env` 變數 | 什麼情況需要 |
|---|---|---|
| **proxy** | `HTTPS_PROXY`、`HTTP_PROXY`、`NO_PROXY` | 出不去的內網一定要 |
| **PyPI 鏡像** | `PIP_INDEX_URL` | 有 Nexus / Artifactory 就填；沒有就留空走官方 PyPI 經 proxy |
| **trusted host** | `PIP_TRUSTED_HOST` | 鏡像用**自簽憑證**時填鏡像主機名（只填主機名、一個）。會同時給 `pip --trusted-host` 與 `uv --allow-insecure-host` |
| 公司 CA | `certs/*.crt` | proxy 會**攔截 TLS**、或鏡像用公司內部 CA 簽的憑證。放進去後 build 與執行階段都信任，通常就不需要 trusted host |

`setup.sh` A6 會逐項問，`PIP_TRUSTED_HOST` 預設帶鏡像主機名；結尾會列出實際會帶入的值。
手寫的話 `.env` 長這樣：

```env
HTTPS_PROXY=http://proxy.corp:3128
HTTP_PROXY=http://proxy.corp:3128
NO_PROXY=localhost,127.0.0.1,auth.corp.example
PIP_INDEX_URL=https://nexus.corp/repository/pypi-proxy/simple
PIP_TRUSTED_HOST=nexus.corp
```

這些是 **build args**（`docker-compose.yml` 的 `build.args`），`docker compose build` 自動帶入，
**不會**進到執行中的容器（執行階段要不要走 proxy是 B5 另外決定的）。

判斷方式：

- build 卡在 `pip install` / `uv sync` 沒有進度 → 沒有 proxy
- `certificate verify failed` / `SSL: CERTIFICATE_VERIFY_FAILED` → 鏡像或 proxy 的憑證不被信任：放 CA 進 `certs/`（正解），或設 `PIP_TRUSTED_HOST`（略過驗證）
- `403` / `401` 從鏡像回來 → `PIP_INDEX_URL` 路徑不對，或鏡像要帳密（`https://user:pw@nexus.corp/...`）

> 注意：主機 shell 若已 `export HTTPS_PROXY`，compose 代換時 **shell 環境變數優先於 `.env`**。
> 兩者通常相同所以沒事；build 行為不如預期時先 `env | grep -i proxy`。

---

## 4. 執行階段的 proxy

容器執行時只會對外連一個地方：**Auth Center**（換 token、抓 JWKS）。第 3 節的 proxy 刻意**只用在 build**，
否則容器對內網 Auth Center 的請求也會被導去 proxy。

只有「容器連 Auth Center 也必須經過 proxy」時，才在 `setup.sh` **B5 選 yes**——它會把 `.env` 的
`HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` 帶進容器。此時 **`NO_PROXY` 要不要含 Auth Center 主機**看拓樸：
Auth Center 直連得到就放進去，只能經 proxy 才到得了就不要放。

---

## 5. 容器連不到 Auth Center 時：離線驗章公鑰

App 驗 JWT 需要 Auth Center 的公鑰，預設透過 JWKS 端點自動取得。若容器出不去，把公鑰放進 `/data/keys/`：

```bash
# volume 模式（名稱：docker volume ls | grep sa-rfi；compose 會加目錄名前綴）
docker run --rm -v sa-rfi-management_sa-rfi-data:/data -v "$PWD":/src alpine \
    sh -c 'mkdir -p /data/keys && cp /src/public.pem /data/keys/public.pem && chown -R 10001:10001 /data/keys'
# 主機目錄模式
sudo mkdir -p /srv/sa-rfi/data/keys && sudo cp public.pem /srv/sa-rfi/data/keys/ && sudo chown -R 10001:10001 /srv/sa-rfi/data/keys
```

然後 `setup.sh --env-only` 在 A2 把 `JWKS_URL` 設成 `off`，`docker compose up -d`。
（不設 `off` 也能用：JWKS 取不到會自動退回本地公鑰，只是每次登入多一次失敗的連線嘗試。）

---

## 6. 升級

程式在 image、資料在 volume，升級就是換 image：

```bash
git pull
bash deploy/setup.sh --env-only    # 一路 Enter，只改 ASSET_VERSION（預設會帶新的 commit sha）
docker compose build               # 或 docker compose pull（registry 模式）
docker compose up -d               # 只重建 app 容器，volume 原封不動
docker compose logs -f app
```

資料表由 App 啟動時自動建立 / 補齊，不需要手動 migration。升級前先備份（第 7 節），花不到一秒。

---

## 7. 備份與還原

備份腳本在 image 裡，掛上同一個 volume 就能跑，**服務不用停**（SQLite 走 online backup API）：

```bash
mkdir -p /srv/sa-rfi-backups
docker run --rm \
    -v sa-rfi-management_sa-rfi-data:/data \
    -v /srv/sa-rfi-backups:/backups \
    sa-rfi-management:latest python scripts/backup.py /backups
# 產出：sa_rfi_<時間>.db 與 uploads_<時間>.tar.gz
```

主機目錄模式把 `-v sa-rfi-management_sa-rfi-data:/data` 換成 `-v /srv/sa-rfi/data:/data`。
cron 每日 02:00：

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

## 8. 換成 PostgreSQL

先用 SQLite 是預期路徑；十幾個 SA、每週幾十筆的量，單副本綽綽有餘。要 HA 或多副本再換。
附件不用搬（一直都在 `/data/uploads`），只搬資料庫：

```bash
docker compose down
docker run --rm -v sa-rfi-management_sa-rfi-data:/d -v "$PWD":/out alpine cp /d/sa_rfi.db /out/sa_rfi.db
bash deploy/setup.sh               # A5 選 PostgreSQL 並設密碼、B4 選 PostgreSQL
docker compose up -d db            # 先只起資料庫
uv run python scripts/migrate_sqlite_to_postgres.py --source ./sa_rfi.db \
    --target postgresql://sarfi:<密碼>@localhost:5432/sa_rfi   # 保留 id，修改紀錄與附件關聯不會斷
docker compose up -d
```

多副本另外要注意：附件目錄必須是共用儲存（NFS 等），`ASSET_VERSION` 必須固定。

---

## 9. 反向代理

容器以 `--proxy-headers --forwarded-allow-ips '*'` 啟動，信任前面代理送來的 `X-Forwarded-Proto / Host`。
`setup.sh` B1 選 `local` 時埠只綁 `127.0.0.1`，代理指到 `http://127.0.0.1:<埠>`：

```nginx
location / {
    proxy_pass         http://127.0.0.1:8003;
    proxy_set_header   Host              $host;
    proxy_set_header   X-Forwarded-Proto $scheme;    # 沒有這行 OAuth callback 會組成 http
    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
    client_max_body_size 30m;                        # ≥ MAX_UPLOAD_MB
}
```

子路徑部署（如 `https://portal.corp/sa-rfi/`）：`APP_BASE_URL` 填完整含路徑，`ROOT_PATH` 自動推導；
代理**不要**剝掉路徑前綴。

---

## 10. 故障排除

| 現象 | 原因 | 處理 |
|---|---|---|
| **build 卡在 `pip install` / `uv sync`** | 沒有 proxy | `setup.sh --env-only` A6 設 `HTTPS_PROXY` |
| **build 出現 `certificate verify failed`** | 鏡像 / proxy 憑證不被信任 | CA 放 `certs/`，或 A6 設 `PIP_TRUSTED_HOST` |
| build 從鏡像拿到 403 / 401 | `PIP_INDEX_URL` 路徑錯或要帳密 | 對照 Nexus 的 simple index 路徑 |
| 容器啟動就退出，log「DATA_DIR 不可寫」 | volume / 目錄權限 | `chown -R 10001:10001 <目錄>`（B3 選主機目錄時腳本會幫做） |
| `/readyz` 回 503 | 看 `checks` 哪一項 `fail` | `database`：DB 連不上；`data_dir`：同上一列 |
| `docker compose config`「required variable X is missing」 | `.env` 缺必填 | `setup.sh --env-only` |
| Auth Center 顯示「Redirect URI 不匹配」 | `APP_BASE_URL` 與註冊的 `redirect_uri` 不一致 | 逐字對齊（含 https、尾端路徑） |
| 登入後被踢回，log `InvalidIssuerError` | `AUTH_CENTER_BASE_URL` 與 Auth Center 自己的 base URL 不同 | 填同一個值 |
| log `InvalidAudienceError` | `APP_ID` 與註冊的 `app_id` 不同 | 對齊 |
| log「無可用驗章公鑰」 | 容器連不到 JWKS 且沒離線公鑰 | 第 5 節 |
| 登入成功但操作都 403 | Auth Center 只給 level 1 | `manage_permissions.py grant ... --level 2` |
| 「Token 交換失敗：invalid_client」 | `CLIENT_SECRET` 錯 | Auth Center 重新產生、`setup.sh --env-only` |
| 「Token 交換失敗：staff_not_found」 | 員工不在 Auth Center 的 MSSQL 主檔 | Auth Center 端處理 |
| 沒被導去 Auth Center、直接進列表 | `DEV_AUTH_BYPASS=true` | A4 改 `false` |
| icon 全變英文字（`expand_more`） | 瀏覽器連不到 `fonts.googleapis.com` | 前端仍靠 Google Fonts CDN；純內網需改成本地字型（待辦） |
| Cookie 一直掉 | `COOKIE_SECURE=true` 但實際走 http | 走 HTTPS，或測試期先 `false` |

看 log：`docker compose logs -f app`；要更細：A3 把 `LOG_LEVEL` 改 `DEBUG` 後 `up -d`。

---

## 檔案索引

| 檔案 | 用途 |
|---|---|
| `deploy/setup.sh` | **互動式產生 `.env` 與 `docker-compose.yml`** |
| `deploy/README.md` | 本手冊 |
| `deploy/.env.example` | 正式環境版 `.env` 範本（根目錄那份是開發用） |
| `deploy/docker-compose.example.yml` | 腳本以預設選項產生的 compose 範例 |
| `deploy/kubernetes.yaml` | K8s 範例（PVC、Secret、probes、fsGroup） |
| `.env`、`docker-compose.yml`（根目錄，**不進 git**） | 腳本產生的實際設定 |
| `certs/` | 公司 CA（build 與執行階段都會信任） |
| `scripts/backup.py` | 線上備份 |
| `scripts/migrate_sqlite_to_postgres.py` | SQLite → PostgreSQL 搬移 |
