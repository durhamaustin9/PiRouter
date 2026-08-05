import { NextRequest, NextResponse } from "next/server";
import { getHistory } from "@/lib/history";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const requestedLimit = Number.parseInt(
    request.nextUrl.searchParams.get("limit") || "90",
    10,
  );
  try {
    const history = await getHistory(requestedLimit);
    return NextResponse.json(history, {
      headers: { "Cache-Control": "no-store" },
    });
  } catch (error) {
    console.error("History read failed", error);
    return NextResponse.json([], {
      headers: { "Cache-Control": "no-store" },
    });
  }
}
