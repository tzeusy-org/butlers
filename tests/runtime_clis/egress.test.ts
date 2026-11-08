import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import net from "node:net";
import { once } from "node:events";
import { lookup } from "node:dns";
import { Readable } from "node:stream";
import { gzipSync } from "node:zlib";
import {
  createGatewayFetch,
  createGaxiosFetch,
  gatewayConfig,
  gatewayURL,
  gatewayWebSocket,
} from "../../deploy/runtime-clis/shared/egress.ts";

async function hop(
  t: import("node:test").TestContext,
  handler: http.RequestListener,
) {
  const server = http.createServer(handler);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const port = (server.address() as import("node:net").AddressInfo).port;
  t.after(async () => {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });
  return { fetch: selectHop(t, port), server, port };
}

function selectHop(
  t: import("node:test").TestContext,
  port: number,
  observe?: (response: Readable) => void,
  timeouts?: NonNullable<Parameters<typeof createGatewayFetch>[0]>["timeouts"],
) {
  const before = [
    process.env["BUTLERS_EGRESS_MODE"],
    process.env["BUTLERS_EGRESS_GATEWAY"],
  ];
  process.env["BUTLERS_EGRESS_MODE"] = "request-v1";
  process.env["BUTLERS_EGRESS_GATEWAY"] = `http://egress-gateway:${port}`;
  t.after(async () => {
    for (const [index, key] of [
      "BUTLERS_EGRESS_MODE",
      "BUTLERS_EGRESS_GATEWAY",
    ].entries()) {
      if (before[index] === undefined) delete process.env[key];
      else process.env[key] = before[index];
    }
  });
  return createGatewayFetch({
    lookup(_name, options, callback) {
      lookup("127.0.0.1", options, callback);
    },
    onResponseBody: observe,
    timeouts,
  });
}

