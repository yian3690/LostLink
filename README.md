# LostLink AI

LostLink AI 是一套以 LINE 為入口的多模態 AI 校園失物招領服務。學生可以用自然語言或照片登記遺失／拾獲物品；系統結合文字語意、圖像特徵、類別、顏色、地點與時間，自動產生候選配對並透過 LINE 通知。

> 核心定位：先以校園作為可驗證的 MVP 場域，再延伸至百貨、車站、機場、展館、飯店與企業內部 Lost & Found SaaS。

## MVP 目標

- LINE 對話式遺失／拾獲登記
- 中文文字與照片的多模態理解
- 地點、時間、類別、品牌、顏色等欄位抽取
- 候選物品相似度排序與可解釋配對
- 高可信候選的 LINE 主動通知
- 安全認領流程、校方管理後台與成效指標

## 系統架構

```mermaid
flowchart LR
    U[學生／拾獲者] -->|文字、照片、位置| LINE[LINE 官方帳號]
    U -->|手機相機、文字查詢| LIFF[Next.js 手機版／LIFF PWA]
    LINE -->|Webhook| API[FastAPI API]
    LIFF -->|HTTPS JSON API| API
    API --> VERIFY[LINE 簽章驗證]
    VERIFY --> APP[LostLink 應用服務]

    APP --> GEMINI[Gemini 多模態理解<br/>欄位抽取、圖片描述]
    APP --> E5[multilingual-e5-base<br/>中文文字 Embedding]
    APP --> SIGLIP[SigLIP 2 Base 256<br/>文字／圖片 Embedding]
    APP --> OBJECT[物件儲存<br/>照片原檔與縮圖]
    APP --> DB[(PostgreSQL)]
    E5 --> VECTOR[(pgvector<br/>文字向量索引)]
    SIGLIP --> VECTOR2[(pgvector<br/>圖文向量索引)]
    VECTOR --> DB
    VECTOR2 --> DB

    DB --> MATCH[多訊號配對引擎]
    MATCH --> RANK[加權評分與門檻判斷]
    RANK -->|高可信候選| PUSH[LINE 主動通知]
    PUSH --> U

    ADMIN[Next.js 校方管理後台] <-->|審核、領回、統計| API
    API --> DB
```

### 元件職責

| 元件 | 技術 | 職責 |
|---|---|---|
| 使用者入口 | LINE Messaging API | 接收文字、照片與按鈕事件，回覆登記結果及可能配對 |
| 手機介面 | Next.js、LINE LIFF、PWA | 拍照登記、地點與時間、拾獲物瀏覽、圖文協尋與認領 |
| 後端 API | Python 3.12、FastAPI | Webhook、流程控制、權限、資料 API 與通知工作 |
| 多模態理解 | Gemini | 抽取類別、品牌、顏色、地點、時間與圖片描述 |
| 文字檢索 | multilingual-e5-base | 比較中文報失與拾獲描述的語意相似度 |
| 跨模態檢索 | google/siglip2-base-patch16-256 | 比較文字與圖片、圖片與圖片的相似度 |
| 主資料庫 | PostgreSQL | 使用者、案件、狀態、通知、認領與稽核紀錄 |
| 向量搜尋 | pgvector | 保存 E5／SigLIP 2 向量並執行 cosine、HNSW 搜尋 |
| 圖片儲存 | Supabase Storage 或 S3 相容服務 | 保存原圖與縮圖，資料庫只存路徑及 metadata |
| 管理後台 | Next.js | 人工複核、案件管理、領回確認與營運儀表板 |

## 使用流程

### 1. 遺失物品登記

```mermaid
sequenceDiagram
    actor Student as 遺失者
    participant Line as LINE
    participant API as FastAPI
    participant AI as Gemini / E5 / SigLIP 2
    participant DB as PostgreSQL + pgvector
    participant Match as 配對引擎

    Student->>Line: 我的黑色 AirPods 在圖書館不見了
    Line->>API: Webhook 事件
    API->>AI: 抽取類別、品牌、顏色、地點與時間
    AI-->>API: 結構化資料 + 文字向量
    API->>DB: 建立 lost report
    API->>Match: 搜尋有效的拾獲紀錄
    Match->>DB: metadata 過濾 + 向量 Top-K
    DB-->>Match: 候選拾獲物品
    Match-->>API: 排序後候選與理由
    API-->>Line: 報失編號、摘要與配對狀態
    Line-->>Student: 已完成登記，系統將持續比對
```

