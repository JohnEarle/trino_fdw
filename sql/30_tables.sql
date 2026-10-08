-- Either declare tables by hand ...
CREATE FOREIGN TABLE audit_events (
    id          bigint,
    event_time  timestamp with time zone,
    username    text,
    action      text
) SERVER trino OPTIONS (table 'audit_events');

-- ... or import a whole Trino schema (uses information_schema).
-- IMPORT FOREIGN SCHEMA prod LIMIT TO (audit_events, users) FROM SERVER trino INTO public;

-- A raw query is also allowed. Useful for views or to verify impersonation.
CREATE FOREIGN TABLE trino_whoami (u text)
    SERVER trino OPTIONS (query 'SELECT current_user AS u');

GRANT SELECT ON audit_events, trino_whoami TO reader;

-- Incremental polling pattern (WHERE and ORDER BY are pushed to Trino):
--   SELECT * FROM audit_events WHERE id > $last_seen ORDER BY id
