# Demo script (< 3 min)
1. (0:00) Open http://localhost:8000. Alice books a Haircut by tapping a slot. Say: "this is exactly what Alexa+ calls through MCP".
2. (0:30) Bob joins the waitlist. Alice cancels. Bob sees "Slot freed!" and accepts. Show stats: refilled 1, recovered, source label "live test events".
3. (1:15) Terminal: `generator.cli build examples/clinic.yaml`; show SKILL.md and addon.json.
4. (1:50) `generator.cli lint ...`: 12 checks pass; open docs/lint-report/report.html.
5. (2:30) State honestly: persistence/AWS deploy and Alexa+ Preview testing are next.
