## Tools
- OpenCode (in-app coding agent, model: Beam Free) for repository inspection, implementation, and test debugging.

## What AI got wrong
- Initially assumed a starter repository was present; the working directory was empty, so the project had to be scaffolded from the assignment specification.
- First test run hit the token bucket because the invalid-body test sent more requests than the configured capacity; adjusted test request counts and used a dedicated small-capacity app for rate-limit checks.
- Early migration used a SQLite-specific JSON import; switched to the generic SQLAlchemy JSON type so metadata comparison passes.

## In my own words

1. **Why does a transfer to someone else's account return 404 and not 403?**

   We return 404 instead of 403 to avoid revealing whether an account exists or belongs to another customer. If we return 403, an attacker may learn that the account exists but they are not allowed to access it. Returning 404 in both cases makes it harder to discover other customers' accounts.

2. **What would happen to a customer if your idempotency record were written in a separate transaction after the transfer?**

   If the transfer succeeds but the application crashes before saving the idempotency record, the customer may retry the request because they did not receive a response. Since the system has no record of the first request, it may execute the transfer again and deduct the money twice. Saving both the transfer and its idempotency record in the same transaction prevents this inconsistency.