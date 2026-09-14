"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { itemDisplayName } from "../lib/itemLabels";
import styles from "./mobile.module.css";

type Tab = "items" | "found" | "lost" | "mine";

type Report = {
  id: string;
  kind: "lost" | "found";
  status: string;
  description: string;
  category: string | null;
  brand: string | null;
  color: string | null;
  distinctive_features: string[];
  campus: string | null;
  location: string | null;
  occurred_at: string | null;
  created_at: string;
  image_url: string | null;
};

type Match = {
  id: string;
  lost_report_id: string;
  found_report_id: string;
  score: number;
  decision: string;
  reasons: string[];
};

type ReportCreated = {
  report: Report;
  matches: Match[];
};

type OwnerReportResolved = {
  lost_report: Report;
  found_report: Report | null;
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

function toDateTimeInput(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function PrivateReportImage({ reportId, accessToken, revision = 0 }: { reportId: string; accessToken: string; revision?: number }) {
  const [source, setSource] = useState("");

  useEffect(() => {
    let objectUrl = "";
    const controller = new AbortController();
    void fetch(`${API}/api/v1/reports/${reportId}/owner-image?v=${revision}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      signal: controller.signal,
      cache: "no-store",
    }).then(async (response) => {
      if (!response.ok) return;
      objectUrl = URL.createObjectURL(await response.blob());
      setSource(objectUrl);
    }).catch(() => undefined);
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [accessToken, reportId, revision]);

  return source ? <img src={source} alt="我的遺失物照片" /> : <span>載入照片中…</span>;
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
  const [myReports, setMyReports] = useState<Report[]>([]);
  const [matches, setMatches] = useState<Match[]>([]);
  const [searchPerformed, setSearchPerformed] = useState(false);
  const [lineUserId, setLineUserId] = useState("web-guest");
  const [lineAccessToken, setLineAccessToken] = useState("");
  const [displayName, setDisplayName] = useState("同學");
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [selectedReport, setSelectedReport] = useState<Report | null>(null);
  const [claimingMatch, setClaimingMatch] = useState<Match | null>(null);
  const [claimEvidence, setClaimEvidence] = useState("");
  const [claimError, setClaimError] = useState("");
  const [claimSubmitting, setClaimSubmitting] = useState(false);
  const [editingMyReport, setEditingMyReport] = useState<Report | null>(null);
  const [myEditDescription, setMyEditDescription] = useState("");
  const [myEditLocation, setMyEditLocation] = useState("");
  const [myEditTime, setMyEditTime] = useState("");
  const [myEditImage, setMyEditImage] = useState("");
  const [myEditPreview, setMyEditPreview] = useState("");
  const [imageRevisions, setImageRevisions] = useState<Record<string, number>>({});
  const [myEditSaving, setMyEditSaving] = useState(false);
  const [myReportMatches, setMyReportMatches] = useState<Match[]>([]);
  const [showResolvePanel, setShowResolvePanel] = useState(false);
  const [resolveChoice, setResolveChoice] = useState("");
  const [myResolving, setMyResolving] = useState(false);

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
      setReports(await response.json());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "讀取失敗");
    }
  }, []);

  const loadMyReports = useCallback(async (accessToken: string) => {
    if (!accessToken) return;
    try {
      const response = await fetch(`${API}/api/v1/reports/mine?kind=lost&limit=100`, {
        headers: { Authorization: `Bearer ${accessToken}` },
        cache: "no-store",
      });
      if (!response.ok) throw new Error("目前無法讀取你的協尋案件");
      setMyReports(await response.json());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "讀取協尋案件失敗");
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
      const accessToken = liff.getAccessToken() ?? "";
      setLineUserId(profile.userId);
      setDisplayName(profile.displayName);
      setLineAccessToken(accessToken);
      if (accessToken) void loadMyReports(accessToken);
    }).catch(() => setError("LINE 登入初始化失敗，仍可使用網頁 Demo。"));
  }, [loadMyReports, loadReports]);

  useEffect(() => {
    if (!selectedReport && !claimingMatch && !editingMyReport) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (selectedReport) setSelectedReport(null);
      if (claimingMatch && !claimSubmitting) {
        setClaimingMatch(null);
        setClaimEvidence("");
        setClaimError("");
      }
      if (editingMyReport && !myEditSaving && !myResolving) {
        setEditingMyReport(null);
        setShowResolvePanel(false);
        setResolveChoice("");
      }
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [claimSubmitting, claimingMatch, editingMyReport, myEditSaving, myResolving, selectedReport]);

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
  const availableReports = useMemo(
    () => reports.filter((item) => item.status !== "returned"),
    [reports],
  );
  const claimedReports = useMemo(
    () => reports.filter((item) => item.status === "returned"),
    [reports],
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
    setSearchPerformed(false);
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
      setSearchPerformed(true);
      setNotice(
        result.matches.length
          ? `找到 ${result.matches.length} 個可能相符的物品`
          : "目前沒有高相似候選，系統會在新物品登記後繼續比對。",
      );
      await loadReports();
      if (lineAccessToken) await loadMyReports(lineAccessToken);
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

  const openClaim = (match: Match) => {
    setClaimingMatch(match);
    setClaimEvidence("");
    setClaimError("");
  };

  const closeClaim = () => {
    if (claimSubmitting) return;
    setClaimingMatch(null);
    setClaimEvidence("");
    setClaimError("");
  };

  const claim = async (event: FormEvent) => {
    event.preventDefault();
    if (!claimingMatch) return;
    const evidence = claimEvidence.trim();
    if (evidence.length < 3) {
      setClaimError("請至少輸入 3 個字，讓校方能核對物品。");
      return;
    }
    setClaimSubmitting(true);
    setClaimError("");
    try {
      const response = await fetch(`${API}/api/v1/matches/${claimingMatch.id}/claims`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ line_user_id: lineUserId, private_evidence: evidence }),
      });
      if (!response.ok) throw new Error("認領申請無法送出，可能已有申請正在審核。");
      setClaimingMatch(null);
      setClaimEvidence("");
      setNotice("認領申請已送出，請等待校方核對。");
    } catch (reason) {
      setClaimError(reason instanceof Error ? reason.message : "認領申請無法送出，請稍後再試。");
    } finally {
      setClaimSubmitting(false);
    }
  };

  const openMyReportEditor = async (report: Report) => {
    setEditingMyReport(report);
    setMyEditDescription(report.description);
    setMyEditLocation(report.location ?? "");
    setMyEditTime(toDateTimeInput(report.occurred_at));
    setMyEditImage("");
    setMyEditPreview("");
    setMyReportMatches([]);
    setShowResolvePanel(false);
    setResolveChoice("");
    setError("");
    if (report.status === "returned") return;
    try {
      const response = await fetch(`${API}/api/v1/reports/${report.id}/matches`, {
        cache: "no-store",
      });
      if (response.ok) setMyReportMatches(await response.json());
    } catch {
      // Editing remains available even if candidate loading temporarily fails.
    }
  };

  const saveMyReport = async (event: FormEvent) => {
    event.preventDefault();
    if (!editingMyReport || !lineAccessToken) return;
    if (!myEditDescription.trim()) {
      setError("物品描述不可留空");
      return;
    }
    setMyEditSaving(true);
    try {
      const response = await fetch(`${API}/api/v1/reports/${editingMyReport.id}/mine`, {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${lineAccessToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          description: myEditDescription.trim(),
          location: myEditLocation.trim() || null,
          occurred_at: myEditTime ? new Date(myEditTime).toISOString() : null,
          image_base64: myEditImage || null,
        }),
      });
      if (!response.ok) throw new Error("案件更新失敗，請重新從 LINE 開啟後再試");
      const result: ReportCreated = await response.json();
      setMyReports((items) => items.map((item) => item.id === result.report.id ? result.report : item));
      if (myEditImage) {
        setImageRevisions((current) => ({
          ...current,
          [result.report.id]: (current[result.report.id] ?? 0) + 1,
        }));
      }
      setEditingMyReport(null);
      setNotice("協尋資料已更新，AI 特徵與候選配對也已重新計算。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "案件更新失敗");
    } finally {
      setMyEditSaving(false);
    }
  };

  const resolveMyReport = async () => {
    if (!editingMyReport || !lineAccessToken || !resolveChoice) return;
    setMyResolving(true);
    setError("");
    try {
      const response = await fetch(`${API}/api/v1/reports/${editingMyReport.id}/mine/resolve`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${lineAccessToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          found_report_id: resolveChoice === "self" ? null : resolveChoice,
        }),
      });
      if (!response.ok) throw new Error("案件結案失敗，請重新整理後再試");
      const result: OwnerReportResolved = await response.json();
      setMyReports((items) => items.map((item) => (
        item.id === result.lost_report.id ? result.lost_report : item
      )));
      if (result.found_report) {
        setReports((items) => items.map((item) => (
          item.id === result.found_report?.id ? result.found_report : item
        )));
      }
      setEditingMyReport(null);
      setShowResolvePanel(false);
      setResolveChoice("");
      setNotice(result.found_report
        ? "已完成結案，協尋案件已關閉，對應拾獲物也已改為已認領。"
        : "已完成結案，這筆持續協尋已停止。"
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "案件結案失敗");
    } finally {
      setMyResolving(false);
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
                {availableReports.length === 0 && <p className={styles.empty}>目前沒有待認領物品。</p>}
                {availableReports.map((report) => (
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
                          <em>{report.status === "claim_pending" ? "認領審核中" : "待認領"}</em>
                        </div>
                      </button>
                    ) : (
                      <div className={styles.photo}>
                        <span>暫無照片</span>
                        <em>{report.status === "claim_pending" ? "認領審核中" : "待認領"}</em>
                      </div>
                      )}
                    <div className={styles.cardBody}>
                      <h3>{itemDisplayName(report)}</h3>
                      <p>⌖ {report.location || "地點由保管單位確認"}</p>
                      <p>◷ {formatTime(report.occurred_at || report.created_at)}</p>
                    </div>
                  </article>
                ))}
              </div>
            </section>
            <section className={styles.section}>
              <div className={styles.sectionTitle}>
                <div><small>CLAIMED ITEMS</small><h2>已認領物品</h2></div>
              </div>
              <div className={styles.grid}>
                {claimedReports.length === 0 && <p className={styles.empty}>目前還沒有已認領物品。</p>}
                {claimedReports.map((report) => (
                  <article className={`${styles.card} ${styles.claimedCard}`} key={report.id}>
                    {report.image_url ? (
                      <button
                        type="button"
                        className={styles.photoButton}
                        onClick={() => setSelectedReport(report)}
                        aria-label={`查看 ${report.description || "已認領物品"} 詳細資料`}
                      >
                        <div className={styles.photo}>
                          <img src={apiImage(report.image_url) ?? ""} alt="已認領物品縮圖" />
                          <span className={styles.zoomHint}>查看詳細</span>
                          <em className={styles.claimedBadge}>已認領</em>
                        </div>
                      </button>
                    ) : (
                      <div className={styles.photo}><span>暫無照片</span><em className={styles.claimedBadge}>已認領</em></div>
                    )}
                    <div className={styles.cardBody}>
                      <h3>{itemDisplayName(report)}</h3>
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

            {searchPerformed && matches.length === 0 && (
              <section className={styles.noResults} role="status">
                <span>⌕</span>
                <h2>目前無符合的物品</h2>
                <p>這次沒有找到相似的拾獲物。案件已保留，之後有新的物品登記時，系統會繼續比對。</p>
              </section>
            )}

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
                      <h3>{itemDisplayName(report)}</h3>
                      <p>{report.location || "地點由校方確認"} · {formatTime(report.occurred_at)}</p>
                      <ul>{match.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                      <button onClick={() => openClaim(match)}>這可能是我的</button>
                    </div>
                  </article>
                ))}
              </section>
            )}
          </section>
        )}

        {tab === "mine" && (
          <section className={styles.formPage}>
            <button className={styles.back} onClick={() => setTab("items")}>← 返回</button>
            <small>MY SEARCHES</small>
            <h1>我的持續協尋</h1>
            <p>查看你透過 LINE 或此頁建立的遺失案件與目前處理狀態。</p>
            {!lineAccessToken && <p className={styles.empty}>請從 LINE 開啟 LostLink，登入後即可查看自己的案件。</p>}
            {lineAccessToken && myReports.length === 0 && <p className={styles.empty}>目前沒有持續協尋案件。</p>}
            {lineAccessToken && myReports.length > 0 && (
              <div className={styles.grid}>
                {myReports.map((report) => (
                  <button type="button" className={styles.myCaseButton} key={report.id} onClick={() => void openMyReportEditor(report)}>
                    <article className={styles.card}>
                      <div className={styles.photo}>
                        {report.image_url
                          ? <PrivateReportImage reportId={report.id} accessToken={lineAccessToken} revision={imageRevisions[report.id]} />
                          : <span>沒有提供照片</span>}
                        <em>{report.status === "returned" ? "已結案" : report.status === "claim_pending" ? "認領確認中" : "持續協尋中"}</em>
                      </div>
                      <div className={styles.cardBody}>
                        <h3>{itemDisplayName(report)}</h3>
                        <p>⌖ {report.location || "遺失地點未提供"}</p>
                        <p>◷ {formatTime(report.occurred_at)}</p>
                        <p title={report.description}>{report.description}</p>
                        <span className={styles.editHint}>點擊查看與修改</span>
                      </div>
                    </article>
                  </button>
                ))}
              </div>
            )}
          </section>
        )}
      </main>

      {editingMyReport && (
        <div className={styles.modalBackdrop} onClick={() => !myEditSaving && setEditingMyReport(null)}>
          <section className={styles.myEditModal} role="dialog" aria-modal="true" aria-labelledby="my-edit-title" onClick={(event) => event.stopPropagation()}>
            <button type="button" className={styles.claimClose} onClick={() => setEditingMyReport(null)} disabled={myEditSaving} aria-label="關閉案件編輯">×</button>
            <small>EDIT MY SEARCH</small>
            <h2 id="my-edit-title">修改持續協尋資料</h2>
            <p>儲存後會更新資料庫，並依新描述重新建立 AI 特徵及候選配對。</p>
            <form onSubmit={saveMyReport}>
              <label>物品照片
                <span className={styles.myEditImagePicker}>
                  {myEditPreview
                    ? <img src={myEditPreview} alt="新的遺失物照片預覽" />
                    : editingMyReport.image_url
                      ? <PrivateReportImage reportId={editingMyReport.id} accessToken={lineAccessToken} revision={imageRevisions[editingMyReport.id]} />
                      : <span>目前沒有照片</span>}
                  <b>{editingMyReport.image_url ? "選擇新照片替換" : "新增照片"}</b>
                  <input
                    type="file"
                    accept="image/*"
                    onChange={(event) => void onImage(event, setMyEditImage, setMyEditPreview)}
                    disabled={myEditSaving}
                  />
                </span>
                <small className={styles.myEditImageHint}>選擇新照片後才會替換原圖；未選擇時會保留目前照片。</small>
              </label>
              <label>物品描述<textarea value={myEditDescription} onChange={(event) => setMyEditDescription(event.target.value)} maxLength={2000} /></label>
              <label>遺失地點<input value={myEditLocation} onChange={(event) => setMyEditLocation(event.target.value)} placeholder="例如：ZB301 教室" maxLength={240} /></label>
              <label>遺失時間<input type="datetime-local" value={myEditTime} onChange={(event) => setMyEditTime(event.target.value)} /></label>
              <div className={styles.claimActions}>
                <button type="button" className={styles.claimCancel} onClick={() => setEditingMyReport(null)} disabled={myEditSaving}>取消</button>
                <button type="submit" className={styles.claimSubmit} disabled={myEditSaving}>{myEditSaving ? "重新分析中…" : "儲存並重新比對"}</button>
              </div>
            </form>
            {editingMyReport.status !== "returned" && (
              <section className={styles.resolveSection}>
                {!showResolvePanel ? (
                  <button type="button" className={styles.resolveStart} onClick={() => setShowResolvePanel(true)}>我已找到物品</button>
                ) : (
                  <>
                    <h3>確認物品如何找回</h3>
                    <p>請選擇實際領回的待認領物；若是在其他地方自行找到，只會停止這筆協尋。</p>
                    <div className={styles.resolveOptions}>
                      {myReportMatches.map((match) => {
                        const found = reports.find((item) => item.id === match.found_report_id);
                        if (!found || found.status === "returned") return null;
                        return (
                          <label key={match.id}>
                            <input type="radio" name="resolve-source" value={found.id} checked={resolveChoice === found.id} onChange={(event) => setResolveChoice(event.target.value)} />
                            <span><b>{itemDisplayName(found)}</b><small>{found.location || "地點未提供"} · 相似度 {Math.round(match.score * 100)}%</small></span>
                          </label>
                        );
                      })}
                      <label>
                        <input type="radio" name="resolve-source" value="self" checked={resolveChoice === "self"} onChange={(event) => setResolveChoice(event.target.value)} />
                        <span><b>我在其他地方自行找到</b><small>只停止協尋，不修改任何待認領物</small></span>
                      </label>
                    </div>
                    <div className={styles.resolveActions}>
                      <button type="button" className={styles.claimCancel} onClick={() => { setShowResolvePanel(false); setResolveChoice(""); }} disabled={myResolving}>返回</button>
                      <button type="button" className={styles.resolveConfirm} onClick={() => void resolveMyReport()} disabled={!resolveChoice || myResolving}>{myResolving ? "更新中…" : "確認已找到並結案"}</button>
                    </div>
                  </>
                )}
              </section>
            )}
          </section>
        </div>
      )}

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
                {itemDisplayName(selectedReport)}
              </h2>
              <p className={styles.detailDescription}>
                {selectedReport.description || "拾獲者尚未提供補充描述。"}
              </p>
              <dl className={styles.detailList}>
                <div><dt>拾獲地點</dt><dd>{selectedReport.location || "由保管單位確認"}</dd></div>
                <div><dt>拾獲時間</dt><dd>{formatTime(selectedReport.occurred_at || selectedReport.created_at)}</dd></div>
                <div><dt>目前狀態</dt><dd>{selectedReport.status === "returned" ? "已認領" : selectedReport.status === "claim_pending" ? "認領審核中" : "待認領"}</dd></div>
                {selectedReport.brand && <div><dt>辨識品牌</dt><dd>{selectedReport.brand}</dd></div>}
                {selectedReport.distinctive_features.length > 0 && (
                  <div><dt>外觀特色</dt><dd>{selectedReport.distinctive_features.join("、")}</dd></div>
                )}
              </dl>
              <button type="button" className={styles.modalDone} onClick={() => setSelectedReport(null)}>
                看完了
              </button>
            </div>
          </section>
        </div>
      )}

      {claimingMatch && (
        <div className={styles.modalBackdrop} onClick={closeClaim}>
          <section
            className={styles.claimModal}
            role="dialog"
            aria-modal="true"
            aria-labelledby="claim-title"
            onClick={(event) => event.stopPropagation()}
          >
            <button type="button" className={styles.claimClose} onClick={closeClaim} aria-label="關閉認領申請">×</button>
            <span className={styles.claimIcon} aria-hidden="true">✓</span>
            <small>PRIVATE VERIFICATION</small>
            <h2 id="claim-title">確認這是你的物品嗎？</h2>
            <p>請提供一項只有你知道的特徵，校方會用來核對身分，內容不會公開。</p>
            <form onSubmit={claim}>
              <label htmlFor="private-evidence">私密辨識特徵</label>
              <textarea
                id="private-evidence"
                value={claimEvidence}
                onChange={(event) => {
                  setClaimEvidence(event.target.value);
                  if (claimError) setClaimError("");
                }}
                placeholder="例如：傘柄內側刻有名字，或保護殼右下角有一道刮痕"
                maxLength={300}
                autoFocus
              />
              <div className={styles.claimMeta}>
                <span>{claimError || "請勿填寫密碼、身分證字號等敏感資料"}</span>
                <b>{claimEvidence.length}/300</b>
              </div>
              <div className={styles.claimActions}>
                <button type="button" className={styles.claimCancel} onClick={closeClaim} disabled={claimSubmitting}>取消</button>
                <button type="submit" className={styles.claimSubmit} disabled={claimSubmitting}>
                  {claimSubmitting ? "送出中…" : "送出認領申請"}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}

      <nav className={styles.nav}>
        <button className={tab === "items" ? styles.active : ""} onClick={() => setTab("items")}><b>⌂</b><span>拾獲物</span></button>
        <button className={tab === "found" ? styles.active : ""} onClick={() => setTab("found")}><b>＋</b><span>我要登記</span></button>
        <button className={tab === "lost" ? styles.active : ""} onClick={() => setTab("lost")}><b>⌕</b><span>尋找物品</span></button>
        <button className={tab === "mine" ? styles.active : ""} onClick={() => { setTab("mine"); if (lineAccessToken) void loadMyReports(lineAccessToken); }}><b>◎</b><span>我的協尋</span></button>
      </nav>
    </div>
  );
}
