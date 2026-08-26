# keys/

放置 Auth Center 的 RS256 **公鑰**（`public.pem`），供離線驗證 JWT 用。

一般情況不需要這個檔案：只要連得到 Auth Center，平台會自動向
`{AUTH_CENTER_BASE_URL}/.well-known/jwks.json` 取得公鑰（見 `.env` 的 `JWKS_URL`）。
只有在無法連到 Auth Center 的環境，才需要把它的 `keys/public.pem` 複製到這裡。

`*.pem` 已列入 `.gitignore`，請勿把任何金鑰提交進版控（尤其是私鑰）。
