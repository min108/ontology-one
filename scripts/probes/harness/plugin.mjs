// Probe-only bounded tools. No filesystem, shell, arbitrary network or business data.
import { request } from "node:http";
export const inject = ["tools"];
export function apply(ctx) {
  const port = Number(process.env.PROBE_PORT);
  const token = process.env.PROBE_TOKEN;
  delete process.env.PROBE_TOKEN;
  const report = (value) =>
    new Promise((resolve) => {
      const body = JSON.stringify(value);
      const req = request(
        {
          hostname: "127.0.0.1",
          port,
          path: "/metric",
          method: "POST",
          headers: {
            Authorization: "Bearer " + token,
            "Content-Type": "application/json",
            "Content-Length": Buffer.byteLength(body),
          },
        },
        (res) => {
          res.resume();
          res.on("end", resolve);
        },
      );
      req.on("error", resolve);
      req.end(body);
    });
  let steps = 0,
    calls = 0;
  ctx.on("agent/pre-step", async (_payload, next) => {
    if (++steps > 45) throw new Error("Probe step limit");
    return next();
  });
  ctx.tools.guard((exec) =>
    ["probe_read", "probe_write"].includes(exec.name) ? undefined : "Denied",
  );
  for (const name of ["probe_read", "probe_write"])
    ctx.tools.register({
      name,
      description: "Read synthetic probe data; never real business data.",
      parameters: {
        type: "object",
        properties: {},
        additionalProperties: false,
      },
      isConcurrencySafe: () => name === "probe_read",
      output: {
        schema: { type: "string" },
        render: (_args, value) => [{ type: "text", text: value }],
      },
      async execute() {
        if (++calls > 120) throw new Error("Probe call limit");
        await report({ type: "tool_start", name });
        await new Promise((resolve) => setTimeout(resolve, 40));
        await report({ type: "tool_end", name });
        return process.env.PROBE_CASE === "compaction"
          ? "synthetic detail ".repeat(2000)
          : "synthetic";
      },
    });
  ctx.on("agent/assistant-stream", ({ frame }) => {
    if (frame?.type === "chunk" && frame.chunk?.type === "text-delta")
      void report({ type: "text_delta" });
  });
}