1. 驗證 LINE Webhook 簽章並識別訊息類型。
2. Gemini 將描述整理成 `category`、`brand`、`color`、`location`、`occurred_at` 等欄位。
3. E5 產生文字向量；若有物品舊照，SigLIP 2 同時產生圖片向量。
4. 系統先按校區、案件狀態、類別及時間範圍篩選，再執行向量搜尋。
5. 若沒有可靠候選，案件保持開放；新增拾獲物時會自動重新比對。

### 2. 拾獲物品登記與主動通知

```mermaid
sequenceDiagram
    actor Finder as 拾獲者
    participant Line as LINE
    participant API as FastAPI
    participant AI as Gemini / E5 / SigLIP 2
    participant DB as PostgreSQL + pgvector
    participant Notify as 通知服務

    Finder->>Line: 上傳黑色無線耳機照片
    Line->>API: 圖片訊息 ID
    API->>Line: 下載原始圖片
    API->>AI: 圖片理解與 Embedding
    AI-->>API: 屬性、圖片描述、文字／圖片向量
    API->>DB: 建立 found report 並搜尋報失紀錄
    DB-->>API: Top-K 候選與各項分數
    API->>Notify: 高可信候選進入通知佇列
    Notify-->>Line: 發送可能配對通知
    Line-->>Finder: 已完成拾獲登記
```

照片不只交給 Gemini 產生描述，也會直接交給 SigLIP 2 產生圖像向量，避免文字描述遺漏保護殼、外型或局部特徵。

### 3. 多訊號配對

E5 和 SigLIP 2 位於不同向量空間，必須分欄儲存、分別搜尋，再融合標準化後的分數；不可直接相加原始向量。

```text
final_score =
    0.35 × cross_modal_score   # SigLIP 2：文字 ↔ 圖片
  + 0.25 × text_score          # E5：描述 ↔ 描述
  + 0.15 × category_brand      # 類別與品牌
  + 0.10 × color_features      # 顏色與可見特徵
  + 0.10 × location_score      # 地點距離／樓層
  + 0.05 × time_score          # 遺失與拾獲時間差
```

```mermaid
flowchart TD
    N[新增遺失／拾獲案件] --> F[狀態、校區、類別與時間篩選]
    F --> T[E5 文字 Top-K]
    F --> V[SigLIP 2 圖文 Top-K]
    T --> M[合併候選集合]
    V --> M
    M --> S[metadata + 向量加權評分]
    S --> H{可信度門檻}
    H -->|高| P[主動通知雙方]
    H -->|中| R[校方後台人工複核]
    H -->|低| W[保留案件並等待新資料]
```

以上權重只是 MVP 初始值。畫面上的「相似度 93%」會以標註測試資料校準成配對信心分數，而不是直接把 cosine similarity 當成百分比。

### 4. 安全認領

1. 遺失者只看到候選摘要，不會取得拾獲者的 LINE ID 或私人聯絡方式。
2. 使用者回覆「可能是我的」或「不是我的」。
3. 認領者提供未公開特徵，例如刻字、刮痕、保護殼內側或配件。
4. 校方在後台核對，通過後安排領回。
5. 案件依序更新為 `matched`、`claim_pending`、`returned` 並保留稽核紀錄。

## 核心資料模型

| 資料表 | 用途 |
|---|---|
| `users` | LINE 使用者識別、角色與同意紀錄 |
| `item_reports` | 遺失／拾獲案件、屬性、地點、時間與狀態 |
| `item_images` | 圖片路徑、縮圖與安全掃描狀態 |
| `item_embeddings` | E5、SigLIP 2 向量及模型版本 |
| `match_candidates` | 候選配對、子分數、總分與判斷理由 |
| `notifications` | LINE 通知內容、狀態與重試次數 |
| `claims` | 認領驗證與交付狀態 |
| `audit_logs` | 校方操作與敏感資料存取紀錄 |

## 隱私與安全原則

