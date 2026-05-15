import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { createConnection } from "node:net";

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";


type BridgeConnection = {
  transport?: "unix" | "tcp";
  url?: string;
  socketPath?: string;
  token: string;
  pid?: number;
  package?: string;
  sublimeVersion?: string;
};

type RpcResponse = {
  id?: string;
  ok: boolean;
  result?: unknown;
  error?: string;
  traceback?: string;
};

const CONNECTION_FILES = [
  join(homedir(), "Library/Caches/Sublime Text/Cache/Sublime Agent Bridge/connection.json"),
  join(homedir(), "Library/Application Support/Sublime Text/Cache/Sublime Agent Bridge/connection.json"),
];

async function readConnection(): Promise<BridgeConnection> {
  let lastError: unknown;
  for (const file of CONNECTION_FILES) {
    try {
      const raw = await readFile(file, "utf8");
      const connection = JSON.parse(raw) as BridgeConnection;
      if ((!connection.url && !connection.socketPath) || !connection.token) {
        throw new Error(`Invalid Sublime Agent Bridge connection file: ${file}`);
      }
      return connection;
    } catch (error) {
      lastError = error;
    }
  }
  throw new Error(`Could not read Sublime Agent Bridge connection file from: ${CONNECTION_FILES.join(", ")} (${lastError})`);
}