async function rawStreamingHop(t: import("node:test").TestContext) {
  let closed!: () => void;
  const settled = new Promise<void>((resolve) => {
    closed = resolve;
  });
  const sockets = new Set<net.Socket>();
  const server = net.createServer((socket) => {
    socket.on("error", (error) =>
      assert.ok(
        ["ECONNRESET", "EPIPE"].includes(
          (error as NodeJS.ErrnoException).code!,
        ),
      ),
    );
    sockets.add(socket);
    socket.once("close", () => {
      sockets.delete(socket);
      closed();
    });
    socket.once("data", () =>
      socket.write(
        "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\nfirst",
      ),
    );
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => {
    for (const socket of sockets) socket.destroy();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });
  const port = (server.address() as net.AddressInfo).port;
  return { fetch: selectHop(t, port), settled, port };
}

const bounded = <T>(promise: Promise<T>): Promise<T> =>
  Promise.race([
    promise,
    new Promise<never>((_, reject) => {
      const timer = setTimeout(() => reject(new Error("test deadline")), 1500);
      timer.unref();
    }),
  ]);

test("strict paired configuration and logical authority refuse before a request", async (t) => {
  let calls = 0;
  const { fetch } = await hop(t, (_req, res) => {
    calls++;
    res.end();
  });
  const valid = process.env["BUTLERS_EGRESS_GATEWAY"];
  for (const value of [
    "http://egress-gateway:03115",
    "http://elsewhere:3115",
    "https://egress-gateway:3115",
    "http://egress-gateway:65536",
    "http://egress-gateway:80",
    "http://egress-gateway:3115/",
  ]) {
    process.env["BUTLERS_EGRESS_GATEWAY"] = value;
    await assert.rejects(
      fetch("https://origin.invalid/"),
      /Invalid selected egress configuration/,
    );
  }
  process.env["BUTLERS_EGRESS_GATEWAY"] = valid;
  for (const url of [
    "http://origin.invalid/",
    "https://127.0.0.1/",
    "https://user:pass@origin.invalid/",
    "https://origin.invalid/#fragment",
    "https://origin.invalid./",
  ])
    await assert.rejects(fetch(url), /Invalid selected egress origin/);
  const forbiddenHeaders: Record<string, string>[] = [
    { Host: "elsewhere.invalid" },
    { "X-Forwarded-Host": "elsewhere.invalid" },
    { Connection: "keep-alive" },
    { Expect: "100-continue" },
  ];
  for (const headers of forbiddenHeaders)
    await assert.rejects(
      fetch("https://origin.invalid/", { headers }),
      /Forbidden selected egress header/,
    );
  assert.equal(calls, 0);
  assert.equal(
    gatewayURL(
      new URL("https://origin.invalid:8443/a%2Fb?q=x%2Fy"),
      gatewayConfig()!,
    ).pathname,
    "/_butlers/egress/v1/https/origin.invalid/8443/a%2Fb",
  );
});

test("native HTTP streams provider429 and retains original URL, auth, path, retry headers", async (t) => {
  let continueBody!: () => void;
  const release = new Promise<void>((resolve) => {
    continueBody = resolve;
  });
  const { fetch } = await hop(t, async (req, res) => {
    assert.equal(
      req.url,
      "/_butlers/egress/v1/https/origin.invalid/443/a%2Fb?key=SYNTHETIC",
    );
    assert.equal(req.headers.authorization, "Bearer synthetic");
    assert.equal(req.headers.cookie, "session=synthetic");
    assert.match(req.headers.host!, /^egress-gateway:/);
    req.resume();
    res.writeHead(429, {
      "Retry-After": "7",
      "Content-Type": "text/event-stream",
    });
    res.write("first");
    await release;
    res.end("second");
  });
  const url = "https://origin.invalid/a%2Fb?key=SYNTHETIC";
  const response = await bounded(
    fetch(url, {
      headers: {
        Authorization: "Bearer synthetic",
        Cookie: "session=synthetic",
      },
    }),
  );
  assert.equal(response.url, url);
  assert.equal(response.status, 429);
  assert.equal(response.statusText, "Too Many Requests");
  assert.equal(response.headers.get("retry-after"), "7");
  const reader = response.body!.getReader();
  assert.equal(
    new TextDecoder().decode((await bounded(reader.read())).value),
    "first",
  );
  continueBody();
  assert.equal(
    new TextDecoder().decode((await bounded(reader.read())).value),
    "second",
  );
  assert.equal((await bounded(reader.read())).done, true);
});

test("every300..399 is refused without target response or followup request", async (t) => {
  let status = 300,
    count = 0;
  const { fetch } = await hop(t, (req, res) => {
    count++;
    req.resume();
    res.writeHead(status, {
      Location: "https://private.invalid/SYNTHETIC",
      "Content-Type": "text/plain",
    });
    res.end("SYNTHETIC PRIVATE BODY");
  });
  for (; status <= 399; status++)
    await assert.rejects(
      bounded(fetch("https://origin.invalid/")),
      /Selected egress request refused/,
    );
  assert.equal(count, 100);
});

test("compressed streaming response decodes once and clones retain logical URL and cookie fields", async (t) => {
  const { fetch } = await hop(t, (req, res) => {
    req.resume();
    const body = gzipSync("synthetic unicode λ");
    res.writeHead(200, {
      "Content-Encoding": "gzip",
      "Content-Length": body.length,
      "Set-Cookie": ["a=1; Path=/", "b=2; Path=/"],
    });
    res.end(body);
  });
  const response = await fetch("https://origin.invalid/compressed");
  const clone = response.clone();
  assert.equal(clone.url, "https://origin.invalid/compressed");
  assert.deepEqual(response.headers.getSetCookie(), [
    "a=1; Path=/",
    "b=2; Path=/",
  ]);
  assert.equal(await bounded(response.text()), "synthetic unicode λ");
  assert.equal(await bounded(clone.text()), "synthetic unicode λ");
});

test("aborting after headers terminates the native response stream and socket", async (t) => {
  const { fetch, settled: socketClosed } = await rawStreamingHop(t);
  const controller = new AbortController();
  const response = await fetch("https://origin.invalid/stream", {
    signal: controller.signal,
  });
  const reader = response.body!.getReader();
  assert.equal(
    new TextDecoder().decode((await bounded(reader.read())).value),
    "first",
  );
  controller.abort();
  await assert.rejects(bounded(reader.read()));
  await bounded(socketClosed);
});

test("early401 cancels unfinished native upload and preserves the response body", async (t) => {
  let cancelled!: () => void;
  const stopped = new Promise<void>((resolve) => {
    cancelled = resolve;
  });
  const { fetch } = await hop(t, (req, res) => {
    req.once("data", () => {
      res.writeHead(401, { "WWW-Authenticate": "Bearer synthetic" });
      res.end("denied");
    });
  });
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode("first"));
    },
    cancel() {
      cancelled();
    },
  });
  const response = await bounded(
    fetch("https://origin.invalid/upload", {
      method: "POST",
      body,
      duplex: "half",
    } as RequestInit),
  );
  assert.equal(response.status, 401);
  assert.equal(response.headers.get("www-authenticate"), "Bearer synthetic");
  assert.equal(await bounded(response.text()), "denied");
  await bounded(stopped);
});

