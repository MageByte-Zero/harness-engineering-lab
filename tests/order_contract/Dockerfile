FROM python@sha256:516b51cb9db2ef061e465c803e50f64361b82cefd91e525b889b967b83e83527
COPY jdk/ /opt/jdk/
COPY jars/ /opt/contract/jars/
COPY ContractCheck.java check.sh /opt/contract/
ENTRYPOINT ["/bin/sh", "/opt/contract/check.sh"]
