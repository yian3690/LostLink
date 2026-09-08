import { NextRequest } from "next/server";

const backend = process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000";

function isLocal(request: NextRequest): boolean {
  const host = (request.headers.get("host") ?? "").split(":")[0].toLowerCase();
  return ["localhost", "127.0.0.1", "::1", "[::1]"].includes(host);
}

async function forward(request: NextRequest, method: "GET" | "POST") {
  if (!isLocal(request)) return Response.json({ detail: "Local access only" }, { status: 403 });
  const key = process.env.ADMIN_API_KEY;
  if (!key) return Response.json({ detail: "Admin key is not configured" }, { status: 503 });
  const query = method === "GET" ? request.nextUrl.search : "";
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
}

export async function GET(request: NextRequest) {
  return forward(request, "GET");
}

export async function POST(request: NextRequest) {
  return forward(request, "POST");
}
