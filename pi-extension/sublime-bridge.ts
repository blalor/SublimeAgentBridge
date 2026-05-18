import { realpathSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { createConnection } from "node:net";
import { fileURLToPath } from "node:url";

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";


type BridgeConnection = {
  socketPath: string;
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

type BridgeParams = Record<string, unknown>;

type BridgeTool = {
  name: string;
  label: string;
  description: string;
  method: string;
  parameters: ReturnType<typeof Type.Object>;
  promptSnippet?: string;
  mapParams?: (params: BridgeParams) => { method: string; params: BridgeParams };
};

const EXTENSION_DIR = dirname(realpathSync(fileURLToPath(import.meta.url)));
const SKILLS_DIR = join(EXTENSION_DIR, "skills");

const CONNECTION_FILES = [
  join(homedir(), "Library/Caches/Sublime Text/Cache/Agent Bridge/connection.json"),
  // Legacy package name used by earlier versions of the Sublime package.
  join(homedir(), "Library/Caches/Sublime Text/Cache/Sublime Agent Bridge/connection.json"),
];

async function readConnection(): Promise<BridgeConnection> {
  let lastError: unknown;
  for (const file of CONNECTION_FILES) {
    try {
      const raw = await readFile(file, "utf8");
      const connection = JSON.parse(raw) as BridgeConnection;
      if (!connection.socketPath || !connection.token) {
        throw new Error(`Invalid Agent Bridge connection file: ${file}`);
      }
      return connection;
    } catch (error) {
      lastError = error;
    }
  }
  throw new Error(`Could not read Agent Bridge connection file from: ${CONNECTION_FILES.join(", ")} (${lastError}). STOP and ask the user to start or re-enable the Sublime Agent Bridge server in Sublime Text before continuing.`);
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

// All Pi extension RPC traffic to Sublime Text must go through this helper.
// Do not shell out to scripts/rpc.py or construct ad-hoc bridge clients in tools/commands.
async function callBridge(method: string, params: BridgeParams = {}, signal?: AbortSignal): Promise<unknown> {
  const connection = await readConnection();
  const payload = { id: `${Date.now()}-${Math.random()}`, method, params, token: connection.token };
  const data = await callUnixSocket(connection.socketPath, payload, signal);

  if (!data.ok) {
    throw new Error(data.error || "Agent Bridge RPC failed");
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

function registerBridgeTool(pi: ExtensionAPI, tool: BridgeTool) {
  pi.registerTool({
    name: tool.name,
    label: tool.label,
    description: tool.description,
    promptSnippet: tool.promptSnippet,
    parameters: tool.parameters,
    async execute(_toolCallId, params, signal) {
      const request = tool.mapParams ? tool.mapParams(params as BridgeParams) : { method: tool.method, params: params as BridgeParams };
      const result = await callBridge(request.method, request.params, signal);
      return { content: resultContent(result), details: result };
    },
  });
}

const BRIDGE_TOOLS: BridgeTool[] = [
  {
    name: "sublime_ping",
    label: "Sublime Ping",
    description: "Check whether the local Agent Bridge is reachable. If this cannot connect or connection.json is missing, STOP and ask the user to start or re-enable the Sublime Agent Bridge server in Sublime Text before continuing.",
    promptSnippet: "Check whether the running Sublime Text bridge is reachable. If connection.json is missing or the bridge cannot be reached, stop and ask the user to start/re-enable the Sublime Agent Bridge server.",
    method: "ping",
    parameters: Type.Object({}),
  },
  {
    name: "sublime_status",
    label: "Sublime Status",
    description: "Return Agent Bridge status and discovery information.",
    method: "status",
    parameters: Type.Object({}),
  },
  {
    name: "sublime_list_windows",
    label: "Sublime Windows",
    description: "List open Sublime Text windows, folders, and active views.",
    method: "list_windows",
    parameters: Type.Object({}),
  },
  {
    name: "sublime_list_views",
    label: "Sublime Views",
    description: "List views in a Sublime Text window.",
    method: "list_views",
    parameters: Type.Object({ window: windowParam }),
  },
  {
    name: "sublime_scope_debug",
    label: "Sublime Scope Debug",
    description: "Inspect scopes around a point in a Sublime view.",
    method: "scope_debug",
    parameters: Type.Object({
      window: windowParam,
      view: Type.Optional(Type.Integer({ description: "Sublime view id" })),
      point: Type.Optional(Type.Integer({ description: "Buffer point" })),
      row: Type.Optional(Type.Integer({ description: "Line/row number; 1-based unless row_base is 0" })),
      line: Type.Optional(Type.Integer({ description: "1-based line number" })),
      row_base: Type.Optional(Type.Integer({ description: "Set to 0 when row is zero-based" })),
      col: Type.Optional(Type.Integer({ description: "Column number" })),
      column: Type.Optional(Type.Integer({ description: "Column number" })),
      context: Type.Optional(Type.Integer({ description: "Number of surrounding lines" })),
    }),
  },
  {
    name: "sublime_run_command",
    label: "Sublime Command",
    description: "Run a Sublime Text window or text command through the local bridge. Use sparingly; prefer dedicated tools when available.",
    method: "run_window_command",
    parameters: Type.Object({
      target: Type.Optional(Type.Union([Type.Literal("window"), Type.Literal("text")], { description: "Command target; defaults to window" })),
      command: Type.String({ description: "Sublime command name" }),
      args: Type.Optional(Type.Record(Type.String(), Type.Any(), { description: "Command arguments" })),
      window: windowParam,
      view: Type.Optional(Type.Integer({ description: "Sublime view id for text commands" })),
    }),
    mapParams: (params) => ({
      method: params.target === "text" ? "run_text_command" : "run_window_command",
      params,
    }),
  },
  {
    name: "sublime_list_output_panels",
    label: "Sublime Output Panels",
    description: "List output panels in a Sublime Text window.",
    method: "list_output_panels",
    parameters: Type.Object({ window: windowParam }),
  },
  {
    name: "sublime_get_output_panel",
    label: "Sublime Output Panel",
    description: "Read text from a Sublime Text output panel.",
    method: "get_output_panel",
    parameters: Type.Object({
      panel: Type.String({ description: "Panel name without output. prefix" }),
      window: windowParam,
    }),
  },
];

export default function (pi: ExtensionAPI) {
  pi.on("resources_discover", async () => ({
    skillPaths: [SKILLS_DIR],
  }));

  for (const tool of BRIDGE_TOOLS) {
    registerBridgeTool(pi, tool);
  }

  pi.registerCommand("sublime-status", {
    description: "Show Agent Bridge status",
    handler: async (_args, ctx) => {
      try {
        const result = await callBridge("status", {}, ctx.signal);
        ctx.ui.notify(stringifyResult(result), "info");
      } catch (error) {
        ctx.ui.notify(error instanceof Error ? error.message : String(error), "error");
      }
    },
  });
}
