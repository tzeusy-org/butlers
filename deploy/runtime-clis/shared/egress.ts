/// <reference path="./undici-private.d.ts" />
/** Explicit request-v1 transport; auth, account, and model URLs remain logical origins. */
import Agent from "butlers-egress-undici/lib/dispatcher/agent.js";
import requestWire from "butlers-egress-undici/lib/api/api-request.js";
import type { Dispatcher } from "butlers-egress-undici/index.js";
import type { LookupFunction } from "node:net";
import { Readable } from "node:stream";
import { pipeline } from "node:stream";
import { createBrotliDecompress, createGunzip, createInflate } from "node:zlib";

export interface GatewayConfig {
  port: number;
}
export interface GatewayFetchInit extends RequestInit {
  gatewayDecodeContent?: boolean;
}

export function gatewayConfig(): GatewayConfig | undefined {
  const mode = process.env["BUTLERS_EGRESS_MODE"];
  const address = process.env["BUTLERS_EGRESS_GATEWAY"];
  if (mode === undefined && address === undefined) return undefined;
  const match = /^http:\/\/egress-gateway:([1-9][0-9]*)$/.exec(address ?? "");
  const port = Number(match?.[1]);
  if (mode !== "request-v1" || !match || port < 1024 || port > 65535)
    throw new TypeError("Invalid selected egress configuration");
  return { port };
}

export function gatewayURL(
  logical: URL,
  config: GatewayConfig,
  websocket = false,
): URL {
  if (
    logical.protocol !== (websocket ? "wss:" : "https:") ||
    logical.username ||
    logical.password ||
    logical.hash ||
    !logical.hostname ||
    logical.hostname.endsWith(".") ||
    !/^[a-zA-Z0-9.-]+$/.test(logical.hostname) ||
    /^[0-9.]+$/.test(logical.hostname)
  )
    throw new TypeError("Invalid selected egress origin");
  const kind = websocket ? "wss" : "https";
  return new URL(
    `${websocket ? "ws" : "http"}://egress-gateway:${config.port}` +
      `/_butlers/egress/v1/${kind}/${logical.hostname}/${logical.port || "443"}` +
      logical.pathname +
      logical.search,
  );
}

function headersForGateway(headers: Headers, logical: URL): Headers {
  const result = new Headers(headers);
  for (const [name, value] of result) {
    if (
      (name === "host" && value.toLowerCase() !== logical.host.toLowerCase()) ||
      (name === "connection" && value.toLowerCase() !== "close") ||
      /^(?:forwarded|proxy-.*|upgrade|trailer|expect|transfer-encoding|te)$/.test(
        name,
      ) ||
      /^(?:x-forwarded-|x-butlers-|x-egress-)/.test(name)
    )
      throw new TypeError("Forbidden selected egress header");
  }
  result.delete("host");
  result.set("connection", "close");
  return result;
}

function logicalResponse(response: Response, url: string): Response {
  Object.defineProperty(response, "url", { value: url });
  Object.defineProperty(response, "clone", {
    value() {
      return logicalResponse(Response.prototype.clone.call(this), url);
    },
  });
  return response;
}

