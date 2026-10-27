/** Isolated HTTPS browser fixture. Never targets a configured owner deployment. */
import https from "node:https";
import http from "node:http";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve, extname } from "node:path";
import { execFileSync } from "node:child_process";

export async function startOwnerAuthHttps(apiTarget) {
  const upstream = new URL(apiTarget);
  if (upstream.protocol !== "http:" || upstream.hostname !== "127.0.0.1" || !upstream.port) {
    throw new Error("Synthetic auth backend must be an explicit loopback HTTP port.");
  }
  const directory = await mkdtemp(join(tmpdir(), "butlers-auth-https-"));
  execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", join(directory, "key.pem"), "-out", join(directory, "cert.pem"), "-days", "1", "-subj", "/CN=butlers.example.test", "-addext", "subjectAltName=DNS:butlers.example.test"], { stdio: "ignore" });
  const types = { ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".html": "text/html" };
  const server = https.createServer({ key: await readFile(join(directory, "key.pem")), cert: await readFile(join(directory, "cert.pem")) }, async (req, res) => {
    const path = new URL(req.url, "https://butlers.example.test").pathname;
    if (path.startsWith("/api/") || path.startsWith("/__test__/")) {
      const headers = { ...req.headers, host: "butlers.example.test", "x-forwarded-proto": "https", "x-forwarded-host": "butlers.example.test" };
      delete headers.forwarded; delete headers["x-forwarded-for"]; delete headers["x-real-ip"];
      const proxy = http.request(new URL(req.url, upstream), { method: req.method, headers }, reply => {
        res.writeHead(reply.statusCode, reply.headers); reply.pipe(res);
      });
      proxy.on("error", () => { if (!res.destroyed) { if (!res.headersSent) res.writeHead(502); res.end(); } });
      res.on("close", () => proxy.destroy());
      req.on("aborted", () => proxy.destroy());
      req.pipe(proxy); return;
    }
    try {
      const root = resolve("dist");
      let file = resolve(root, `.${path}`);
      if (!file.startsWith(root + "/")) file = join(root, "index.html");
      let data;
      try { data = await readFile(file); } catch { file = join(root, "index.html"); data = await readFile(file); }
      res.writeHead(200, { "Content-Type": types[extname(file)] ?? "application/octet-stream", "Cache-Control": "no-store" }); res.end(data);
    } catch { res.writeHead(500); res.end(); }
  });
  const upgrades = new Set();
  server.on("upgrade", (req, downstream, head) => {
    if (!req.url.startsWith("/api/")) { downstream.destroy(); return; }
    const headers = { ...req.headers, host: "butlers.example.test", "x-forwarded-proto": "https", "x-forwarded-host": "butlers.example.test" };
    delete headers.forwarded; delete headers["x-forwarded-for"]; delete headers["x-real-ip"];
    const proxy = http.request(new URL(req.url, upstream), { method: "GET", headers });
    proxy.on("upgrade", (reply, socket, upstreamHead) => {
      upgrades.add(socket); upgrades.add(downstream);
      downstream.write(`HTTP/1.1 101 Switching Protocols\r\n${Object.entries(reply.headers).map(([key, value]) => `${key}: ${value}`).join("\r\n")}\r\n\r\n`);
      if (head.length) socket.write(head);
      if (upstreamHead.length) downstream.write(upstreamHead);
      socket.pipe(downstream); downstream.pipe(socket);
      socket.on("close", () => { upgrades.delete(socket); downstream.destroy(); });
      downstream.on("close", () => { upgrades.delete(downstream); socket.destroy(); });
      socket.on("error", () => downstream.destroy());
      downstream.on("error", () => socket.destroy());
    });
    proxy.on("response", () => downstream.destroy());
    proxy.on("error", () => downstream.destroy());
    proxy.end();
  });
  await new Promise((resolveListen, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolveListen); });
  return { port: server.address().port, async close() { for (const socket of upgrades) socket.destroy(); server.closeAllConnections(); await new Promise(resolveClose => server.close(resolveClose)); await rm(directory, { recursive: true, force: true }); } };
}
