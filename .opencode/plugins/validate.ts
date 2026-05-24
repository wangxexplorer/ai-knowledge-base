import type { Plugin } from "@opencode-ai/plugin"

export const ValidateHook: Plugin = async ({ $ }) => {
  return {
    "tool.execute.after": async (input) => {
      const tool = input.tool
      const filePath = input.args?.file_path ?? input.args?.filePath ?? ""

      if (
        (tool === "write" || tool === "edit") &&
        typeof filePath === "string" &&
        filePath.includes("knowledge/articles/") &&
        filePath.endsWith(".json")
      ) {
        try {
          await $`python3 hooks/validate_json.py ${filePath}`.nothrow()
        } catch {}
      }
    },
  }
}