- LINE Channel Secret、Access Token、Gemini API Key 只放在環境變數。
- 通知採最少揭露原則，不公開雙方身分與完整物品細節。
- 圖片使用不可猜測的路徑或短效簽章網址。
- Webhook 驗證 LINE 簽章；管理後台使用角色權限與操作紀錄。
- 設定案件及圖片保存期限，並支援使用者刪除請求。

## 預計專案結構

```text
LostLink AI/
├─ backend/                 # FastAPI、LINE Webhook、AI 與配對服務
│  ├─ app/
│  │  ├─ api/
│  │  ├─ core/
│  │  ├─ models/
│  │  ├─ repositories/
│  │  ├─ schemas/
│  │  └─ services/
│  └─ tests/
├─ admin-web/               # Next.js 校方管理後台 + /mobile 手機 LIFF
├─ migrations/              # PostgreSQL／pgvector migration
├─ scripts/                 # 測試資料與模型評估工具
├─ docs/                    # 競賽提案、API 與部署文件
├─ .env.example
├─ docker-compose.yml
└─ README.md
```

## 快速啟動

### Windows 一鍵啟動（建議）

直接雙擊專案根目錄的 **`啟動 LostLink AI.cmd`**。系統會自動檢查環境、啟動 FastAPI 與 Next.js、等待服務就緒，再開啟手機版頁面。首次執行時也會自動安裝缺少的套件。

啟動視窗會顯示同一 Wi-Fi 手機網址；若已設定 ngrok，也會顯示評審可從外網開啟的 HTTPS、Webhook 與 LIFF Endpoint。使用完畢後雙擊 **`停止 LostLink AI.cmd`**；執行記錄與本機密鑰位於 Git 已忽略的 `.runtime`。

### 免費 ngrok HTTPS（只需設定一次）

1. 登入 ngrok Dashboard，進入 **Your Authtoken** 並複製 Authtoken。
2. 雙擊 **`設定 ngrok.cmd`**，在本機視窗貼上；輸入不會顯示，也不需要傳給其他人。
3. 雙擊 **`啟動 LostLink AI.cmd`**。啟動視窗會列出三個可直接使用的網址。
4. 之後可雙擊 **`查看 LostLink AI 公開網址.cmd`** 再次查看。

免費方案會配置固定的開發網域；一鍵啟動會把 `https://該網域/mobile` 提供給 LIFF，並把 `https://該網域/webhooks/line` 提供給 Messaging API。電腦必須保持開機且程式持續執行，公開網址才有服務。

### LINE 對話測試資料

專案已內建四筆含照片與正式向量的拾獲物：瓶裝茶、黑色無線耳機、藍色折疊傘、黑色保溫瓶。照片位於 `demo-assets/`。需要在新資料庫重新建立時執行：

```powershell
..venvScriptspython.exe backendscriptsseed_demo_data.py
```

腳本可重複執行，已存在的資料會略過，而且建立資料時不會發送 LINE Push。建議依序在 LINE 測試：

1. `你能列目前的遺失物嗎`：列出目前拾獲物，不會誤建拾獲案件。
2. `我的飲料不見了`：建立遺失案件並回傳同類候選。
3. `瓶裝飲料，綠色瓶蓋，在圖書館二樓`：加入最近案件作為新線索並重新比對。
4. `可以給我照片嗎`：再次傳送最高候選照片與「可能是我的／不是我的」按鈕。

### 1. Demo 模式

Demo 模式不需要 LINE、Gemini 或 PostgreSQL 金鑰，會使用 SQLite 與本機 deterministic embedding 跑通產品流程。

```powershell
cd "<LostLink 專案資料夾>"
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
.\scripts\run_backend.ps1
```

後端啟動後可開啟：

- Swagger API：<http://127.0.0.1:8000/docs>
- 健康檢查：<http://127.0.0.1:8000/health>

另一個 PowerShell 視窗啟動管理後台：

```powershell
cd "<LostLink 專案資料夾>"
.\scripts\run_admin.ps1
```

管理後台位於 <http://localhost:3000>，可點擊「建立示範案件」產生 AirPods 報失與拾獲案例。

手機版位於 <http://localhost:3000/mobile>，包含：

- 拍照或選圖登記拾獲物，必填拾獲地點與時間
- 瀏覽目前待認領拾獲物的安全縮圖
- 以文字、照片或兩者一起搜尋，顯示 AI 配對信心與理由
- 提交只有失主知道的私密特徵，交由校方審核

