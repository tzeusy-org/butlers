import http from "node:http";

const configuredPort = process.env.E2E_API_MOCK_PORT ?? "4174";
const port = Number(configuredPort);

if (!Number.isInteger(port) || port < 1 || port > 65_535) {
  throw new Error(`E2E_API_MOCK_PORT must be a valid TCP port; received ${configuredPort}.`);
}

function writeJson(response, statusCode, body) {
  response.writeHead(statusCode, {
    "cache-control": "no-store",
    "content-type": "application/json; charset=utf-8",
  });
  response.end(JSON.stringify(body));
}

const server = http.createServer((request, response) => {
  const method = request.method ?? "GET";
  const url = new URL(request.url ?? "/", "http://127.0.0.1");

  if (method === "GET" && url.pathname === "/api/health") {
    writeJson(response, 200, { status: "ok" });
    return;
  }

  if (url.pathname === "/api" || url.pathname.startsWith("/api/")) {
    writeJson(response, 404, {
      error: {
        code: "E2E_UNMOCKED_API_ROUTE",
        message: `No Playwright API mock is registered for ${method} ${url.pathname}.`,
        butler: null,
        details: null,
      },
    });
    return;
  }

  writeJson(response, 404, {
    error: {
      code: "NOT_FOUND",
      message: `No route is available for ${method} ${url.pathname}.`,
      butler: null,
      details: null,
    },
  });
});

server.on("error", (error) => {
  console.error("Playwright API mock failed to start:", error);
  process.exitCode = 1;
});

function shutdown() {
  server.close((error) => {
    if (error) {
      console.error("Playwright API mock failed to stop:", error);
      process.exitCode = 1;
    }
  });
}

process.once("SIGINT", shutdown);
process.once("SIGTERM", shutdown);

server.listen(port, "127.0.0.1", () => {
  console.log(`Playwright API mock listening on http://127.0.0.1:${port}`);
});
