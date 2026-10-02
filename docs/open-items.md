# Open items
## TODO(verify) against Alexa+ docs
- addon.json: `manifestVersion`, privacy/terms key names, `mediaAssets` layout, whether 6 icon sizes + 600x900 image are required. (Verified: `storeListing.examplePhrases` 3-4 <=200 chars, `distributionCountries`, `integrations[].type="MCP"` + `uri`.) Prefer `alexa-ai new mcp` output as the source of truth.
- Alexa+ OAuth redirect URIs and client credentials (set `OAUTH_REDIRECTS`, `OAUTH_CLIENT_SECRET`).
- Agent Skills spec details (validator checks name pattern, <=64 chars, description <=1024, frontmatter) written from memory, not re-read.
- MCP spec 2025-11-25 conformance of the installed SDK: handshake negotiated 2025-11-25 in tests; MCP Apps (`resourceUri`) not implemented.
- Whether Alexa+ passes a usable account id/token per linked account exactly as assumed.
## Licence checks
- No-show dataset (Kaggle "Medical Appointment No Shows"): licence NOT yet checked (I believe it is non-commercial; confirm before any commercial use). Not committed. `evaluation/results.json` currently holds a SYNTHETIC replay.
## Not built
DynamoDB store, DynamoDB Streams + Lambda, SNS offers, CDK/SAM, AgentCore deploy, Strands insights agent, hour-by-weekday heatmap, orders pack + simulated checkout, Cognito owner login, web-simulator/Alexa+ Preview testing (needs account access), demo video.
