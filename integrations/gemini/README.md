# Google Gemini / Antigravity Skill Integration

The Tana Semantic Engine skill is packaged natively in this repository:
- Path: `skills/tana-semantic-engine/SKILL.md`

## Usage with Antigravity / Gemini CLI

To enable the skill in your local agent runtime:
1. In your project's `.agents/skills.json` (or `~/.gemini/skills`):
```json
{
  "entries": [
    { "path": "./skills/tana-semantic-engine" }
  ]
}
```
2. The agent will automatically discover `tana-semantic-engine` and apply the GraphRAG retrieval workflow, native reference formatting (`[[^nodeID]]`), and canonical chat link rules (`https://app.tana.inc?nodeid=<NODE_ID>`).
