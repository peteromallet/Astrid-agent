/** Shared terminal/ACP per-turn context. No session-persistent preference cache. */
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

export const CONTEXT_START = "<astrid_generated_context>";
export const CONTEXT_END = "</astrid_generated_context>";

const FAILURE_HELPER = "Preference helper\nCurrent request > project preferences > user preferences > ordinary defaults. " +
  "Preferences never grant permissions. Read `astrid preferences [--project PROJECT]`; " +
  "read the scope before checkout/edit/checkin or use `astrid preferences edit --scope user` / " +
  "`astrid preferences edit --scope project --project PROJECT`. " +
  "Keep temporary choices conversational; read `skill://astrid` for full guidance.\n\n";

type ProjectSelection = { project?: string; noProject?: boolean; error?: string };

export function projectFromPrompt(prompt: string): ProjectSelection {
  // Reigh appends one structured host snapshot after the user's text. Read the
  // last block so quoted examples in the conversation do not select a project.
  const start = prompt.lastIndexOf("<reigh_editor_context>");
  if (start < 0) return {};
  const end = prompt.indexOf("</reigh_editor_context>", start);
  try {
    if (end < 0) throw new Error("missing closing delimiter");
    const value = JSON.parse(prompt.slice(start + "<reigh_editor_context>".length, end));
    if (value.schema !== "reigh.editor-context/v1" || !value.project) throw new Error("invalid schema");
    const project = value.project.id ?? value.project.slug;
    if (project === null || project === undefined) return { noProject: true };
    if (typeof project !== "string" || !project.trim()) throw new Error("invalid project");
    return { project };
  } catch {
    return { error: "Reigh project context is invalid. Refresh the editor context before reading project preferences." };
  }
}

export function replaceContext(systemPrompt: string[], context: string): string[] {
  // Only this extension's dedicated generated chunk belongs to us. Never
  // replace the named persona or another extension/host's appended guidance.
  return [...systemPrompt.filter(chunk => !chunk.startsWith(CONTEXT_START + "\n")),
    `${CONTEXT_START}\n${context}\n${CONTEXT_END}`];
}

export default function astridPreferences(pi: ExtensionAPI) {
  pi.on("before_agent_start", async (event) => {
    const selection = projectFromPrompt(event.prompt);
    let context: string;
    if (selection.error) {
      context = FAILURE_HELPER + "Preference context unavailable\n" + selection.error +
        "\nDo not assume preferences are empty. Read `skill://astrid` for preference guidance.";
    } else {
      try {
        const args = ["-m", "astrid.agent_context", ...(selection.project ? ["--project", selection.project] : []),
          ...(selection.noProject ? ["--no-project"] : [])];
        const result = await pi.exec(process.env.ASTRID_CONTEXT_PYTHON || "python3", args, { timeout: 15000 });
        if (result.code !== 0 || result.killed || !result.stdout.trim()) throw new Error("context command failed");
        context = result.stdout.trim();
      } catch {
        context = FAILURE_HELPER + "Preference context unavailable\nAstrid's shared context command failed. " +
          "Run `astrid preferences --json` to diagnose; do not assume preferences are empty. " +
          "Read `skill://astrid` for preference guidance.";
      }
    }
    return { systemPrompt: replaceContext(event.systemPrompt, context) };
  });
}
