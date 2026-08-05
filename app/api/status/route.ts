import { NextResponse } from "next/server";
import { recordSnapshot } from "@/lib/history";
import { collectPiStatus } from "@/lib/status";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const status = await collectPiStatus();
    await recordSnapshot(status);
    return NextResponse.json(status, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    console.error("Status collection failed", error);
    return NextResponse.json(
      { error: "Status collection failed" },
      { status: 500 },
    );
  }
}
