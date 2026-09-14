import { NextRequest } from "next/server";

const backend = process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000";

function isLocal(request: NextRequest): boolean {
  const hostHeader = (request.headers.get("host") ?? "").trim().toLowerCase();
  const host = hostHeader.startsWith("[")
    ? hostHeader.slice(1, hostHeader.indexOf("]"))
    : hostHeader.split(":")[0];
  return ["localhost", "127.0.0.1", "::1"].includes(host);
}

async function forward(request: NextRequest, method: "GET" | "POST") {
  if (!isLocal(request)) return Response.json({ detail: "Local access only" }, { status: 403 });
  const key = process.env.ADMIN_API_KEY;
  if (!key) return Response.json({ detail: "Admin key is not configured" }, { status: 503 });
  const query = method === "GET" ? request.nextUrl.search : "";
  try {
    const response = await fetch(`${backend}/api/v1/admin/reports${query}`, {
      method,
      headers: {
        "X-Admin-Key": key,
        ...(method === "POST" ? { "Content-Type": "application/json" } : {}),
      },
      body: method === "POST" ? await request.text() : undefined,
      cache: "no-store",
    });
    return new Response(response.body, {
      status: response.status,
      headers: { "Content-Type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return Response.json(
      { detail: "LostLink 後端尚未就緒，請稍後重新整理" },
      { status: 503 },
    );
  }
}

export async function GET(request: NextRequest) {
  return forward(request, "GET");
}

export async function POST(request: NextRequest) {
  return forward(request, "POST");
}
