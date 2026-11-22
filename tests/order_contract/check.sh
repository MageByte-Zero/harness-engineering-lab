#!/bin/sh
set -eu
mkdir -p /tmp/classes
jvm_cp='/opt/contract/jars/*'
/opt/jdk/bin/javac -J-Xmx192m -parameters --release 17 -encoding UTF-8 \
  -cp "$jvm_cp" -d /tmp/classes /candidate/code/src/*.java /opt/contract/ContractCheck.java
exec /opt/jdk/bin/java -Xmx128m -XX:-UsePerfData -cp "/tmp/classes:$jvm_cp" demo.orders.ContractCheck
