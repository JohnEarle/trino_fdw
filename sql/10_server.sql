-- Example server definition. Adjust host, catalog, schema and file paths.
-- No secret appears here: the password is read from password_file on the
-- database host, mounted from your secret store.

CREATE SERVER trino FOREIGN DATA WRAPPER multicorn OPTIONS (
    wrapper       'trino_fdw.TrinoFDW',
    host          'trino.example.internal',
    port          '8443',
    catalog       'hive',
    schema        'prod',
    auth          'password',
    user          'svc_fdw',
    password_file '/etc/trino_fdw/password',
    ca_file       '/etc/trino_fdw/ca.pem',
    strict_file_permissions 'true',
    source        'trino_fdw'
);

-- The role your client application connects as. Not a superuser.
CREATE ROLE reader LOGIN;

-- One-to-one identity: queries run by "reader" reach Trino as user "reader"
-- via impersonation by the svc_fdw principal. Still no secret in the catalog.
CREATE USER MAPPING FOR reader SERVER trino OPTIONS (trino_user 'reader');

GRANT USAGE ON FOREIGN SERVER trino TO reader;