同一 Wi-Fi 手機可直接使用啟動視窗顯示的區網網址，不必修改 API 網址。前端、API 與 Webhook 已採同源代理；正式 LIFF 與精確定位請使用 ngrok HTTPS 網址。

### 2. Docker + PostgreSQL/pgvector

先啟動 Docker Desktop，再執行：

```powershell
docker compose up --build
```

此模式會啟動 PostgreSQL/pgvector、FastAPI 與 Next.js。資料庫首次建立時會自動執行 `migrations/001_initial.sql`。

### 3. 正式 AI 與 LINE 模式

複製設定範本，填入自己的金鑰：

```powershell
Copy-Item .env.example .env
pip install -r requirements-ml.txt
```

至少設定：

```dotenv
DEMO_MODE=false
DATABASE_URL=postgresql+asyncpg://lostlink:password@localhost:5432/lostlink
LINE_CHANNEL_SECRET=...
LINE_CHANNEL_ACCESS_TOKEN=...
GEMINI_API_KEY=...
ADMIN_API_KEY=...
```

正式模式第一次使用 E5／SigLIP 2 時會從 Hugging Face 下載模型權重。LINE Developers 的 Webhook URL 設為：

目前本機展示環境已切換為 `DEMO_MODE=false`，使用 `intfloat/multilingual-e5-base` 與 `google/siglip2-base-patch16-256` 的 768 維正式向量。模型會在後端啟動時預熱並於程序內共用；CPU 模式首次啟動約需數十秒，之後不需任何付費 API。

LINE Bot 每次建立案件後都會回覆比對結果：有候選時顯示候選數量、最高相似度與原因；沒有候選時明確告知目前尚未找到，並保留後續主動通知。

```text
https://你的公開網域/webhooks/line
```

### LINE LIFF 手機入口

1. 在 LINE Developers 同一個 Provider 建立 LIFF app，Endpoint URL 設為 `https://你的公開網域/mobile`。
2. 啟用 `profile` scope，取得 LIFF ID。
3. 建立 `admin-web/.env.local`：

```dotenv
NEXT_PUBLIC_LIFF_ID=你的-LIFF-ID
```

4. 重新執行 `npm run build`。LIFF ID 是建置期公開設定；Channel Secret 與 Access Token 仍只能放在後端 `.env`。

未設定 LIFF ID 時，`/mobile` 會以一般瀏覽器 Demo 模式運作，方便先在電腦或手機測試介面。

### 常用 API

| 方法 | 路徑 | 用途 |
|---|---|---|
| `GET` | `/health` | 健康檢查 |
| `POST` | `/api/v1/reports` | 建立遺失／拾獲案件 |
| `GET` | `/api/v1/reports` | 查詢最新案件 |
| `GET` | `/api/v1/reports/{id}/image` | 取得待認領拾獲物的去 metadata 安全縮圖 |
| `GET` | `/api/v1/reports/{id}/matches` | 查詢候選配對 |
| `POST` | `/api/v1/matches/{id}/claims` | 提交私密特徵進行認領 |
| `POST` | `/api/v1/claims/{id}/review` | 校方批准／拒絕認領 |
| `GET` | `/api/v1/dashboard/stats` | 管理後台指標 |
| `POST` | `/api/v1/demo/seed` | 建立示範資料 |
| `POST` | `/webhooks/line` | LINE Messaging API Webhook |

## 測試

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd admin-web
npm run build
npm audit --audit-level=high
```

## 專案狀態

- [x] 建立專案資料夾與 Python 3.12 虛擬環境
- [x] 確認 MVP 技術架構與使用流程
- [x] 建立 FastAPI 與 LINE Webhook
- [x] 建立 PostgreSQL／pgvector schema 與 HNSW index
- [x] 串接 Gemini、E5 與 SigLIP 2（需使用者金鑰／首次下載模型）
- [x] 實作候選配對、LINE 通知與安全認領
- [x] 建立管理後台、Demo 資料與自動測試
- [x] 建立手機版 LIFF／PWA、相機上傳、拾獲物瀏覽與圖文協尋
- [x] 圖片重新編碼去除 metadata，公開介面只提供縮圖
- [ ] 建立評估集並校準配對信心分數
- [ ] 使用正式 LINE／Gemini 帳號完成線上環境驗收
