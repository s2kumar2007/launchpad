# Architecture
Customer -> Alexa+ -> `POST /mcp` (Streamable HTTP, stateless) -> `AuthGate` (401 on bad token; no token = guest) -> FastMCP tools -> `Store`.
- **Slot engine:** one 288-bit mask per staff/resource/day (5-min units); free run = AND of masks; buffers widen the run; top-3 ranked by closeness and gap-avoidance.
- **Matcher:** normalise + Jaro-Winkler + trigram overlap; auto-accept only above threshold with a margin, else candidate list. (Double Metaphone not implemented.)
- **Idempotency:** key = SHA-256(customer, tool, canonical args); failures are not cached. Slot reservation is a locked conditional write (DynamoDB ConditionExpression equivalent).
- **Gap Filler (in-process):** cancel -> slot kept HELD -> best waitlisted customer by `0.5*wait + 0.5*reliability` gets an offer with a hold timer -> `accept_offer` wins first -> expiry frees the slot or offers the next. Events: BOOKED, CANCELLED, OFFERED, EXPIRED, REFILLED.
- **Auth:** `oauth/` implements authorization code + PKCE S256, RFC 8414 metadata, RFC 9728 PRM, `resource` check, single-use codes. Tokens are opaque and in memory. Customer id comes only from the validated token.
- **Latency:** no model or network call inside tools. Measured local numbers: `docs/latency-report.md`. AWS numbers: not measured.
## Not implemented
Persistence (state is in memory and resets on restart; **not** safe for multiple instances), SNS notifications, Lambda/Streams, CDK, Strands insights, orders pack and simulated checkout, MCP Apps UI resources.