function callUnixSocket(socketPath: string, payload: Record<string, unknown>, signal?: AbortSignal): Promise<RpcResponse> {
  return new Promise((resolve, reject) => {
    const socket = createConnection(socketPath);
    let buffer = "";
    const cleanup = () => {
      signal?.removeEventListener("abort", onAbort);
      socket.removeAllListeners();
    };
    const onAbort = () => {
      socket.destroy();
      cleanup();
      reject(new Error("Sublime bridge RPC aborted"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    socket.setEncoding("utf8");
    socket.on("connect", () => socket.write(JSON.stringify(payload) + "\n"));
    socket.on("data", (chunk) => {
      buffer += chunk;
      const newline = buffer.indexOf("\n");
      if (newline === -1) return;
      const line = buffer.slice(0, newline);
      cleanup();
      socket.end();
      resolve(JSON.parse(line) as RpcResponse);
    });
    socket.on("error", (error) => {
      cleanup();
      reject(error);
    });
  });
}

async function callBridge(method: string, params: Record<string, unknown> = {}, signal?: AbortSignal): Promise<unknown> {
  const connection = await readConnection();
  const payload = { id: `${Date.now()}-${Math.random()}`, method, params, token: connection.token };
  let data: RpcResponse;
  if ((connection.transport === "unix" || connection.socketPath) && connection.socketPath) {
    data = await callUnixSocket(connection.socketPath, payload, signal);
  } else {
    const response = await fetch(`${connection.url}/rpc`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "authorization": `Bearer ${connection.token}`,
      },
      body: JSON.stringify(payload),
      signal,
    });
    data = (await response.json()) as RpcResponse;
    if (!response.ok) {
      throw new Error(data.error || `Sublime Agent Bridge HTTP ${response.status}`);
    }
  }

  if (!data.ok) {
    throw new Error(data.error || "Sublime Agent Bridge RPC failed");
  }
  return data.result;
}

function stringifyResult(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function resultContent(value: unknown) {
  return [{ type: "text" as const, text: stringifyResult(value) }];
}

const windowParam = Type.Optional(Type.Union([
  Type.Literal("active"),
  Type.Integer({ description: "Window index or Sublime window id" }),
]));

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "sublime_ping",
    label: "Sublime Ping",
    description: "Check whether the local Sublime Agent Bridge is reachable.",
    promptSnippet: "Check whether the running Sublime Text bridge is reachable",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, signal) {
      const result = await callBridge("ping", {}, signal);
      return { content: resultContent(result), details: result };
    },
  });

  pi.registerTool({
    name: "sublime_status",
    label: "Sublime Status",
    description: "Return Sublime Agent Bridge status and discovery information.",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, signal) {
      const result = await callBridge("status", {}, signal);
      return { content: resultContent(result), details: result };
    },
  });

  pi.registerTool({
    name: "sublime_list_windows",
    label: "Sublime Windows",
    description: "List open Sublime Text windows, folders, and active views.",
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, signal) {
      const result = await callBridge("list_windows", {}, signal);
      return { content: resultContent(result), details: result };
    },
  });

  pi.registerTool({
    name: "sublime_list_views",
    label: "Sublime Views",
    description: "List views in a Sublime Text window.",
    parameters: Type.Object({
      window: windowParam,
    }),
    async execute(_toolCallId, params, signal) {
      const result = await callBridge("list_views", params, signal);
      return { content: resultContent(result), details: result };
    },
  });

  pi.registerTool({
    name: "sublime_run_command",
    label: "Sublime Command",
    description: "Run a Sublime Text window or text command through the local bridge. Use sparingly; prefer dedicated tools when available.",
    parameters: Type.Object({
      target: Type.Optional(Type.Union([Type.Literal("window"), Type.Literal("text")], { description: "Command target; defaults to window" })),
      command: Type.String({ description: "Sublime command name, e.g. env_doctor" }),
      args: Type.Optional(Type.Record(Type.String(), Type.Any(), { description: "Command arguments" })),
      window: windowParam,
      view: Type.Optional(Type.Integer({ description: "Sublime view id for text commands" })),
    }),
    async execute(_toolCallId, params, signal) {
      const method = params.target === "text" ? "run_text_command" : "run_window_command";
      const result = await callBridge(method, params, signal);
      return { content: resultContent(result), details: result };
    },
  });

  pi.registerTool({
    name: "sublime_get_output_panel",
    label: "Sublime Output Panel",
    description: "Read text from a Sublime Text output panel.",
    parameters: Type.Object({
      panel: Type.String({ description: "Panel name without output. prefix, e.g. env_doctor" }),
      window: windowParam,
    }),
    async execute(_toolCallId, params, signal) {
      const result = await callBridge("get_output_panel", params, signal);
      return { content: resultContent(result), details: result };
    },
  });

  pi.registerTool({
    name: "sublime_resolve_environment",
    label: "Sublime Resolve Environment",
    description: "Resolve a deterministic direnv/Flox-backed environment for a Sublime window without changing UI state.",
    parameters: Type.Object({
      window: windowParam,
      path: Type.Optional(Type.String({ description: "Optional file or directory path to resolve from" })),
      tools: Type.Optional(Type.Array(Type.String(), { description: "Command names to locate in the resolved PATH" })),
      interestingVars: Type.Optional(Type.Array(Type.String(), { description: "Environment variable names to return" })),
      includeEnv: Type.Optional(Type.Boolean({ description: "Return the full environment; may include secrets" })),
    }),
    async execute(_toolCallId, params, signal) {
      const result = await callBridge("resolve_environment", params, signal);
      return { content: resultContent(result), details: result };
    },
  });

  pi.registerTool({
    name: "sublime_which",
    label: "Sublime Which",
    description: "Resolve command paths for a Sublime window after applying its direnv/Flox environment.",
    parameters: Type.Object({
      window: windowParam,
      path: Type.Optional(Type.String({ description: "Optional file or directory path to resolve from" })),
      tools: Type.Array(Type.String(), { description: "Command names to locate" }),
    }),
    async execute(_toolCallId, params, signal) {
      const result = await callBridge("which", params, signal);
      return { content: resultContent(result), details: result };
    },
  });

  pi.registerTool({
    name: "sublime_env_doctor",
    label: "Sublime Env Doctor",
    description: "Run Env Doctor inside the running Sublime Text process and return its output panel text.",
    promptSnippet: "Run Env Doctor inside Sublime Text and inspect the real plugin-host environment",
    promptGuidelines: [
      "Use sublime_env_doctor when verifying Sublime Text environment/plugin behavior from inside the running Sublime process.",
    ],
    parameters: Type.Object({
      window: windowParam,
    }),
    async execute(_toolCallId, params, signal) {
      const result = await callBridge("env_doctor", params, signal);
      return { content: resultContent(result), details: result };
    },
  });

  pi.registerCommand("sublime-status", {
    description: "Show Sublime Agent Bridge status",
    handler: async (_args, ctx) => {
      try {
        const result = await callBridge("status", {}, ctx.signal);
        ctx.ui.notify(stringifyResult(result), "info");
      } catch (error) {
        ctx.ui.notify(error instanceof Error ? error.message : String(error), "error");
      }
    },
  });

  pi.registerCommand("sublime-env-doctor", {
    description: "Run Env Doctor inside Sublime and show the result",
    handler: async (_args, ctx) => {
      try {
        const result = await callBridge("env_doctor", {}, ctx.signal);
        ctx.ui.notify(stringifyResult(result), "info");
      } catch (error) {
        ctx.ui.notify(error instanceof Error ? error.message : String(error), "error");
      }
    },
  });
}
