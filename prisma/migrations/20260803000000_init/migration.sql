CREATE TABLE "HealthSnapshot" (
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
);

CREATE INDEX "HealthSnapshot_capturedAt_idx"
ON "HealthSnapshot"("capturedAt");