/** Tests can resolve the owned internal hop to loopback and observe its native buffer. */
export function createGatewayFetch(
  dependencies: {
    lookup?: LookupFunction;
    onResponseBody?: (body: Readable) => void;
    timeouts?: Partial<{
      connect: number;
      headers: number;
      bodyIdle: number;
      total: number;
    }>;
  } = {},
) {
  const limits = {
    connect: 10000,
    headers: 10000,
    bodyIdle: 300000,
    total: 3600000,
  };
  for (const key of Object.keys(limits) as (keyof typeof limits)[]) {
    const value = dependencies.timeouts?.[key];
    if (value !== undefined) {
      if (!Number.isFinite(value) || value <= 0 || value > limits[key])
        throw new TypeError("Invalid selected egress timeout");
      limits[key] = value;
    }
  }
  return async function gatewayFetch(
    input: RequestInfo | URL,
    init?: GatewayFetchInit,
  ): Promise<Response> {
    const config = gatewayConfig();
    if (!config) return globalThis.fetch(input, init);
    const logical = new URL(input instanceof Request ? input.url : input);
    const wire = gatewayURL(logical, config);
    const request = new Request(input, init);
    const headers = headersForGateway(request.headers, logical);
    if (request.signal.aborted) throw request.signal.reason;
    if (init?.gatewayDecodeContent !== false && !headers.has("accept-encoding"))
      headers.set("accept-encoding", "gzip, deflate, br");
    // This is a private dispatcher, never a global, environment, PAC or proxy agent.
    // Undici's parser and pause/resume machinery are shared across Node and Bun;
    // Bun 1.3.14's node:http facade otherwise buffers a paused response without bound.
    const dispatcher = new Agent({
      connections: 1,
      pipelining: 0,
      allowH2: false,
      maxHeaderSize: 65536,
      connect: {
        timeout: limits.connect,
        ...(dependencies.lookup && { lookup: dependencies.lookup }),
      },
    });
    const total = new AbortController();
    const signal = AbortSignal.any([request.signal, total.signal]);
    const timer = setTimeout(
      () => total.abort(new Error("Selected egress deadline exceeded")),
      limits.total,
    );
    timer.unref();
    let upload: Readable | undefined;
    let settlement: Promise<void> | undefined;
    const settle = () => {
      if (!settlement) {
        clearTimeout(timer);
        upload?.destroy();
        settlement = dispatcher.destroy();
      }
      return settlement;
    };
    try {
      if (request.body)
        upload = Readable.fromWeb(
          request.body as import("node:stream/web").ReadableStream<Uint8Array>,
        );
      // Low-level request has no redirect interceptor or automatic redirect route.
      const incoming = await requestWire.call(dispatcher, {
        origin: wire.origin,
        path: wire.pathname + wire.search,
        method: request.method as Dispatcher.HttpMethod,
        headers: Object.fromEntries(headers),
        body: upload,
        signal,
        headersTimeout: limits.headers,
        bodyTimeout: limits.bodyIdle,
        highWaterMark: 16384,
      });
      // A deliberately discarded response can emit an abort error during dispatcher teardown.
      // Stream consumers still receive their own error through the readable interface.
      incoming.body.on("error", () => {});
      dependencies.onResponseBody?.(incoming.body);
      const status = incoming.statusCode;
      if (
        (status >= 300 && status <= 399) ||
        incoming.headers["x-egress-refusal"]
      ) {
        incoming.body.destroy();
        await settle();
        throw new TypeError("Selected egress request refused");
      }
      // Do not keep an unfinished producer alive after an early provider rejection.
      if (upload && !upload.readableEnded) upload.destroy();
      const responseHeaders = new Headers();
      for (const [name, value] of Object.entries(incoming.headers)) {
        if (Array.isArray(value))
          for (const item of value) responseHeaders.append(name, item);
        else if (value !== undefined) responseHeaders.append(name, value);
      }
      const empty =
        request.method === "HEAD" || status === 204 || status === 205;
      if (empty) {
        incoming.body.destroy();
        await settle();
        return logicalResponse(
          new Response(null, {
            status,
            statusText: incoming.statusText,
            headers: responseHeaders,
          }),
          request.url,
        );
      }
      let body: Readable = incoming.body;
      const encoding =
        init?.gatewayDecodeContent === false
          ? undefined
          : incoming.headers["content-encoding"];
      const decoder =
        encoding === "gzip" || encoding === "x-gzip"
          ? createGunzip()
          : encoding === "deflate"
            ? createInflate()
            : encoding === "br"
              ? createBrotliDecompress()
              : undefined;
      if (decoder) {
        body = decoder;
        pipeline(incoming.body, decoder, () => {});
      }
      const reader = body[Symbol.asyncIterator]();
      let cancelled = false;
      const stream = new ReadableStream<Uint8Array>({
        async pull(controller) {
          try {
            const chunk = await reader.next();
            if (cancelled) return;
            if (chunk.done) {
              await settle();
              if (!cancelled) controller.close();
            } else controller.enqueue(chunk.value);
          } catch (error) {
            await settle();
            if (!cancelled)
              controller.error(signal.aborted ? signal.reason : error);
          }
        },
        async cancel(reason) {
          cancelled = true;
          incoming.body.destroy();
          body.destroy();
          try {
            await reader.return?.();
          } finally {
            await settle();
          }
        },
      });
      return logicalResponse(
        new Response(stream, {
          status,
          statusText: incoming.statusText,
          headers: responseHeaders,
        }),
        request.url,
      );
    } catch (error) {
      await settle();
      if (signal.aborted) throw signal.reason;
      if (error instanceof TypeError) throw error;
      if (error instanceof Error && error.message === "bad upgrade")
        throw new TypeError("Invalid selected egress HTTP upgrade");
      throw new TypeError("fetch failed", { cause: error });
    }
  };
}