test("Gaxios view supports independent text parsing and Node-stream paths", async (t) => {
  const { fetch } = await hop(t, (req, res) => {
    req.resume();
    res.end('{"synthetic":true}');
  });
  const gaxios = createGaxiosFetch(fetch);
  const parsed = await gaxios("https://origin.invalid/json");
  assert.deepEqual(await parsed.json(), { synthetic: true });
  const streamed = await gaxios("https://origin.invalid/stream", {
    method: "POST",
    body: Readable.from(["upload"]),
  });
  assert.equal(streamed.url, "https://origin.invalid/stream");
  assert.ok(streamed.body instanceof Readable);
  const chunks: Buffer[] = [];
  for await (const chunk of streamed.body) chunks.push(chunk);
  assert.equal(Buffer.concat(chunks).toString(), '{"synthetic":true}');
});

test("explicit WS mapping retains auth and rejects a direct logical wire destination", async (t) => {
  await hop(t, (_req, res) => res.end());
  const value = gatewayWebSocket(
    "wss://origin.invalid/responses?session=synthetic",
    { Authorization: "Bearer synthetic" },
  )!;
  assert.match(
    value.url,
    /^ws:\/\/egress-gateway:[0-9]+\/_butlers\/egress\/v1\/wss\/origin.invalid\/443\/responses\?session=synthetic$/,
  );
  assert.equal(value.headers.authorization, "Bearer synthetic");
  assert.equal(value.headers.host, undefined);
  assert.throws(
    () => gatewayWebSocket("ws://origin.invalid/", {}),
    /Invalid selected egress origin/,
  );
});

test("off mode retains the native fetch request and response path", async (t) => {
  const { fetch, port } = await hop(t, (req, res) => {
    assert.equal(req.url, "/native-off");
    req.resume();
    res.end("native");
  });
  delete process.env["BUTLERS_EGRESS_MODE"];
  delete process.env["BUTLERS_EGRESS_GATEWAY"];
  const url = `http://127.0.0.1:${port}/native-off`;
  const response = await fetch(url);
  assert.equal(response.url, url);
  assert.equal(await response.text(), "native");
});

test("partial or mismatched selection never calls the native hop", async (t) => {
  let count = 0;
  const { fetch } = await hop(t, (_req, res) => {
    count++;
    res.end();
  });
  const address = process.env["BUTLERS_EGRESS_GATEWAY"];
  process.env["BUTLERS_EGRESS_MODE"] = "off";
  await assert.rejects(
    fetch("https://origin.invalid/"),
    /Invalid selected egress configuration/,
  );
  process.env["BUTLERS_EGRESS_MODE"] = "request-v1";
  delete process.env["BUTLERS_EGRESS_GATEWAY"];
  await assert.rejects(
    fetch("https://origin.invalid/"),
    /Invalid selected egress configuration/,
  );
  delete process.env["BUTLERS_EGRESS_MODE"];
  process.env["BUTLERS_EGRESS_GATEWAY"] = address;
  await assert.rejects(
    fetch("https://origin.invalid/"),
    /Invalid selected egress configuration/,
  );
  assert.equal(count, 0);
});

test("an unexpected101 on the HTTP executor is refused without a tunnel", async (t) => {
  const { fetch } = await hop(t, (req, res) => {
    req.resume();
    res.writeHead(101, { Connection: "Upgrade", Upgrade: "websocket" });
    res.end();
  });
  await assert.rejects(
    bounded(fetch("https://origin.invalid/")),
    /Invalid selected egress HTTP upgrade/,
  );
});

test("cancelling a response body closes the owned native hop", async (t) => {
  const { fetch, settled } = await rawStreamingHop(t);
  const response = await fetch("https://origin.invalid/cancel");
  await response.body!.cancel();
  await bounded(settled);
});

