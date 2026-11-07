import { NextResponse } from "next/server";
import { readArchiveCoordination } from "@/lib/archiveCoordination";
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

function validProgress(value: unknown) {
  return value == null ||
    (typeof value === "number" && Number.isFinite(value) && value >= 0);
}

function validSnapshotName(value: unknown) {
  return value == null || (typeof value === "string" && value.length <= 240);
}

export async function GET(request: Request) {
  if (!authorized(request)) {
    return NextResponse.json({ error: "Unauthorized exporter" }, { status: 401 });
  }
  const [status, archiveCoordination] = await Promise.all([
    readPortableExportStatus(), readArchiveCoordination(),
  ]);
  return NextResponse.json({ ...status, archiveCoordination }, {
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
      !body ||
      typeof body !== "object" ||
      Array.isArray(body) ||
      typeof body.requestId !== "string" ||
      !["running", "paused", "success", "error"].includes(body.state ?? "") ||
      typeof body.message !== "string" ||
      body.message.length < 1 ||
      body.message.length > 240 ||
      !validProgress(body.bytesCopied) ||
      !validProgress(body.filesCopied) ||
      !validSnapshotName(body.activeSnapshotName) ||
      !validSnapshotName(body.lastSnapshotName) ||
      (body.pauseReason != null &&
        !["session-budget", "archive-handoff"].includes(body.pauseReason)) ||
      (body.state === "success" && body.verified !== true) ||
      (body.errorDetail != null &&
        (typeof body.errorDetail !== "string" || body.errorDetail.length > 4000))
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
      activeSnapshotName: body.activeSnapshotName,
      bytesCopied: body.bytesCopied,
      filesCopied: body.filesCopied,
      errorDetail: body.errorDetail,
      pauseReason: body.pauseReason,
      verified: body.verified === true,
    });
    return NextResponse.json(status);
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Unable to update export state";
    return NextResponse.json(
      { error: message },
      { status: error instanceof SyntaxError ? 400 : 409 },
    );
  }
}
