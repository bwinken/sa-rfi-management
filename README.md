# SA RFI 管理平台

供 SA / 業務登錄、追蹤客戶 RFI（Request For Information），並一鍵產出週報投影片的平台。
架構與操作邏輯沿用 [ic-spec-platform](https://github.com/bwinken/ic-spec-platform)，
資料模型與畫面則換成 SA 實際在用的 RFI 欄位。

- **後端**：FastAPI + SQLite（async SQLAlchemy）
- **前端**：FastAPI 直接以 Jinja2 渲染 HTML（無 SPA）
- **認證**：[Auth Center](https://github.com/bwinken/authcenter) OAuth2（RS256 JWT）

## 為什麼要從單檔 HTML 換成這個平台

原本的 HTML 雛形已經把「SA 想要什麼」定義得很清楚（多條件篩選、排序、Dashboard、
匯出投影片），但資料只活在瀏覽器分頁裡：關掉就沒了、兩個人各有一份、也沒有人知道
某一欄是誰在什麼時候改的。這個平台保留雛形的全部功能，並補上 ic-spec-platform
已經驗證過的那一半：

| 雛形（單檔 HTML） | 本平台 |
|---|---|
| 資料存在記憶體，重整即消失 | SQLite 持久化，附 online backup 腳本 |
| 誰都能改，改了不留痕跡 | Auth Center SSO + read / write / admin 分級，逐欄位修改紀錄 |
| 兩人同時編輯 → 後蓋前 | 欄位層級樂觀合併 + 版本鎖，衝突時明確提示 |
| 篩選只影響畫面 | 篩選條件直接帶進 Excel / 投影片匯出（畫面所見即匯出內容） |
| 匯入只是往表格塞列 | 匯入會辨識舊表頭、修正選項值、略過重複，並回報每一列的處理結果 |
| 無附件 | 每筆 RFI 可掛客戶 spec / 來信截圖 / 報價單 |

## 功能

- **RFI 管理**：17 個欄位（下拉 + 可自由輸入的建議清單 + 數字 + 日期 + 解析度複合欄位），
  日期自動換算成 SA 習慣的週別（`2026-07-13` → `26W29`），案件自動編號（`R26W29-01`）
- **多條件交叉篩選**：週別 / 產品 / 終端客戶 / 面板廠 / IC 型號 / 尺寸 / 狀態，
  每個下拉都可搜尋、顯示筆數，且**選項會隨其他條件縮減**（勾了 HP，面板廠只剩 HP 出現過的）；
  條件在網址上，可直接把連結貼給同事
- **排序**：任一欄位點標題即排序，數字採自然排序（9.7" 排在 14.0" 前）
- **共同編輯 + 修改紀錄**：任何具 `write` 權限者皆可編輯；每次變更保留完整版本快照，
  逐欄位呈現新舊值 diff、修改者、時間與說明
- **匯出投影片（PPTX）**：一鍵產生 `Customer RFI Collection W29~W35` 週報，16:9、
  每頁 6 筆、沿用原本的欄序與配色，中文字型一併寫入（Microsoft JhengHei）
- **匯出 / 匯入 Excel**：匯出沿用當下篩選條件；匯入相容 SA 舊表頭
  （`日期` / `Date` / `週別`、`面板尺寸`、`頻率`…），可略過重複資料
- **Dashboard**：依週別 / 產品 / 面板廠 / 終端客戶 / 狀態 / 負責 SA 六個維度統計，
  含圓餅圖、占比長條與統計 Excel 匯出
- **附件**：PDF / PPTX / XLSX / PNG / JPG，依「編號_客戶」分資料夾存放
- **日誌**：以 loguru 記錄登入、RFI 增刪改、匯入匯出、附件操作

## 協作與並行編輯

RFI 常常無法一次填完 —— 建案時只知道客戶與面板廠，規格與評估事項要等後續回覆才補得上。
平台用**欄位層級的樂觀合併**支援這種協作，並避免「後蓋前」的資料遺失：

1. 開啟編輯頁時，前端會記住當下的整份資料（baseline）與版本號。
2. 送出時，後端只挑出你**實際改動**的欄位，套用到資料庫**目前最新**的資料上。
   - 例：A 更新處理狀態、B 同時補風險評估 → 兩人的編輯都會保留，互不覆蓋。
3. 若你改的欄位在你編輯期間**被別人改成不同值**，會偵測為衝突 → 回傳 409 並重新顯示編輯頁，
   列出「目前最新值 / 你填入的值」讓你人工確認，未衝突的欄位仍自動保留他人編輯。
4. 每次成功編輯都會在時間綫留下一筆紀錄（誰、何時、改了哪些欄位的新舊值、為什麼改）。

必填欄位只有「日期 / 終端客戶 / 面板廠 / 產品類別 / 處理狀態」五個，
所以案子一進來就能先建檔，其餘規格由不同人陸續補齊。

- **權限分級**（沿用 Auth Center 的 scope）：
  - `read` — 瀏覽列表、詳情、修改紀錄、Dashboard，並匯出 Excel / 投影片
  - `write` — 新增 / 編輯 RFI、匯入 Excel、上傳 / 刪除附件
  - `admin` — 刪除整筆 RFI

## 快速開始（uv）

```bash
uv sync                       # 建立虛擬環境並安裝相依套件
cp .env.example .env          # 依環境調整

# 開發模式（自動重載）
uv run fastapi dev app/main.py --port 8003

# 正式模式
uv run fastapi run app/main.py --port 8003
```

> FastAPI CLI 需指定 `dev` 或 `run` 子指令；它會自動偵測 `app/main.py` 內的 `app` 物件。
> 也可用 `uv run uvicorn app.main:app --port 8003`，或傳統 pip：`pip install -r requirements.txt`。

開啟 http://localhost:8003 ，未登入會看到登入頁，點擊後導向 Auth Center 完成 SSO。

### 本機開發（免 Auth Center）

開發前端 / RFI 功能時，可在 `.env` 設定略過認證並注入測試使用者：

```env
DEV_AUTH_BYPASS=true
DEV_USER=dev.user
DEV_SCOPES=read,write,admin   # 調整以測試不同權限
```

> ⚠️ `DEV_AUTH_BYPASS` 僅限本機，正式環境務必設為 `false`。

想先看看畫面長什麼樣，可灌入示範資料（正式環境請勿執行）：

```bash
uv run python scripts/seed_demo.py           # 寫入 8 筆示範 RFI
uv run python scripts/seed_demo.py --clear   # 先清空再寫入
```

## 唯讀 API

平台提供 `/api/v1` 的 JSON API，**只能讀**。新增與修改一律走網頁介面 ——
那樣才會留下修改說明與逐欄位紀錄；token 外流的後果也就限縮在「資料被看到」，
不會變成「資料被竄改」。

| 端點 | 用途 |
|---|---|
| `GET /api/v1/me` | 確認憑證有效、看得到哪些權限 |
| `GET /api/v1/rfis` | RFI 列表，篩選 / 排序參數與網頁完全相同，支援 `limit` / `offset` |
| `GET /api/v1/rfis/{id}` | 單筆，含完整修改紀錄與附件中繼資料 |
| `GET /api/v1/filters` | 各篩選欄位目前可選的值與筆數（會依已帶入的條件交叉收斂） |
| `GET /api/v1/stats` | Dashboard 統計（`group` / `year`） |
| `GET /api/v1/fields` | 欄位定義與可選值 |

互動式文件在 `/docs`。列表回傳同時給 `values`（原始值，方便運算）與
`display`（顯示字串，與網頁、投影片上看到的一致）。

### 認證：兩種憑證

```bash
# 1. Auth Center JWT —— 12 小時效期，適合互動式取得
curl -H "Authorization: Bearer <JWT>" https://rfi.example.com/api/v1/rfis

# 2. 個人 API Token —— 長期有效、可撤銷，適合腳本與排程
curl -H "Authorization: Bearer sarfi_xxxxxxxx..." https://rfi.example.com/api/v1/rfis
```

Auth Center 的 JWT 只有 12 小時而且**沒有 refresh token**，放進腳本或設定檔
就得天天重貼。所以平台自己提供個人 Token：登入後到 **API Token** 頁面建立，
指定用途與有效期（預設 90 天，也可不設期限），**完整內容只顯示一次**，
資料庫只留 SHA-256 雜湊。外流或不用了隨時可以撤銷，撤銷後立即失效。

Token 一律只有 `read` 權限 —— 即使建立者本人有 `write` / `admin`，
拿 token 去打寫入端點也會被擋（實測回 401）。

## 容器部署

App 本身是 **stateless** 的：容器裡沒有任何重啟後還需要的東西。
會被寫入且必須留存的只有兩類 —— **資料庫**與**附件** —— 兩者都放在 `DATA_DIR`
（容器內預設 `/data`）底下，部署時掛成 volume 即可。日誌只走 stdout/stderr，
匯出的 Excel / PPT 全在記憶體組完直接回應，不落地。

```bash
cp .env.example .env      # 填 CLIENT_SECRET 等；內網再加 proxy 設定（見下）
docker compose up -d
docker compose logs -f app
```

Auth Center 還沒接好、只想先把畫面跑起來看，可在 `.env` 加 `DEV_AUTH_BYPASS=true`
（**正式環境務必移除**）。

Kubernetes 範例（PVC、probes、`fsGroup` 權限對齊）見 `deploy/kubernetes.yaml`。

### 內網 build：proxy / 私有 PyPI / 公司 CA

build 需要下載套件，內網環境通常要走 proxy、指到內部 PyPI 鏡像，
或信任公司的自簽憑證。這些都是 build args，寫進 `.env` 後 `docker compose build`
會自動帶入，**不需要改 Dockerfile**：

```bash
# .env
HTTPS_PROXY=http://proxy.corp:3128
HTTP_PROXY=http://proxy.corp:3128
NO_PROXY=localhost,127.0.0.1,.corp
PIP_INDEX_URL=https://nexus.corp/repository/pypi-proxy/simple
PIP_TRUSTED_HOST=nexus.corp
```

公司自簽 / 攔截式 proxy 的 CA 憑證，放進 `certs/` 即可（`.crt`、PEM 格式）：

```bash
cp /path/to/corp-root-ca.crt certs/
docker compose build
```

`certs/` 的內容會裝進 image 的信任清單，**build 階段**（下載套件）與
**執行階段**（App 用 HTTPS 連 Auth Center）都會信任。目錄空的話這步是 no-op。

直接用 docker build 的話：

```bash
docker build \
    --build-arg HTTPS_PROXY=http://proxy.corp:3128 \
    --build-arg PIP_INDEX_URL=https://nexus.corp/repository/pypi-proxy/simple \
    --build-arg PIP_TRUSTED_HOST=nexus.corp \
    -t sa-rfi-management:latest .
```

### 資料庫：SQLite 還是 PostgreSQL

兩種都支援，靠同一個 `DATABASE_URL` 切換，程式碼不用改：

| | SQLite（預設） | PostgreSQL |
|---|---|---|
| 設定 | `DATABASE_URL` 留空 | `DATABASE_URL=postgresql://user:pw@host:5432/sa_rfi` |
| 副本數 | **只能 1**（SQLite 不支援多寫入者） | 可多副本 |
| 需要的儲存 | 一個 RWO volume 就夠 | DB 自己的儲存；附件仍需 volume |
| 適合 | 十幾個 SA 的內部工具 | 要 HA、或想接既有 DB 叢集 |

連線字串可以直接貼常見的同步版 `postgresql://...`，App 會自動補上 async
driver（`postgresql+asyncpg://`），不用記得改。

> 建議先從 SQLite 開始。以這個平台的用量（每週幾十筆 RFI），單一副本綽綽有餘，
> 而且少一個要顧的元件。之後真的要 HA 再換 PostgreSQL —— 換的時候只有
> `DATABASE_URL` 這一行要動。

### 從 SQLite 換到 PostgreSQL

先用 SQLite 起步、之後再換 PostgreSQL 是預期中的路徑，附一支搬移腳本
（會保留原本的 id，所以修改紀錄與附件的關聯不會斷）：

```bash
# 1. 停掉服務，確保沒人在寫入
docker compose down

# 2. 從 volume 取出 SQLite 檔
docker run --rm -v sa-rfi-management_sa-rfi-data:/d -v "$PWD":/out \
    alpine cp /d/sa_rfi.db /out/sa_rfi.db

# 3. 起好 PostgreSQL（或用下面的 compose override），然後搬移
uv run python scripts/migrate_sqlite_to_postgres.py \
    --source ./sa_rfi.db \
    --target postgresql://sarfi:pw@localhost:5432/sa_rfi

# 4. 在 .env 設好 POSTGRES_PASSWORD，改用 override 檔啟動
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d
```

**附件不需要搬** —— 它們一直都在 `DATA_DIR/uploads`，不在資料庫裡，
換 DB 之後照樣讀得到。腳本會在目標資料庫已有資料時中止，避免不小心搬兩次。

### 多副本要注意的兩件事

1. **附件是檔案，不是資料庫欄位**。多副本必須共用同一份儲存
   （RWX 的 PVC：NFS / CephFS / EFS…），否則 A 副本收的附件 B 副本看不到。
2. **`ASSET_VERSION` 要設成固定值**（image tag 或 commit sha）。
   否則每個副本各自用啟動時間當靜態資源版本，使用者在副本間跳轉會一直重抓 CSS。

### 健康檢查

| 端點 | 用途 | 行為 |
|---|---|---|
| `/healthz` | liveness | 只確認行程活著，不碰外部相依 |
| `/readyz` | readiness | 檢查資料庫連得上、`DATA_DIR` 可寫；有問題回 503 並附上哪一項壞了 |

volume 沒掛好或權限不對時，App **啟動就會失敗**並印出實際原因與 UID，
不會裝作正常跑到有人上傳附件才炸。容器內以 UID/GID `10001` 執行，
Kubernetes 請設 `securityContext.fsGroup: 10001` 讓 volume 權限對齊。

## 與 Auth Center 整合

認證流程與 Auth Center `example_app` 一致：

1. 使用者按登入 → 導向 `{AUTH_CENTER_BASE_URL}/auth/login?app_id=..&redirect_uri=..`
2. 登入成功 → Auth Center 以 `?code=xxx` 導回 `/auth/callback`
3. 後端以 `code + client_secret` 向 `/auth/token` 換取 RS256 JWT
4. JWT 存入 httponly Cookie，後續請求以 Auth Center 公鑰驗證
   （`audience = APP_ID`、`issuer = AUTH_CENTER_BASE_URL`）
5. JWT 的 `scopes`（由 Auth Center 的 level 自動映射）決定可執行的操作

### 在 Auth Center 註冊本 App

於 Auth Center 的 `config/apps.yaml` 新增：

```yaml
sa_rfi_management:
  name: SA RFI 管理平台
  client_secret: <bcrypt hash of CLIENT_SECRET>
  redirect_uri: http://localhost:8003/auth/callback
  app_url: http://localhost:8003
  default_level: 1          # 預設給 read；需要 write 的 SA 授 level 2
  token_expire_hours: 12
```

驗章公鑰建議走 JWKS（免維護 public.pem）；離線環境則把 Auth Center 的
`keys/public.pem` 複製到本專案 `keys/public.pem`（路徑由 `PUBLIC_KEY_PATH` 設定）。

授予使用者權限（在 Auth Center 端）：

```bash
# level 1 → read；level 2 → read+write；level 3 → read+write+admin
python scripts/manage_permissions.py grant <employee> sa_rfi_management --level 2
```

## 設定項（.env）

| 變數 | 說明 | 預設 |
|------|------|------|
| `APP_BASE_URL` | 本平台對外的 Base URL | `http://localhost:8003` |
| `ROOT_PATH` | 反向代理子路徑前綴，留空自動由 `APP_BASE_URL` 推導 | （自動） |
| `DATA_DIR` | **所有狀態的根目錄**（SQLite 檔 + 附件），容器部署掛成 volume | `.` |
| `DATABASE_URL` | 留空＝用 `{DATA_DIR}/sa_rfi.db`；填 PostgreSQL 連線字串即改用 PG | （空） |
| `ASSET_VERSION` | 靜態資源版本，多副本請設成 image tag / commit sha | （啟動時間） |
| `AUTH_CENTER_BASE_URL` | Auth Center 位址 | `http://localhost:8000` |
| `APP_ID` | 在 apps.yaml 註冊的 App ID | `sa_rfi_management` |
| `CLIENT_SECRET` | App 明文密鑰 | — |
| `REDIRECT_URI` | OAuth callback，留空自動組出 | （自動） |
| `JWKS_URL` | 驗章公鑰端點，留空自動推導；設為空字串則停用 | （自動） |
| `PUBLIC_KEY_PATH` | 離線後備的 RS256 公鑰路徑 | `./keys/public.pem` |
| `SQLITE_PATH` | 單獨指定 SQLite 檔位置（一般不需要，用 `DATA_DIR` 即可） | `{DATA_DIR}/sa_rfi.db` |
| `UPLOAD_DIR` | 單獨指定附件目錄（一般不需要） | `{DATA_DIR}/uploads` |
| `MAX_UPLOAD_MB` | 單檔大小上限 | `25` |
| `DECK_TITLE` | 匯出投影片的標題（自動接上週別區間） | `Customer RFI Collection` |
| `COOKIE_SECURE` | Cookie 限定 HTTPS（正式設 true） | `false` |
| `DESIGNED_BY` / `EXECUTED_BY` | 頁尾署名（留空不顯示） | — |
| `LOG_LEVEL` / `LOG_FILE` | loguru 等級；`LOG_FILE` 留空＝只輸出 stdout（容器請留空） | `INFO` / （空） |
| `DEV_AUTH_BYPASS` | 略過認證（僅開發） | `false` |

## 專案結構

```
app/
  main.py          FastAPI 進入點、認證路由、例外處理
  config.py        設定（.env）
  auth.py          Auth Center OAuth 整合、JWT 驗證、scope 守衛
  database.py      async SQLite engine / session
  models.py        Rfi / RfiRevision / Attachment
  fields.py        17 個 RFI 欄位定義 + 週別換算（單一事實來源）
  query.py         多選交叉篩選、關鍵字、自然排序（列表與匯出共用同一套規則）
  routes/rfis.py     RFI CRUD、修改紀錄、Dashboard、附件
  routes/exports.py  Excel 匯出 / 匯入、投影片（PPTX）匯出
  routes/api.py      唯讀 JSON API（/api/v1）
  routes/tokens.py   個人 API Token 的建立與撤銷
templates/         Jinja2 HTML（list / form / detail / history / dashboard / import / login / error）
static/css/style.css   樣式
static/js/filters.js   多選篩選器互動（停用 JS 時仍可用「套用篩選」按鈕）
scripts/backup.py      資料庫備份（SQLite online backup / pg_dump）+ 附件打包
scripts/migrate_sqlite_to_postgres.py  SQLite → PostgreSQL 資料搬移
scripts/seed_demo.py   示範資料
uploads/           附件儲存（預設值；實際位置由 DATA_DIR 決定）
Dockerfile         多階段建置，非 root 執行
docker-compose.yml 單機部署（預設 SQLite）
docker-compose.postgres.yml  疊上去即改用 PostgreSQL
certs/             公司自簽 CA（放 .crt 進去，build 與執行階段都會信任）
deploy/kubernetes.yaml  Kubernetes 部署範例（PVC / probes / fsGroup）
```

## 新增 / 調整 RFI 欄位

所有表單、列表、篩選、Dashboard、匯入匯出與 diff 都從 `app/fields.py` 的
`RFI_FIELDS` 迭代產生。新增欄位只需在該清單加入一個 `RfiField`
（指定 `key` / `label` / `type` / `options` / `group`），畫面會自動套用。

若要讓新欄位也出現在列表、投影片或篩選器，再把它的 `key` 加進同檔的
`LIST_COLUMNS` / `SLIDE_COLUMNS` / `FILTER_KEYS` 即可，毋須改動模板或路由。

> `status`（處理狀態）與 `owner`（負責 SA）是原雛形沒有、為了「追蹤」而加的兩個欄位，
> Dashboard 的「未結案」統計也建立在 `status` 上。若貴單位不需要，
> 從 `RFI_FIELDS` 移除該行、並把 `status` 從 `LIST_COLUMNS` / `FILTER_KEYS`
> 與 `DASH_GROUPS`（`app/routes/rfis.py`）拿掉即可。

## Excel 匯入格式

第 1 列為表頭，中英文皆可辨識（`終端客戶` / `Client`、`日期` / `Date` / `週別`…）。
不確定格式時，先用平台的「下載匯入範本」，或用列表頁的「匯出 Excel」拿一份現有資料當範例。

匯入時會自動處理：

- 日期：`2026-07-13`、`2026/07/13`、`26W29` 都接受（週別會換算成該週星期一）
- 尺寸 / 頻率：`14.0"`、`60Hz` 這類帶單位的值會取出數字
- 解析度：`2560x1600` 會拆成寬 / 高
- 下拉欄位：`COF 硬性` 之類的寫法會對應到最接近的選項，並在結果頁列出「已修正」
- 缺必填欄位的列不會匯入，會在結果頁標示是第幾列、缺什麼

## 備份

服務運行中也可安全執行，兩種資料庫都支援（SQLite 走 online backup API，
PostgreSQL 走 `pg_dump -Fc`），附件目錄一併打包：

```bash
uv run python scripts/backup.py /data/backups

# 容器部署時，讓備份工作掛上同一個 data volume：
docker run --rm -v sa-rfi-data:/data -v /srv/backups:/backups \
    -e DATA_DIR=/data sa-rfi-management:latest \
    python scripts/backup.py /backups

# 建議搭配 cron，每日 02:00：
# 0 2 * * * cd /path/to/sa-rfi-management && .venv/bin/python scripts/backup.py /data/backups
```

預設保留最近 14 份（`BACKUP_KEEP` 可調）。

## 部署注意

- 介面的圖示與字型走 Google Fonts（`fonts.googleapis.com`）；與 ic-spec-platform 一致。
  若部署環境完全不通外網，圖示會退化成文字（如 `expand_more`），
  此時把 Material Icons 與字型檔自架、改寫 `templates/base.html` 的兩行 `<link>` 即可。
- 部署在反向代理子路徑時，nginx 請用尾斜線 `proxy_pass` 剝掉前綴，
  並設定 `APP_BASE_URL`（或 `ROOT_PATH`），模板的內部連結與 cookie 路徑都會跟著調整。