test("a native hop outage retains the connection error without another wire attempt", async (t) => {
  const { fetch, server } = await hop(t, (_req, res) => res.end());
  await new Promise<void>((resolve) => server.close(() => resolve()));
  await assert.rejects(
    bounded(fetch("https://origin.invalid/outage")),
    (error: unknown) => {
      assert.ok(error instanceof TypeError);
      assert.equal((error.cause as NodeJS.ErrnoException).code, "ECONNREFUSED");
      return true;
    },
  );
});

test("Gaxios parses exact array-buffer bytes and enforces configured decoded size", async (t) => {
  const { fetch } = await hop(t, (req, res) => {
    req.resume();
    res.end("four");
  });
  const gaxios = createGaxiosFetch(fetch);
  const response = await gaxios("https://origin.invalid/bytes");
  assert.deepEqual(
    new Uint8Array(await response.arrayBuffer()),
    new TextEncoder().encode("four"),
  );
  const limited = await gaxios("https://origin.invalid/size", { size: 3 });
  await assert.rejects(limited.text(), (error: unknown) => {
    assert.equal((error as { type: string }).type, "max-size");
    return true;
  });
});

test("Gaxios retains separate header and body timeout categories", async (t) => {
  const { fetch } = await hop(t, (req, res) => {
    req.resume();
    if (req.url!.endsWith("/body")) res.write("first");
  });
  const gaxios = createGaxiosFetch(fetch);
  await assert.rejects(
    bounded(gaxios("https://origin.invalid/header", { timeout: 50 })),
    (error: unknown) => {
      assert.equal((error as { type: string }).type, "request-timeout");
      return true;
    },
  );
  const response = await gaxios("https://origin.invalid/body", { timeout: 50 });
  await assert.rejects(bounded(response.text()), (error: unknown) => {
    assert.equal((error as { type: string }).type, "body-timeout");
    return true;
  });
});

test("Gaxios explicit compress=false preserves native raw bytes and Blob type", async (t) => {
  const compressed = gzipSync("synthetic");
  const { fetch } = await hop(t, (req, res) => {
    req.resume();
    assert.equal(req.headers["accept-encoding"], undefined);
    res.writeHead(200, {
      "Content-Encoding": "gzip",
      "Content-Type": "application/octet-stream",
    });
    res.end(compressed);
  });
  const response = await createGaxiosFetch(fetch)(
    "https://origin.invalid/raw",
    { compress: false },
  );
  const blob = await response.blob();
  assert.equal(blob.type, "application/octet-stream");
  assert.deepEqual(
    new Uint8Array(await blob.arrayBuffer()),
    new Uint8Array(compressed),
  );
});

