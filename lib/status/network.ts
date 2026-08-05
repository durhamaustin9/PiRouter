import { lookup } from "node:dns/promises";
import os from "node:os";
import type { NetworkStatus } from "@/lib/types";
import { createCachedProbe } from "@/lib/status/cache";
import { readNumber, round, runCommand } from "@/lib/status/shared";

type NetworkSample = {
  interfaceName: string;
  receivedBytes: number;
  transmittedBytes: number;
  sampledAt: number;
};

type NetworkQuality = {
  dnsOk: boolean;
  latencyMs: number | null;
  packetLossPct: number | null;
};

const getCachedInterface = createCachedProbe<string | null>(5 * 60_000);
const getCachedQuality = createCachedProbe<NetworkQuality>(15_000);
let previousSample: NetworkSample | null = null;

async function defaultInterface() {
  return getCachedInterface(async () => {
    const configured = process.env.NETWORK_INTERFACE?.trim();
    if (configured && configured !== "auto") return configured;

    if (process.platform === "linux") {
      const route = await runCommand("ip", ["route", "show", "default"]);
      const match = route?.match(/\bdev\s+(\S+)/);
      if (match) return match[1];
    }

    return (
      Object.entries(os.networkInterfaces()).find(([, addresses]) =>
        addresses?.some(
          (address) => !address.internal && address.family === "IPv4",
        ),
      )?.[0] ?? null
    );
  });
}

async function readCounters(interfaceName: string | null) {
  if (!interfaceName || process.platform !== "linux") return null;

  const [receivedBytes, transmittedBytes] = await Promise.all([
    readNumber(`/sys/class/net/${interfaceName}/statistics/rx_bytes`),
    readNumber(`/sys/class/net/${interfaceName}/statistics/tx_bytes`),
  ]);
  if (receivedBytes === null || transmittedBytes === null) return null;
  return { receivedBytes, transmittedBytes };
}

async function sampleInterface() {
  const interfaceName = await defaultInterface();
  const addresses = interfaceName
    ? os.networkInterfaces()[interfaceName] ?? []
    : [];
  const ipv4 =
    addresses.find(
      (address) => address.family === "IPv4" && !address.internal,
    )?.address ?? null;
  const counters = await readCounters(interfaceName);
  const sampledAt = Date.now();
  let downloadBps = 0;
  let uploadBps = 0;

  if (
    counters &&
    interfaceName &&
    previousSample?.interfaceName === interfaceName
  ) {
    const elapsedSeconds = (sampledAt - previousSample.sampledAt) / 1_000;
    if (elapsedSeconds > 0) {
      downloadBps = Math.max(
        0,
        (counters.receivedBytes - previousSample.receivedBytes) /
          elapsedSeconds,
      );
      uploadBps = Math.max(
        0,
        (counters.transmittedBytes - previousSample.transmittedBytes) /
          elapsedSeconds,
      );
    }
  }

  if (counters && interfaceName) {
    previousSample = { interfaceName, ...counters, sampledAt };
  }

  const linkMbps = interfaceName
    ? await readNumber(`/sys/class/net/${interfaceName}/speed`)
    : null;

  return {
    interfaceName,
    ipv4,
    downloadBps,
    uploadBps,
    linkMbps: linkMbps && linkMbps > 0 ? linkMbps : null,
  };
}

async function pingHost(host: string) {
  const args =
    process.platform === "darwin"
      ? ["-c", "1", "-W", "1000", host]
      : ["-c", "1", "-W", "1", host];
  const output = await runCommand("ping", args, 3_000);
  if (!output) return { latencyMs: null, packetLossPct: 100 };

  const loss = output.match(/([\d.]+)%\s+packet loss/i);
  const timing = output.match(
    /(?:round-trip|rtt).*?=\s*[\d.]+\/([\d.]+)\/[\d.]+/i,
  );
  return {
    latencyMs: timing ? round(Number.parseFloat(timing[1])) : null,
    packetLossPct: loss ? round(Number.parseFloat(loss[1])) : null,
  };
}

async function checkDns() {
  try {
    await Promise.race([
      lookup("tailscale.com"),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error("DNS timeout")), 2_000),
      ),
    ]);
    return true;
  } catch {
    return false;
  }
}

async function networkQuality(healthTarget: string) {
  return getCachedQuality(async () => {
    const [ping, dnsOk] = await Promise.all([
      pingHost(healthTarget),
      checkDns(),
    ]);
    return { ...ping, dnsOk };
  });
}

export async function collectNetworkStatus(
  healthTarget: string,
): Promise<NetworkStatus> {
  const [network, quality] = await Promise.all([
    sampleInterface(),
    networkQuality(healthTarget),
  ]);

  return {
    interface: network.interfaceName,
    online: quality.packetLossPct !== 100 && quality.dnsOk,
    ipv4: network.ipv4,
    downloadBps: round(network.downloadBps),
    uploadBps: round(network.uploadBps),
    linkMbps: network.linkMbps,
    latencyMs: quality.latencyMs,
    packetLossPct: quality.packetLossPct,
    dnsOk: quality.dnsOk,
    healthTarget,
  };
}
