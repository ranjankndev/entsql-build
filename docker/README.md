# PostgreSQL

PostgreSQL 18.4 runs in the container `app_postgres`, which is managed by the
human outside this repository (`~/stacks/postgres`) and bound to
`127.0.0.1:5432`. This repo has no compose file and never starts, stops or
execs into the container.

`init/01-roles.sql` creates the database `benchdata`, the owner role
`bench_owner` and the read-only role `bench_read` with default privileges. The
human applies it once:

```sh
docker exec -i app_postgres psql -U postgres -v ON_ERROR_STOP=1 \
  -v owner_password='...' -v read_password='...' < docker/init/01-roles.sql
```

The file is idempotent. Project code only connects over TCP as `bench_owner` or
`bench_read`, with passwords from `~/.pgpass`. Anything that needs superuser
(roles, tuning, backups) is done by the human in the container.
