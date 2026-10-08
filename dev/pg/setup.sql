\set ON_ERROR_STOP on
\i /sql/00_extension.sql
\i /sql/20_guard.sql

CREATE SERVER trino FOREIGN DATA WRAPPER multicorn OPTIONS (
    wrapper 'trino_fdw.TrinoFDW',
    host 'trino', port '8443',
    catalog 'memory', schema 'demo',
    auth 'password', user 'svc_fdw',
    password_file '/etc/trino-fdw/password',
    ca_file '/etc/trino-fdw/ca.pem'
);
CREATE ROLE reader LOGIN PASSWORD 'reader';
CREATE USER MAPPING FOR reader SERVER trino OPTIONS (trino_user 'reader');
CREATE USER MAPPING FOR postgres SERVER trino;
GRANT USAGE ON FOREIGN SERVER trino TO reader;

CREATE FOREIGN TABLE audit_events (
    id bigint, event_time timestamp with time zone, username text, action text,
    ok boolean, amount numeric(10,2), score double precision, day date, details jsonb,
    uid uuid, ip inet
) SERVER trino OPTIONS (table 'audit_events');
CREATE FOREIGN TABLE trino_whoami (u text) SERVER trino OPTIONS (query 'SELECT current_user AS u');
GRANT SELECT ON audit_events, trino_whoami TO reader;

-- same Trino, other authenticators
CREATE SERVER trino_cert FOREIGN DATA WRAPPER multicorn OPTIONS (
    wrapper 'trino_fdw.TrinoFDW', host 'trino', port '8443', catalog 'memory', schema 'demo',
    auth 'certificate', cert_file '/etc/trino-fdw/client.crt', key_file '/etc/trino-fdw/client.key',
    ca_file '/etc/trino-fdw/ca.pem'
);
CREATE SERVER trino_jwt FOREIGN DATA WRAPPER multicorn OPTIONS (
    wrapper 'trino_fdw.TrinoFDW', host 'trino', port '8443', catalog 'memory', schema 'demo',
    auth 'jwt', jwt_file '/etc/trino-fdw/token', ca_file '/etc/trino-fdw/ca.pem'
);
CREATE USER MAPPING FOR reader SERVER trino_cert OPTIONS (trino_user 'reader');
CREATE USER MAPPING FOR reader SERVER trino_jwt  OPTIONS (trino_user 'reader');
CREATE USER MAPPING FOR postgres SERVER trino_cert;
CREATE USER MAPPING FOR postgres SERVER trino_jwt;
GRANT USAGE ON FOREIGN SERVER trino_cert, trino_jwt TO reader;
CREATE FOREIGN TABLE whoami_cert (u text) SERVER trino_cert OPTIONS (query 'SELECT current_user AS u');
CREATE FOREIGN TABLE whoami_jwt  (u text) SERVER trino_jwt  OPTIONS (query 'SELECT current_user AS u');
CREATE FOREIGN TABLE events_cert (id bigint, username text) SERVER trino_cert OPTIONS (table 'audit_events');
GRANT SELECT ON whoami_cert, whoami_jwt, events_cert TO reader;
