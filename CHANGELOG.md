# 更新紀錄

本文件記錄 LostLink AI 的重要功能與版本變更。

格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版本編號遵循 [Semantic Versioning](https://semver.org/lang/zh-TW/)。

## [Unreleased]

### 規劃中

- 建立正式評估集，校準多模態配對分數與通知門檻。
- 完成 PostgreSQL／pgvector 正式環境與部署驗收。
- 增加校方管理員權限、稽核紀錄及案件保存期限設定。

## [0.2.0] - 2026-09-08

### 新增

- 新增本機 Ollama + Gemma 3 4B 自然語言與圖片理解服務。
- 新增 LINE「我遺失物品」、「我撿到物品」及「一般聊天」三種對話模式。
- 新增大型 LINE Rich Menu，上方品牌橫幅可直接開啟 LostLink AI LIFF 頁面。
- 新增中文自然問句搜尋，例如「有水壺嗎」、「瓶子呢」及「目前有人撿到錢包嗎」。
- 新增水壺、水杯、保溫瓶、寶特瓶與飲料容器的相近類別搜尋。
- 新增 LINE 候選物品照片、相似度、判斷理由與後續認領提示。
- 新增管理者專用本機資料庫頁面，可新增、修改、刪除及搜尋案件。
- 新增待認領／已認領狀態切換，已認領物品會移至獨立區域。
- 新增物品照片詳情頁與可點擊縮圖。
- 新增 AI 物品類別、顏色、品牌、形狀、圖案、貼紙與特色描述。
- 新增近期案件去重複機制，避免相同使用者重複詢問時產生大量案件。
- 新增舊資料 AI 特徵回填及照片重新分析工具。
- 新增一鍵啟動時的 Ollama 服務與 gemma3:4b 模型檢查。

### 改善

- LINE Bot 搜尋結果改為最多顯示 3 筆相關待認領物品，不再列出無關資料。
- 改善地點辨識，可保留如 ZB302、圖書館樓層與教室等位置資訊。
- 改善黃色飲料、瓶子、耳機、雨傘等文字與圖片辨識。
- 改善多輪對話，可在後續訊息補充物品、顏色、地點及照片。
- 改善照片上傳提示，移除一般使用者不需要知道的資料庫術語。
- 美化管理頁面的照片選擇按鈕與檔名顯示。
- 調整手機頁面的待認領及已認領物品版面。
- 放大 Rich Menu 品牌標語與 LIFF 開啟按鈕，提升手機閱讀性。
- 更新 README 架構圖、啟動方式、零成本技術方案與正式模型設定。

### 修正

- 修正「有水壺嗎」等自然問句未查詢資料庫、錯誤回答沒有結果的問題。
- 修正一般聊天可能編造網站網址或自行宣稱資料庫結果的問題。
- 修正 LINE 傳送無關照片時可能直接建立案件的問題。
- 修正沒有選擇遺失／拾獲模式時，文字或照片可能被錯誤登記的問題。
- 修正重複報失產生多筆不必要資料的問題。
- 修正手機頁面部分按鈕無法操作及公開網址開啟失敗的問題。

### 技術變更

- 使用 multilingual-e5-base 產生中文文字向量。
- 使用 SigLIP 2 Base 256 產生文字／圖片跨模態向量。
- 保留 PostgreSQL／pgvector schema、向量欄位與 HNSW 索引。
- 將 LINE Webhook、LIFF、圖片下載、簽章驗證、Reply 與 Push 流程整合至 FastAPI。
- 移除 Gemini API 整合、GEMINI_API_KEY、GEMINI_MODEL 與 google-genai 套件。
- 將固定聊天內容改為 Gemma 3 自然回覆，只保留必要的安全與交易狀態訊息。
- 移除封閉式物品名稱判斷清單，保留本機模型失效時的最小安全備援。

### 安全與儲存

- .gitignore 排除環境密鑰、LINE Token、SQLite、上傳照片、執行紀錄及建置快取。
- 排除 Ollama、GGUF、SafeTensors 與其他本機模型檔案，避免大型權重進入 GitHub。
- 圖片重新編碼以移除 metadata，公開頁面只提供安全縮圖。
- 管理資料庫頁面限制為本機存取。

## [0.1.0] - 2026-09-06

### 新增

- 建立 LostLink AI 專案骨架、Python 虛擬環境與 Next.js 前端。
- 建立 FastAPI API、LINE Webhook、資料模型及初始測試。
- 建立手機版拾獲登記、失物搜尋及管理後台雛形。

[Unreleased]: https://github.com/yian3690/LostLink/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/yian3690/LostLink/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/yian3690/LostLink/releases/tag/v0.1.0
