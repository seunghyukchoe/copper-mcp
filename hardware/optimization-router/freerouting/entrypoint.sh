#!/bin/sh
set -eu

# Fixed DSN stdin -> SES stdout bridge. The operator-provided base must contain these exact paths.
umask 077
mkdir -p /work
trap 'rm -f /work/input.dsn /work/output.ses' EXIT
# /work is a runner-capped tmpfs; this independent ceiling prevents an unbounded shell write.
ulimit -f 32768
cat > /work/input.dsn
# Fixed documented options; stdout must contain only SES, never diagnostics.
# https://github.com/freerouting/freerouting/blob/v2.2.4/docs/command_line_arguments.md
java -Djava.awt.headless=true -Duser.home=/work -Djava.io.tmpdir=/work \
  -jar /opt/freerouting/freerouting-2.2.4.jar \
  -de /work/input.dsn -do /work/output.ses -mp 4 -mt 1 -da -dl \
  --gui.enabled=false --api_server.enabled=false --logging.console.enabled=false \
  --user_data_path=/work/user >/dev/null 2>&1
[ -s /work/output.ses ] || exit 65
cat /work/output.ses
