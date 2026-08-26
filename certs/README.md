# certs/

放公司內部的自簽 / 攔截式 Proxy 的 CA 憑證（`.crt`，PEM 格式）。

build 時這個目錄的內容會被裝進 image 的信任清單，**build 階段**（下載套件）
與**執行階段**（App 連 Auth Center）都會信任它。目錄空的話這一步是 no-op，
不影響一般環境。

```bash
cp /path/to/corp-root-ca.crt certs/
docker compose build
```

副檔名必須是 `.crt`（Debian 的 `update-ca-certificates` 只認這個）。
若手上是 `.pem`，直接改名即可。

⚠️ 只放 CA **公開憑證**，不要放任何私鑰。
