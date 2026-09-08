"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import styles from "./mobile.module.css";

type Tab = "items" | "found" | "lost";

type Report = {
  id: string;
  kind: "lost" | "found";
  status: string;
  description: string;
  category: string | null;
  brand: string | null;
  color: string | null;
  campus: string | null;
  location: string | null;
  occurred_at: string | null;
  created_at: string;
  image_url: string | null;
};

type Match = {
  id: string;
  found_report_id: string;
  score: number;
  decision: string;
  reasons: string[];
};

type ReportCreated = {
  report: Report;
  matches: Match[];
};

const API = process.env.NEXT_PUBLIC_API_URL ?? "";
const LIFF_ID = process.env.NEXT_PUBLIC_LIFF_ID ?? "";

function apiImage(path: string | null): string | null {
  if (!path) return null;
  return path.startsWith("http") ? path : `${API}${path}`;
}

function formatTime(value: string | null): string {
  if (!value) return "時間未提供";
  return new Intl.DateTimeFormat("zh-TW", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

async function compressImage(file: File): Promise<{ base64: string; preview: string }> {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("無法讀取照片"));
    reader.readAsDataURL(file);
  });
  const image = await new Promise<HTMLImageElement>((resolve, reject) => {
    const element = new Image();
    element.onload = () => resolve(element);
    element.onerror = () => reject(new Error("照片格式不支援"));
    element.src = dataUrl;
  });
  const maxSide = 1280;
  const ratio = Math.min(1, maxSide / Math.max(image.width, image.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(image.width * ratio);
  canvas.height = Math.round(image.height * ratio);
  const context = canvas.getContext("2d");
  if (!context) throw new Error("無法處理照片");
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  const compressed = canvas.toDataURL("image/jpeg", 0.82);
  return { base64: compressed.split(",", 2)[1], preview: compressed };
}

export default function MobilePage() {
  const [tab, setTab] = useState<Tab>("items");
  const [reports, setReports] = useState<Report[]>([]);
  const [matches, setMatches] = useState<Match[]>([]);
  const [lineUserId, setLineUserId] = useState("web-guest");
  const [displayName, setDisplayName] = useState("同學");
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [selectedReport, setSelectedReport] = useState<Report | null>(null);

  const [foundDescription, setFoundDescription] = useState("");
  const [foundLocation, setFoundLocation] = useState("");
  const [foundTime, setFoundTime] = useState("");
  const [foundImage, setFoundImage] = useState("");
  const [foundPreview, setFoundPreview] = useState("");

  const [lostDescription, setLostDescription] = useState("");
  const [lostLocation, setLostLocation] = useState("");
  const [lostTime, setLostTime] = useState("");
  const [lostImage, setLostImage] = useState("");
  const [lostPreview, setLostPreview] = useState("");

  const loadReports = useCallback(async () => {
    try {
      const response = await fetch(`${API}/api/v1/reports?kind=found&limit=100`, {
        cache: "no-store",
      });
      if (!response.ok) throw new Error("目前無法讀取拾獲物");
      setReports((await response.json()).filter((item: Report) => item.status !== "returned"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "讀取失敗");
    }
  }, []);

  useEffect(() => {
    void loadReports();
    if (!LIFF_ID) return;
    void import("@line/liff").then(async ({ default: liff }) => {
      await liff.init({ liffId: LIFF_ID });
      const canUseLineLogin = liff.isInClient() || window.location.protocol === "https:";
      if (!canUseLineLogin) {
        setDisplayName("本機 Demo");
        return;
      }
      if (!liff.isLoggedIn()) {
        liff.login({ redirectUri: `${window.location.origin}/mobile` });
        return;
      }
      const profile = await liff.getProfile();
      setLineUserId(profile.userId);
      setDisplayName(profile.displayName);
    }).catch(() => setError("LINE 登入初始化失敗，仍可使用網頁 Demo。"));
  }, [loadReports]);

  useEffect(() => {
    if (!selectedReport) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelectedReport(null);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [selectedReport]);

  const matchedItems = useMemo(
    () =>
      matches
        .map((match) => ({
          match,
          report: reports.find((item) => item.id === match.found_report_id),
        }))
        .filter((item): item is { match: Match; report: Report } => Boolean(item.report)),
    [matches, reports],
  );

  const onImage = async (
    event: ChangeEvent<HTMLInputElement>,
    setImage: (value: string) => void,
    setPreview: (value: string) => void,
  ) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setError("請選擇照片檔案");
      return;
    }
    if (file.size > 15 * 1024 * 1024) {
      setError("照片不可超過 15MB");
      return;
    }
    try {
      const result = await compressImage(file);
      setImage(result.base64);
      setPreview(result.preview);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "照片處理失敗");
    }
  };

  const submitFound = async (event: FormEvent) => {
    event.preventDefault();
    if (!foundImage || !foundLocation || !foundTime) {
      setError("請拍照並填寫拾獲地點與時間");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API}/api/v1/reports`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind: "found",
          description: foundDescription,
          location: foundLocation,
          occurred_at: new Date(foundTime).toISOString(),
          line_user_id: lineUserId,
          image_base64: foundImage,
        }),
      });
      if (!response.ok) throw new Error("拾獲物登記失敗");
      setFoundDescription("");
      setFoundLocation("");
      setFoundTime("");
      setFoundImage("");
      setFoundPreview("");
      setNotice("謝謝你！拾獲物已登記，AI 正在尋找失主。");
      await loadReports();
      setTab("items");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登記失敗");
    } finally {
      setLoading(false);
    }
  };

  const submitLost = async (event: FormEvent) => {
    event.preventDefault();
    if (!lostDescription.trim() && !lostImage) {
      setError("請輸入描述或上傳物品照片");
      return;
    }
    setLoading(true);
    setError("");
    setMatches([]);
    try {
      const response = await fetch(`${API}/api/v1/reports`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kind: "lost",
          description: lostDescription,
          location: lostLocation || null,
          occurred_at: lostTime ? new Date(lostTime).toISOString() : null,
          line_user_id: lineUserId,
          image_base64: lostImage || null,
        }),
      });
      if (!response.ok) throw new Error("搜尋失敗，請稍後再試");
      const result: ReportCreated = await response.json();
      setMatches(result.matches);
      setNotice(
        result.matches.length
          ? `找到 ${result.matches.length} 個可能相符的物品`
          : "目前沒有高相似候選，系統會在新物品登記後繼續比對。",
      );
      await loadReports();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "搜尋失敗");
    } finally {
      setLoading(false);
    }
  };

  const useLocation = (setter: (value: string) => void) => {
    if (!navigator.geolocation) {
      setError("此裝置不支援定位");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) =>
        setter(`${position.coords.latitude.toFixed(6)}, ${position.coords.longitude.toFixed(6)}`),
      () => setError("無法取得定位，請手動輸入地點"),
      { enableHighAccuracy: true, timeout: 8000 },
    );
  };

  const claim = async (match: Match) => {
    const evidence = window.prompt("請輸入只有失主知道的特徵（不會公開）");
    if (!evidence || evidence.trim().length < 3) return;
    const response = await fetch(`${API}/api/v1/matches/${match.id}/claims`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ line_user_id: lineUserId, private_evidence: evidence }),
    });
    if (response.ok) {
      setNotice("認領申請已送出，請等待校方核對。");
    } else {
      setError("認領申請無法送出，可能已有申請正在審核。");
    }
  };

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <div>
          <span className={styles.logoMark}>L</span>
          <strong>LostLink AI</strong>
        </div>
        <span className={styles.user}>嗨，{displayName}</span>
      </header>

      <main className={styles.main}>
        {notice && <button className={styles.notice} onClick={() => setNotice("")}>{notice}<b>×</b></button>}
        {error && <button className={styles.error} onClick={() => setError("")}>{error}<b>×</b></button>}

        {tab === "items" && (
          <>
            <section className={styles.hero}>
              <p>AI × LINE 校園協尋</p>
              <h1>遺失的物品，<br />我們一起找回來。</h1>
              <div>
                <button onClick={() => setTab("lost")}>我遺失物品</button>
                <button className={styles.lightButton} onClick={() => setTab("found")}>我撿到物品</button>
              </div>
            </section>
            <section className={styles.section}>
              <div className={styles.sectionTitle}>
                <div><small>FOUND ITEMS</small><h2>目前拾獲物</h2></div>
                <button onClick={() => void loadReports()}>重新整理</button>
              </div>
              <div className={styles.grid}>
                {reports.length === 0 && <p className={styles.empty}>目前沒有待認領物品。</p>}
                {reports.map((report) => (
                  <article className={styles.card} key={report.id}>
                    {report.image_url ? (
                      <button
                        type="button"
                        className={styles.photoButton}
                        onClick={() => setSelectedReport(report)}
                        aria-label={`查看 ${report.description || "拾獲物品"} 詳細資料`}
                      >
                        <div className={styles.photo}>
                        <img src={apiImage(report.image_url) ?? ""} alt="拾獲物品縮圖" />
                          <span className={styles.zoomHint}>查看詳細</span>
                          <em>待認領</em>
                        </div>
                      </button>
                    ) : (
                      <div className={styles.photo}>
                        <span>暫無照片</span>
                        <em>待認領</em>
                      </div>
                      )}
                    <div className={styles.cardBody}>
                      <h3>{[report.color, report.category].filter(Boolean).join(" ") || "待辨識物品"}</h3>
                      <p>⌖ {report.location || "地點由保管單位確認"}</p>
                      <p>◷ {formatTime(report.occurred_at || report.created_at)}</p>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          </>
        )}

        {tab === "found" && (
          <section className={styles.formPage}>
            <button className={styles.back} onClick={() => setTab("items")}>← 返回</button>
            <small>REPORT FOUND ITEM</small>
            <h1>登記拾獲物</h1>
            <p>拍下清楚照片並留下地點與時間，AI 會自動尋找可能的失主。</p>
            <form onSubmit={submitFound}>
              <label className={styles.camera}>
                {foundPreview ? <img src={foundPreview} alt="拾獲物預覽" /> : <><b>＋</b><span>拍照或選擇照片</span></>}
                <input type="file" accept="image/*" capture="environment" onChange={(event) => void onImage(event, setFoundImage, setFoundPreview)} />
              </label>
              <label>在哪裡撿到？<input value={foundLocation} onChange={(e) => setFoundLocation(e.target.value)} placeholder="例如：圖書館 2F 靠窗座位" /></label>
              <button type="button" className={styles.locationButton} onClick={() => useLocation(setFoundLocation)}>使用目前位置</button>
              <label>什麼時間撿到？<input type="datetime-local" value={foundTime} onChange={(e) => setFoundTime(e.target.value)} /></label>
              <label>補充描述（選填）<textarea value={foundDescription} onChange={(e) => setFoundDescription(e.target.value)} placeholder="例如：黑色耳機，有透明保護殼" /></label>
              <button className={styles.submit} disabled={loading}>{loading ? "AI 辨識中…" : "完成拾獲登記"}</button>
            </form>
          </section>
        )}

        {tab === "lost" && (
          <section className={styles.formPage}>
            <button className={styles.back} onClick={() => setTab("items")}>← 返回</button>
            <small>FIND MY ITEM</small>
            <h1>尋找遺失物</h1>
            <p>可以用文字描述、上傳過往照片，或兩者一起提供以提升準確率。</p>
            <form onSubmit={submitLost}>
              <label>描述遺失物<textarea value={lostDescription} onChange={(e) => setLostDescription(e.target.value)} placeholder="我的黑色 AirPods Pro 在圖書館不見了…" /></label>
              <label className={styles.camera}>
                {lostPreview ? <img src={lostPreview} alt="遺失物預覽" /> : <><b>＋</b><span>上傳物品照片（選填）</span></>}
                <input type="file" accept="image/*" capture="environment" onChange={(event) => void onImage(event, setLostImage, setLostPreview)} />
              </label>
              <label>可能遺失地點（選填）<input value={lostLocation} onChange={(e) => setLostLocation(e.target.value)} placeholder="例如：圖書館 3F" /></label>
              <button type="button" className={styles.locationButton} onClick={() => useLocation(setLostLocation)}>使用目前位置</button>
              <label>可能遺失時間（選填）<input type="datetime-local" value={lostTime} onChange={(e) => setLostTime(e.target.value)} /></label>
              <button className={styles.submit} disabled={loading}>{loading ? "AI 比對中…" : "開始 AI 比對"}</button>
            </form>

            {matches.length > 0 && (
              <section className={styles.results}>
                <small>POSSIBLE MATCHES</small>
                <h2>可能相符的物品</h2>
                {matchedItems.map(({ match, report }) => (
                  <article className={styles.match} key={match.id}>
                    {report.image_url && (
                      <button
                        type="button"
                        className={styles.matchPhotoButton}
                        onClick={() => setSelectedReport(report)}
                        aria-label="查看候選拾獲物詳細資料"
                      >
                        <img src={apiImage(report.image_url) ?? ""} alt="候選拾獲物" />
                      </button>
                    )}
                    <div>
                      <strong>{Math.round(match.score * 100)}% 可能相符</strong>
                      <h3>{[report.color, report.category].filter(Boolean).join(" ") || "拾獲物品"}</h3>
                      <p>{report.location || "地點由校方確認"} · {formatTime(report.occurred_at)}</p>
                      <ul>{match.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                      <button onClick={() => void claim(match)}>這可能是我的</button>
                    </div>
                  </article>
                ))}
              </section>
            )}
          </section>
        )}
      </main>

      {selectedReport && (
        <div className={styles.modalBackdrop} onClick={() => setSelectedReport(null)}>
          <section
            className={styles.detailModal}
            role="dialog"
            aria-modal="true"
            aria-labelledby="item-detail-title"
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              className={styles.modalClose}
              onClick={() => setSelectedReport(null)}
              aria-label="關閉詳細資料"
            >
              ×
            </button>
            {selectedReport.image_url && (
              <img
                className={styles.detailImage}
                src={apiImage(selectedReport.image_url) ?? ""}
                alt={selectedReport.description || "拾獲物品詳細照片"}
              />
            )}
            <div className={styles.detailBody}>
              <small>FOUND ITEM DETAILS</small>
              <h2 id="item-detail-title">
                {[selectedReport.color, selectedReport.category].filter(Boolean).join(" ") || "待辨識物品"}
              </h2>
              <p className={styles.detailDescription}>
                {selectedReport.description || "拾獲者尚未提供補充描述。"}
              </p>
+              <dl className={styles.detailList}>
                <div><dt>拾獲地點</dt><dd>{selectedReport.location || "由保管單位確認"}</dd></div>
                <div><dt>拾獲時間</dt><dd>{formatTime(selectedReport.occurred_at || selectedReport.created_at)}</dd></div>
                <div><dt>目前狀態</dt><dd>{selectedReport.status === "open" ? "待認領" : selectedReport.status}</dd></div>
                {selectedReport.brand && <div><dt>辨識品牌</dt><dd>{selectedReport.brand}</dd></div>}
              </dl>
              <button type="button" className={styles.modalDone} onClick={() => setSelectedReport(null)}>
                看完了
              </button>
            </div>
          </section>
        </div>
      )}

      <nav className={styles.nav}>
        <button className={tab === "items" ? styles.active : ""} onClick={() => setTab("items")}><b>⌂</b><span>拾獲物</span></button>
        <button className={tab === "found" ? styles.active : ""} onClick={() => setTab("found")}><b>＋</b><span>我要登記</span></button>
        <button className={tab === "lost" ? styles.active : ""} onClick={() => setTab("lost")}><b>⌕</b><span>尋找物品</span></button>
      </nav>
    </div>
  );
}
