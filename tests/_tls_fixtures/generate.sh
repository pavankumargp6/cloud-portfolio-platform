#!/bin/sh
# Generate a self-signed CA + server cert for TLS tests.
# All files are test-only and safe to regenerate; they carry no value.
set -e
cd "$(dirname "$0")"

if [ -f server.pem ] && [ -f server.key ] && [ -f ca.pem ]; then
  exit 0
fi

# 1. CA
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout ca.key -out ca.pem \
  -subj "/CN=Agency Reliability Desk Test CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  2>/dev/null

# 2. Server key + CSR
openssl req -newkey rsa:2048 -nodes \
  -keyout server.key -out server.csr \
  -subj "/CN=127.0.0.1" \
  2>/dev/null

# 3. Server cert signed by CA, with SAN for localhost and 127.0.0.1
cat > server-ext.cnf <<EOF
subjectAltName=DNS:localhost,IP:127.0.0.1
basicConstraints=critical,CA:FALSE
EOF
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial \
  -out server.pem -days 365 \
  -extfile server-ext.cnf \
  2>/dev/null

rm -f server.csr ca.srl server-ext.cnf
echo "TLS fixtures generated: ca.pem, server.pem, server.key"