test("a paused consumer keeps the native incoming body bounded", async (t) => {
  let payloadSize = 8388608;
  const sockets = new Set<net.Socket>();
  const server = net.createServer((socket) => {
    socket.on("error", (error) =>
      assert.ok(
        ["ECONNRESET", "EPIPE"].includes(
          (error as NodeJS.ErrnoException).code!,
        ),
      ),
    );
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
    socket.once("data", () => {
      socket.write(
        `HTTP/1.1 200 OK\r\nContent-Length: ${payloadSize}\r\nConnection: close\r\n\r\n`,
      );
      socket.write(Buffer.alloc(payloadSize, 97));
    });
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => {
    for (const socket of sockets) socket.destroy();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });
  let incoming: Readable | undefined;
  const port = (server.address() as net.AddressInfo).port;
  const fetch = selectHop(t, port, (value) => {
    incoming = value;
  });
  // A native TCP read slab may overshoot the 16KiB high-water mark once.
  // Bun's slab is 512KiB; the 1MiB ceiling must hold independently of payload size.
  for (payloadSize of [8388608, 33554432]) {
    const response = await fetch("https://origin.invalid/backpressure");
    try {
      await new Promise((resolve) => setTimeout(resolve, 100));
      assert.ok(incoming);
      console.info(
        "backpressure payload/buffer bytes",
        payloadSize,
        incoming.readableLength,
      );
      assert.ok(
        incoming.readableLength <= 1048576,
        `unconsumed native buffer=${incoming.readableLength}`,
      );
    } finally {
      await response.body!.cancel();
    }
  }
});

test("discard cleanup does not hide a truncated response or a broken decoder", async (t) => {
  let compressed = false;
  const { fetch } = await hop(t, (req, res) => {
    req.resume();
    res.writeHead(
      200,
      compressed ? { "Content-Encoding": "gzip" } : { "Content-Length": 100 },
    );
    res.end("broken");
  });
  const truncated = await fetch("https://origin.invalid/truncated");
  await assert.rejects(bounded(truncated.text()));
  compressed = true;
  const invalid = await fetch("https://origin.invalid/compressed");
  await assert.rejects(bounded(invalid.text()));
});

test("the native parser accepts ordinary headers and refuses a64KiB overflow", async (t) => {
  let length = 1024;
  const { fetch } = await hop(t, (req, res) => {
    req.resume();
    res.writeHead(200, { "X-Synthetic-Header": "a".repeat(length) });
    res.end("ok");
  });
  const response = await fetch("https://origin.invalid/headers");
  assert.equal(response.headers.get("x-synthetic-header")!.length, 1024);
  assert.equal(await response.text(), "ok");
  length = 65536;
  await assert.rejects(
    bounded(fetch("https://origin.invalid/overflow")),
    (error: unknown) => {
      assert.equal(
        ((error as Error).cause as { code: string }).code,
        "UND_ERR_HEADERS_OVERFLOW",
      );
      return true;
    },
  );
});

test("HEAD and empty provider statuses retain native empty bodies", async (t) => {
  let status = 200;
  const { fetch } = await hop(t, (req, res) => {
    req.resume();
    res.writeHead(status);
    res.end();
  });
  for (status of [200, 204, 205]) {
    const response = await fetch("https://origin.invalid/empty", {
      method: status === 200 ? "HEAD" : "GET",
    });
    assert.equal(response.status, status);
    assert.equal(response.body, null);
    assert.equal(await response.text(), "");
  }
});

test("native header, body-idle, and total deadlines settle their owned hops", async (t) => {
  const headerHop = await hop(t, (req, _res) => req.resume());
  const headerFetch = selectHop(t, headerHop.port, undefined, { headers: 50 });
  await assert.rejects(
    bounded(headerFetch("https://origin.invalid/no-headers")),
    (error: unknown) => {
      assert.equal(
        ((error as Error).cause as { code: string }).code,
        "UND_ERR_HEADERS_TIMEOUT",
      );
      return true;
    },
  );
  const idle = await rawStreamingHop(t);
  const idleFetch = selectHop(t, idle.port, undefined, { bodyIdle: 50 });
  const idleResponse = await idleFetch("https://origin.invalid/idle");
  await assert.rejects(bounded(idleResponse.text()), (error: unknown) => {
    assert.equal((error as { code: string }).code, "UND_ERR_BODY_TIMEOUT");
    return true;
  });
  await bounded(idle.settled);
  const total = await rawStreamingHop(t);
  const totalFetch = selectHop(t, total.port, undefined, {
    total: 75,
    bodyIdle: 1000,
  });
  const totalResponse = await totalFetch("https://origin.invalid/deadline");
  await assert.rejects(
    bounded(totalResponse.text()),
    /Selected egress deadline exceeded/,
  );
  await bounded(total.settled);
});

test("active native streaming survives longer than its idle budget", async (t) => {
  const { port } = await hop(t, (req, res) => {
    req.resume();
    let count = 1;
    res.write("a");
    const interval = setInterval(() => {
      res.write("a");
      count++;
      if (count === 22) {
        clearInterval(interval);
        res.end();
      }
    }, 50);
    t.after(() => clearInterval(interval));
  });
  const fetch = selectHop(t, port, undefined, { bodyIdle: 150, total: 3000 });
  const response = await fetch("https://origin.invalid/active");
  assert.equal(await bounded(response.text()), "a".repeat(22));
});

test("a stalled native DNS connection reaches the connect deadline without a wire request", async (t) => {
  let count = 0;
  await hop(t, (_req, res) => {
    count++;
    res.end();
  });
  const fetch = createGatewayFetch({
    lookup() {},
    timeouts: { connect: 50, total: 1200 },
  });
  await assert.rejects(
    bounded(fetch("https://origin.invalid/connect")),
    (error: unknown) => {
      assert.equal(
        ((error as Error).cause as { code: string }).code,
        "UND_ERR_CONNECT_TIMEOUT",
      );
      return true;
    },
  );
  assert.equal(count, 0);
});
