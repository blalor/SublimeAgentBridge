import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";


type BridgeConnection = {
  url: string;
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

const CONNECTION_FILE = join(
  homedir(),
  "Library/Application Support/Sublime Text/Cache/Sublime Agent Bridge/connection.json",
);

async function readConnection(): Promise<BridgeConnection> {
  const raw = await readFile(CONNECTION_FILE, "utf8");
  const connection = JSON.parse(raw) as BridgeConnection;
  if (!connection.url || !connection.token) {
    throw new Error(`Invalid Sublime Agent Bridge connection file: ${CONNECTION_FILE}`);
  }
  return connection;
}

async function callBridge(method: string, params: Record<string, unknown> = {}, signal?: AbortSignal): Promise<unknown> {
  const connection = await readConnection();
  const response = await fetch(`${connection.url}/rpc`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "authorization": `Bearer ${connection.token}`,
    },
    body: JSON.stringify({ id: `${Date.now()}-${Math.random()}`, method, params }),
    signal,
  });

  const data = (await response.json()) as RpcResponse;
  if (!response.ok || !data.ok) {
    throw new Error(data.error || `Sublime Agent Bridge HTTP ${response.status}`);
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
