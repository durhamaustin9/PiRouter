import { NextResponse } from "next/server";
import {
  portableExportAuthorized,
  readPortableExportStatus,
  requestPortableExport,
  updatePortableExport,
  type PortableExportUpdate,
} from "@/lib/portableExport";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function authorized(request: Request) {
  return portableExportAuthorized(request.headers.get("authorization"));
}

export async function GET(request: Request) {
  if (!authorized(request)) {
    return NextResponse.json({ error: "Unauthorized exporter" }, { status: 401 });
  }
  return NextResponse.json(await readPortableExportStatus(), {
    headers: { "Cache-Control": "no-store" },
  });
}

export async function POST(request: Request) {
  const current = await readPortableExportStatus();
  if (!current.configured) {
    return NextResponse.json(
      { error: "The macOS portable exporter is not paired" },
      { status: 503 },
    );
  }

  const trigger = authorized(request) ? "automatic" : "manual";
  const status = await requestPortableExport(trigger);
  const alreadyActive =
    current.state === "requested" || current.state === "running";
  return NextResponse.json(
    {
      started: !alreadyActive,
      message: alreadyActive
        ? "A portable export is already active"
        : "Portable export requested",
      status,
    },
    { status: alreadyActive ? 200 : 202 },
  );
}

export async function PATCH(request: Request) {
  if (!authorized(request)) {
    return NextResponse.json({ error: "Unauthorized exporter" }, { status: 401 });
  }

  try {
    const body = (await request.json()) as Partial<PortableExportUpdate>;
    if (
      typeof body.requestId !== "string" ||
      !["running", "success", "error"].includes(body.state ?? "") ||
      typeof body.message !== "string" ||
      body.message.length < 1 ||
      body.message.length > 240
    ) {
      return NextResponse.json(
        { error: "Invalid portable-export update" },
        { status: 400 },
      );
    }

    const status = await updatePortableExport({
      requestId: body.requestId,
      state: body.state as PortableExportUpdate["state"],
      message: body.message,
      lastSnapshotName: body.lastSnapshotName,
      bytesCopied: body.bytesCopied,
    });
    return NextResponse.json(status);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Unable to update export state";
    return NextResponse.json({ error: message }, { status: 409 });
  }
}
