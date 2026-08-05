import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { NextResponse } from "next/server";

const execFileAsync = promisify(execFile);

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST() {
  if (process.platform !== "linux") {
    return NextResponse.json(
      { error: "Archive replication can only run on the Pi" },
      { status: 503 },
    );
  }

  try {
    await execFileAsync(
      "sudo",
      [
        "-n",
        "/usr/bin/systemctl",
        "start",
        "--no-block",
        "pirouter-archive-backup.service",
      ],
      { timeout: 5_000 },
    );
    return NextResponse.json(
      { message: "Archive backup check started" },
      { status: 202 },
    );
  } catch {
    return NextResponse.json(
      { error: "Unable to start the archive backup service" },
      { status: 503 },
    );
  }
}
