import { NextResponse } from "next/server";
import { getSpeedTestJobStatus, startSpeedTestJob } from "@/lib/status";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const noStoreHeaders = { "cache-control": "no-store" };

export async function GET() {
  return NextResponse.json(getSpeedTestJobStatus(), {
    headers: noStoreHeaders,
  });
}

export async function POST() {
  const response = startSpeedTestJob();
  return NextResponse.json(response, {
    status: response.started ? 202 : 200,
    headers: noStoreHeaders,
  });
}
