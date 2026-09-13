import { NextRequest } from "next/server";

const backend = process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000";
const idPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isLocal(request: NextRequest): boolean {
  const hostHeader = (request.headers.get("host") ?? "").trim().toLowerCase();
  const host = hostHeader.startsWith("[")
    ? hostHeader.slice(1, hostHeader.indexOf("]"))
    : hostHeader.split(":")[0];
  return ["localhost", "127.0.0.1", "::1"].includes(host);
}

async function forward(request: NextRequest, method: "PATCH" | "DELETE") {
  if (!isLocal(request)) return Response.json({ detail: "Local access only" }, { status: 403 });
  const key = process.env.ADMIN_API_KEY;
  if (!key) return Response.json({ detail: "Admin key is not configured" }, { status: 503 });
  const payload = await request.json();
  if (typeof payload.id !== "string" || !idPattern.test(payload.id)) {
    return Response.json({ detail: "Invalid report ID" }, { status: 422 });
  }
  const isStatusUpdate =
    method === "PATCH" && ["open", "returned"].includes(payload.status);
  if (
    method === "PATCH" &&
    !isStatusUpdate &&
    (typeof payload.description !== "string" || !payload.description.trim())
  ) return Response.json({ detail: "Description or status is required" }, { status: 422 });
  const response = await fetch(
    `${backend}/api/v1/admin/reports/${encodeURIComponent(payload.id)}${isStatusUpdate ? "/status" : ""}`,
    {
      method,
      headers: {
        "X-Admin-Key": key,
        ...(method === "PATCH" ? { "Content-Type": "application/json" } : {}),
      },
      body: method === "PATCH"
        ? JSON.stringify(
            isStatusUpdate
              ? { status: payload.status }
              : {
                  description: payload.description.trim(),
                  location: typeof payload.location === "string" ? payload.location.trim() || null : null,
                  occurred_at: typeof payload.occurred_at === "string" ? payload.occurred_at : null,
                  category: typeof payload.category === "string" ? payload.category.trim() || null : null,
                  brand: typeof payload.brand === "string" ? payload.brand.trim() || null : null,
                  color: typeof payload.color === "string" ? payload.color.trim() || null : null,
                  distinctive_features: Array.isArray(payload.distinctive_features)
                    ? payload.distinctive_features.filter((value: unknown): value is string => typeof value === "string").slice(0, 30)
                    : [],
                },
          )
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

export async function GET(request: NextRequest) {
  if (!isLocal(request)) return Response.json({ detail: "Local access only" }, { status: 403 });
  const key = process.env.ADMIN_API_KEY;
  if (!key) return Response.json({ detail: "Admin key is not configured" }, { status: 503 });
  const id = request.nextUrl.searchParams.get("image") ?? "";
  if (!idPattern.test(id)) return Response.json({ detail: "Invalid report ID" }, { status: 422 });
  const response = await fetch(`${backend}/api/v1/admin/reports/${encodeURIComponent(id)}/image`, {
    headers: { "X-Admin-Key": key },
    cache: "no-store",
  });
  return new Response(response.body, {
    status: response.status,
    headers: {
      "Content-Type": response.headers.get("content-type") ?? "application/octet-stream",
      "Cache-Control": "private, max-age=300",
    },
  });
}

export async function PATCH(request: NextRequest) {
  return forward(request, "PATCH");
}

export async function DELETE(request: NextRequest) {
  return forward(request, "DELETE");
}
