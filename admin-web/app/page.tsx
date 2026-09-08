"use client";

import { useCallback, useEffect, useState } from "react";

type Stats = {
  open_lost: number;
  open_found: number;
  high_confidence_matches: number;
  returned_items: number;
  return_rate: number;
};

type Report = {
  id: string;
  kind: "lost" | "found";
  status: string;
  description: string;
  category: string | null;
  color: string | null;
  location: string | null;
  created_at: string;
};

const API = process.env.NEXT_PUBLIC_API_URL ?? "";

const EMPTY_STATS: Stats = {
  open_lost: 0,
  open_found: 0,
  high_confidence_matches: 0,
  returned_items: 0,
  return_rate: 0,
};

export default function Dashboard() {
  const [stats, setStats] = useState<Stats>(EMPTY_STATS);
  const [reports, setReports] = useState<Report[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [statsResponse, reportsResponse] = await Promise.all([
        fetch(`${API}/api/v1/dashboard/stats`, { cache: "no-store" }),
        fetch(`${API}/api/v1/reports?limit=12`, { cache: "no-store" }),
      ]);
      if (!statsResponse.ok || !reportsResponse.ok) {
        throw new Error("API 尚未就緒");
      }
      setStats(await statsResponse.json());
      setReports(await reportsResponse.json());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "無法載入資料");
    } finally {
      setLoading(false);
    }
  }, []);

  const seedDemo = async () => {
    setError("");
    const response = await fetch(`${API}/api/v1/demo/seed`, { method: "POST" });
    if (!response.ok) {
      setError("無法建立示範資料");
      return;
    }
    await refresh();
  };

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const cards = [
    ["開放報失", stats.open_lost, "等待配對"],
    ["拾獲待領", stats.open_found, "校方保管"],
    ["高可信配對", stats.high_confidence_matches, "建議通知"],
    ["已成功領回", stats.returned_items, `${(stats.return_rate * 100).toFixed(0)}% 領回率`],
  ];

  return (
    <main>
      <header>
        <div>
          <p className="eyebrow">LOSTLINK AI · CAMPUS CONSOLE</p>
          <h1>讓每件遺失物，<span>更快找到回家的路。</span></h1>
          <p className="lede">整合 LINE、中文語意與圖像特徵，協助校方自動篩選可能配對。</p>
        </div>
        <div className="actions">
          <a className="mobileLink" href="/mobile">開啟手機前台</a>
          <button className="secondary" onClick={() => void refresh()}>重新整理</button>
          <button onClick={() => void seedDemo()}>建立示範案件</button>
        </div>
      </header>

      {error && <div className="notice">{error}，請先啟動 FastAPI 後端。</div>}

      <section className="metrics" aria-label="營運指標">
        {cards.map(([label, value, note]) => (
          <article className="metric" key={String(label)}>
            <p>{label}</p>
            <strong>{loading ? "—" : value}</strong>
            <small>{note}</small>
          </article>
        ))}
      </section>

      <section className="panel">
        <div className="panelHeading">
          <div>
            <p className="eyebrow">LIVE REPORTS</p>
            <h2>最新案件</h2>
          </div>
          <span className="status"><i />AI 配對服務正常</span>
        </div>

        <div className="tableWrap">
          <table>
            <thead>
              <tr>
                <th>案件</th>
                <th>物品描述</th>
                <th>特徵</th>
                <th>地點</th>
                <th>狀態</th>
              </tr>
            </thead>
            <tbody>
              {!loading && reports.length === 0 && (
                <tr><td colSpan={5} className="empty">尚無案件，建立示範資料開始體驗。</td></tr>
              )}
              {reports.map((report) => (
                <tr key={report.id}>
                  <td>
                    <span className={`kind ${report.kind}`}>
                      {report.kind === "lost" ? "遺失" : "拾獲"}
                    </span>
                    <code>{report.id.slice(0, 8)}</code>
                  </td>
                  <td className="description">{report.description}</td>
                  <td>{[report.color, report.category].filter(Boolean).join(" · ") || "待辨識"}</td>
                  <td>{report.location || "未提供"}</td>
                  <td><span className="open">{report.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
