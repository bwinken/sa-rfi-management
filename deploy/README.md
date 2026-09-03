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

build 會做三件對外的事，內網環境每一件都可能卡住，**三個都要設**：

| # | 誰在做 | 要設什麼 | 沒設會怎樣 |
|---|---|---|---|
| 1 | **docker daemon** 拉 base image `python:3.11-slim` | `BASE_IMAGE` 指到內網 registry，或 daemon 自己設 proxy（見 3.2） | build 第一步就 `failed to resolve` / 卡住 |
| 2 | 容器內 `pip install uv` → `uv sync` 下載套件 | `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` | 卡在下載沒有進度 |
| 3 | 同上，TLS 驗證 | **`PIP_TRUSTED_HOST`**（或 CA 放 `certs/`） | `certificate verify failed` |

### 3.1 `.env` 裡的 build args（`setup.sh` A6 會問）

| 變數 | 填什麼 | 說明 |
|---|---|---|
| `BASE_IMAGE` | `python:3.11-slim`（預設）或 `registry.corp/python:3.11-slim` | 內網 registry 有鏡像就填，daemon 就不必出外網 |
| `HTTPS_PROXY`、`HTTP_PROXY` | `http://proxy.corp:3128` | 必填 |
| `NO_PROXY` | `localhost,127.0.0.1,auth.corp.example` | 腳本會自動放 Auth Center 主機名 |
| `PIP_INDEX_URL` | 內部鏡像的 simple index，例 `https://nexus.corp/repository/pypi-proxy/simple` | 沒有鏡像就留空，走官方 PyPI 經 proxy |
| **`PIP_TRUSTED_HOST`** | 用鏡像 → `nexus.corp`；走官方 PyPI → `pypi.org,files.pythonhosted.org` | **必填**，逗號分隔可多個。內網 proxy 幾乎都會攔 TLS，沒設幾乎一定 fail。會同時給 `pip --trusted-host` 與 `uv --allow-insecure-host`，每個主機各加一次 |

走官方 PyPI 時 **兩個主機都要**：index 在 `pypi.org`，套件檔案在 `files.pythonhosted.org`；只填一個會在下載第一個 wheel 時才炸。
`setup.sh` 會依你有沒有填鏡像自動給對的預設，結尾列出實際會帶入的值。

公司 CA 放 `certs/*.crt` 是比 trusted host 更正確的做法（不略過驗證、執行階段連 Auth Center 也會用到）；
兩個一起設也沒衝突，先求 build 過再說。

這些是 **build args**（`docker-compose.yml` 的 `build.args`），`docker compose build` 自動帶入，
**不會**進到執行中的容器（執行階段要不要走 proxy 是 B5 另外決定的）。

### 3.2 base image：內網 registry 或 daemon proxy

`FROM` 那一行是 daemon 去拉 image，**不吃 proxy 類的 build args**。兩條路擇一：

**A. 指到內網 registry（推薦）**：A6 的 `BASE_IMAGE` 填 `registry.corp/python:3.11-slim`，daemon 完全不用出外網。
（`python:3.11-slim` 目前等同 `python:3.11-slim-bookworm`，內網鏡像通常只有前者。）

**B. 給 daemon 設 proxy**：`setup.sh` 結尾會檢查，daemon 沒 proxy、本機又沒有 base image 就警示。

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/http-proxy.conf <<'EOF'
[Service]
Environment="HTTPS_PROXY=http://proxy.corp:3128" "HTTP_PROXY=http://proxy.corp:3128" "NO_PROXY=localhost,127.0.0.1"
EOF
sudo systemctl daemon-reload && sudo systemctl restart docker
docker info | grep -i proxy        # 應看到 HTTPS Proxy: http://proxy.corp:3128
```

**C. 手動搬**：有網路的機器 `docker pull python:3.11-slim && docker save -o py.tar python:3.11-slim`，
搬進來 `docker load -i py.tar`，之後 build 就不需要拉。

### 3.3 錯誤怎麼判讀

| 現象 | 原因 | 處理 |
|---|---|---|
| 第一步 `FROM` 就 `failed to resolve source metadata` / 卡住 | daemon 拉不到 base image | 3.2：`BASE_IMAGE` 指內網 registry，或 daemon 設 proxy |
| 卡在 `pip install` / `uv sync` 沒有進度 | 容器內沒 proxy | A6 設 `HTTPS_PROXY` |
| `certificate verify failed` / `SSL: CERTIFICATE_VERIFY_FAILED` | proxy 攔 TLS 或鏡像自簽 | A6 設 `PIP_TRUSTED_HOST`，或 CA 放 `certs/` |
| 官方 PyPI 模式下 index 過了、第一個套件下載才炸 | trusted host 只填了 `pypi.org` | 補 `files.pythonhosted.org` |
| 鏡像回 `403` / `401` | `PIP_INDEX_URL` 路徑錯，或要帳密 | 對照 Nexus 的 simple index 路徑；帳密寫成 `https://user:pw@nexus.corp/...` |

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

