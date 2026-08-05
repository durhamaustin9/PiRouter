import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export async function runCommand(
  command: string,
  args: string[],
  timeoutMs = 4_000,
): Promise<string | null> {
  try {
    const { stdout } = await execFileAsync(command, args, {
      timeout: timeoutMs,
      maxBuffer: 2 * 1024 * 1024,
      encoding: "utf8",
    });
    return stdout.trim();
  } catch {
    return null;
  }
}

export async function readNumber(file: string) {
  try {
    const value = Number.parseFloat(
      await fs.readFile(/* turbopackIgnore: true */ file, "utf8"),
    );
    return Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

export function round(value: number, digits = 1) {
  const multiplier = 10 ** digits;
  return Math.round(value * multiplier) / multiplier;
}
