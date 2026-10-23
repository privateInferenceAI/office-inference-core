# badj.ai test tenant freeze

**Date:** 2026-10-23. **Status:** stopped.

- Instance: i-0b6aa00095ae40e54 (badj.ai account 426063972668).
- Elastic IP: eipalloc-0da2730bda34519ff, 34.225.62.245, retained.
- DNS: mcp.badj.ai still points to the Elastic IP.
- Services: stopped with the instance. Caddy, the MCP gateway, the connector and the tenant SES path are not
  running while the instance is stopped.
- Billing: compute stops. EBS storage and the idle Elastic IP remain billable; both are small compared with the
  running instance.

## Thaw

1. Start i-0b6aa00095ae40e54.
2. Confirm the Elastic IP is still associated and 34.225.62.245 answers.
3. Start Caddy and the client services, then run the tenant health and SES checks.

The dev box in account 878 is the active test environment while this tenant is frozen. The tenant is a fixture,
not a customer.
