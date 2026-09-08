import { NextRequest } from "next/server";

const backend = process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000";
const idPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isLocal(request: NextRequest): boolean {
  const host = (request.headers.get("host") ?? "").split(":")[0].toLowerCase();
  return ["localhost", "127.0.0.1", "::1", "[::1]"].includes(host);
}

async function forward(request: NextRequest, method: "PATCH" | "DELETE") {
  if (!isLocal(request)) return Response.json({ detail: "Local access only" }, { status: 403 });
  const key = process.env.ADMIN_API_KEY;
  if (!key) return Response.json({ detail: "Admin key is not configured" }, { status: 503 });
  const payload = await request.json();
  if (typeof payload.id !== "string" || !idPattern.test(payload.id)) {
    return Response.json({ detail: "Invalid report ID" }, { status: 422 });
  }
  if (method === "PATCH" && (typeof payload.description !== "string" || !payload.description.trim())) {
    return Response.json({ detail: "Description is required" }, { status: 422 });
  }
  const response = await fetch(
    `${backend}/api/v1/admin/reports/${encodeURIComponent(payload.id)}`,
    {
      method,
      headers: {
        "X-Admin-Key": key,
        ...(method === "PATCH" ? { "Content-Type": "application/json" } : {}),
      },
      body: method === "PATCH"
        ? JSON.stringify({ description: payload.description.trim() })
        : undefined,
      cache: "no-store",
    },
  );
  return new Response(response.body, {
    status: response.status,
    headers: response.status === 204
      ? {}
      : { "Content-Type": response.headers.get("content-type") ?? "application/json" },
  });
}

export async function PATCH(request: NextRequest) {
  return forward(request, "PATCH");
}

export async function DELETE(request: NextRequest) {
  return forward(request, "DELETE");
}
