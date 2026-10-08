-- Defence in depth: refuse any DDL that would store a secret-looking option
-- on a trino_fdw server, its user mappings or its foreign tables.
-- The Python wrapper refuses the same options at runtime; this trigger stops
-- the secret from ever reaching the catalog, a base backup or pg_dump.

CREATE OR REPLACE FUNCTION trino_fdw_guard() RETURNS event_trigger
LANGUAGE plpgsql AS $$
DECLARE
    forbidden text[] := ARRAY[
        'password','passwd','pwd','pass','token','jwt','bearer','secret',
        'client_secret','api_key','apikey','private_key','key','keytab'];
    offender text;
BEGIN
    SELECT string_agg(DISTINCT what, ', ') INTO offender
    FROM (
        SELECT 'server ' || s.srvname AS what
        FROM pg_foreign_server s
        JOIN pg_foreign_data_wrapper w ON w.oid = s.srvfdw AND w.fdwname = 'multicorn'
        WHERE EXISTS (SELECT 1 FROM unnest(s.srvoptions) o
                      WHERE split_part(o, '=', 1) = 'wrapper'
                        AND split_part(o, '=', 2) LIKE 'trino_fdw.%')
          AND EXISTS (SELECT 1 FROM unnest(s.srvoptions) o
                      WHERE lower(split_part(o, '=', 1)) = ANY (forbidden))
        UNION ALL
        SELECT 'user mapping on server ' || s.srvname
        FROM pg_user_mapping um
        JOIN pg_foreign_server s ON s.oid = um.umserver
        JOIN pg_foreign_data_wrapper w ON w.oid = s.srvfdw AND w.fdwname = 'multicorn'
        WHERE EXISTS (SELECT 1 FROM unnest(s.srvoptions) o
                      WHERE split_part(o, '=', 1) = 'wrapper'
                        AND split_part(o, '=', 2) LIKE 'trino_fdw.%')
          AND EXISTS (SELECT 1 FROM unnest(um.umoptions) o
                      WHERE lower(split_part(o, '=', 1)) = ANY (forbidden))
        UNION ALL
        SELECT 'foreign table ' || ft.ftrelid::regclass::text
        FROM pg_foreign_table ft
        JOIN pg_foreign_server s ON s.oid = ft.ftserver
        JOIN pg_foreign_data_wrapper w ON w.oid = s.srvfdw AND w.fdwname = 'multicorn'
        WHERE EXISTS (SELECT 1 FROM unnest(s.srvoptions) o
                      WHERE split_part(o, '=', 1) = 'wrapper'
                        AND split_part(o, '=', 2) LIKE 'trino_fdw.%')
          AND EXISTS (SELECT 1 FROM unnest(ft.ftoptions) o
                      WHERE lower(split_part(o, '=', 1)) = ANY (forbidden))
    ) x;

    IF offender IS NOT NULL THEN
        RAISE EXCEPTION 'trino_fdw: secret-bearing option refused on %', offender
            USING HINT = 'Store the secret in a file on the database host and reference it with password_file, jwt_file or key_file.';
    END IF;
END;
$$;

DROP EVENT TRIGGER IF EXISTS trino_fdw_guard;
CREATE EVENT TRIGGER trino_fdw_guard ON ddl_command_end
    WHEN TAG IN ('CREATE SERVER', 'ALTER SERVER',
                 'CREATE USER MAPPING', 'ALTER USER MAPPING',
                 'CREATE FOREIGN TABLE', 'ALTER FOREIGN TABLE',
                 'IMPORT FOREIGN SCHEMA')
    EXECUTE FUNCTION trino_fdw_guard();
