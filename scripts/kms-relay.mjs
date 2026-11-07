import net from "node:net";

const listenHost = process.env.KMS_RELAY_LISTEN_HOST || "127.0.0.1";
const upstreamHost = process.env.KMS_RELAY_UPSTREAM_HOST || "kms-server.example";

function portFromEnv(name, fallback) {
  const value = Number(process.env[name] || fallback);
  if (!Number.isInteger(value) || value < 1 || value > 65535) {
    throw new Error(`${name} must be a TCP port between 1 and 65535`);
  }
  return value;
}

const listenPort = portFromEnv("KMS_RELAY_LISTEN_PORT", 1688);
const upstreamPort = portFromEnv("KMS_RELAY_UPSTREAM_PORT", 1688);

const server = net.createServer((client) => {
  const upstream = net.connect({ host: upstreamHost, port: upstreamPort });

  client.setTimeout(35_000);
  upstream.setTimeout(5_000);

  upstream.on("connect", () => {
    upstream.setTimeout(35_000);
    client.pipe(upstream);
    upstream.pipe(client);
  });

  client.on("timeout", () => client.destroy());
  upstream.on("timeout", () => upstream.destroy());
  client.on("error", () => upstream.destroy());
  upstream.on("error", (error) => {
    console.error(`KMS upstream connection failed: ${error.message}`);
    client.destroy();
  });
  client.on("close", () => upstream.destroy());
  upstream.on("close", () => {
    if (!client.writableEnded) client.end();
  });
});

server.on("error", (error) => {
  console.error(`KMS relay could not listen: ${error.message}`);
  process.exitCode = 1;
});

server.listen(listenPort, listenHost, () => {
  console.log(`KMS relay listening on ${listenHost}:${listenPort}, forwarding to ${upstreamHost}:${upstreamPort}`);
});