export const egressFetch = createGatewayFetch();

export interface GaxiosSelectedInit extends Omit<RequestInit, "body"> {
  body?: BodyInit | Readable | null;
  timeout?: number;
  size?: number;
  compress?: boolean;
}

/** Gaxios's explicit hook retains Node-readable bodies and node-fetch controls. */
export function createGaxiosFetch(fetchImpl = egressFetch) {
  return async (input: string | URL, init?: GaxiosSelectedInit) => {
    const timeout = new AbortController();
    const signal = init?.signal
      ? AbortSignal.any([init.signal, timeout.signal])
      : timeout.signal;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const clear = () => {
      if (timer) clearTimeout(timer);
    };
    const start = (type: "request-timeout" | "body-timeout") => {
      if (init?.timeout && init.timeout > 0) {
        timer = setTimeout(() => {
          const error = Object.assign(
            new Error("Selected egress request timed out"),
            {
              name: "FetchError",
              type,
            },
          );
          timeout.abort(error);
        }, init.timeout);
        timer.unref();
      }
    };
    const body =
      init?.body instanceof Readable ? Readable.toWeb(init.body) : init?.body;
    start("request-timeout");
    let response: Response;
    try {
      response = await fetchImpl(input, {
        ...init,
        body,
        signal,
        duplex: "half",
        gatewayDecodeContent: init?.compress !== false,
      } as GatewayFetchInit);
    } finally {
      clear();
    }
    let nodeBody: Readable | null | undefined;
    const consume = async (): Promise<Uint8Array> => {
      if (response.bodyUsed || response.body?.locked)
        throw new TypeError("Body is unusable");
      if (!response.body) return new Uint8Array();
      const reader = response.body.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      start("body-timeout");
      try {
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          size += chunk.value.length;
          if (init?.size && init.size > 0 && size > init.size) {
            await reader.cancel();
            throw Object.assign(
              new Error("Selected egress response exceeds configured size"),
              {
                name: "FetchError",
                type: "max-size",
              },
            );
          }
          chunks.push(chunk.value);
        }
        const result = new Uint8Array(size);
        let offset = 0;
        for (const chunk of chunks) {
          result.set(chunk, offset);
          offset += chunk.length;
        }
        return result;
      } catch (error) {
        if (init?.signal?.aborted) throw init.signal.reason;
        if (timeout.signal.aborted) throw timeout.signal.reason;
        throw error;
      } finally {
        clear();
        reader.releaseLock();
      }
    };
    return {
      status: response.status,
      statusText: response.statusText,
      url: response.url,
      headers: response.headers,
      get body() {
        if (nodeBody === undefined)
          nodeBody = response.body
            ? Readable.fromWeb(
                response.body as import("node:stream/web").ReadableStream<Uint8Array>,
              )
            : null;
        return nodeBody;
      },
      text: async () => new TextDecoder().decode(await consume()),
      json: async () =>
        JSON.parse(new TextDecoder().decode(await consume())) as unknown,
      arrayBuffer: async () => (await consume()).buffer,
      blob: async () =>
        new Blob([await consume()], {
          type: response.headers.get("content-type") ?? "",
        }),
    };
  };
}
export const egressGaxiosFetch = createGaxiosFetch();

export function gatewayWebSocket(url: string, headers: Record<string, string>) {
  const config = gatewayConfig();
  if (!config) return undefined;
  const logical = new URL(url);
  const selected = headersForGateway(new Headers(headers), logical);
  selected.delete("connection");
  return {
    url: gatewayURL(logical, config, true).toString(),
    headers: Object.fromEntries(selected),
  };
}
