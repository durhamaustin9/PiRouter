import { PrismaBetterSqlite3 } from "@prisma/adapter-better-sqlite3";
import { PrismaClient } from "@/prisma/generated/client";

const globalForPrisma = globalThis as unknown as {
  prisma?: PrismaClient;
  prismaReady?: Promise<void>;
};

const adapter = new PrismaBetterSqlite3({
  url: process.env.DATABASE_URL || "file:./data/pirouter.db",
});

export const prisma = globalForPrisma.prisma ?? new PrismaClient({ adapter });

if (process.env.NODE_ENV !== "production") {
  globalForPrisma.prisma = prisma;
}

export function ensureDatabase() {
  if (!globalForPrisma.prismaReady) {
    globalForPrisma.prismaReady = (async () => {
      await prisma.$executeRawUnsafe(`
        CREATE TABLE IF NOT EXISTS "HealthSnapshot" (
          "id" INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          "capturedAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          "downloadBps" REAL NOT NULL,
          "uploadBps" REAL NOT NULL,
          "latencyMs" REAL,
          "packetLossPct" REAL,
          "cpuTempC" REAL,
          "cpuLoadPct" REAL NOT NULL,
          "memoryPct" REAL NOT NULL,
          "diskPct" REAL NOT NULL,
          "tailscaleUp" BOOLEAN NOT NULL,
          "endpointReachable" BOOLEAN,
          "timeMachineOk" BOOLEAN
        )
      `);
      await prisma.$executeRawUnsafe(`
        CREATE INDEX IF NOT EXISTS "HealthSnapshot_capturedAt_idx"
          ON "HealthSnapshot"("capturedAt")
      `);
    })().catch((error) => {
      globalForPrisma.prismaReady = undefined;
      throw error;
    });
  }

  return globalForPrisma.prismaReady;
}
