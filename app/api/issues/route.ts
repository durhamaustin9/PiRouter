import { NextResponse } from "next/server";
import { collectPiStatus } from "@/lib/status";
import { dismissIssue } from "@/lib/status/issues";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const origin = request.headers.get("origin");
  if (origin) {
    const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host") ?? new URL(request.url).host;
    let originHost: string | null = null;
    try { originHost = new URL(origin).host; } catch { /* Reject malformed origins. */ }
    if (originHost !== host) {
      return NextResponse.json({ error: "Invalid request origin" }, { status: 403 });
    }
  }
  try {
    const body = await request.json();
    if (!body || typeof body.id !== "string" || typeof body.firstSeenAt !== "string" || ![0, 1, 24, 168].includes(body.hours)) {
      return NextResponse.json({ error: "Invalid alert action" }, { status: 400 });
    }
    await collectPiStatus();
    await dismissIssue(body.id, body.firstSeenAt, body.hours);
    return NextResponse.json(await collectPiStatus());
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Unable to update alert" }, { status: 409 });
  }
}
