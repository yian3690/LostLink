"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import styles from "./database.module.css";

type Report = {
  id: string;
  kind: "lost" | "found";
  status: string;
  description: string;
  category: string | null;
  color: string | null;
  distinctive_features: string[];
  location: string | null;
  occurred_at: string | null;
  created_at: string;
  image_url: string | null;
};

const API = process.env.NEXT_PUBLIC_API_URL ?? "";
const PAGE_SIZE = 8;

function formatDate(value: string | null): string {
  if (!value) return "未提供";
  return new Intl.DateTimeFormat("zh-TW", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

export default function DatabasePage() {
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState<"all" | "lost" | "found">("all");
  const [statusFilter, setStatusFilter] = useState<"all" | "open" | "returned">("all");
  const [page, setPage] = useState(1);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingDescription, setEditingDescription] = useState("");
  const [kind, setKind] = useState<"lost" | "found">("found");
  const [description, setDescription] = useState("");
  const [location, setLocation] = useState("");
  const [occurredAt, setOccurredAt] = useState("");
  const [imageBase64, setImageBase64] = useState("");
  const [imageName, setImageName] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/database/reports?limit=500", { cache: "no-store" });
      if (response.status === 403) throw new Error("資料庫管理僅限本機使用");
      if (!response.ok) throw new Error("無法讀取資料庫");
      setReports(await response.json());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "載入失敗");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const isLocal = ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
    setAllowed(isLocal);
    if (isLocal) void refresh();
  }, [refresh]);

  const visibleReports = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return reports.filter((report) => {
      if (kindFilter !== "all" && report.kind !== kindFilter) return false;
      if (statusFilter !== "all" && report.status !== statusFilter) return false;
      if (!needle) return true;
      return [report.id, report.description, report.location, report.category, report.color]
        .filter(Boolean)
        .some((value) => String(value).toLocaleLowerCase().includes(needle));
    });
  }, [kindFilter, query, reports, statusFilter]);
  const pageCount = Math.max(1, Math.ceil(visibleReports.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const pagedReports = visibleReports.slice(
    (safePage - 1) * PAGE_SIZE,
    safePage * PAGE_SIZE,
  );

  useEffect(() => setPage(1), [kindFilter, query, statusFilter]);

  const selectImage = (file: File | undefined) => {
    if (!file) { setImageBase64(""); setImageName(""); return; }
    if (!file.type.startsWith("image/")) { setError("請選擇照片檔案"); return; }
    if (file.size > 15 * 1024 * 1024) { setError("照片不可超過 15MB"); return; }
    const reader = new FileReader();
    reader.onload = () => {
      setImageBase64(String(reader.result).split(",", 2)[1] ?? "");
      setImageName(file.name);
      setError("");
    };
    reader.onerror = () => setError("無法讀取照片");
    reader.readAsDataURL(file);
  };

  const createReport = async (event: FormEvent) => {
    event.preventDefault();
    if (!description.trim() && !imageBase64) { setError("請輸入描述或選擇照片"); return; }
    setLoading(true);
    setError("");
    const response = await fetch("/api/database/reports", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind, description: description.trim(), location: location.trim() || null,
        occurred_at: occurredAt ? new Date(occurredAt).toISOString() : null,
        image_base64: imageBase64 || null,
      }),
    });
    if (response.ok) {
      setDescription(""); setLocation(""); setOccurredAt(""); setImageBase64(""); setImageName("");
      setNotice("資料已新增，AI 向量與配對也已建立。");
      await refresh();
    } else {
      setError(response.status === 403 ? "資料庫管理僅限本機使用" : "新增失敗");
      setLoading(false);
    }
  };

  const updateStatus = async (report: Report) => {
    const nextStatus = report.status === "returned" ? "open" : "returned";
    const label = nextStatus === "returned" ? "已認領" : "待認領";
    if (!window.confirm(`確定將「${report.description}」標註為${label}？`)) return;
    setLoading(true);
    const response = await fetch("/api/database/reports/manage", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: report.id, status: nextStatus }),
    });
    if (response.ok) {
      setNotice(`物品已標註為${label}。`);
      await refresh();
    } else {
      setError("狀態更新失敗");
      setLoading(false);
    }
  };

  const startEdit = (report: Report) => {
    setEditingId(report.id);
    setEditingDescription(report.description);
    setError("");
  };

  const saveDescription = async (reportId: string) => {
    if (!editingDescription.trim()) { setError("描述不可留空"); return; }
    setLoading(true);
    const response = await fetch("/api/database/reports/manage", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: reportId, description: editingDescription.trim() }),
    });
    if (response.ok) {
      setEditingId(null);
      setNotice("描述已更新，AI 特徵與所有配對已重新計算。");
      await refresh();
    } else {
      setError("修改失敗"); setLoading(false);
    }
  };

  const deleteReport = async (report: Report) => {
    const confirmed = window.confirm(
      `確定刪除這筆${report.kind === "found" ? "拾獲" : "遺失"}資料？\n\n${report.description}\n\n相關配對與照片也會一併刪除，無法復原。`,
    );
    if (!confirmed) return;
    setLoading(true);
    const response = await fetch("/api/database/reports/manage", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: report.id }),
    });
    if (response.ok) {
      setNotice("資料及其關聯配對已刪除。");
      await refresh();
    } else {
      setError("刪除失敗"); setLoading(false);
    }
  };

  if (allowed === null) return <main className={styles.center}>正在確認存取位置…</main>;
  if (!allowed) {
    return (
      <main className={styles.center}>
        <section className={styles.blocked}>
          <b>LOCAL ACCESS ONLY</b>
          <h1>資料庫管理頁僅限本機</h1>
          <p>請在執行 LostLink AI 的電腦開啟 http://localhost:3000/database。</p>
        </section>
      </main>
    );
  }

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div><p>LOSTLINK AI · LOCAL DATABASE</p><h1>資料庫管理</h1><span>此頁沒有出現在原本網站，且只允許本機操作。</span></div>
        <button onClick={() => void refresh()} disabled={loading}>重新整理</button>
      </header>

      {notice && <button className={styles.notice} onClick={() => setNotice("")}>{notice}<b>×</b></button>}
      {error && <button className={styles.error} onClick={() => setError("")}>{error}<b>×</b></button>}

      <section className={styles.createPanel}>
        <div><p>NEW RECORD</p><h2>新增資料</h2></div>
        <form onSubmit={createReport}>
          <label>資料類型
            <select value={kind} onChange={(event) => setKind(event.target.value as "lost" | "found")}>
              <option value="found">拾獲物</option><option value="lost">遺失通報</option>
            </select>
          </label>
          <label className={styles.wide}>物品描述
            <textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="例如：黑色無線耳機，附黑色充電盒" />
          </label>
          <label>地點<input value={location} onChange={(event) => setLocation(event.target.value)} placeholder="例如：圖書館 2F" /></label>
          <label>時間<input type="datetime-local" value={occurredAt} onChange={(event) => setOccurredAt(event.target.value)} /></label>
          <label>物品照片
            <span className={styles.filePicker}>
              <input
                className={styles.fileInput}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                onChange={(event) => selectImage(event.target.files?.[0])}
              />
              <span className={styles.fileButton}>＋ 選擇照片</span>
              <span className={imageName ? styles.fileName : styles.filePlaceholder}>
                {imageName || "JPG、PNG 或 WebP"}
              </span>
            </span>
          </label>
          <button className={styles.createButton} disabled={loading}>{loading ? "處理中…" : "新增並建立 AI 向量"}</button>
        </form>
      </section>

      <section className={styles.dataPanel}>
        <div className={styles.toolbar}>
          <div><p>ITEM REPORTS</p><h2>資料列表 <span>{visibleReports.length} / {reports.length}，每頁 {PAGE_SIZE} 筆</span></h2></div>
          <div className={styles.filters}>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜尋描述、地點或 ID" />
            <select value={kindFilter} onChange={(event) => setKindFilter(event.target.value as typeof kindFilter)}>
              <option value="all">全部類型</option><option value="found">只看拾獲</option><option value="lost">只看遺失</option>
            </select>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as typeof statusFilter)}>
              <option value="all">全部狀態</option><option value="open">待認領</option><option value="returned">已認領</option>
            </select>
          </div>
        </div>
        <div className={styles.tableWrap}>
          <table>
            <thead><tr><th>照片</th><th>類型／ID</th><th>描述</th><th>AI 特徵</th><th>地點／時間</th><th>操作</th></tr></thead>
            <tbody>
              {!loading && visibleReports.length === 0 && <tr><td colSpan={6} className={styles.empty}>沒有符合條件的資料。</td></tr>}
              {pagedReports.map((report) => (
                <tr key={report.id}>
                  <td>{report.image_url ? <a href={`${API}${report.image_url}`} target="_blank" rel="noreferrer"><img className={styles.thumbnail} src={`${API}${report.image_url}`} alt="物品照片" /></a> : <span className={styles.noPhoto}>無照片</span>}</td>
                  <td>
                    <b className={report.kind === "found" ? styles.found : styles.lost}>{report.kind === "found" ? "拾獲" : "遺失"}</b>
                    <b className={report.status === "returned" ? styles.returned : styles.open}>{report.status === "returned" ? "已認領" : report.status === "claim_pending" ? "認領審核中" : "待認領"}</b>
                    <code title={report.id}>{report.id.slice(0, 8)}</code>
                  </td>
                  <td className={styles.description}>
                    {editingId === report.id ? <textarea value={editingDescription} onChange={(event) => setEditingDescription(event.target.value)} autoFocus /> : report.description}
                  </td>
                  <td className={styles.aiFeatures}>
                    <strong>{[report.color, report.category].filter(Boolean).join(" · ") || "待辨識"}</strong>
                    {report.distinctive_features.length > 0 && <span>{report.distinctive_features.join("、")}</span>}
                  </td>
                  <td><strong>{report.location || "未提供"}</strong><span>{formatDate(report.occurred_at || report.created_at)}</span></td>
                  <td><div className={styles.rowActions}>
                    {editingId === report.id ? (
                      <><button className={styles.save} onClick={() => void saveDescription(report.id)} disabled={loading}>儲存</button><button className={styles.cancel} onClick={() => setEditingId(null)}>取消</button></>
                    ) : (
                      <><button className={styles.edit} onClick={() => startEdit(report)}>修改描述</button>{report.kind === "found" && <button className={report.status === "returned" ? styles.reopen : styles.claimed} onClick={() => void updateStatus(report)}>{report.status === "returned" ? "恢復待認領" : "標記已領取"}</button>}<button className={styles.delete} onClick={() => void deleteReport(report)}>刪除</button></>
                    )}
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {visibleReports.length > PAGE_SIZE && (
          <nav className={styles.pagination} aria-label="資料分頁">
            <button disabled={safePage === 1 || loading} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一頁</button>
            <span>第 {safePage} / {pageCount} 頁</span>
            <button disabled={safePage === pageCount || loading} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>下一頁</button>
          </nav>
        )}
      </section>
    </main>
  );
}
