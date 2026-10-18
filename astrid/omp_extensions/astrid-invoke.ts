/** Packaged Astrid admission boundary; OMP owns persistence and ACP transport. */
export default function (pi: any) {
  const z = pi.zod;
  pi.registerTool({
    name: "astrid_invoke",
    label: "Astrid task",
    loadMode: "essential",
    description: "Admit generation, rendering or media work to Astrid Runtime. Returns immediately with the real task identity; Runtime continues execution. Use the selected project from Astrid App editor context. Read the capability skill for its inputs.",
    parameters: z.object({
      capability_id: z.string().min(1),
      kind: z.enum(["executor", "orchestrator"]),
      project: z.string().min(1),
      inputs: z.record(z.string(), z.unknown()),
      execution_request: z.record(z.string(), z.unknown()).optional(),
    }),
    async execute(_toolCallId: string, params: unknown, signal: AbortSignal | undefined, _onUpdate: unknown, ctx: any) {
      const python = process.env.ASTRID_OMP_PYTHON;
      if (!python) throw new Error("Astrid launcher did not select its Python interpreter");
      const response = await pi.exec(python, ["-m", "astrid.omp_invoke", JSON.stringify(params)],
        { cwd: ctx.cwd, signal, timeout: 120000 });
      if (response.killed) throw new Error("Astrid admission was interrupted");
      try {
        return JSON.parse(response.stdout);
      } catch {
        throw new Error(response.stderr || "Astrid admission did not return a structured result");
      }
    },
  });
}
