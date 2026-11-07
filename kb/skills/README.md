# Claude skill for the sorghum-protein MCP

The MCP server gives Claude hybrid vector + knowledge-graph tools. This skill tells Claude
how to retrieve relationship evidence, update both indexes when adding papers, perform
evidence-reviewed graph enrichment, cite titles/DOIs, and never invent a paper list.

## Attach in Claude Desktop

1. Confirm **sorghum-protein** MCP is connected (local stdio on this Mac, or the remote bridge on another lab machine).
2. Zip the folder `sorghum-protein-kb` (the zip must contain that folder at the top level, with `SKILL.md` inside it). A ready zip is `sorghum-protein-kb.zip` next to this README if you built it.
3. Claude Desktop → **Settings → Capabilities → Skills** (wording may be **Customize → Skills**).
4. Upload the zip, enable the skill, start a **new** chat.
5. Ask something like: *Why does wet cooking reduce sorghum protein digestibility?*

Keep the MCP server **and** the skill enabled. The skill does not replace the server.

## Share with the lab

Send `sorghum-protein-kb.zip` plus the MCP config. Each person uploads the skill on their own Claude Desktop.