資料表由 App 啟動時自動建立 / 補齊，不需要手動 migration。
升級**不會**動到 volume 裡的資料；升級前跑一下 `bash deploy/backup.sh` 純粹是保險，幾秒鐘的事。
要回滾：`.env` 的 `ASSET_VERSION` 改回上一個值、`docker compose up -d`（舊 image 還在，不用重 build）。

---

## 7. 備份與還原

先講清楚**為什麼還要備份**：資料本來就在 volume / 主機目錄上，升級、重啟、換 image 都不會掉；
備份防的是另一類事——硬碟壞掉、有人匯錯 Excel 蓋掉一批、誤刪附件、要搬到另一台機器。

### 備份：`bash deploy/backup.sh`

```bash
bash deploy/backup.sh                    # → /srv/sa-rfi-backups/
bash deploy/backup.sh /mnt/nas/sa-rfi    # 指定目的目錄
bash deploy/backup.sh --dry-run          # 只看會執行什麼
```

**服務不用停**。腳本讀 `docker-compose.yml` / `.env` 自動判斷：

| 模式 | 資料庫怎麼備 | 附件 |
|---|---|---|
| SQLite | 在 app 容器裡跑 `scripts/backup.py`，用 SQLite online backup API 取一致性快照 → `sa_rfi_<時間>.db` | 同一次打包成 `uploads_<時間>.tar.gz` |
| PostgreSQL | `docker compose exec db pg_dump -Fc` → `sa_rfi_<時間>.dump` | 同上 |

透過 `docker compose run` 掛的是 compose 定義的那份 `/data`，所以 volume 模式或主機目錄模式都不用改指令。
各保留最近 14 份，舊的自動清掉。產出檔由 root 擁有（容器內以 root 執行才寫得進主機目錄）。

cron 每日 02:00：

```cron
0 2 * * * cd /path/to/sa-rfi-management && bash deploy/backup.sh >> /var/log/sa-rfi-backup.log 2>&1
```

### 還原（會蓋掉現有資料，先停服務）

SQLite：

```bash
docker compose down
docker compose run --rm -T --no-deps --user 0 -v /srv/sa-rfi-backups:/b app sh -c '
    cp /b/sa_rfi_<時間>.db /data/sa_rfi.db &&
    rm -rf /data/uploads && tar xzf /b/uploads_<時間>.tar.gz -C /data &&
    chown -R 10001:10001 /data'
docker compose up -d
```

PostgreSQL：

```bash
docker compose stop app
docker compose exec -T db pg_restore -U sarfi -d sa_rfi --clean --if-exists --no-owner < /srv/sa-rfi-backups/sa_rfi_<時間>.dump
docker compose run --rm -T --no-deps --user 0 -v /srv/sa-rfi-backups:/b app sh -c '
    rm -rf /data/uploads && tar xzf /b/uploads_<時間>.tar.gz -C /data && chown -R 10001:10001 /data'
docker compose up -d
```

搬到另一台機器：新機器跑完 `setup.sh`、`docker compose up -d` 建好空的 volume 後，用同樣的還原指令灌進去。

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
| **build 任何階段失敗** | proxy / trusted host / daemon proxy | **第 3 節有完整判讀表** |
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
| `deploy/backup.sh` | 線上備份（自動判斷 SQLite / PostgreSQL、volume / 主機目錄） |
| `scripts/backup.py` | 備份的實作，被 `backup.sh` 在容器內呼叫；本機開發也可直接跑 |
| `scripts/migrate_sqlite_to_postgres.py` | SQLite → PostgreSQL 搬移 |
